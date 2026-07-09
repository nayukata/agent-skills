# agent-skills

nayukata 個人の AI エージェント用スキル集。SKILL.md 形式（Agent Skills 仕様）を読むエージェント全般で使え、skills CLI / Claude Code plugin marketplace / install.sh の3つの方法で導入できる。

## インストール

### 一般ユーザー向け（推奨）

Codex CLI など、SKILL.md 形式の Agent Skills 仕様を読むエージェント全般で使える。[skills CLI](https://github.com/vercel-labs/skills)（`npx skills`）経由で導入する。

```bash
npx skills add nayukata/agent-skills
```

- `--skill <name>`: 個別のスキルだけを選んで導入する。
- `--copy`: symlink の代わりに実体コピーする（symlink 先を読めないサンドボックス向け）。

### Claude Code ユーザー向け

Claude Code 上で marketplace を追加し、欲しいものだけを選んで導入できる。

```bash
# 1. marketplace を追加（最初の一度だけ）
/plugin marketplace add nayukata/agent-skills

# 2-a. 欲しいものだけ個別に入れる
/plugin install create-pr@nayukata
/plugin install review-pr@nayukata

# 2-b. まとめて全部入れる
/plugin install nayukata-skills@nayukata
```

`/plugin marketplace add` に渡すのは GitHub の `owner/repo`。`@nayukata` の部分は marketplace 名（`.claude-plugin/marketplace.json` の `name`）。

### ローカル開発向け

このリポジトリを clone してスキルを編集する人向け。symlink ベースの `install.sh` で、編集内容を各エージェントに即反映できる。

```bash
git clone https://github.com/nayukata/agent-skills.git
cd agent-skills
./install.sh
```

これを実行すると、`~/.agents/skills/` を共有ハブとして各スキルへの symlink を作り、そこから `~/.claude/skills/`・`~/.codex/skills/`・`~/.gemini/skills/` にスキル単位で symlink を張る（親ディレクトリが存在するエージェントのみ対象）。

- `--copy`: symlink の代わりに実体コピーする。サンドボックスなどで symlink 先を読めないエージェント向けのフォールバック。
- `--agent-dir <path>`: 上記3つに加えて、任意のエージェントのスキルディレクトリを追加で対象にする（複数指定可）。

## 配布している plugin

すべてタスクの内容に応じてエージェントが自動で発動するスキル。

| plugin | 用途 | 説明 |
|---|---|---|
| `create-pr` | PR 作成 | 設計意図・トレードオフを織り込んだ説明文 + インラインコメントを生成。UI 変更時は前後スクショ / 動作 GIF を screen-review 連携で自動添付 |
| `review-pr` | PR レビュー | PR URL から変更を解説し、問題点や確認すべき質問を提示 |
| `ux-laws` | UI 設計・改修 | 10 の UX 法則に基づいて既存 UI をレビューし、推奨改善案を出す |
| `design-hearing` | UI デザイン具体化 | 曖昧なデザイン要望（「いい感じに」等）を仮埋めブリーフと対比モックのヒアリングで具体化してから実装。擬態語・素材・景色を実装値に翻訳する辞書つき |
| `writing-tasks` | チケット作成 | タスク / バグチケットの説明文をユーザー視点で書く（ClickUp / Jira / GitHub Issues / Linear / Notion など） |
| `screen-review` | UI 動作確認 | スクショ / 画面録画を集約して macOS Preview で一括表示。PR への修正前後テーブル / GIF 埋め込みも自動化 |

### 全部入り

| plugin | 内容 |
|---|---|
| `nayukata-skills` | 上記 6 スキルをまとめて導入 |

> `create-pr` は UI 変更の動作確認で `screen-review` を利用する。両方使う場合は `nayukata-skills` か、`create-pr` と `screen-review` の併用がおすすめ。

## リポジトリ構成

```
.
├── .claude-plugin/
│   └── marketplace.json   # marketplace 定義（個別 plugin + 全部入り）
├── install.sh             # ローカル開発向けの symlink 導入スクリプト
└── skills/                # 自動発動スキル
    ├── create-pr/SKILL.md
    ├── review-pr/SKILL.md
    ├── ux-laws/SKILL.md
    ├── writing-tasks/SKILL.md
    ├── screen-review/
    │   ├── SKILL.md
    │   └── scripts/       # 画面確認 / PR 添付の補助スクリプト
    │       ├── publish-pr.sh
    │       └── show.sh
    └── design-hearing/
        ├── SKILL.md
        ├── references/    # ブリーフ形式 / 翻訳辞書 / 禁止パターン
        └── for-humans.md  # 人間向けの語彙ガイド（スキル動作には不要）
```

各 plugin は `source: "./"`（リポジトリ root）を共有し、`skills` フィールドで含める対象を絞り込んでいる。新しい skill を追加するときは、`skills/` にディレクトリを置き、`marketplace.json` の `plugins` に entry を 1つ足す。

## 最新版を取り込む

skill を編集して GitHub に push したあと、手元の Claude Code に反映するには次を実行する。

```bash
/plugin marketplace update nayukata
```
