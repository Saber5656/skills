---
name: skill-updater
description: 既存スキルの修正・改善・最適化・挙動変更・description更新を行うときに必ず使う。ユーザーが「スキルを直したい」「既存スキルを改善したい」「SKILL.mdを更新したい」「このスキルの出力が不満」「トリガーを調整したい」「テストして改善したい」と言った場合は、明示的に skill-updater と言っていなくてもこのスキルを使う。修正前に不満点、望む改善、最終アウトプット像を厳密に擦り合わせ、修正後は eval / benchmark テスト完了まで完了宣言しない。
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
category: Dev
created: 2026-05-15
status: active
purpose: 既存スキルの改善要求を厳密に擦り合わせ、修正・テスト・ベンチマークまで完了させる
argument-hint: "[対象スキル名または改善内容]"
---

# Skill Updater

既存スキルを修正するときの専用ワークフロー。`skill-creator` と同じく eval / benchmark を必須にしつつ、修正前の擦り合わせをより厳密に行う。

## When I Activate

- ユーザーが既存スキルの修正、改善、最適化、挙動変更、トリガー調整、description 更新に言及したとき
- ユーザーが特定スキルの出力、保存内容、判断、運用フローに不満や違和感を示したとき
- ユーザーが「このスキルをこう直したい」「前の挙動が微妙」「テストして改善して」と言ったとき
- 新規スキルをゼロから作る場合は `skill-creator` を使う。既存スキルの変更が含まれる場合は、このスキルを併用する

## Core Rule

既存スキルを修正するときは、次の3点が明確になるまで編集を始めない。

| 確認項目 | 確認すること |
|---|---|
| 不満・改善点 | 既存スキルのどの挙動、出力、判断、トリガー、運用に不満があるか |
| 改善方針 | その課題をどのように直したいか。禁止したい挙動、増やしたい判断、残したい既存挙動は何か |
| 出力イメージ | 修正後にどのような最終アウトプット、保存内容、応答、ファイル、レビュー結果を期待しているか |

ユーザーの発言に3点がすでに含まれている場合は、短く要約して「この理解で進める」と明示し、作業を続ける。不足がある場合は、A/B/C で答えられる選択肢を添えて確認する。

## Workflow

1. **対象スキルの特定**
   - `~/dev/skills/<skill-name>/SKILL.md` を確認する。
   - 類似スキルや関連 README がある場合は必要最小限だけ読む。

2. **現状挙動の把握**
   - 現在の description、トリガー条件、出力形式、禁止事項、テスト資産を確認する。
   - 既存の eval / benchmark が `skills/.workspace/<skill-name>/` にあるか確認する。

3. **改善ブリーフの作成**
   - 修正前に次の形で短く整理する。

```markdown
## 改善ブリーフ

| 項目 | 内容 |
|---|---|
| 対象スキル |  |
| 現在の不満・改善点 |  |
| 望む改善方針 |  |
| 修正後の出力イメージ |  |
| 維持すべき既存挙動 |  |
| テストで確認すること |  |
```

4. **テスト設計**
   - 修正前または旧仕様相当を baseline とし、修正後と比較できる eval を2〜3件以上作る。
   - eval は `skills/.workspace/<skill-name>/evals/evals.json` に保存する。
   - 各 eval に客観的なアサーションを置く。主観評価だけで終わらせない。

5. **修正**
   - 既存の責務とスタイルを尊重し、必要最小限の差分で `SKILL.md` や関連 README / references を更新する。
   - description を変更する場合は、トリガーすべきケースと対象外ケースが誤解されないか確認する。

6. **テスト実行**
   - `skill-creator` の eval / benchmark 形式に従い、`skills/.workspace/<skill-name>/iteration-N/` に結果を保存する。
   - `with_skill` と baseline（`old_skill` または `without_skill` / 旧仕様相当）を比較する。
   - 各実行に `grading.json` を作成し、`expectations` は `text`, `passed`, `evidence` を使う。
   - `scripts.aggregate_benchmark` で `benchmark.json` と `benchmark.md` を生成する。
   - 可能なら `eval-viewer/generate_review.py --static` で `review.html` も生成する。

7. **レビュー**
   - 独立レビュー観点で、要件充足、回帰リスク、テスト妥当性を確認する。
   - スキル修正がセキュリティ、秘密情報、権限、外部サービス利用に関わる場合は `security-professor` など利用可能な安全性レビュー観点も加える。

8. **ドキュメント更新**
   - 設定済みの task/evidence vault に、改善ブリーフ、修正内容、テスト結果、残課題を記録する。
   - Vault 更新前に完了宣言しない。

## Completion Criteria

次のすべてが終わるまで完了宣言しない。

| ゲート | 必須条件 |
|---|---|
| 擦り合わせ | 不満・改善方針・出力イメージが明文化されている |
| 修正 | 対象スキルの差分が要件に対応している |
| テスト | eval / grading / benchmark が作成されている |
| レビュー | ドメインレビューと独立レビューの観点を通している |
| 記録 | 設定済みの task/evidence vault に成果と判断を保存している |

## Output Format

完了報告は次の内容を簡潔に含める。

```markdown
## skill-updater 実施結果

| 項目 | 内容 |
|---|---|
| 対象スキル |  |
| 修正した不満・改善点 |  |
| 変更ファイル |  |
| Benchmark |  |
| 判定 |  |

成果物:
- evals: ...
- benchmark: ...
- review: ...
- Vault: ...
```

## Related Skills

- `skill-creator`: 新規スキル作成、eval / benchmark 基盤、description 最適化に使う。
