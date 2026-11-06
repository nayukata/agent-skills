#!/usr/bin/env python3
"""正規表現で機械的に拾える兆候を数える。Python 3 の標準ライブラリのみ。判定の中身は rules.json。"""
import json
import os
import re
import sys


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
RULES_PATH = os.path.join(HERE, "rules.json")
try:
    with open(RULES_PATH, encoding="utf-8") as _f:
        RULES = json.load(_f)
    if not isinstance(RULES, dict) or not isinstance(RULES.get("checks"), list) or not isinstance(RULES.get("whitespace"), str):
        raise ValueError("bad shape")
except (OSError, ValueError):
    sys.stderr.write("rules.json を読めない: %s\n" % RULES_PATH)
    sys.exit(2)


def expand(pattern):
    # rules.json の <WS> は空白の文字クラス (Python の \s とは U+001C-001F が違うため、明示している)
    return pattern.replace("<WS>", RULES["whitespace"])


SKIP_HEAD_RES = [re.compile(expand(p)) for p in RULES["prose_skip_heads"]]
BLANK_RE = re.compile(expand(RULES["blank_pattern"]))
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def load_body(path):
    # 行番号を元ファイルと一致させるため、front matter とコードブロックは空行に置き換える
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        sys.stderr.write("UTF-8 ではない: %s\n" % path)
        sys.exit(2)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw = text.split("\n")
    if raw[-1] == "":
        raw.pop()
    out = []
    fm = False
    skip = False
    for i, line in enumerate(raw):
        if i == 0 and line == "---":
            fm = True
            out.append("")
            continue
        if fm:
            out.append("")
            if line == "---":
                fm = False
            continue
        if line.startswith("```"):
            skip = not skip
            out.append("")
            continue
        if skip:
            out.append("")
            continue
        out.append(line)
    return [INLINE_CODE_RE.sub("", line) for line in out]


def is_prose(paragraph):
    return not any(r.search(paragraph["lines"][0]) for r in SKIP_HEAD_RES)


def build_paragraphs(body_text):
    paragraphs = []
    cur = []
    start = None
    for i, line in enumerate(body_text.split("\n") + [""]):
        if BLANK_RE.search(line):
            if cur:
                paragraphs.append({"start": start, "lines": cur})
            cur = []
            start = None
            continue
        if start is None:
            start = i + 1
        cur.append(line)
    if cur:
        paragraphs.append({"start": start, "lines": cur})
    return paragraphs


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: %s <file>\n" % sys.argv[0])
        sys.exit(2)
    path = sys.argv[1]
    if not os.path.isfile(path):
        sys.stderr.write("not found: %s\n" % path)
        sys.exit(2)

    # シェルのコマンド置換と同じく、末尾の改行は落とす
    body_text = "\n".join(load_body(path)).rstrip("\n")
    body_lines = body_text.split("\n")
    paragraphs = build_paragraphs(body_text)
    out = []

    def run_regex(rule):
        minimum = rule.get("min", 1)
        rx = re.compile(expand(rule["pattern"]), re.S)
        hits = ["%d:%s" % (i + 1, line) for i, line in enumerate(body_lines) if rx.search(line)]
        n = len(hits)
        if n < minimum:
            if minimum > 1:
                out.append("## %s: %d 件 (%d 件未満は表示しない)\n\n" % (rule["label"], n, minimum))
            else:
                out.append("## %s: 0 件\n\n" % rule["label"])
            return
        out.append("## %s: %d 件\n" % (rule["label"], n))
        for h in hits:
            out.append(h[:140] + "\n")
        out.append("\n")

    def run_bullet_run(rule):
        out.append("## %s\n" % rule["label"])
        rx = re.compile(expand(rule["pattern"]))
        start = 0
        count = 0
        for i, line in enumerate(body_lines):
            nr = i + 1
            if rx.search(line):
                if not count:
                    start = nr
                count += 1
                continue
            if count >= rule["min_run"]:
                out.append("行 %d-%d: %d 行\n" % (start, nr - 1, count))
            count = 0
        if count >= rule["min_run"]:
            out.append("行 %d-%d: %d 行\n" % (start, len(body_lines), count))
        out.append("\n")

    def run_ending_repeat(rule):
        out.append("## %s\n" % rule["label"])
        blank = re.compile(expand(rule["blank_pattern"]))
        endings = rule["endings"]
        counts = [0] * len(endings)
        start = 0

        def flush():
            nonlocal start
            if any(counts[k] >= e["min"] for k, e in enumerate(endings)):
                detail = " ".join("%s=%d" % (e["name"], counts[k]) for k, e in enumerate(endings))
                out.append("行 %d からの段落: %s\n" % (start, detail))
            for k in range(len(counts)):
                counts[k] = 0
            start = 0

        for i, line in enumerate(body_lines):
            if blank.search(line):
                flush()
                continue
            if not start:
                start = i + 1
            for k, e in enumerate(endings):
                counts[k] += line.count(e["text"])
        flush()
        out.append("\n")

    def run_heading_only(rule):
        out.append("## %s\n" % rule["label"])
        heading = re.compile(expand(rule["heading_pattern"]))
        depth_re = re.compile(expand(rule["depth_pattern"]))
        blank = re.compile(expand(rule["blank_pattern"]))
        prev = ""
        pl = 0
        pd = 0
        for i, line in enumerate(body_lines):
            if heading.search(line):
                depth = len(depth_re.search(line).group(0))
                if prev != "" and depth <= pd:
                    out.append("行 %d: %s\n" % (pl, prev))
                prev = line
                pl = i + 1
                pd = depth
                continue
            if blank.search(line):
                continue
            prev = ""
        out.append("\n")

    def run_opening_repeat(rule):
        out.append("## %s\n" % rule["label"])
        strip = re.compile(expand(rule["strip_prefix"]))
        length = rule["length"]
        count = {}
        lines_for = {}
        for p in paragraphs:
            if not is_prose(p):
                continue
            text = strip.sub("", "".join(p["lines"]), count=1)
            head = text[:length]
            if len(head) < length:
                continue
            count[head] = count.get(head, 0) + 1
            lines_for.setdefault(head, []).append(p["start"])
        # 同数のときは出現順 (安定ソート)
        for k in sorted(count, key=lambda h: -count[h]):
            if count[k] < rule["min_paragraphs"]:
                continue
            out.append("書き出し「%s」: %d 段落 (行 %s)\n" % (k, count[k], ", ".join(str(n) for n in lines_for[k])))
        out.append("\n")

    def run_short_sentence_shortage(rule):
        out.append("## %s\n" % rule["label"])
        rx = re.compile(expand(rule["sentence_pattern"]), re.S)
        total = 0
        short = 0
        for p in paragraphs:
            if not is_prose(p):
                continue
            for m in rx.finditer("".join(p["lines"])):
                total += 1
                if len(m.group(0)) <= rule["short_len"]:
                    short += 1
        if total >= rule["min_total"] and short <= rule["max_short"]:
            out.append("文 %d / %d字以下 %d\n" % (total, rule["short_len"], short))

    runners = {
        "regex": run_regex,
        "bullet_run": run_bullet_run,
        "ending_repeat": run_ending_repeat,
        "heading_only": run_heading_only,
        "opening_repeat": run_opening_repeat,
        "short_sentence_shortage": run_short_sentence_shortage,
    }
    for rule in RULES["checks"]:
        runners[rule["kind"]](rule)

    sys.stdout.write("".join(out))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
