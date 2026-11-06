#!/usr/bin/env node
// 正規表現で機械的に拾える兆候を数える。Node.js 18 以上、標準モジュールのみ。
import { existsSync, readFileSync, statSync, writeSync } from "node:fs";
import { fileURLToPath } from "node:url";

function die(message, code) {
  writeSync(2, message.endsWith("\n") ? message : message + "\n");
  process.exit(code);
}

// BOM は外さず、UTF-8 として読めなければ API に送る前に止める。改行は LF にそろえる
function readUtf8(path) {
  try {
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(readFileSync(path));
    return text.replace(/\r\n?/g, "\n");
  } catch (e) {
    if (e instanceof TypeError) die(`UTF-8 ではない: ${path}`, 2);
    throw e;
  }
}

const file = process.argv[2];
if (process.argv.length < 3) {
  process.stderr.write(`usage: ${process.argv[1]} <file>\n`);
  process.exit(2);
}
if (!existsSync(file) || !statSync(file).isFile()) {
  process.stderr.write(`not found: ${file}\n`);
  process.exit(2);
}

const RULES_PATH = fileURLToPath(new URL("./rules.json", import.meta.url));
let RULES;
try {
  RULES = JSON.parse(readFileSync(RULES_PATH, "utf8"));
  if (!Array.isArray(RULES.checks) || typeof RULES.whitespace !== "string") throw new Error();
} catch {
  die(`rules.json を読めない: ${RULES_PATH}`, 2);
}
// rules.json の <WS> は空白の文字クラス (JavaScript の \s とは U+0085 と U+FEFF が違うため、明示している)
const expand = (p) => p.replaceAll("<WS>", RULES.whitespace);
const SKIP_HEAD_RES = RULES.prose_skip_heads.map((p) => new RegExp(expand(p), "u"));
const BLANK_RE = new RegExp(expand(RULES.blank_pattern), "u");

const chars = (s) => Array.from(s);

// 行番号を元ファイルと一致させるため、front matter とコードブロックは空行に置き換える
function loadBody(path) {
  const text = readUtf8(path);
  const raw = text.split("\n");
  if (raw[raw.length - 1] === "") raw.pop();
  const out = [];
  let fm = false;
  let skip = false;
  raw.forEach((line, i) => {
    if (i === 0 && line === "---") {
      fm = true;
      out.push("");
      return;
    }
    if (fm) {
      out.push("");
      if (line === "---") fm = false;
      return;
    }
    if (line.startsWith("```")) {
      skip = !skip;
      out.push("");
      return;
    }
    if (skip) {
      out.push("");
      return;
    }
    out.push(line);
  });
  return out.map((l) => l.replace(/`[^`]*`/g, ""));
}

// シェルのコマンド置換と同じく、末尾の改行は落とす
function trimTrailingNewlines(lines) {
  const joined = lines.join("\n").replace(/\n+$/, "");
  return joined;
}

const bodyText = trimTrailingNewlines(loadBody(file));
const bodyLines = bodyText.split("\n"); // printf '%s\n' "$body" と同じ並び (空なら空行1つ)

let out = "";

function runRegex(rule) {
  const min = rule.min ?? 1;
  const re = new RegExp(expand(rule.pattern), "su");
  const hits = [];
  bodyLines.forEach((line, i) => {
    if (re.test(line)) hits.push(`${i + 1}:${line}`);
  });
  const n = hits.length;
  if (n < min) {
    out += min > 1 ? `## ${rule.label}: ${n} 件 (${min} 件未満は表示しない)\n\n` : `## ${rule.label}: 0 件\n\n`;
    return;
  }
  out += `## ${rule.label}: ${n} 件\n`;
  for (const h of hits) out += chars(h).slice(0, 140).join("") + "\n";
  out += "\n";
}

function runBulletRun(rule) {
  out += `## ${rule.label}\n`;
  const re = new RegExp(expand(rule.pattern), "u");
  let start = 0;
  let count = 0;
  bodyLines.forEach((line, i) => {
    const nr = i + 1;
    if (re.test(line)) {
      if (!count) start = nr;
      count++;
      return;
    }
    if (count >= rule.min_run) out += `行 ${start}-${nr - 1}: ${count} 行\n`;
    count = 0;
  });
  if (count >= rule.min_run) out += `行 ${start}-${bodyLines.length}: ${count} 行\n`;
  out += "\n";
}

function runEndingRepeat(rule) {
  out += `## ${rule.label}\n`;
  const blank = new RegExp(expand(rule.blank_pattern), "u");
  const countOf = (s, sub) => s.split(sub).length - 1;
  const counts = rule.endings.map(() => 0);
  let start = 0;
  const flush = () => {
    if (rule.endings.some((e, k) => counts[k] >= e.min)) {
      out += `行 ${start} からの段落: ${rule.endings.map((e, k) => `${e.name}=${counts[k]}`).join(" ")}\n`;
    }
    counts.fill(0);
    start = 0;
  };
  bodyLines.forEach((line, i) => {
    if (blank.test(line)) {
      flush();
      return;
    }
    if (!start) start = i + 1;
    rule.endings.forEach((e, k) => (counts[k] += countOf(line, e.text)));
  });
  flush();
  out += "\n";
}

function runHeadingOnly(rule) {
  out += `## ${rule.label}\n`;
  const heading = new RegExp(expand(rule.heading_pattern), "u");
  const depthRe = new RegExp(expand(rule.depth_pattern), "u");
  const blank = new RegExp(expand(rule.blank_pattern), "u");
  let prev = "";
  let pl = 0;
  let pd = 0;
  bodyLines.forEach((line, i) => {
    if (heading.test(line)) {
      const depth = line.match(depthRe)[0].length;
      if (prev !== "" && depth <= pd) out += `行 ${pl}: ${prev}\n`;
      prev = line;
      pl = i + 1;
      pd = depth;
      return;
    }
    if (blank.test(line)) return;
    prev = "";
  });
  out += "\n";
}

// 空行で区切った段落 (行番号つき)。入力は printf '%s\n' "$body" 相当なので末尾に空行が1つ付く
const paragraphs = [];
{
  const lines = bodyText.split("\n").concat([""]);
  let cur = [];
  let start;
  lines.forEach((line, i) => {
    const ln = i + 1;
    if (BLANK_RE.test(line)) {
      if (cur.length) paragraphs.push({ start, lines: cur });
      cur = [];
      start = undefined;
      return;
    }
    if (start === undefined) start = ln;
    cur.push(line);
  });
  if (cur.length) paragraphs.push({ start, lines: cur });
}

function runOpeningRepeat(rule) {
  out += `## ${rule.label}\n`;
  const strip = new RegExp(expand(rule.strip_prefix), "u");
  const count = new Map();
  const linesFor = new Map();
  for (const p of paragraphs) {
    if (SKIP_HEAD_RES.some((re) => re.test(p.lines[0]))) continue;
    const text = p.lines.join("").replace(strip, "");
    const head = chars(text).slice(0, rule.length).join("");
    if (chars(head).length < rule.length) continue;
    count.set(head, (count.get(head) ?? 0) + 1);
    if (!linesFor.has(head)) linesFor.set(head, []);
    linesFor.get(head).push(p.start);
  }
  // 同数のときは出現順 (安定ソート)
  const keys = [...count.keys()].sort((a, b) => count.get(b) - count.get(a));
  for (const k of keys) {
    if (count.get(k) < rule.min_paragraphs) continue;
    out += `書き出し「${k}」: ${count.get(k)} 段落 (行 ${linesFor.get(k).join(", ")})\n`;
  }
  out += "\n";
}

function runShortSentenceShortage(rule) {
  out += `## ${rule.label}\n`;
  const sentenceRe = new RegExp(expand(rule.sentence_pattern), "gsu");
  let total = 0;
  let shortCount = 0;
  for (const p of paragraphs) {
    if (SKIP_HEAD_RES.some((re) => re.test(p.lines[0]))) continue;
    const text = p.lines.join("");
    for (const m of text.matchAll(sentenceRe)) {
      total++;
      if (chars(m[0]).length <= rule.short_len) shortCount++;
    }
  }
  if (total >= rule.min_total && shortCount <= rule.max_short) out += `文 ${total} / ${rule.short_len}字以下 ${shortCount}\n`;
}

const RUNNERS = {
  regex: runRegex,
  bullet_run: runBulletRun,
  ending_repeat: runEndingRepeat,
  heading_only: runHeadingOnly,
  opening_repeat: runOpeningRepeat,
  short_sentence_shortage: runShortSentenceShortage,
};
for (const rule of RULES.checks) RUNNERS[rule.kind](rule);

process.stdout.write(out);
