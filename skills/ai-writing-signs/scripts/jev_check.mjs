#!/usr/bin/env node
// 正規表現で拾えない兆候 (QUESTIONS に定義) を、Jev (TypeSafe の判定 API) に1文ずつ判定させる。
// Node.js 18 以上、標準モジュールのみ。
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const SELF = fileURLToPath(import.meta.url);

function die(message, code) {
  fs.writeSync(2, message.endsWith("\n") ? message : message + "\n");
  process.exit(code);
}

const out1 = (text) => fs.writeSync(1, text.endsWith("\n") ? text : text + "\n");

const RULES_PATH = fileURLToPath(new URL("./rules.json", import.meta.url));
let RULES;
try {
  const top = JSON.parse(fs.readFileSync(RULES_PATH, "utf8"));
  RULES = top.jev;
  if (!Array.isArray(top.checks) || typeof top.whitespace !== "string") throw new Error();
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  if (!isObj(RULES) || !isObj(RULES.questions) || !Array.isArray(RULES.question_selection)) throw new Error();
  for (const sel of RULES.question_selection) {
    if (!isObj(sel) || !isObj(RULES.questions[sel.id])) throw new Error();
    for (const k of ["contains_all", "contains_any"]) {
      if (k in sel && !(Array.isArray(sel[k]) && sel[k].every((t) => typeof t === "string"))) throw new Error();
    }
  }
} catch {
  die(`rules.json を読めない: ${RULES_PATH}`, 2);
}
const { api_url: API_URL, model: MODEL, console_url: CONSOLE_URL } = RULES;
const THRESHOLDS = RULES.thresholds;
const QUESTIONS = RULES.questions;
const CATEGORY_ORDER = RULES.category_order;
const MIN_SENTENCE_LEN = RULES.min_sentence_len;
const TIMEOUT = 60;
const RETRY_DELAYS = [1, 3]; // 429・5xx・タイムアウト・通信断はこの間隔で最大2回まで再試行する
const MAX_WORKERS = 4;
const SETUP_TIMEOUT_MS = 10 * 60 * 1000;

// ---- 文の切り出し (Python 版と同じ結果にするため、空白・改行・数字の範囲を Python に合わせている) ----

const PY_WS = "\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_WS_RE = new RegExp(`[${PY_WS}]`, "u");

function pyStrip(s) {
  return s.replace(new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, "gu"), "");
}

const codePoints = (s) => Array.from(s);

// BOM は外さず、UTF-8 として読めなければ API に送る前に止める。改行は LF にそろえる
function readUtf8(file) {
  try {
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(fs.readFileSync(file));
    return text.replace(/\r\n?/g, "\n");
  } catch (e) {
    if (e instanceof TypeError) die(`UTF-8 ではない: ${file}`, 2);
    throw e;
  }
}

// Python の open() の改行変換 + str.splitlines()
function splitLines(text) {
  const normalized = text.replace(/\r\n?/g, "\n");
  const parts = normalized.split(new RegExp("[\\n\\v\\f\\x1c\\x1d\\x1e\\x85\\u2028\\u2029]"));
  if (parts[parts.length - 1] === "") parts.pop();
  return parts;
}

const HEADING_RE = new RegExp(`^#+[${PY_WS}]`, "u");
const TABLE_RE = new RegExp(`^[${PY_WS}]*\\|`, "u");
const ALERT_RE = new RegExp(`^[${PY_WS}]*>[${PY_WS}]*\\[!`, "u");
const LIST_RE = new RegExp(`^(?:[-*]|\\p{Nd}+[.)])[${PY_WS}]+`, "u");
const QUOTE_RE = new RegExp(`^>[${PY_WS}]?`, "u");

function stripFrontMatterAndCodeBlocks(rawLines) {
  const out = [];
  let inFrontMatter = false;
  let inCodeBlock = false;
  rawLines.forEach((line, i) => {
    if (i === 0 && line === "---") {
      inFrontMatter = true;
      out.push("");
      return;
    }
    if (inFrontMatter) {
      out.push("");
      if (line === "---") inFrontMatter = false;
      return;
    }
    if (line.startsWith("```")) {
      inCodeBlock = !inCodeBlock;
      out.push("");
      return;
    }
    if (inCodeBlock) {
      out.push("");
      return;
    }
    out.push(line);
  });
  return out;
}

function classifyLine(line) {
  line = line.replace(/`([^`]*)`/gu, "$1");
  if (pyStrip(line) === "") return "";
  if (HEADING_RE.test(line)) return "";
  if (TABLE_RE.test(line)) return "";
  if (ALERT_RE.test(line)) return "";
  if (LIST_RE.test(line)) return line.replace(LIST_RE, "");
  if (QUOTE_RE.test(line)) return line.replace(QUOTE_RE, "");
  return line;
}

function buildParagraphs(lines, listStarts) {
  const paragraphs = [];
  let current = [];
  lines.forEach((text, idx) => {
    const lineNo = idx + 1;
    if (pyStrip(text) === "") {
      if (current.length) {
        paragraphs.push(current);
        current = [];
      }
      return;
    }
    if (listStarts[idx] && current.length) {
      paragraphs.push(current);
      current = [];
    }
    current.push([lineNo, text]);
  });
  if (current.length) paragraphs.push(current);
  return paragraphs;
}

function paragraphTextAndOffsets(paragraphLines) {
  let text = "";
  const offsets = [];
  paragraphLines.forEach(([lineNo, lineText], idx) => {
    offsets.push([text.length, lineNo]);
    if (idx > 0) text += " ";
    text += lineText;
  });
  return [text, offsets];
}

function lineForOffset(offsets, offset) {
  let result = offsets[0][1];
  for (const [start, lineNo] of offsets) {
    if (start <= offset) result = lineNo;
    else break;
  }
  return result;
}

const countOf = (s, sub) => s.split(sub).length - 1;

function splitSentences(text) {
  const sentences = [];
  let start = 0;
  let depth = 0;
  let i = 0;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (ch === "「") {
      depth += 1;
    } else if (ch === "」") {
      if (depth > 0) depth -= 1;
    } else if (ch === "。" && depth === 0) {
      let end = i + 1;
      if (text.slice(end, end + 2) === "**" && countOf(text.slice(0, end), "**") % 2 === 1) end += 2;
      sentences.push([start, text.slice(start, end)]);
      start = end;
      i = end;
      continue;
    }
    i += 1;
  }
  return sentences;
}

function collectSentences(file) {
  const rawLines = splitLines(readUtf8(file));
  let lines = stripFrontMatterAndCodeBlocks(rawLines);
  const listStarts = lines.map((line) => LIST_RE.test(line));
  lines = lines.map(classifyLine);
  const paragraphs = buildParagraphs(lines, listStarts);

  const items = [];
  for (const paragraphLines of paragraphs) {
    const [paragraphText, offsets] = paragraphTextAndOffsets(paragraphLines);
    for (const [offset, sentence] of splitSentences(paragraphText)) {
      if (codePoints(sentence).length < MIN_SENTENCE_LEN) continue;
      items.push({
        line: lineForOffset(offsets, offset),
        paragraph: paragraphText,
        sentence: pyStrip(sentence),
      });
    }
  }
  return items;
}

function questionsFor(sentence) {
  const result = {};
  for (const sel of RULES.question_selection) {
    if (sel.contains_all && !sel.contains_all.every((t) => sentence.includes(t))) continue;
    if (sel.contains_any && !sel.contains_any.some((t) => sentence.includes(t))) continue;
    const q = QUESTIONS[sel.id];
    result[sel.id] = { type: q.type, instructions: q.instructions, criteria: q.criteria };
  }
  return result;
}

// ---- API キー ----

class KeyPathError extends Error {}

function absHome() {
  let home = "";
  try {
    home = os.homedir();
  } catch {
    return null;
  }
  return home && path.isAbsolute(home) ? home : null;
}

function keyFilePath() {
  if (process.platform === "win32") {
    let appData = process.env.APPDATA || "";
    if (!(appData && path.win32.isAbsolute(appData))) {
      const profile = process.env.USERPROFILE || "";
      if (!(profile && path.win32.isAbsolute(profile))) throw new KeyPathError();
      appData = path.join(profile, "AppData", "Roaming");
    }
    return path.join(appData, "typesafe", "api_key");
  }
  let base = process.env.XDG_CONFIG_HOME || "";
  // XDG の決まりでは、相対パスの XDG_CONFIG_HOME は無視する
  if (!path.isAbsolute(base)) {
    const home = absHome();
    if (home === null) throw new KeyPathError();
    base = path.join(home, ".config");
  }
  return path.join(base, "typesafe", "api_key");
}

class KeyFileError extends Error {}
class AuthError extends Error {}
class CertError extends Error {}

const CERT_MESSAGE =
  "TypeSafe API の証明書を確かめられない。" +
  "社内の認証局 (CA) の証明書が OS に入っているなら、`node --use-system-ca` で実行する。" +
  "プロキシ経由なら、`NODE_USE_ENV_PROXY=1` を付けて実行する。";
const CERT_MESSAGE_SHORT =
  "証明書を確かめられなかった。`node --use-system-ca` か `NODE_USE_ENV_PROXY=1` を付けて、起動し直す。";
class ApiCallError extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

// 返り値は [キー, 出どころの説明]。キーが無ければ ["", null]
function loadKey() {
  const envKey = pyStrip(process.env.TYPESAFE_API_KEY || "");
  if (envKey) return [envKey, "環境変数 TYPESAFE_API_KEY"];
  let file;
  try {
    file = keyFilePath();
  } catch (e) {
    if (e instanceof KeyPathError) return ["", null];
    throw e;
  }
  try {
    fs.lstatSync(file);
  } catch (e) {
    if (e && e.code === "ENOENT") return ["", null];
    throw new KeyFileError(file);
  }
  let text;
  try {
    // メモ帳などで保存すると先頭に BOM が付くので、BOM は外して読む
    text = new TextDecoder("utf-8", { fatal: true }).decode(fs.readFileSync(file));
  } catch {
    throw new KeyFileError(file);
  }
  const line = text.split(/\r\n|\r|\n/).map(pyStrip).find((l) => l !== "") || "";
  return [line, line ? file : null];
}

function shellQuote(p) {
  return /^[A-Za-z0-9_\/.:\\-]+$/.test(p) ? p : `"${p}"`;
}

function setupCommand() {
  return `node ${shellQuote(SELF)} --setup`;
}

// ---- API 呼び出し ----

const CERT_CODES = new Set([
  "CERT_HAS_EXPIRED",
  "CERT_NOT_YET_VALID",
  "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
  "UNABLE_TO_GET_ISSUER_CERT",
  "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
  "SELF_SIGNED_CERT_IN_CHAIN",
  "DEPTH_ZERO_SELF_SIGNED_CERT",
  "ERR_TLS_CERT_ALTNAME_INVALID",
]);

function httpPost(bodyStr, key) {
  return new Promise((resolve, reject) => {
    let req;
    try {
      req = https.request(
        API_URL,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${key}`,
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(bodyStr),
          },
        },
        (res) => {
          const chunks = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
          res.on("error", () => reject({ kind: "network" }));
          res.on("close", () => {
            if (!res.complete) reject({ kind: "network" });
          });
        },
      );
    } catch {
      reject({ kind: "bad_request" });
      return;
    }
    // ソケットが無通信のまま TIMEOUT 秒たったら打ち切る
    req.setTimeout(TIMEOUT * 1000, () => {
      reject({ kind: "timeout" });
      req.destroy();
    });
    req.on("error", (e) => {
      if (e && (CERT_CODES.has(e.code) || /certificate/i.test(String(e.message)))) reject({ kind: "certificate" });
      else reject({ kind: "network" });
    });
    req.end(bodyStr);
  });
}

const sleep = (sec) => new Promise((r) => setTimeout(r, sec * 1000));

async function callApi(key, state, questions) {
  const bodyStr = JSON.stringify({ state, model: MODEL, questions });
  const attempts = 1 + RETRY_DELAYS.length;
  for (let attempt = 0; attempt < attempts; attempt++) {
    let res;
    try {
      res = await httpPost(bodyStr, key);
    } catch (e) {
      const kind = e && e.kind;
      if (kind === "timeout" || kind === "network") {
        if (attempt < RETRY_DELAYS.length) {
          await sleep(RETRY_DELAYS[attempt]);
          continue;
        }
        throw new ApiCallError(kind);
      }
      if (kind === "certificate") throw new CertError();
      throw new ApiCallError(kind || "bad_response");
    }
    const { status } = res;
    if (status === 401 || status === 403) throw new AuthError();
    if (status === 429 || (status >= 500 && status < 600)) {
      if (attempt < RETRY_DELAYS.length) {
        await sleep(RETRY_DELAYS[attempt]);
        continue;
      }
      throw new ApiCallError(`HTTP ${status}`);
    }
    if (status < 200 || status >= 300) throw new ApiCallError(`HTTP ${status}`);
    try {
      const data = JSON.parse(res.body.toString("utf8"));
      if (data === null || typeof data !== "object" || Array.isArray(data)) throw new Error();
      return data;
    } catch {
      throw new ApiCallError("bad_response");
    }
  }
  throw new ApiCallError("network");
}

async function judgeOne(key, item) {
  let questions;
  try {
    questions = questionsFor(item.sentence);
  } catch {
    throw new ApiCallError("bad_response");
  }
  const state = {
    paragraph: item.paragraph.replaceAll("**", ""),
    sentence: item.sentence.replaceAll("**", ""),
  };
  let data;
  try {
    data = await callApi(key, state, questions);
  } catch (e) {
    if (e instanceof AuthError || e instanceof ApiCallError || e instanceof CertError) throw e;
    throw new ApiCallError("bad_response");
  }
  const answers = data.answers;
  const scores = {};
  if (answers && typeof answers === "object" && !Array.isArray(answers)) {
    for (const qid of Object.keys(questions)) {
      const entry = answers[qid];
      if (entry && typeof entry === "object" && typeof entry.noul === "number") scores[qid] = entry.noul;
    }
  }
  const scoreCount = Object.keys(scores).length;
  // 一部の判定だけ欠けている場合は取れた分だけ使うので、ここに来るのは全滅した時だけ
  if (scoreCount === 0) throw new ApiCallError("bad_response");
  const usage = data.usage && typeof data.usage === "object" ? data.usage : {};
  const rawTokens = "input_tokens" in usage ? usage.input_tokens : "prompt_tokens" in usage ? usage.prompt_tokens : 0;
  const inputTokens = typeof rawTokens === "number" ? rawTokens : 0;
  return { scores, inputTokens, missing: Object.keys(questions).length - scoreCount };
}

// Python の f"{x:.2f}" と同じ丸め (ちょうど中間になる値は偶数側へ)
function fmt2(x) {
  const y = x * 100;
  if (Number.isInteger(x * 8) && (x * 8) % 2 === 1) {
    const lower = Math.floor(y);
    const even = lower % 2 === 0 ? lower : lower + 1;
    return (even / 100).toFixed(2);
  }
  return x.toFixed(2);
}

// ---- --setup ----

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const SETUP_SECTIONS = ["form_title", "message", "form", "done"];
const SETUP_PLACEHOLDERS = ["MESSAGE", "CONSOLE_URL", "KEY_PATH"];
let SETUP_HTML = "";

// --setup のときだけ読み、画面の部品が揃っているかを起動時に確かめる
function loadSetupHtml() {
  const htmlPath = fileURLToPath(new URL("./setup.html", import.meta.url));
  try {
    const html = fs.readFileSync(htmlPath, "utf8");
    const sections = new Set([...html.matchAll(/<!--section:(\w+)-->[\s\S]*?<!--\/section-->/g)].map((m) => m[1]));
    const holders = new Set([...html.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]));
    if (!SETUP_SECTIONS.every((n) => sections.has(n))) throw new Error();
    if (!SETUP_PLACEHOLDERS.every((n) => holders.has(n)) || ![...holders].every((n) => SETUP_PLACEHOLDERS.includes(n))) throw new Error();
    SETUP_HTML = html;
  } catch {
    die(`setup.html を読めない: ${htmlPath}`, 2);
  }
}

// setup.html の <!--section:名前--> ... <!--/section--> のうち、この画面で使うものだけを残して値を差し込む
function renderPage({ keyPath, message, done }) {
  const keep = new Set(done ? ["done"] : message ? ["form_title", "message", "form"] : ["form_title", "form"]);
  const values = { MESSAGE: message || "", CONSOLE_URL, KEY_PATH: keyPath };
  return SETUP_HTML.replace(/<!--section:(\w+)-->\n?([\s\S]*?)<!--\/section-->\n?/g, (_, name, body) => (keep.has(name) ? body : ""))
    .replace(/\{\{(\w+)\}\}/g, (_, k) => escapeHtml(values[k] ?? ""));
}

function openBrowser(url) {
  return new Promise((resolve) => {
    let cmd;
    let args;
    const opts = { stdio: "ignore", detached: true, windowsHide: true };
    if (process.platform === "darwin") {
      cmd = "open";
      args = [url];
    } else if (process.platform === "win32") {
      cmd = "cmd";
      args = ["/c", "start", '""', url];
      opts.windowsVerbatimArguments = true;
    } else {
      cmd = "xdg-open";
      args = [url];
    }
    try {
      const child = spawn(cmd, args, opts);
      child.on("error", () => resolve(false));
      child.on("spawn", () => resolve(true));
      child.unref();
    } catch {
      resolve(false);
    }
  });
}

async function runSetup() {
  loadSetupHtml();
  let keyPath;
  try {
    keyPath = keyFilePath();
  } catch (e) {
    if (e instanceof KeyPathError) die("キーの保存先が決められない (APPDATA / USERPROFILE / HOME を確かめる)。", 1);
    throw e;
  }
  const token = crypto.randomBytes(24).toString("hex");
  const tokenPath = `/${token}`;
  const tokenBuf = Buffer.from(tokenPath);
  let port = 0;
  let busy = false;
  let finished = false;

  const send = (res, status, html) => {
    res.writeHead(status, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Referrer-Policy": "same-origin",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    });
    res.end(html);
  };
  const reject = (res, status, text) => {
    res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" });
    res.end(text);
  };

  const saveKey = (key) => {
    const dir = path.dirname(keyPath);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    try {
      fs.chmodSync(dir, 0o700);
    } catch {
      // Windows などで権限を変えられない場合は無視する
    }
    const tmp = path.join(dir, `.api_key.${crypto.randomBytes(6).toString("hex")}`);
    let fd;
    try {
      fd = fs.openSync(tmp, "wx", 0o600);
      fs.writeSync(fd, key + "\n");
      fs.fsyncSync(fd);
      fs.closeSync(fd);
      fd = undefined;
      fs.renameSync(tmp, keyPath);
    } catch (e) {
      if (fd !== undefined) {
        try {
          fs.closeSync(fd);
        } catch {
          // 閉じられなくても一時ファイルの削除を試みる
        }
      }
      try {
        fs.unlinkSync(tmp);
      } catch {
        // 作れていなければ消すものも無い
      }
      throw e;
    }
  };

  const server = http.createServer((req, res) => {
    if (finished) return reject(res, 403, "forbidden");
    if (req.headers.host !== `127.0.0.1:${port}`) return reject(res, 403, "forbidden");
    const origin = req.headers.origin;
    if (origin !== undefined && origin !== `http://127.0.0.1:${port}`) return reject(res, 403, "forbidden");

    const given = Buffer.from(String((req.url || "").split("?")[0]));
    const tokenOk = given.length === tokenBuf.length && crypto.timingSafeEqual(given, tokenBuf);
    if (!tokenOk) return reject(res, 403, "forbidden");

    if (req.method === "GET") return send(res, 200, renderPage({ keyPath }));
    if (req.method !== "POST") return reject(res, 405, "method not allowed");

    const chunks = [];
    let size = 0;
    let tooBig = false;
    req.on("data", (c) => {
      size += c.length;
      if (size > 8192) {
        tooBig = true;
        return;
      }
      chunks.push(c);
    });
    req.on("error", () => {});
    req.on("end", async () => {
      if (tooBig) return reject(res, 413, "too large");
      if (busy) return send(res, 409, renderPage({ keyPath, message: "確認中。しばらく待ってから、もう一度試す。" }));
      const key = pyStrip(new URLSearchParams(Buffer.concat(chunks).toString("utf8")).get("key") || "");
      if (!key) return send(res, 400, renderPage({ keyPath, message: "キーが空だった。貼り付けてから保存を押す。" }));
      if (!/^[\x21-\x7e]+$/.test(key)) {
        return send(res, 400, renderPage({ keyPath, message: "キーに使えない文字が含まれている。貼り付け直す。" }));
      }
      busy = true;
      try {
        // 使えないキーを保存して、点検のたびに認証で落ちるのを避けるため、保存の前に1回だけ呼んで確かめる
        const probe = "テスト。";
        await callApi(
          key,
          { paragraph: probe, sentence: probe },
          { actor_missing: { type: QUESTIONS.actor_missing.type, instructions: QUESTIONS.actor_missing.instructions, criteria: QUESTIONS.actor_missing.criteria } },
        );
      } catch (e) {
        busy = false;
        if (e instanceof AuthError) {
          return send(res, 401, renderPage({ keyPath, message: "キーが受け付けられなかった (401/403)。キーを確かめて、もう一度入力する。" }));
        }
        if (e instanceof CertError) return send(res, 502, renderPage({ keyPath, message: CERT_MESSAGE_SHORT }));
        const reason = e instanceof ApiCallError ? e.reason : "unknown";
        return send(res, 502, renderPage({ keyPath, message: `キーを確かめられなかった (${reason})。通信を確かめて、もう一度入力する。` }));
      }
      try {
        saveKey(key);
      } catch (e) {
        busy = false;
        const why = e && e.code ? e.code : "error";
        return send(res, 500, renderPage({ keyPath, message: `キーを保存できなかった (${why})。保存先の権限を確かめて、もう一度入力する。` }));
      }
      finished = true;
      // 応答が届いたかどうかに関わらず、保存が済んだら終了する
      let exiting = false;
      const finish = () => {
        if (exiting) return;
        exiting = true;
        try {
          out1(`保存した: ${keyPath} (本人だけが読める権限)。`);
          if (pyStrip(process.env.TYPESAFE_API_KEY || "")) {
            out1("ただし環境変数 TYPESAFE_API_KEY が設定されているので、点検では環境変数が優先される。");
          }
        } catch {
          // 保存は済んでいるので、出力に失敗しても成功として終える
        }
        server.close();
        server.closeAllConnections();
        process.exit(0);
      };
      res.on("finish", finish);
      res.on("close", finish);
      res.on("error", finish);
      setTimeout(finish, 1000).unref();
      try {
        send(res, 200, renderPage({ keyPath, done: true }));
      } catch {
        finish();
      }
    });
  });

  await new Promise((resolve, reject2) => {
    server.once("error", reject2);
    server.listen(0, "127.0.0.1", resolve);
  }).catch(() => die("入力画面用のサーバーを起動できなかった。", 1));
  port = server.address().port;
  const url = `http://127.0.0.1:${port}${tokenPath}`;

  setTimeout(() => {
    if (finished) return;
    die("10 分たっても保存されなかったので終了した。保存していない。", 1);
  }, SETUP_TIMEOUT_MS);

  if (process.env.AI_WRITING_SIGNS_NO_BROWSER === "1") {
    console.log(`このURLを開く:\n${url}`);
  } else {
    console.log(`ブラウザーに入力画面を開く。開かないときは、このURLを開く:\n${url}`);
    openBrowser(url).then((ok) => {
      if (!ok) console.log(`ブラウザーを開けなかった。このURLを開く:\n${url}`);
    });
  }
  console.log(`キーを貼って保存するまで待つ (10 分で終了する)。保存先: ${keyPath}`);
}

// ---- 点検 ----

function usageExit() {
  die("usage: jev_check.mjs <file>\n       jev_check.mjs --setup", 2);
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1) usageExit();
  if (args[0] === "--setup") {
    await runSetup();
    return;
  }
  const file = args[0];
  let isFile = false;
  try {
    isFile = fs.statSync(file).isFile();
  } catch {
    isFile = false;
  }
  if (!isFile) usageExit();

  const items = collectSentences(file);
  if (items.length === 0) {
    out1("判定する文が無い");
    process.exit(0);
  }

  let key;
  let keySource;
  try {
    [key, keySource] = loadKey();
  } catch (e) {
    if (e instanceof KeyFileError) die(`キーのファイルを読めない: ${e.message}。権限と中身を確かめるか、\`${setupCommand()}\` で保存し直す。`, 3);
    throw e;
  }
  if (!key) {
    die(
      "TypeSafe の API キーが無いので、Jev の判定はしていない。\n" +
        `キーを設定するには、エージェントが \`${setupCommand()}\` を実行する。` +
        "ブラウザーに入力画面が開くので、利用者がキーを貼って保存する。未登録なら、画面の手順で登録から進める。\n" +
        `キーは ${CONSOLE_URL} で作れる。今回は SKILL.md の点検手順 2 の観点で読む。`,
      3,
    );
  }

  const results = new Map(); // item -> 結果
  let failed = 0;
  const failureReasons = new Map();
  let totalInputTokens = 0;
  const authFailMessage = `TypeSafe API の認証に失敗した (401/403)。${keySource} のキーが無効な可能性がある。`;

  const recordFailure = (reason) => {
    failed += 1;
    failureReasons.set(reason, (failureReasons.get(reason) ?? 0) + 1);
  };
  const handle = async (item) => {
    try {
      const result = await judgeOne(key, item);
      results.set(item, result);
      totalInputTokens += result.inputTokens;
    } catch (e) {
      if (e instanceof AuthError) die(authFailMessage, 4);
      if (e instanceof CertError) die(CERT_MESSAGE, 5);
      recordFailure(e instanceof ApiCallError ? e.reason : "bad_response");
    }
  };

  // 認証エラーは全文が失敗するので、最初の1文だけ先に判定して早く検知する
  await handle(items[0]);
  const remaining = items.slice(1);
  let next = 0;
  const worker = async () => {
    while (next < remaining.length) await handle(remaining[next++]);
  };
  await Promise.all(Array.from({ length: Math.min(MAX_WORKERS, remaining.length) }, worker));

  const byCategory = Object.fromEntries(CATEGORY_ORDER.map((q) => [q, []]));
  let partial = 0;
  for (const item of items) {
    const result = results.get(item);
    if (!result) continue;
    if (result.missing) partial += 1;
    for (const [qid, score] of Object.entries(result.scores)) {
      if (score >= THRESHOLDS[qid]) byCategory[qid].push([item.line, score, item.sentence]);
    }
  }

  let out = "";
  for (const qid of CATEGORY_ORDER) {
    const entries = byCategory[qid];
    out += `## ${QUESTIONS[qid].label}: ${entries.length} 件 (しきい値 ${THRESHOLDS[qid]})\n`;
    for (const [lineNo, score, sentence] of [...entries].sort((a, b) => b[1] - a[1])) {
      out += `${lineNo}:${fmt2(score)}: ${codePoints(sentence).slice(0, 100).join("")}\n`;
    }
    out += "\n";
  }
  out += `判定した文: ${items.length - failed} / 失敗: ${failed} / 入力トークン計: ${totalInputTokens}\n`;
  if (failed > 0) {
    out += `失敗の内訳: ${[...failureReasons].map(([reason, count]) => `${reason} ×${count}`).join(", ")}\n`;
  }
  if (partial > 0) out += `一部の判定が返らなかった文: ${partial}\n`;
  process.exitCode = failed > 0 || partial > 0 ? 1 : 0;
  process.stdout.write(out);
}

main();
