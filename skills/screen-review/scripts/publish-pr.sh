#!/usr/bin/env bash
# Publish PNG/GIF/WebM under <TOPIC_DIR> to a GitHub PR via user-attachments
# upload (drag-and-drop into a comment textarea) and weave them into the PR
# body as a 修正前/修正後 table + 動作確認 GIF section.
#
# Usage: publish-pr.sh <TOPIC_DIR> [PR_NUMBER]
#
# Naming convention:
#   <NN>-before-<label>.png ⟷ <NN>-after-<label>.png   → table row
#   <NN>-<label>.png                                    → supplementary
#   *.webm                                               → ffmpeg → *.gif (embed) + original (link)

set -euo pipefail

LAYOUT="vertical"  # solos セクションのレイアウト。horizontal でラベル列の横並びテーブルに切替
LABELS=""          # horizontal レイアウト時の列ヘッダー (カンマ区切り)。例: "上部,中盤,下部"
COLUMNS=0          # horizontal レイアウト時の 1 行あたりの列数。0 は全部を 1 行に並べる。
                   # 列が増えるほど 1 枚が小さくなり、5 列では文字が読めなくなるため、
                   # 読ませたいときは 2〜3 に絞る

while [ $# -gt 0 ]; do
  case "$1" in
    --layout)
      LAYOUT="$2"
      shift 2
      ;;
    --layout=*)
      LAYOUT="${1#--layout=}"
      shift
      ;;
    --labels)
      LABELS="$2"
      shift 2
      ;;
    --labels=*)
      LABELS="${1#--labels=}"
      shift
      ;;
    --columns)
      COLUMNS="$2"
      shift 2
      ;;
    --columns=*)
      COLUMNS="${1#--columns=}"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--layout horizontal|vertical] [--labels \"上部,中盤,下部\"] [--columns N] <TOPIC_DIR> [PR_NUMBER]" >&2
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

case "$LAYOUT" in
  horizontal|vertical) ;;
  *)
    echo "invalid --layout: $LAYOUT (expected: horizontal | vertical)" >&2
    exit 1
    ;;
esac

if [ $# -lt 1 ]; then
  echo "Usage: $0 [--layout horizontal|vertical] <TOPIC_DIR> [PR_NUMBER]" >&2
  exit 1
fi

TOPIC_DIR="$1"
[ -d "$TOPIC_DIR" ] || { echo "not a directory: $TOPIC_DIR" >&2; exit 1; }
TOPIC_DIR="${TOPIC_DIR%/}"

PR_NUMBER="${2:-}"
if [ -z "$PR_NUMBER" ]; then
  PR_NUMBER=$(gh pr view --json number -q .number 2>/dev/null) || {
    echo "no PR for current branch. pass PR_NUMBER as 2nd arg." >&2
    exit 1
  }
fi

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
PR_URL="https://github.com/${REPO}/pull/${PR_NUMBER}"
SESSION="screen-review-github"
# Chrome の永続プロファイルディレクトリ。--profile で launchPersistentContext を使い、
# cookies / localStorage / IndexedDB を含むブラウザ状態をディレクトリに完全永続化する。
# --session-name (state json) は daemon 再起動後に load が反映されない問題があるため、
# auth を確実に再利用するには --profile が必須。
PROFILE_DIR="$HOME/.agent-browser/profiles/$SESSION"
mkdir -p "$PROFILE_DIR"
AB=(agent-browser --profile "$PROFILE_DIR")

# GitHub PR の最下部コメント欄。UI 変更で壊れたら SKILL.md の手順で実値を確認。
# COMMENT_TEXTAREA は ID 単独指定が必須。`textarea[name="comment[body]"]` を OR で
# 並べると、DOM 上で先頭に出る inline comment 用 textarea を querySelector が返し、
# bottom comment の値を polling できずタイムアウトする。
FILE_INPUT='#fc-new_comment_field'
COMMENT_TEXTAREA='#new_comment_field'

echo "→ repo: $REPO"
echo "→ PR:   #$PR_NUMBER"

# ----------------------------------------------------------------------------
# 1. WebM → GIF (skip if up-to-date)
# ----------------------------------------------------------------------------
for webm in "$TOPIC_DIR"/*.webm; do
  [ -f "$webm" ] || continue
  gif="${webm%.webm}.gif"
  if [ ! -f "$gif" ] || [ "$webm" -nt "$gif" ]; then
    echo "→ ffmpeg $(basename "$webm") -> $(basename "$gif")"
    ffmpeg -hide_banner -loglevel error -y -i "$webm" \
      -vf "fps=15,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
      -loop 0 "$gif"
  fi
done

# ----------------------------------------------------------------------------
# 2. Ensure agent-browser session is logged in to GitHub
# ----------------------------------------------------------------------------
# 既存の headed セッションが残っていると以降の open がそのウィンドウを再利用するため、
# ユーザー操作不要時はブラウザを表示しない方針で必ず close してから headless で開き直す。
"${AB[@]}" close >/dev/null 2>&1 || true

ensure_login() {
  "${AB[@]}" open "$PR_URL" >/dev/null 2>&1 || return 1
  "${AB[@]}" wait --load networkidle >/dev/null 2>&1 || true
  local cur_url logged_in
  cur_url=$("${AB[@]}" get url 2>/dev/null | tail -1)
  # URL チェックだけだと private repo の 404 (Page not found) でも素通りするため、
  # ログイン済みユーザー名を持つ <meta name="user-login"> の存在を併せて確認する。
  logged_in=$("${AB[@]}" eval "!!document.querySelector('meta[name=\"user-login\"]')?.content" 2>/dev/null | tail -1)
  [[ "$cur_url" != *"/login"* ]] && [[ "$cur_url" == *"/pull/${PR_NUMBER}"* ]] && [[ "$logged_in" == "true" ]]
}

if ! ensure_login; then
  echo "→ GitHub にログインが必要です"
  echo "→ Chrome ウィンドウを立ち上げます。GitHub にログインしてください (2FA 含む)"
  # ログインが必要なときだけ headed で立ち上げる。一旦 close してから --headed で開く。
  "${AB[@]}" close >/dev/null 2>&1 || true
  "${AB[@]}" --headed open https://github.com/login >/dev/null
  read -p "→ ログイン完了したら Enter: " _
  # ログイン完了後は headed ウィンドウを閉じて headless で本処理を進める
  "${AB[@]}" close >/dev/null 2>&1 || true
  ensure_login || { echo "✗ ログイン後も PR ページに到達できません" >&2; exit 1; }
fi
echo "✓ GitHub session ready"

# ----------------------------------------------------------------------------
# 3. Collect assets in stable order: png → gif → webm
# ----------------------------------------------------------------------------
ASSETS=()
for ext in png gif webm; do
  for f in "$TOPIC_DIR"/*."$ext"; do
    [ -f "$f" ] && ASSETS+=("$f")
  done
done
if [ ${#ASSETS[@]} -eq 0 ]; then
  echo "no assets in $TOPIC_DIR" >&2
  exit 1
fi
echo "→ uploading ${#ASSETS[@]} asset(s) via comment textarea drop"

# ----------------------------------------------------------------------------
# 4. Drop files into the bottom comment textarea (uploads to user-attachments)
# ----------------------------------------------------------------------------
"${AB[@]}" upload "$FILE_INPUT" "${ASSETS[@]}" >/dev/null

# Upload progresses async. Wait based on file count, then poll textarea until
# all "Uploading…" placeholders have been replaced with final URLs.
sleep $(( ${#ASSETS[@]} * 2 + 4 ))

TEXTAREA_JSON=$(mktemp)
trap 'rm -f "$TEXTAREA_JSON"' EXIT

# Upload takes ~2-5s per file. Poll until "Uploading…" disappears AND user-attachments
# URLs appear for every asset. If polling exhausts without success, abort instead of
# pasting empty URLs into the PR body.
POLL_ATTEMPTS=30
poll_ok=0
for i in $(seq 1 $POLL_ATTEMPTS); do
  "${AB[@]}" eval "document.querySelector('${COMMENT_TEXTAREA}')?.value" > "$TEXTAREA_JSON" 2>/dev/null || true
  if python3 -c "
import json, sys
try:
    v = json.load(open('$TEXTAREA_JSON'))
except Exception:
    sys.exit(2)
if not isinstance(v, str):
    sys.exit(2)
if 'Uploading' in v:
    sys.exit(1)
if v.count('user-attachments/assets/') < ${#ASSETS[@]}:
    sys.exit(1)
sys.exit(0)
"; then
    poll_ok=1
    break
  fi
  sleep 2
done

if [ "$poll_ok" -ne 1 ]; then
  echo "✗ アップロード待機がタイムアウト ($((POLL_ATTEMPTS * 2)) 秒)。textarea の最終値:" >&2
  cat "$TEXTAREA_JSON" >&2
  echo >&2
  echo "→ ネットワーク状況を確認し、再度実行してください。PR 本文は変更していません。" >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# 5. Extract filename → URL map, then clear the textarea (no comment posted)
# ----------------------------------------------------------------------------
MAPPING_JSON=$(python3 - "$TEXTAREA_JSON" <<'PYEOF'
import json, re, sys
v = json.load(open(sys.argv[1]))
# <img ... alt="NAME" src="https://github.com/user-attachments/assets/UUID" />
# [NAME](https://github.com/user-attachments/assets/UUID)
mapping = {}
# 同一ファイル名で再アップロードされている場合、最初の URL を採用する (setdefault)。
# textarea が clear されずに蓄積していた場合の保険。
for m in re.finditer(r'<img[^>]*alt="([^"]+)"[^>]*src="(https://github\.com/user-attachments/assets/[^"]+)"', v):
    mapping.setdefault(m.group(1), m.group(2))
for m in re.finditer(r'\[([^\]]+)\]\((https://github\.com/user-attachments/assets/[^)]+)\)', v):
    mapping.setdefault(m.group(1), m.group(2))
print(json.dumps(mapping))
PYEOF
)

"${AB[@]}" eval --stdin <<'EVALEOF' >/dev/null
const sel = '#new_comment_field, textarea[name="comment[body]"]';
const ta = document.querySelector(sel);
if (ta) { ta.value = ""; ta.dispatchEvent(new Event('input', {bubbles: true})); }
EVALEOF

# ----------------------------------------------------------------------------
# 6. Resolve URL for each asset (alt = basename without extension)
# ----------------------------------------------------------------------------
url_for() {
  python3 - "$MAPPING_JSON" "$1" <<'PYEOF'
import json, os, sys
mapping = json.loads(sys.argv[1])
fname = sys.argv[2]
stem = os.path.splitext(fname)[0]
print(mapping.get(stem, ""))
PYEOF
}

# 画像は恒久 URL へのリンクで包む。GitHub は本文の描画時に 5 分で失効する
# 署名付き URL を画像に当てるため、素の画像だとページを開いてしばらく経った
# 後にクリックすると「見つかりません」になる。リンク先を恒久 URL にしておけば
# クリックのたびに新しい署名付き URL へ転送される。
img_md() {
  local alt="$1" url="$2" width="${3:-}"
  if [ -n "$width" ]; then
    # 横並びの表は列幅が中身の実寸で決まり、枚数の少ない行や実寸の違う画像で
    # 大きさが揃わない。幅を明示して行内・行間で同じ大きさにする
    printf '[<img alt="%s" src="%s" width="%s">](%s)' "$alt" "$url" "$width" "$url"
  else
    printf '[![%s](%s)](%s)' "$alt" "$url" "$url"
  fi
}

# ----------------------------------------------------------------------------
# 6.5. Fail fast if any asset lacks a resolved URL
# ----------------------------------------------------------------------------
# url_for が空を返したまま進むと PR 本文に壊れた画像 (`![x]()`) が入る。
# 本文を書き換える前に全 asset の URL 解決を確認し、欠けがあれば中断する。
MISSING=()
for f in "${ASSETS[@]}"; do
  bn=$(basename "$f")
  [ -n "$(url_for "$bn")" ] || MISSING+=("$bn")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "✗ アップロード URL を解決できないファイルがあります: ${MISSING[*]}" >&2
  echo "→ GitHub が textarea に挿入する形式が変わった可能性があります。PR 本文は変更していません。" >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# 7. Compose screen-review body section
# ----------------------------------------------------------------------------
SECTION_FILE=$(mktemp)
BODY_FILE=$(mktemp)
trap 'rm -f "$TEXTAREA_JSON" "$SECTION_FILE" "$BODY_FILE"' EXIT

{
  echo "<!-- screen-review:start -->"

  pairs_emitted=0
  solos=()
  for f in "$TOPIC_DIR"/*.png; do
    [ -f "$f" ] || continue
    bn=$(basename "$f")
    case "$bn" in
      *-before-*)
        nn="${bn%%-before-*}"
        rest="${bn#${nn}-before-}"
        label="${rest%.png}"
        after_bn="${nn}-after-${label}.png"
        if [ -f "${TOPIC_DIR}/${after_bn}" ]; then
          if [ $pairs_emitted -eq 0 ]; then
            echo
            echo "## スクリーンショット"
            echo
            echo "| 修正前 | 修正後 |"
            echo "|---|---|"
            pairs_emitted=1
          fi
          before_url=$(url_for "$bn")
          after_url=$(url_for "$after_bn")
          echo "| $(img_md "${bn%.png}" "$before_url") | $(img_md "${after_bn%.png}" "$after_url") |"
        else
          solos+=("$bn")
        fi
        ;;
      *-after-*)
        nn="${bn%%-after-*}"
        rest="${bn#${nn}-after-}"
        label="${rest%.png}"
        before_bn="${nn}-before-${label}.png"
        [ -f "${TOPIC_DIR}/${before_bn}" ] || solos+=("$bn")
        ;;
      *)
        solos+=("$bn")
        ;;
    esac
  done

  if [ ${#solos[@]} -gt 0 ]; then
    echo
    # before/after ペアが既に「## スクリーンショット」を出している場合、単発分は補足として区別する
    if [ $pairs_emitted -eq 1 ]; then
      echo "## 補足スクリーンショット"
    else
      echo "## スクリーンショット"
    fi
    echo
    if [ "$LAYOUT" = "horizontal" ]; then
      # 列ヘッダーは --labels を最優先。指定が無ければファイル名 (<NN>- を除いた部分) にフォールバック
      IFS=',' read -r -a label_arr <<< "$LABELS"
      if [ -n "$LABELS" ] && [ "${#label_arr[@]}" -ne "${#solos[@]}" ]; then
        echo "✗ --labels の数 (${#label_arr[@]}) と画像数 (${#solos[@]}) が一致しません" >&2
        exit 1
      fi
      per_row="$COLUMNS"
      [ "$per_row" -gt 0 ] 2>/dev/null || per_row="${#solos[@]}"
      # PR 本文の幅 (約 840px) を列数で割った値。表の余白の分だけ小さくする
      cell_width=$(( 840 / per_row - 20 ))
      header=""
      sep=""
      row=""
      idx=0
      in_row=0
      for png in "${solos[@]}"; do
        stem="${png%.png}"
        if [ -n "$LABELS" ]; then
          label="${label_arr[$idx]}"
        else
          label="${stem#*-}"
        fi
        url=$(url_for "$png")
        header+="| ${label} "
        sep+="|---"
        row+="| $(img_md "$stem" "$url" "$cell_width") "
        idx=$((idx + 1))
        in_row=$((in_row + 1))
        # 行が埋まったら 1 つの表として書き出し、次の行を始める。
        # 1 つの表に複数の見出し行は置けないため、行ごとに表を分ける
        if [ "$in_row" -ge "$per_row" ]; then
          echo "${header}|"
          echo "${sep}|"
          echo "${row}|"
          echo
          header=""; sep=""; row=""; in_row=0
        fi
      done
      if [ "$in_row" -gt 0 ]; then
        # 端の行も列数を揃える。列が少ない表は 1 枚が表いっぱいに広がり、
        # 他の行の 2 倍の大きさで突出する
        while [ "$in_row" -lt "$per_row" ]; do
          header+="| "
          sep+="|---"
          row+="| "
          in_row=$((in_row + 1))
        done
        echo "${header}|"
        echo "${sep}|"
        echo "${row}|"
      fi
    else
      for png in "${solos[@]}"; do
        url=$(url_for "$png")
        img_md "${png%.png}" "$url"; echo
        echo
      done
    fi
  fi

  gif_emitted=0
  for f in "$TOPIC_DIR"/*.gif; do
    [ -f "$f" ] || continue
    if [ $gif_emitted -eq 0 ]; then
      echo
      echo "## 動作確認"
      echo
      gif_emitted=1
    fi
    bn=$(basename "$f")
    url=$(url_for "$bn")
    img_md "${bn%.gif}" "$url"; echo
    echo
  done

  webm_emitted=0
  for f in "$TOPIC_DIR"/*.webm; do
    [ -f "$f" ] || continue
    if [ $webm_emitted -eq 0 ]; then
      echo
      echo "## 元動画 (高画質)"
      echo
      webm_emitted=1
    fi
    bn=$(basename "$f")
    url=$(url_for "$bn")
    echo "- [${bn}](${url})"
  done

  echo
  echo "<!-- screen-review:end -->"
} > "$SECTION_FILE"

# ----------------------------------------------------------------------------
# 8. Merge into PR body (replace marker block or append)
# ----------------------------------------------------------------------------
gh pr view "$PR_NUMBER" -R "$REPO" --json body -q .body > "$BODY_FILE"

python3 - "$BODY_FILE" "$SECTION_FILE" <<'PYEOF' > "$BODY_FILE.new"
import sys, re
body = open(sys.argv[1]).read()
section = open(sys.argv[2]).read().rstrip()
start = "<!-- screen-review:start -->"
end = "<!-- screen-review:end -->"
pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
if pat.search(body):
    out = pat.sub(section, body)
else:
    sep = "\n\n" if body.strip() else ""
    out = body.rstrip() + sep + section
sys.stdout.write(out)
PYEOF

gh pr edit "$PR_NUMBER" -R "$REPO" --body-file "$BODY_FILE.new"
rm -f "$BODY_FILE.new"

echo "✓ PR #$PR_NUMBER updated"
echo "  https://github.com/${REPO}/pull/${PR_NUMBER}"
