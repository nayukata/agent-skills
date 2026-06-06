#!/bin/bash
# Usage: show.sh <topic-dir>
# 指定ディレクトリ配下の *.png を 1 つの Preview ウィンドウにまとめて開く。
# 画像サイズが揃っていないと Preview が複数ウィンドウに分割するため、最大寸法に揃えて
# パディングしてから開く。
set -euo pipefail

dir="${1:-}"
if [[ -z "$dir" || ! -d "$dir" ]]; then
  echo "Usage: $0 <topic-dir>" >&2
  exit 1
fi

# 直下の *.png のみ対象 (_review/ 等のサブディレクトリは除外)
pngs=()
while IFS= read -r f; do
  pngs+=("$f")
done < <(find "$dir" -maxdepth 1 -type f -name "*.png" | sort)

if [ ${#pngs[@]} -eq 0 ]; then
  echo "no png found in $dir" >&2
  exit 1
fi

# 最大幅・高さを算出
max_w=0
max_h=0
for f in "${pngs[@]}"; do
  size=$(sips -g pixelWidth -g pixelHeight "$f" 2>/dev/null | awk '/pixel(Width|Height)/ {print $2}')
  w=$(echo "$size" | sed -n '1p')
  h=$(echo "$size" | sed -n '2p')
  (( w > max_w )) && max_w=$w
  (( h > max_h )) && max_h=$h
done

# 正規化先 (毎回作り直す)
out="$dir/_review"
rm -rf "$out"
mkdir -p "$out"

for f in "${pngs[@]}"; do
  base=$(basename "$f")
  sips -p "$max_h" "$max_w" --padColor 222222 "$f" --out "$out/$base" >/dev/null 2>&1
done

# 既存の Preview ウィンドウを閉じてからまとめて開く
osascript -e 'tell application "Preview" to close every window' 2>/dev/null || true
sleep 0.3
open -a Preview "$out"/*.png

echo "Opened ${#pngs[@]} shots from $dir in Preview"
