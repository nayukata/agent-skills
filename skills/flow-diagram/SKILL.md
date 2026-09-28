---
name: flow-diagram
description: 複数の登場人物が絡む処理の流れを HTML の図にし、PNG に撮って PR 本文や Artifact ページに貼る。「処理の流れを図に」「流れが分かりにくい」「図(HTML)で」「PR に図を貼る」「原因の経路を図に」「変更前後の違いを図に」で使う。箱だけの簡素な図やシーケンス図で登場人物や並行処理が分からなくなった時にも使う。
---

# flow-diagram

アプリ、サーバー、外部サービスなど複数の登場人物が絡む処理の流れを、動く場所ごとに整理した HTML の図で描く。箱だけの図やシーケンス図では、誰がどこで動くか、どの処理が同時に進むかが読み取れないため、この形式を崩さずに使う。

## 使う場面

- 複数の登場人物が絡む処理の流れを説明する時
- 不具合の原因の経路を説明する時
- 変更前後で挙動が分かれる箇所を説明する時
- 2つの処理が同時に進む(競合する)箇所を説明する時

単一の登場人物の中だけで完結する説明、または箇条書きで十分な単純な手順には使わない。

## 部品の語彙

見本は `assets/template.html` にある。コピーして書き始める。CSS・ライトボックスの script はそのまま使い、`<div class="wrap">` 以下の内容だけを差し替える。

| 部品 | クラス | 使う場面 |
|---|---|---|
| 登場人物と動く場所の入れ子図 | `.map` / `.zone` / `.actor` | **常に最初のセクションに置く。** 場所の入れ子 (例: 端末の中 ⊃ アプリ ⊃ アプリの中の画面) と、その横に「インターネットの先」を並べる |
| 場所の印付きの札 | `.who` (`::before` で印) | 登場人物を指す時は常にこれを使う。印は場所ごとに1つ、名前と重ならない言葉にする (`iPhone`／`アプリ`／`Web ページ`／`外部` など)。プロジェクトごとに実際の場所名で決め直す |
| 用語表 | `.cast` | 「トークン」「設定」のような曖昧な総称が出てくる図に置く。無ければ省略する |
| 縦の行 flow | `.flow` / `.row` (`<small>` で補足、`.pause` で中断時間) | 基本の流れ。札 (誰が) + 何をする、の順で縦に並べる |
| 変更前/変更後の左右レーン | `.fork` / `.lane.before` / `.lane.after` | 分岐して結末が変わる箇所 |
| 並行の表 | `.race` (`.mark.before` / `.mark.after` で目印) | 同時に進む2つの処理を比べる時。行が時間、列が経路。なぜ同時に始まるのかを本文にも書く |
| 実測タイムライン | `.row.r3` (時刻列付き) | 実機・実環境で観測した時刻を示す時 |

## 規範

- **同じサービスが複数の場所で動くなら、場所ごとに別の札にし、同じ色でそろえる。** 例: アプリの Firebase、Web ページの Firebase、Firebase 認証サーバー。1つの札にまとめると「どこで動くか」が曖昧になる
- **実測値を入れる。** 「約1.1秒」「224秒」のように、その図が扱う時間や回数の具体値を書く
- **各セクションは `<section id="...">` で囲む。** id は英字小文字で、内容を表す短い言葉にする。撮影スクリプトがこの id を自動で拾う
- **図はクリックで拡大できるようにする。** テンプレートの `<script>` をそのまま使えば、`<figure tabindex="0">` を追加するだけで効く
- ライト/ダーク両方の配色トークンを保つ (`:root` / `@media (prefers-color-scheme: dark)` / `:root[data-theme="dark"]`)

### 文章

- 地の文は日本語で書く
- 漢字一文字の名詞を不自然に使わない (「語」「版」「句」「偽」など)
- 箇条書きで中黒を並べた列挙をしない。文で書くか表にする
- 「〜側」のような曖昧な指示語を使わない。処理や画面の実名で書く
- 曖昧な総称表現 (「トークン」「設定」など、種類が複数ある言葉) は種類名で書く。用語表を添える

## 手順

1. **`assets/template.html` を元に HTML を書く。** 部品の語彙から必要なものを選び、登場人物の色・印をプロジェクトの実際の場所名に合わせて決め直す。テンプレートの `--a`〜`--e` の色変数名とプレースホルダーの印 (`場所A` など) は実名にリネームする
2. **`scripts/render.cjs` で PNG に撮る。**
   ```
   node scripts/render.cjs <html-path> <out-dir>
   ```
   `full.png` (全体) と、`<section id>` ごとの PNG、図の文章を書き出した `text.txt` が `<out-dir>` に出力される。撮る id を指定する必要はない。playwright が見つからない場合は下記「playwright の解決」を見る
3. **撮った PNG を Read で確認する。** 次を見る。
   - 札の印がはみ出したり、名前と重なっていないか
   - `.race` や横並びの列が窮屈になっていないか (列数が多すぎる時は行を分けるか、内容を削る)
   - 同系色の背景 (`.lane.after` など) に乗った札が、背景と同化して読めなくなっていないか
   - 崩れがあれば HTML を直して2に戻る
4. **図の文章を点検する。** `<out-dir>/text.txt` に、図に表示される文章が段落ごとに書き出されている。文章を点検するスキルや検出器 (AI が書いた文章の兆候の点検、文章校正など) が使える環境なら、その手順どおりに、用意されている検出器をすべて `text.txt` にかける。外部の判定 API を使う検出器も省かない。ヒットした文ごとに直すか直さない理由を決め、直したら2に戻る
5. **ユーザーが公開を求めたら Artifact として公開する。** HTML をそのまま `Artifact` ツールで publish する (page のデザイン規範は Artifact 側のスキルに従う。このテンプレートの CSS 構成を保てば追加の調整は要らないことが多い)
6. **PR に貼るなら「PR への埋め込み」の手順に従う**

## playwright の解決

`scripts/render.cjs` は `require('playwright')` を試みる。グローバルに無い場合は、既存プロジェクトの pnpm store を指す。

```
find <repo>/node_modules/.pnpm -maxdepth 1 -iname "playwright@*"
NODE_PATH=<repo>/node_modules/.pnpm/playwright@<version>/node_modules node scripts/render.cjs <html-path> <out-dir>
```

pnpm store が無いプロジェクトでは次を使う。

```
npm exec -y --package=playwright -- node scripts/render.cjs <html-path> <out-dir>
```

## PR への埋め込み

画像のアップロードと PR 本文への埋め込みは `screen-review` スキルの `scripts/publish-pr.sh` をそのまま使う。このスキル専用のアップロード処理は持たない。

1. 撮った PNG を screen-review のディレクトリ規約に合わせて配置する。`full.png` は使わず、セクション単位の PNG だけを使う。ファイル名は連番と見出しに合わせたラベルにする (before/after の対比図でない限り、ファイル名に `-before-` `-after-` という文字列を含めない。含めると publish-pr.sh が対比テーブル行として扱ってしまう)。
   ```
   mkdir -p /tmp/claude-shots/<TOPIC>
   cp <out-dir>/cast.png    /tmp/claude-shots/<TOPIC>/01-cast.png
   cp <out-dir>/handoff.png /tmp/claude-shots/<TOPIC>/02-handoff.png
   ```
2. `<screen-review スキルのディレクトリ>/scripts/publish-pr.sh` を実行して画像をアップロードする。図は上から順に読ませたいので `--layout vertical` (省略時の既定) を使う。列ヘッダーを揃えたい場合だけ `--layout horizontal --labels "..."` にする。
   ```
   <screen-review スキルのディレクトリ>/scripts/publish-pr.sh /tmp/claude-shots/<TOPIC> [PR_NUMBER]
   ```
3. publish-pr.sh は、画像を PR 本文の末尾の `<!-- screen-review:start -->` 〜 `<!-- screen-review:end -->` にまとめて差し込む。図は説明の段落の直後で読ませたいので、各画像を該当する節へ移す。
   1. 現在の PR 本文を `gh pr view <PR> --json body -q .body` で取得する。手元のファイルから書き直さない (ユーザーが GitHub 上で編集している場合がある)
   2. 差し込まれた各画像の URL (`https://github.com/user-attachments/assets/...`) を取り出し、該当する節の段落の直後に `<img width="760" alt="<図の見出し>" src="<URL>" />` で置く
   3. screen-review の区間を削除する
   4. 前提の説明に当たる図 (用語表、仕組みの図など) は、本流を長くしないよう `<details>` に畳む。登場人物と動く場所の図は畳まずに概要の直後に置く
   5. 本文を点検してから `gh pr edit <PR> --body-file <file>` で更新する。PR 本文の書き方と点検は create-pr スキルに従う
4. 前提 (`gh auth status`、GitHub ログイン) は screen-review の SKILL.md を見る

変更前/変更後の対比を PR に貼る場合は、`.fork` セクションの PNG を1枚のまま貼ってよい (左右レーンが図の中で完結しているため)。screen-review の before/after ペア機能 (別々の PNG を左右に並べる) は使わない。
