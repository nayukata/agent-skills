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
    -h|--help)
      echo "Usage: $0 [--layout horizontal|vertical] [--labels \"上部,中盤,下部\"] <TOPIC_DIR> [PR_NUMBER]" >&2
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
          echo "| ![${bn%.png}](${before_url}) | ![${after_bn%.png}](${after_url}) |"
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
    echo "## スクリーンショット"
    echo
    if [ "$LAYOUT" = "horizontal" ]; then
      # 列ヘッダーは --labels を最優先。指定が無ければファイル名 (<NN>- を除いた部分) にフォールバック
      IFS=',' read -r -a label_arr <<< "$LABELS"
      if [ -n "$LABELS" ] && [ "${#label_arr[@]}" -ne "${#solos[@]}" ]; then
        echo "✗ --labels の数 (${#label_arr[@]}) と画像数 (${#solos[@]}) が一致しません" >&2
        exit 1
      fi
      header=""
      sep=""
      row=""
      idx=0
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
        row+="| ![${stem}](${url}) "
        idx=$((idx + 1))
      done
      echo "${header}|"
      echo "${sep}|"
      echo "${row}|"
    else
      for png in "${solos[@]}"; do
        url=$(url_for "$png")
        echo "![${png%.png}](${url})"
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
    echo "![${bn%.gif}](${url})"
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
