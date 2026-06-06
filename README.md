# agent-skills

nayukata 個人の Claude Code スキルを配布する plugin marketplace。

## インストール

Claude Code 上で marketplace を追加し、欲しいものだけを選んで導入できる。

```bash
# 1. marketplace を追加（最初の一度だけ）
/plugin marketplace add nayukata/agent-skills

# 2-a. 欲しいものだけ個別に入れる
/plugin install create-pr@nayukata
/plugin install review-pr@nayukata

# 2-b. まとめて全部入れる
/plugin install agent-skills-all@nayukata
```

`/plugin marketplace add` に渡すのは GitHub の `owner/repo`。`@nayukata` の部分は marketplace 名（`.claude-plugin/marketplace.json` の `name`）。

## 配布している plugin

すべてタスクの内容に応じて Claude が自動で発動するスキル。

| plugin | 用途 | 説明 |
|---|---|---|
| `create-pr` | PR 作成 | 設計意図・トレードオフを織り込んだ説明文 + インラインコメントを生成。UI 変更時は前後スクショ / 動作 GIF を screen-review 連携で自動添付 |
| `review-pr` | PR レビュー | PR URL から変更を解説し、問題点や確認すべき質問を提示 |
| `ux-laws` | UI 設計・改修 | 10 の UX 法則に基づいて既存 UI をレビューし、推奨改善案を出す |
| `writing-tasks` | チケット作成 | タスク / バグチケットの説明文をユーザー視点で書く（ClickUp / Jira / GitHub Issues / Linear / Notion など） |
| `screen-review` | UI 動作確認 | スクショ / 画面録画を集約して macOS Preview で一括表示。PR への修正前後テーブル / GIF 埋め込みも自動化 |

### 全部入り

| plugin | 内容 |
|---|---|
| `agent-skills-all` | 上記 5 スキルをまとめて導入 |

> `create-pr` は UI 変更の動作確認で `screen-review` を利用する。両方使う場合は `agent-skills-all` か、`create-pr` と `screen-review` の併用がおすすめ。

## リポジトリ構成

```
.
├── .claude-plugin/
│   └── marketplace.json   # marketplace 定義（個別 plugin + 全部入り）
└── skills/                # 自動発動スキル
    ├── create-pr/SKILL.md
    ├── review-pr/SKILL.md
    ├── ux-laws/SKILL.md
    ├── writing-tasks/SKILL.md
    └── screen-review/
        ├── SKILL.md
        └── scripts/       # 画面確認 / PR 添付の補助スクリプト
            ├── publish-pr.sh
            └── show.sh
```

各 plugin は `source: "./"`（リポジトリ root）を共有し、`skills` フィールドで含める対象を絞り込んでいる。新しい skill を追加するときは、`skills/` にディレクトリを置き、`marketplace.json` の `plugins` に entry を 1つ足す。

## 管理者向け: marketplace の更新

plugin を追加・変更したら、利用者は次で最新を取り込む。

```bash
/plugin marketplace update nayukata
```
