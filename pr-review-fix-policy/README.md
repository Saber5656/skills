# PR Review Fix Policy

> 現在ブランチのPRに残る有効な未解決レビューコメントを整理し、実装修正前にユーザーと方針合意するスキル。

## What It Does

- 現在のgitワークツリーブランチからPRを特定する。
- unresolvedかつnot outdatedのreview threadsだけを修正対象にする。
- ファイル単位でクラスタリングし、コメントごとの指摘を残す。
- 各コメントについて、現状の問題/デメリットと対応メリット/解決される課題を明記する。
- 原則一括確認し、曖昧・衝突・高リスクだけ個別に確認する。
- 合意後は実装用スキルへhandoffし、実装・push・remote-head確認後に対応済みreview thread/commentごとへ個別返信することを含める。

## What It Does Not Do

- 実装編集はしない。
- commitやpushはしない。
- この方針確認スキル自身はGitHubコメント返信やthread resolveはしない。
- handoff後の実装用スキルは、対応済み指摘ごとに対応内容、commitまたは差分、検証結果を返信する。
- コメント取得に失敗した状態で内容を推測しない。

## Quick Prompt

```text
現在のブランチのPRに未解決コメントがあるはずなので、修正方針を確認して
```

See [SKILL.md](SKILL.md) for full workflow details.
