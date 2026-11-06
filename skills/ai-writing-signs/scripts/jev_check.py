#!/usr/bin/env python3
"""正規表現で拾えない兆候 (QUESTIONS に定義) を、Jev (TypeSafe の判定 API) に1文ずつ判定させる。
標準ライブラリのみで動く。
"""
import concurrent.futures
import errno
import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer



def _setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except Exception:
            try:
                stream.reconfigure(errors="replace", newline="\n")
            except Exception:
                pass


_setup_stdio()

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.join(HERE, os.path.basename(__file__))
RULES_PATH = os.path.join(HERE, "rules.json")
try:
    with open(RULES_PATH, encoding="utf-8") as _f:
        _TOP_RULES = json.load(_f)
    RULES = _TOP_RULES["jev"]
    API_URL = RULES["api_url"]
    MODEL = RULES["model"]
    CONSOLE_URL = RULES["console_url"]
    THRESHOLDS = RULES["thresholds"]
    QUESTIONS = RULES["questions"]
    CATEGORY_ORDER = RULES["category_order"]
    MIN_SENTENCE_LEN = RULES["min_sentence_len"]
    if not isinstance(QUESTIONS, dict) or not isinstance(RULES["question_selection"], list):
        raise ValueError("bad shape")
    if not isinstance(_TOP_RULES.get("checks"), list) or not isinstance(_TOP_RULES.get("whitespace"), str):
        raise ValueError("bad shape")
    for _sel in RULES["question_selection"]:
        if not isinstance(_sel, dict) or _sel.get("id") not in QUESTIONS or not isinstance(QUESTIONS[_sel["id"]], dict):
            raise ValueError("bad shape")
        for _k in ("contains_all", "contains_any"):
            if _k in _sel and not (isinstance(_sel[_k], list) and all(isinstance(_t, str) for _t in _sel[_k])):
                raise ValueError("bad shape")
except (OSError, ValueError, KeyError, TypeError):
    sys.stderr.write("rules.json を読めない: %s\n" % RULES_PATH)
    sys.exit(2)
SYSTEM_PYTHON = "/usr/bin/python3"
REEXEC_ENV = "AI_WRITING_SIGNS_REEXEC"
CERT_ERROR_MESSAGE = (
    "この python3 ({python}) では、TypeSafe API の証明書を確かめられない。"
    "python.org 版なら、付属の「Install Certificates.command」を実行する。"
    "macOS なら、xcode-select --install のあと /usr/bin/python3 で実行してもよい。"
)
TIMEOUT = 60
RETRY_DELAYS = (1, 3)  # 429・5xx・タイムアウト・通信断はこの間隔で最大2回まで再試行する
SETUP_TIMEOUT = 10 * 60


def usage_exit():
    print("usage: jev_check.py <file>\n       jev_check.py --setup", file=sys.stderr)
    sys.exit(2)


class KeyPathError(Exception):
    pass


def _abs_home():
    home = os.path.expanduser("~")
    return home if home != "~" and os.path.isabs(home) else None


def key_file_path():
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA", "")
        if not (app_data and os.path.isabs(app_data)):
            profile = os.environ.get("USERPROFILE", "")
            if not (profile and os.path.isabs(profile)):
                raise KeyPathError()
            app_data = os.path.join(profile, "AppData", "Roaming")
        return os.path.join(app_data, "typesafe", "api_key")
    base = os.environ.get("XDG_CONFIG_HOME", "")
    # XDG の決まりでは、相対パスの XDG_CONFIG_HOME は無視する
    if not os.path.isabs(base):
        home = _abs_home()
        if home is None:
            raise KeyPathError()
        base = os.path.join(home, ".config")
    return os.path.join(base, "typesafe", "api_key")


class KeyFileError(Exception):
    pass


def load_key():
    """(キー, 出どころの説明) を返す。キーが無ければ ("", None)。"""
    # エージェントが動かすシェルはシェルの設定ファイルを読まないことが多いので、環境変数が無ければファイルを見る
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key, "環境変数 TYPESAFE_API_KEY"
    try:
        path = key_file_path()
    except KeyPathError:
        return "", None
    try:
        os.lstat(path)
    except FileNotFoundError:
        return "", None
    except OSError:
        raise KeyFileError(path) from None
    try:
        # メモ帳などで保存すると先頭に BOM が付くので utf-8-sig で読む
        with open(path, encoding="utf-8-sig") as f:
            lines = [line.strip() for line in f]
    except (OSError, ValueError):
        raise KeyFileError(path) from None
    key = next((line for line in lines if line), "")
    return key, (path if key else None)


def shell_quote(p):
    return p if re.fullmatch(r"[A-Za-z0-9_/.:\\-]+", p) else '"%s"' % p


def setup_command():
    return "%s %s --setup" % (shell_quote(sys.executable), shell_quote(SELF))


def cert_error_exit():
    """python.org 版の python3 は、証明書を別に入れないと HTTPS が通らない。

    macOS で開発者ツールが入っていれば、付属の python3 で同じ引数のまま実行し直す。
    """
    can_reexec = (
        sys.platform == "darwin"
        and not os.environ.get(REEXEC_ENV)
        and os.path.exists(SYSTEM_PYTHON)
        and os.path.realpath(sys.executable) != os.path.realpath(SYSTEM_PYTHON)
        and has_developer_tools()
    )
    if can_reexec:
        os.environ[REEXEC_ENV] = "1"
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execv(SYSTEM_PYTHON, [SYSTEM_PYTHON, SELF] + sys.argv[1:])
        except OSError:
            os.environ.pop(REEXEC_ENV, None)
    print(CERT_ERROR_MESSAGE.format(python=sys.executable), file=sys.stderr)
    sys.exit(5)


def has_developer_tools():
    # 開発者ツールが無いと /usr/bin/python3 はインストールを促す画面を出すだけなので、先に確かめる
    try:
        return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def check_certificates():
    """キーを入力させる前に、この python3 で API に HTTPS で繋がるかだけを確かめる。"""
    host = urllib.parse.urlparse(API_URL).hostname
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
                return
    except ssl.SSLCertVerificationError:
        cert_error_exit()
    except OSError:
        # 通信の問題は、このあとのキーの確認で理由つきで分かるので、ここでは止めない
        return


# ---- --setup ----

def open_browser(url):
    """待たずに戻る。webbrowser.open は Linux で端末用のブラウザーを前面で起動して止まることがある。"""
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        if sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], **quiet)
            return True
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, url], start_new_session=True, **quiet)
            return True
    except OSError:
        pass
    return False


_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def escape_html(s):
    return "".join(_ESCAPES.get(c, c) for c in s)


SETUP_HTML_PATH = os.path.join(HERE, "setup.html")
SETUP_HTML = ""
_SETUP_SECTIONS = ("form_title", "message", "form", "done")
_SETUP_PLACEHOLDERS = ("MESSAGE", "CONSOLE_URL", "KEY_PATH")


def load_setup_html():
    """--setup のときだけ読む。必要なセクションとプレースホルダーが揃っていなければ終了する。"""
    global SETUP_HTML
    try:
        with open(SETUP_HTML_PATH, encoding="utf-8") as f:
            html = f.read()
        sections = set(re.findall(r"<!--section:(\w+)-->[\s\S]*?<!--/section-->", html))
        holders = set(re.findall(r"\{\{(\w+)\}\}", html))
        ok = all(n in sections for n in _SETUP_SECTIONS)
        ok = ok and all(n in holders for n in _SETUP_PLACEHOLDERS) and all(n in _SETUP_PLACEHOLDERS for n in holders)
    except (OSError, ValueError):
        ok = False
    if not ok:
        print("setup.html を読めない: %s" % SETUP_HTML_PATH, file=sys.stderr)
        sys.exit(2)
    SETUP_HTML = html


def render_page(key_path, message=None, done=False):
    """setup.html の <!--section:名前--> ... <!--/section--> のうち、この画面で使うものだけを残して値を差し込む。"""
    if done:
        keep = {"done"}
    elif message:
        keep = {"form_title", "message", "form"}
    else:
        keep = {"form_title", "form"}
    values = {"MESSAGE": message or "", "CONSOLE_URL": CONSOLE_URL, "KEY_PATH": key_path}
    html = re.sub(
        r"<!--section:(\w+)-->\n?([\s\S]*?)<!--/section-->\n?",
        lambda m: m.group(2) if m.group(1) in keep else "",
        SETUP_HTML,
    )
    return re.sub(r"\{\{(\w+)\}\}", lambda m: escape_html(values.get(m.group(1), "")), html)


def save_key(key_path, key):
    d = os.path.dirname(key_path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass  # Windows などで権限を変えられない場合は無視する
    tmp = None
    try:
        # 書きかけのファイルが残らないよう、同じフォルダーに別名 (権限 600) で書いてから置き換える
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".api_key.")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(key + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, key_path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def run_setup():
    load_setup_html()
    try:
        key_path = key_file_path()
    except KeyPathError:
        print("キーの保存先が決められない (APPDATA / USERPROFILE / HOME を確かめる)。", file=sys.stderr)
        sys.exit(1)
    check_certificates()
    token_path = "/" + secrets.token_hex(24)
    state = {"port": 0, "finished": False}
    busy = threading.Lock()
    done_event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass  # 標準のログは URL (合言葉入り) を stderr に出すので止める

        def _send(self, status, html):
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()

        def _reject(self, status, text):
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self):
            if state["finished"]:
                return self._reject(403, "forbidden")
            port = state["port"]
            if self.headers.get("Host") != "127.0.0.1:%d" % port:
                return self._reject(403, "forbidden")
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://127.0.0.1:%d" % port:
                return self._reject(403, "forbidden")
            given = self.path.split("?")[0].encode("utf-8")
            if not hmac.compare_digest(given, token_path.encode("utf-8")):
                return self._reject(403, "forbidden")
            if self.command == "GET":
                return self._send(200, render_page(key_path))
            if self.command != "POST":
                return self._reject(405, "method not allowed")

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._reject(400, "bad request")
            if length < 0 or length > 8192:
                return self._reject(413, "too large")
            body = self.rfile.read(length)

            if not busy.acquire(blocking=False):
                return self._send(409, render_page(key_path, "確認中。しばらく待ってから、もう一度試す。"))
            try:
                fields = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
                key = (fields.get("key") or [""])[0].strip()
                if not key:
                    return self._send(400, render_page(key_path, "キーが空だった。貼り付けてから保存を押す。"))
                if not re.fullmatch(r"[\x21-\x7e]+", key):
                    return self._send(400, render_page(key_path, "キーに使えない文字が含まれている。貼り付け直す。"))
                # 使えないキーを保存して、点検のたびに認証で落ちるのを避けるため、保存の前に1回だけ呼んで確かめる
                probe = "テスト。"
                q = QUESTIONS["actor_missing"]
                try:
                    call_api(
                        key,
                        {"paragraph": probe, "sentence": probe},
                        {"actor_missing": {"type": q["type"], "instructions": q["instructions"], "criteria": q["criteria"]}},
                    )
                except AuthError:
                    return self._send(401, render_page(key_path, "キーが受け付けられなかった (401/403)。キーを確かめて、もう一度入力する。"))
                except CertError:
                    return self._send(502, render_page(key_path, CERT_ERROR_MESSAGE.format(python=sys.executable)))
                except ApiCallError as e:
                    return self._send(502, render_page(key_path, "キーを確かめられなかった (%s)。通信を確かめて、もう一度入力する。" % e.reason))
                try:
                    save_key(key_path, key)
                except OSError as e:
                    why = errno.errorcode.get(e.errno, "error") if e.errno is not None else "error"
                    return self._send(500, render_page(key_path, "キーを保存できなかった (%s)。保存先の権限を確かめて、もう一度入力する。" % why))
                state["finished"] = True
                try:
                    self._send(200, render_page(key_path, done=True))
                except Exception:
                    pass  # 保存は済んでいるので、応答を送れなくても終了に進む
                finally:
                    done_event.set()
            finally:
                busy.release()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    class Server(ThreadingHTTPServer):
        # 既定は True。同じポートに別のプロセスが居ても、合言葉入りの URL を横取りされないようにする
        allow_reuse_address = False

    try:
        server = Server(("127.0.0.1", 0), Handler)
    except OSError:
        print("入力画面用のサーバーを起動できなかった。", file=sys.stderr)
        sys.exit(1)
    server.daemon_threads = True
    state["port"] = server.server_address[1]
    url = "http://127.0.0.1:%d%s" % (state["port"], token_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    if os.environ.get("AI_WRITING_SIGNS_NO_BROWSER") == "1":
        print("このURLを開く:\n" + url)
    else:
        print("ブラウザーに入力画面を開く。開かないときは、このURLを開く:\n" + url)
        if not open_browser(url):
            print("ブラウザーを開けなかった。このURLを開く:\n" + url)
    print("キーを貼って保存するまで待つ (10 分で終了する)。保存先: " + key_path)
    sys.stdout.flush()

    try:
        saved = done_event.wait(SETUP_TIMEOUT)
    except KeyboardInterrupt:
        print("\n中断した。保存していない。", file=sys.stderr)
        os._exit(130)
    if not saved:
        print("10 分たっても保存されなかったので終了した。保存していない。", file=sys.stderr)
        os._exit(1)
    try:
        print("保存した: %s (本人だけが読める権限)。" % key_path)
        if os.environ.get("TYPESAFE_API_KEY", "").strip():
            print("ただし環境変数 TYPESAFE_API_KEY が設定されているので、点検では環境変数が優先される。")
        sys.stdout.flush()
    except Exception:
        pass  # 保存は済んでいるので、出力に失敗しても成功として終える
    os._exit(0)


def strip_front_matter_and_code_blocks(raw_lines):
    """front matter とコードブロックを行番号を保ったまま空行に置き換える。"""
    out = []
    in_front_matter = False
    in_code_block = False
    for i, line in enumerate(raw_lines):
        if i == 0 and line == "---":
            in_front_matter = True
            out.append("")
            continue
        if in_front_matter:
            out.append("")
            if line == "---":
                in_front_matter = False
            continue
        if line.startswith("```"):
            in_code_block = not in_code_block
            out.append("")
            continue
        if in_code_block:
            out.append("")
            continue
        out.append(line)
    return out


_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_HEADING_RE = re.compile(r"^#+\s")
_TABLE_RE = re.compile(r"^\s*\|")
_ALERT_RE = re.compile(r"^\s*>\s*\[!")
_LIST_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+")
_QUOTE_RE = re.compile(r"^>\s?")


def classify_line(line):
    """行ごとの前処理。判定対象から外す行は空文字を返す (段落の区切りとしても働く)。"""
    line = _INLINE_CODE_RE.sub(r"\1", line)
    if line.strip() == "":
        return ""
    if _HEADING_RE.match(line):
        return ""
    if _TABLE_RE.match(line):
        return ""
    if _ALERT_RE.match(line):
        return ""
    if _LIST_RE.match(line):
        return _LIST_RE.sub("", line, count=1)
    if _QUOTE_RE.match(line):
        return _QUOTE_RE.sub("", line, count=1)
    return line


def build_paragraphs(lines, list_starts):
    """空行、および箇条書きの項目の開始で区切った段落ごとに、行番号付きの行リストを持たせる。

    箇条書きの項目どうしは間に空行が無くても連結しない。項目の続きである
    行頭が空白の継続行は、直前の項目にそのまま含める。
    """
    paragraphs = []
    current = []
    for line_no, (text, is_list_start) in enumerate(zip(lines, list_starts), start=1):
        if text.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
            continue
        if is_list_start and current:
            paragraphs.append(current)
            current = []
        current.append((line_no, text))
    if current:
        paragraphs.append(current)
    return paragraphs


def paragraph_text_and_offsets(paragraph_lines):
    """段落内の行を結合しつつ、文字位置ごとの行番号を引けるようにする。"""
    text = ""
    offsets = []  # (start_offset, line_no) の昇順リスト
    for idx, (line_no, line_text) in enumerate(paragraph_lines):
        # 行をつなぐ空白は次の文の先頭に入るので、行の開始位置は空白の位置に合わせる
        offsets.append((len(text), line_no))
        if idx > 0:
            text += " "
        text += line_text
    return text, offsets


def line_for_offset(offsets, offset):
    result = offsets[0][1]
    for start, line_no in offsets:
        if start <= offset:
            result = line_no
        else:
            break
    return result


def split_sentences(text):
    """「。」で文を切る。鉤括弧「」の中の「。」では切らない。

    「。」の直後に太字の閉じ `**` が続く場合は、閉じまでを前の文に含める
    (次の文の先頭に閉じ `**` だけが漏れるのを防ぐ)。
    """
    sentences = []
    start = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "「":
            depth += 1
        elif ch == "」":
            if depth > 0:
                depth -= 1
        elif ch == "。" and depth == 0:
            end = i + 1
            # 「。」の直後の `**` は、次の文の太字の開きのこともある。
            # ここまでの `**` が奇数個なら太字が開いたままなので、閉じと判断する
            if text[end : end + 2] == "**" and text[:end].count("**") % 2 == 1:
                end += 2
            sentences.append((start, text[start:end]))
            start = end
            i = end
            continue
        i += 1
    return sentences


def collect_sentences(path):
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        print("UTF-8 ではない: %s" % path, file=sys.stderr)
        sys.exit(2)
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    lines = strip_front_matter_and_code_blocks(raw_lines)
    list_starts = [bool(_LIST_RE.match(line)) for line in lines]
    lines = [classify_line(line) for line in lines]
    paragraphs = build_paragraphs(lines, list_starts)

    items = []
    for paragraph_lines in paragraphs:
        paragraph_text, offsets = paragraph_text_and_offsets(paragraph_lines)
        for offset, sentence in split_sentences(paragraph_text):
            if len(sentence) < MIN_SENTENCE_LEN:
                continue
            items.append(
                {
                    "line": line_for_offset(offsets, offset),
                    "paragraph": paragraph_text,
                    "sentence": sentence.strip(),
                }
            )
    return items


def questions_for(sentence):
    result = {}
    for sel in RULES["question_selection"]:
        if "contains_all" in sel and not all(t in sentence for t in sel["contains_all"]):
            continue
        if "contains_any" in sel and not any(t in sentence for t in sel["contains_any"]):
            continue
        q = QUESTIONS[sel["id"]]
        result[sel["id"]] = {"type": q["type"], "instructions": q["instructions"], "criteria": q["criteria"]}
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 3xx には従わず HTTPError にする。Authorization を別のホストへ送らないため
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class AuthError(Exception):
    pass


class CertError(Exception):
    pass


class ApiCallError(Exception):
    """失敗の種別 (HTTP 400 / timeout / network / bad_response など) を短い文字列で持つ。"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def call_api(key, state, questions):
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    attempts = 1 + len(RETRY_DELAYS)
    for attempt in range(attempts):
        try:
            with _OPENER.open(req, timeout=TIMEOUT) as resp:
                parsed = json.load(resp)
            if not isinstance(parsed, dict):
                raise ApiCallError("bad_response")
            return parsed
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError() from None
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise ApiCallError(f"HTTP {e.code}") from None
            raise ApiCallError(f"HTTP {e.code}") from None
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
                raise CertError() from None
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise ApiCallError("network") from None
        except ssl.SSLCertVerificationError:
            raise CertError() from None
        except (TimeoutError, socket.timeout):
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise ApiCallError("timeout") from None
        except (OSError, http.client.HTTPException):
            # 応答の読み取り中に接続が切れた場合 (RemoteDisconnected・ConnectionResetError・IncompleteRead など)
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise ApiCallError("network") from None
        except ApiCallError:
            raise
        except Exception:
            # レスポンスの形が壊れている場合などは、この文の失敗として扱う
            raise ApiCallError("bad_response") from None
    raise ApiCallError("network")


def judge_one(key, item):
    try:
        questions = questions_for(item["sentence"])
    except (KeyError, TypeError, AttributeError):
        raise ApiCallError("bad_response") from None
    state = {
        "paragraph": item["paragraph"].replace("**", ""),
        "sentence": item["sentence"].replace("**", ""),
    }
    data = call_api(key, state, questions)
    if not isinstance(data, dict):
        raise ApiCallError("bad_response")
    answers = data.get("answers")
    scores = {}
    if isinstance(answers, dict):
        for qid in questions:
            entry = answers.get(qid)
            if isinstance(entry, dict) and isinstance(entry.get("noul"), (int, float)) and not isinstance(entry["noul"], bool):
                scores[qid] = entry["noul"]
    if not scores:
        # 一部の判定だけ欠けている場合は取れた分だけ使うので、ここに来るのは全滅した時だけ
        raise ApiCallError("bad_response")
    usage = data.get("usage")
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        raise ApiCallError("bad_response")
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    if input_tokens is None:
        input_tokens = 0
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, (int, float)):
        raise ApiCallError("bad_response")
    return item, {"scores": scores, "input_tokens": input_tokens, "missing": len(questions) - len(scores)}


def main():
    if len(sys.argv) != 2:
        usage_exit()
    if sys.argv[1] == "--setup":
        run_setup()
        return
    path = sys.argv[1]
    if not os.path.isfile(path):
        usage_exit()

    items = collect_sentences(path)
    if not items:
        print("判定する文が無い")
        sys.exit(0)

    try:
        key, key_source = load_key()
    except KeyFileError as e:
        print(f"キーのファイルを読めない: {e}。権限と中身を確かめるか、`{setup_command()}` で保存し直す。", file=sys.stderr)
        sys.exit(3)
    if not key:
        print(
            "TypeSafe の API キーが無いので、Jev の判定はしていない。\n"
            f"キーを設定するには、エージェントが `{setup_command()}` を実行する。"
            "ブラウザーに入力画面が開くので、利用者がキーを貼って保存する。未登録なら、画面の手順で登録から進める。\n"
            f"キーは {CONSOLE_URL} で作れる。今回は SKILL.md の点検手順 2 の観点で読む。",
            file=sys.stderr,
        )
        sys.exit(3)

    results = {}  # id(item) -> result dict
    failed = 0
    failure_reasons = {}  # 理由 -> 件数
    total_input_tokens = 0

    def record_failure(reason):
        nonlocal failed
        failed += 1
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    # スレッドプールの __exit__ は実行中のスレッドの完了を待つため、認証エラーや証明書
    # エラーの検知後もそれらが尽きるまでブロックする。始める前に1文だけ同期で判定し、
    # ここで検知できればプールを作らずに済む。
    first_item, remaining = items[0], items[1:]
    try:
        _, first_result = judge_one(key, first_item)
    except AuthError:
        print(f"TypeSafe API の認証に失敗した (401/403)。{key_source} のキーが無効な可能性がある。", file=sys.stderr)
        sys.exit(4)
    except CertError:
        cert_error_exit()
    except ApiCallError as e:
        record_failure(e.reason)
    else:
        results[id(first_item)] = first_result
        total_input_tokens += first_result["input_tokens"]

    if remaining:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(judge_one, key, item): item for item in remaining}
            for future in concurrent.futures.as_completed(futures):
                try:
                    item, result = future.result()
                except AuthError:
                    sys.stderr.write(f"TypeSafe API の認証に失敗した (401/403)。{key_source} のキーが無効な可能性がある。\n")
                    sys.stderr.flush()
                    os._exit(4)
                except CertError:
                    sys.stderr.write(CERT_ERROR_MESSAGE.format(python=sys.executable) + "\n")
                    sys.stderr.flush()
                    os._exit(5)
                except ApiCallError as e:
                    record_failure(e.reason)
                    continue
                results[id(item)] = result
                total_input_tokens += result["input_tokens"]

    by_category = {qid: [] for qid in CATEGORY_ORDER}
    partial = 0
    for item in items:
        result = results.get(id(item))
        if result is None:
            continue
        if result["missing"]:
            partial += 1
        for qid, score in result["scores"].items():
            if score >= THRESHOLDS[qid]:
                by_category[qid].append((item["line"], score, item["sentence"]))

    for qid in CATEGORY_ORDER:
        entries = by_category[qid]
        print(f"## {QUESTIONS[qid]['label']}: {len(entries)} 件 (しきい値 {THRESHOLDS[qid]})")
        for line_no, score, sentence in sorted(entries, key=lambda e: e[1], reverse=True):
            print(f"{line_no}:{score:.2f}: {sentence[:100]}")
        print()

    judged = len(items) - failed
    print(f"判定した文: {judged} / 失敗: {failed} / 入力トークン計: {total_input_tokens}")
    if failed > 0:
        breakdown = ", ".join(f"{reason} ×{count}" for reason, count in failure_reasons.items())
        print(f"失敗の内訳: {breakdown}")
    if partial > 0:
        print(f"一部の判定が返らなかった文: {partial}")

    sys.exit(1 if failed > 0 or partial > 0 else 0)


if __name__ == "__main__":
    main()
