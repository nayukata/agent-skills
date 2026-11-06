#!/usr/bin/env python3
"""正規表現で拾えない兆候 (QUESTIONS に定義) を、Jev (TypeSafe の判定 API) に1文ずつ判定させる。
標準ライブラリのみで動く。
"""
import concurrent.futures
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT = 60
RETRY_DELAYS = (1, 3)  # 429・5xx・タイムアウト・通信断はこの間隔で最大2回まで再試行する

# 判定ごとに確率の出方が違い、該当文でも0.4〜0.7にとどまる判定があるため、しきい値を判定ごとに分けている。
THRESHOLDS = {
    "actor_missing": 0.5,
    "actor_shift": 0.4,
    "thing_acts": 0.35,
    "kagi_emphasis": 0.5,
    "odd_collocation": 0.5,
    "human_emphasis": 0.35,
    "unknowable": 0.5,
}

# レビューしやすさのため、質問文をここに集約する
QUESTIONS = {
    "actor_missing": {
        "label": "主語や立場があいまい (主体が書かれていない)",
        "type": "noul",
        "instructions": (
            "`sentence` の中に、誰が、または何がその動作をするのかが `sentence` にも `paragraph` "
            "のそれより前の文にも書かれておらず、読み手が主体を推測しなければならない動詞があるか。"
            "書き手自身の作業 (直しました、確認しました、移しました) の主語が省かれているだけの場合は"
            "含めない。"
        ),
        "criteria": {
            "true": "主体を推測しないと読めない動詞がある",
            "false": "すべての動作の主体が文脈から一意に分かる",
        },
    },
    "actor_shift": {
        "label": "主語や立場があいまい (途中で入れ替わる)",
        "type": "noul",
        "instructions": (
            "`sentence` の途中で、動作の主体または視点 (書き手、システム、利用者、運用者など) が、"
            "断りなく別のものに入れ替わっているか。"
        ),
        "criteria": {
            "true": "1 文の中で主体か視点が切り替わり、読み手が読み直す必要がある",
            "false": "1 文を通して主体と視点が一貫している、または切り替わりが明示されている",
        },
    },
    "thing_acts": {
        "label": "モノに人の動作をさせる",
        "type": "noul",
        "instructions": (
            "`sentence` で、情報、データ、設定、画面、ライブラリなどのモノが、判定する、待つ、決める、"
            "丸める、持つのような、本来は人や仕組みが行う動作の主語になっているか。API が応答を返す、"
            "画面が文言を表示する、テストが通過する、のような技術文書の慣用は含めない。"
        ),
        "criteria": {
            "true": "モノが人の動作の主語になっている",
            "false": "動作の主語は人か仕組みである、または技術文書の慣用の範囲である",
        },
    },
    "kagi_emphasis": {
        "label": "概念を鉤括弧で強調",
        "type": "noul",
        "instructions": (
            "`sentence` に、引用、画面の文言、メッセージの文面のいずれでもない言葉を、目立たせる目的で"
            "鉤括弧「」で囲んだ箇所があるか。"
        ),
        "criteria": {
            "true": "概念や評価の言葉を鉤括弧で強調している",
            "false": "鉤括弧が無い、または鉤括弧の中身は実際の文言の引用である",
        },
    },
    "odd_collocation": {
        "label": "名詞に普段付かない動詞を当てる",
        "type": "noul",
        "instructions": (
            "`sentence` に、名詞と動詞の組み合わせが、日本語のエンジニアが同僚とのチャットで普段使う"
            "組み合わせになっていない箇所があるか。例: Cookie には「設定する」が普通で、"
            "「Cookie を書く」は不自然。機能や表示には「有効になる」「表示される」が普通で、"
            "「案内が働く」は不自然。"
        ),
        "criteria": {
            "true": "普段は使わない動詞が名詞に当てられている箇所がある",
            "false": (
                "名詞と動詞の組み合わせはどれも普段使うものである。"
                "「Cookie に書き込む」「Cookie の書き込み」のような言い方も普段使うものに含める"
            ),
        },
    },
    # 除外する言い方を instructions に書くと該当文まで 0.2 前後に沈むため、criteria の false に置いている
    "human_emphasis": {
        "label": "人が行うことを強調するだけの言葉を付ける",
        "type": "noul",
        "instructions": (
            "`sentence` に、人が行うことを強調するためだけに足された、目で、手で、自分の目で、"
            "のような言葉があるか。"
        ),
        "criteria": {
            "true": "人が行うことを強調するためだけの言葉があり、削っても意味が変わらない",
            "false": (
                "そのような言葉が無い、または目を通す、手を入れる、目視確認、手作業のように"
                "普段から使う決まった言い方である"
            ),
        },
    },
    "unknowable": {
        "label": "確かめようのないこと・知り得ないことの断定",
        "type": "noul",
        "instructions": (
            "`sentence` に、語り手がその場で確かめようのないこと、またはその時点で知り得なかったはずのことを、"
            "事実として言い切っている箇所があるか。"
        ),
        "criteria": {
            "true": "確かめようのないことを言い切っている",
            "false": "見聞きしたこと、推測と分かる書き方、または語り手が知っているはずのことだけを書いている",
        },
    },
}
CATEGORY_ORDER = [
    "actor_missing",
    "actor_shift",
    "thing_acts",
    "kagi_emphasis",
    "odd_collocation",
    "human_emphasis",
    "unknowable",
]

# 箇条書きの短い項目 (「次の項目。」のような5文字) も判定対象に残すための下限
MIN_SENTENCE_LEN = 5


def usage_exit():
    print("usage: jev_check.py <file>", file=sys.stderr)
    sys.exit(2)


def strip_front_matter_and_code_blocks(raw_lines):
    """check.sh と同じ考え方: front matter とコードブロックを行番号を保ったまま空行に置き換える。"""
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
    with open(path, encoding="utf-8") as f:
        raw_lines = f.read().splitlines()
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
    ids = ["actor_missing", "actor_shift", "thing_acts", "odd_collocation"]
    if "「" in sentence and "」" in sentence:
        ids.append("kagi_emphasis")
    if "目" in sentence or "手" in sentence:
        ids.append("human_emphasis")
    ids.append("unknowable")
    return {qid: {"type": "noul", "instructions": QUESTIONS[qid]["instructions"], "criteria": QUESTIONS[qid]["criteria"]} for qid in ids}


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
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    attempts = 1 + len(RETRY_DELAYS)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.load(resp)
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
        except TimeoutError:
            if attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise ApiCallError("timeout") from None
        except Exception:
            # レスポンスの形が壊れている場合などは、この文の失敗として扱う
            raise ApiCallError("bad_response") from None
    raise ApiCallError("network")


def judge_one(key, item):
    questions = questions_for(item["sentence"])
    state = {
        "paragraph": item["paragraph"].replace("**", ""),
        "sentence": item["sentence"].replace("**", ""),
    }
    data = call_api(key, state, questions)
    answers = data.get("answers")
    scores = {}
    if isinstance(answers, dict):
        for qid in questions:
            entry = answers.get(qid)
            if isinstance(entry, dict) and "noul" in entry:
                scores[qid] = entry["noul"]
    if not scores:
        # 一部の判定だけ欠けている場合は取れた分だけ使うので、ここに来るのは全滅した時だけ
        raise ApiCallError("bad_response")
    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    return item, {"scores": scores, "input_tokens": input_tokens, "missing": len(questions) - len(scores)}


def main():
    if len(sys.argv) != 2:
        usage_exit()
    path = sys.argv[1]
    if not os.path.isfile(path):
        usage_exit()

    items = collect_sentences(path)
    if not items:
        print("判定する文が無い")
        sys.exit(0)

    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        print(
            "TYPESAFE_API_KEY が無い。環境変数にキーを渡して実行するか、"
            "SKILL.md の点検手順 2 の兆候を 1 文ずつ読む",
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
        print("TypeSafe API の認証に失敗した (401/403)。キーが無効な可能性がある。", file=sys.stderr)
        sys.exit(4)
    except CertError:
        print(
            "この python3 には証明書が無い。別の python3 (macOS は /usr/bin/python3) で実行する。",
            file=sys.stderr,
        )
        sys.exit(5)
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
                    sys.stderr.write("TypeSafe API の認証に失敗した (401/403)。キーが無効な可能性がある。\n")
                    sys.stderr.flush()
                    os._exit(4)
                except CertError:
                    sys.stderr.write(
                        "この python3 には証明書が無い。別の python3 (macOS は /usr/bin/python3) で実行する。\n"
                    )
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
