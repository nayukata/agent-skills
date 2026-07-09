---
name: screen-review
description: UI 動作確認のスクリーンショット (PNG) と画面録画 (WebM) を 1 ディレクトリに集約し、macOS Preview に 1 ウィンドウで一括表示する。GitHub PR への修正前後テーブル / GIF 埋め込みも自動で行える。agent-browser cli で UI 動作確認時、ユーザーが「スクショ見せて」「画面を見たい」「動きが見たい」「どんな感じ？」「見せて」「こっちに見せて」「画像見せて」「PR に貼って」と実装結果を確認された時に使う。
---

# Screen Review

UI 動作確認で撮影した PNG / WebM を `/tmp/claude-shots/<TOPIC>/` に集約し、`scripts/show.sh` で macOS Preview に流し込んで 1 ウィンドウのサイドバーで比較する。動画は `agent-browser record` で取り Chrome / VLC / Quick Look で再生する。PR 添付は `scripts/publish-pr.sh` で GitHub の user-attachments にアップロードして本文に組み込む。

## 絶対ルール

- **MUST**: 撮影したら `scripts/show.sh` を実行して macOS Preview で開く。これがユーザーに「見せる」唯一の手段
- **NEVER**: ファイル読み込みツールで画像を開いて「見せた」つもりにしない。それはエージェントが自分で確認しただけで、ユーザーの画面には何も表示されない
- **NEVER**: 「画像はこっちに見せて」「見せて」「画面確認したい」系の指示を、PR publish やファイル読み込みツールでの表示で代替しない。必ず `show.sh` を通す
- **NEVER**: 「3 枚揃いました」「ご確認ください」等の文章だけで `show.sh` をスキップしない。文章は通知の補助であり、実体は Preview ウィンドウ

## ファイル規約

- ディレクトリ: `/tmp/claude-shots/<TOPIC>/`
  - `<TOPIC>` はタスク識別子 (`ERU-72`)、ブランチ名、短いトピック名 (`login-flow`)
  - 同一セッション内で 1 つに固定
- ファイル名:
  - `<NN>-before-<label>.png` ⟷ `<NN>-after-<label>.png`: 修正前/修正後ペア (PR 本文でテーブル化される)
  - `<NN>-<label>.png`: 単発 (補足セクション送り)
  - `<NN>-<label>.webm`: 動画 (GIF 化されて埋め込み + 元ファイルを高画質リンク)
  - `<NN>` は 2 桁連番 (`01`, `02`, …)。Preview のサイドバー順とテーブル行順を決めるため必須

## 静止画フロー

1. **撮影前**: ディレクトリ作成
   ```bash
   mkdir -p /tmp/claude-shots/<TOPIC>
   ```

2. **撮影**: 出力先を `/tmp/claude-shots/<TOPIC>/<NN>-before-<label>.png` (修正前) or `<NN>-after-<label>.png` (修正後) or `<NN>-<label>.png` (単発) に揃える
   ```bash
   agent-browser --session <s> screenshot /tmp/claude-shots/<TOPIC>/01-before-list.png
   ```

3. **一括表示**: `scripts/show.sh` を **実行** する (内容を読み込まない)。パスはこのスキルのディレクトリ (スキル読み込み時に提示される base directory) からの相対
   ```bash
   <このスキルのディレクトリ>/scripts/show.sh /tmp/claude-shots/<TOPIC>
   ```
   スクリプトは以下を自動でやる:
   1. 直下の `*.png` を全部拾い、最大寸法 (`max(width)` × `max(height)`) に `sips -p` でパディング (背景 `#222222`)。揃えないと Preview がサイズ別に複数ウィンドウに分散する
   2. 既存の Preview ウィンドウを `osascript` で閉じる
   3. パディング済みの `*.png` を `open -a Preview` で 1 ウィンドウにまとめて開く

4. **ユーザーへの伝達（必須）**: `show.sh` 実行後に「`/tmp/claude-shots/<TOPIC>/` を Preview で開きました」と通知する。ファイル読み込みツールで画像を開いてもエージェントが自分で確認しただけで、ユーザーの画面には何も表示されないので、必ず `show.sh` で `open` を通すこと。ファイル読み込みで「見せた」と判断するのは禁止

## 動画フロー (インタラクティブ確認)

ホバー、ドラッグ、トランジション、複数ステップフローなど静止画では伝わらない動作の確認に使う。`agent-browser record` は Playwright の CDP 録画なので、ブラウザ操作と同期して WebM が記録される。

1. **撮影開始** (Cookie / localStorage は引き継がれる)
   ```bash
   agent-browser --session <s> record start /tmp/claude-shots/<TOPIC>/<NN>-<label>.webm
   ```

2. **シナリオ実行**: 通常の `open` / `click` / `fill` / `scroll` を順次実行

3. **撮影終了**
   ```bash
   agent-browser --session <s> record stop
   ```

4. **再生**: WebM は QuickTime 非対応。Chrome、VLC、または Quick Look を明示的に指定する
   ```bash
   open -a "Google Chrome" /tmp/claude-shots/<TOPIC>/<NN>-<label>.webm
   ```

## PR 添付フロー

`scripts/publish-pr.sh` が以下を自動でやる

- `*.webm` を `ffmpeg` で GIF 化 (15fps / 800px) して埋め込み画像にする。元 WebM は高画質リンクとして併記
  - `<video>` タグは GitHub サニタイザに剥がされるため
- `*.png` `*.gif` `*.webm` を PR 下部のコメント textarea に drop してアップロード (URL は `github.com/user-attachments/assets/...`)
- 抽出した URL で PR 本文の `<!-- screen-review:start --> ... <!-- screen-review:end -->` ブロックを修正前/修正後テーブル + 動作確認 GIF + 元動画リンクで差し替え
- コメント textarea はクリア (送信しないので PR タイムラインに余計なコメントは残らない)

### 前提

- `gh auth status` で GitHub 認証済み
- `ffmpeg` インストール済み (`brew install ffmpeg`)
- `agent-browser` インストール済み
- 対象 PR が GitHub に存在 (現ブランチに紐付くか、引数で番号指定)

### 実行

```bash
<このスキルのディレクトリ>/scripts/publish-pr.sh \
  [--layout horizontal|vertical] [--labels "上部,中盤,下部"] \
  /tmp/claude-shots/<TOPIC> [PR_NUMBER]
```

PR_NUMBER 省略時は現ブランチに紐付く PR を取得。

### レイアウトの選択 (`--layout`)

before/after ペアがあるとき (修正前/修正後の比較) は常に 2 列テーブル。**before/after ペアが無い単発スクショの並べ方** を `--layout` で切り替える。

- `--layout vertical` (default) — 1 枚ずつ縦に並べる。画面が大きく表示されレビュアーが細部を確認しやすい。**修正の前後比較ではなく単一機能の挙動を見せたい時** や、**画像内の情報量が多い時** に向く
- `--layout horizontal` — 列ヘッダー付きの横並び 1 行テーブル。**新規機能を紹介する時や、同一画面の上部/中部/下部などを並べて見せたい時** に向く。PR 本文が縦に長くならない代わりに、各画像は小さく表示される

### 列ヘッダーのラベル (`--labels`)

`--layout horizontal` 時の列ヘッダーは、可能な限り **日本語ラベルを `--labels` で渡す** こと。

- `--labels "上部,中盤,下部"` — カンマ区切りで画像順に対応。画像数と一致しない場合はエラー終了する
- 未指定時はファイル名の `<NN>-` を除いた残りがヘッダーになる (例: `01-question-form-top.png` → `question-form-top`)。英語 kebab-case のままになるので PR レビュアーには読みにくい。ファイル名でラベル化できる場合のみフォールバックとして使う

### GitHub ログイン

スクリプトは内部で `agent-browser --profile ~/.agent-browser/profiles/screen-review-github` を使う。Chrome の永続プロファイルディレクトリに cookies / localStorage / IndexedDB をまるごと永続化することで、daemon 再起動後も auth を保持する。

`--session-name` (state json への auto-save) は daemon を完全に kill した場合に load が反映されない挙動を確認しているため、auth の確実な再利用には **`--profile` を使う** こと。

セッションが GitHub にログインしていなければ headed Chrome が立ち上がり、対話入力を待つ。

1. 立ち上がった Chrome ウィンドウで GitHub にログイン (username / password / 2FA / SSO 全て可)
2. ログイン完了後、ターミナルで Enter

以降の実行ではログイン不要。ログイン判定は URL に加えて `<meta name="user-login">` の存在も併せて確認するため、private repo の 404 (Page not found) で素通りすることはない。意図的にリセットしたい場合は `~/.agent-browser/profiles/screen-review-github` ディレクトリを削除する。

スクリプトは **ユーザー操作不要時はブラウザを表示しない (headless)**。初回ログインなど対話入力が必要なときだけ headed Chrome を立ち上げ、ログイン完了後は headed ウィンドウを閉じて以降の処理を headless で進める。前回 headed で立ち上げた Chrome が daemon に残っていてもスクリプト冒頭で `close` するため、再実行時にウィンドウが再利用されることはない。

### 出力される PR 本文ブロック

```markdown
<!-- screen-review:start -->
## スクリーンショット
| 修正前 | 修正後 |
|---|---|
| ![01-before-list](https://github.com/user-attachments/assets/...) | ![01-after-list](https://github.com/user-attachments/assets/...) |

## 動作確認
![demo](https://github.com/user-attachments/assets/...)

## 元動画 (高画質)
- [demo.webm](https://github.com/user-attachments/assets/...)
<!-- screen-review:end -->
```

手書き本文は壊さない。

### マージ後の清掃

不要。user-attachments は GitHub 側で保持されるだけで、リリースタグやリポジトリ汚染も発生しない。

### セレクタが UI 変更で壊れた場合

`scripts/publish-pr.sh` 冒頭の `FILE_INPUT` / `COMMENT_TEXTAREA` を更新する。現在の値は `agent-browser` で確認可能

```bash
agent-browser --session screen-review-github eval --stdin <<'EVAL'
JSON.stringify(Array.from(document.querySelectorAll("input[type=file]")).map(el => el.id))
EVAL
```

## 注意

- `/tmp/` は再起動でクリアされる。長期保管は `~/Pictures/claude-shots/` 等へ `cp` で移す
- 散在した既存スクショを集約する場合は連番リネームを伴ってコピー
  ```bash
  cp /tmp/foo.png /tmp/claude-shots/<TOPIC>/01-foo.png
  ```
- 認証情報・個人情報の写り込みを目視確認する。写っていればローカル確認に留める
- `show.sh` は `*.png` のみ対象。`*.webm` は手動で `open -a "Google Chrome"` を呼ぶ
- 録画は新規 browser context で開始される。途中で `--headed` を変えたい場合は事前に `agent-browser close` してやり直す
- user-attachments URL は GitHub の認証付き経路で配信されるが、PR の閲覧権限を持つメンバーには表示される。GitHub 外への共有時は権限を確認すること

## GitHub UI が変わったときに見るべきセレクタ

GitHub の DOM が変わったら publish-pr.sh の `FILE_INPUT` `COMMENT_TEXTAREA` を以下の手順で実値確認する。

- PR 最下部コメント欄の textarea ID と、その隣接する hidden file input の ID を確認する
- inline comment 用 textarea (`name="comment[body]"`) と bottom comment 用 textarea は `name` が同一。**OR セレクタで `textarea[name="comment[body]"]` を含めると `querySelector` が DOM 順で先頭の inline comment 用を返してしまう**。polling 用セレクタは bottom comment の ID 単独指定にする (現在 `#new_comment_field`)
- file input は隣接セレクタ (`~ input[type=file]`) より ID 直接指定が安全。GitHub の DOM 構造変更でファイルが意図しない input に投げられるとアップロード自体は走るが結果が見えない
