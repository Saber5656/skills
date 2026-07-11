---
name: pr-review-fix-policy
description: >
  現在のgitワークツリーブランチから生成されたGitHub PRについて、
  unresolvedかつnot outdatedのレビューコメントを確認し、修正実装前に
  ユーザーと修正方針を合意するために使う。ユーザーが「PRの指摘を確認」、
  「未解決コメントの修正方針」、「CodeRabbit/Codexのコメントどう直す？」、
  「PRレビュー対応の方針だけ見て」などと言ったら必ず使う。実装・commit・push
  まで直接行うスキルではなく、合意後は `gh-address-comments`、commit、
  push系スキルへ引き渡す。合意済み修正の実装後は、対応したreview
  threadごとに対応内容コメントを返信するところまで後続作業に含める。
user-invocable: true
allowed-tools: Read, Grep, Bash, Write, Edit
category: Dev
created: 2026-06-27
status: active
purpose: 現在ブランチのPRに残る有効な未解決レビューコメントを整理し、実装修正前にユーザーと方針合意する
argument-hint: "[任意: PR番号/URL or 方針確認メモ]"
---

# PR Review Fix Policy

このスキルは、PRレビューコメントを「すぐ直す」前に、何をどう直すかをユーザーと合意するための方針ゲートである。
対象は、現在のgitワークツリーブランチに紐づくPRのうち、unresolvedかつnot outdatedのreview threadsに限定する。

## When I Activate

- ユーザーが現在ブランチのPRについて、未解決レビューコメントの確認や修正方針整理を求めたとき。
- ユーザーが「PRに指摘きてる」「修正方針確認して」「CodeRabbit/Codexのコメントを見て」と言ったとき。
- ユーザーが実装前に、どの指摘を受け入れるか、保留するか、説明で返すかを確認したいとき。
- 明示PR番号やURLが渡された場合も使ってよい。ただし既定は現在ワークツリーブランチのPR。
- 実装、commit、push、GitHub返信、thread resolveを直接求められた場合は、このスキルだけで完結させず、方針合意後に該当スキルへ引き渡す。

## Core Policy

1. コメント内容を推測しない。
2. 対象は `isResolved == false` かつ `isOutdated == false` のreview threadsに絞る。
3. Top-level PR commentsとoutdated threadsは補足情報として扱い、修正対象リストには混ぜない。
4. ファイル単位でクラスタリングし、クラスタ内でコメントごとの指摘が分かる形にする。
5. 各コメントについて、既存の記述やコードを放置した場合の問題/デメリットと、対応した場合のメリット/解決される課題を明記する。
6. 原則は一括確認。ただし曖昧、衝突、高リスク、または設計判断を変える指摘は個別にユーザー確認する。
7. コードベースを読めば判断できることは、質問せずに調査してから方針案を出す。
8. このスキル中にファイル編集、commit、push、GitHub返信、review thread resolveをしない。
9. ユーザーが推奨方針の実装を承認した場合、後続handoffには「対応したreview threadごとに対応内容コメントを返信する」を標準で含める。thread resolveは、ユーザーが明示した場合だけ含める。
10. 複数threadを1クラスタとして実装する場合でも、GitHub返信はクラスタ単位でまとめず、対応した指摘ごとに個別返信する。共通修正で複数指摘を解決した場合も、それぞれのthreadに同じcommitと該当する対応内容を返す。

## Workflow

### 1. PRを特定する

- 明示されたPR URLまたは番号があればそれを使う。
- 明示がなければ、現在のgitワークツリーからブランチ名とremoteを確認し、`gh pr view`でPRを特定する。
- `gh`認証、network、権限不足でPRコメントを取得できない場合は、PR候補の特定結果だけ出して停止する。コメント内容を想像して方針を作らない。

### 2. Thread-awareにコメントを取得する

- `gh-address-comments` が使える環境では、そのthread-aware取得手順を優先する。
- GraphQLまたは同等の方法で、少なくとも次を取得する:
  - thread id
  - `isResolved`
  - `isOutdated`
  - file path
  - lineまたはoriginal line
  - author
  - body summary
  - related review state if available
- FlatなPR commentsだけを完全なreview thread情報として扱わない。

### 3. 対象コメントを分類する

各threadを次のどれかに分類する。

| 分類 | 意味 |
|---|---|
| `actionable` | 変更すべき指摘 |
| `explanation-only` | コード変更ではなく返信や説明で足りる可能性が高い指摘 |
| `duplicate` | 他threadと同じ原因を指している指摘 |
| `ambiguous` | 追加調査またはユーザー確認が必要な指摘 |
| `conflicting` | 他指摘、既存方針、仕様と衝突する指摘 |
| `high-risk` | security-sensitive、release、権限、データ破壊、互換性に触れる指摘 |
| `blocked` | 情報不足や権限不足で方針化できない指摘 |

分類時には、単に「何を直すか」だけでなく、なぜ直すべきかを明確にする。
各コメントに対して次の2点を必ず整理する。

| 項目 | 書く内容 |
|---|---|
| 現状の問題/デメリット | 指摘事項をもとに、既存の記述、コード、設定、テストだとどのような誤解、漏れ、バグ、運用リスク、レビュー抜けが起きるか |
| 対応メリット/解決される課題 | 指摘に対応すると、どのリスクが減り、どの判断や実装が明確になり、後続レビューや運用で何が改善されるか |

### 4. ファイル単位でクラスタリングする

同じファイルの指摘を1クラスタにまとめる。
同じ原因が複数ファイルにまたがる場合は、主ファイルクラスタにまとめ、関連ファイルを明示する。
クラスタ内では、必ずコメントごとの指摘を見える形で残す。

### 5. 修正方針を作る

各クラスタに対して、次を出す。

- 受け入れるか、保留するか、説明で返すか。
- コメントごとの現状の問題/デメリット。
- コメントごとの対応メリット/解決される課題。
- 変更する場合、どのファイルまたは挙動をどう変えるか。
- 実装に進む場合の担当スキルまたは後続ワークフロー。
- 実装後に返信すべきreview threadと返信方針。返信は指摘ごとに個別に行い、「どのcommit/差分で何を直したか」「その指摘に対する具体的な対応内容」「検証結果」「説明で対応する場合の理由」を含める。
- テストまたは確認観点。
- リスクと未決事項。

### 6. ユーザー確認を行う

通常は全クラスタをまとめて確認する。
ただし、`ambiguous`、`conflicting`、`high-risk`、`blocked` がある場合は、そのクラスタだけ一問ずつ確認する。
選択肢は `A`、`B`、`C` で答えられる形にし、推奨案を必ず書く。

## Output Format

```markdown
## PR Review Fix Policy

| Field | Value |
|---|---|
| PR | #123 title |
| Branch | feature/example |
| Scope | unresolved and not outdated review threads |
| Actionable threads | 4 |
| Outdated / resolved ignored | 7 |

## Clusters

### 1. path/to/file.ts

| Thread | Author | Line | Classification | Summary | Current problem / downside | Benefit after fix |
|---|---|---:|---|---|---|---|
| T1 | coderabbitai | 42 | actionable | null case is not handled | Null input can pass into the normal path and fail later with an unclear error. | Validation fails early with a specific error and prevents the regression from recurring. |
| T2 | codex | 51 | duplicate | same validation path lacks tests | The same validation rule can regress without detection. | A focused regression test proves the boundary and supports future refactors. |

**Recommended policy:** accept both as one validation fix.
**Fix direction:** add guard in `validateX`, add regression test for null input.
**Risk:** low.
**Handoff:** implement with `gh-address-comments`; commit/push only after validation; reply to each addressed review thread with its specific fix summary after the fix is pushed.

## Confirmation

A. Proceed with all recommended policies and hand off to implementation, including per-thread replies to addressed review comments after the fix is pushed.
B. Choose specific clusters to adjust.
C. Stop without implementation.

Recommended: A
```

## Handoff Manifest

ユーザーが方針を承認したら、次の形で後続へ渡す。

```markdown
## PR Review Implementation Handoff

- PR: #123
- Branch: feature/example
- Approved scope: cluster 1, cluster 2
- Excluded threads: T5 explanation-only
- Required reasoning fields: current problem/downside and benefit after fix for each approved thread
- Required checks: unit tests, `git diff --check`, project-specific checks
- GitHub write actions: after implementation, push, and remote-head verification, reply to each addressed review thread/comment with the specific fix summary, pushed commit or diff reference, and validation result; do not resolve threads unless the user explicitly asks
- Vault update: required / not required / blocked
```

## Vault Recording

リポジトリの `AGENTS.md`、プロジェクト指示、またはユーザー指示が作業記録を要求している場合だけ、Vaultまたは指定の正本へ記録する。
記録する内容は、PR番号、対象thread数、クラスタ概要、合意した修正方針、保留事項、後続handoffである。
各コメントについて、現状の問題/デメリットと対応メリット/解決される課題も記録対象に含める。
記録先が不明、または書き込みできない場合は、記録できなかった理由をユーザーに返す。

## Write Safety

- このスキルでは実装編集をしない。
- このスキルではcommitしない。
- このスキルではpushしない。
- このスキルではGitHubへ返信しない。
- このスキルではreview threadをresolveしない。
- ユーザーが実装を承認したら、合意内容をhandoffとして出し、実装用スキルへ移る。
- handoff後の実装用スキルは、対応済みthreadごとの返信を標準後続作業として扱う。返信内容には対応commitまたは差分、指摘ごとの具体的な対応内容、検証結果を含める。複数指摘を同じ修正で解決した場合も、それぞれのthreadへ個別に返信する。
- review threadのresolveは、返信とは別のGitHub write actionとして扱い、ユーザーの明示承認がある場合だけ行う。

## Failure Modes

| 状況 | 対応 |
|---|---|
| PRが見つからない | branch、remote、候補PRを示し、PR番号またはURLを求める |
| `gh`未認証 | `gh auth status`結果を示し、認証が必要と伝える |
| network不可 | コメント取得できないため停止し、推測しない |
| unresolved/not outdated threadが0件 | 対象コメントなしと報告し、resolved/outdated/top-levelの補足だけ必要なら提示する |
| コメント同士が衝突 | 衝突内容を一問ずつ確認する |
| security-sensitive変更を含む | リスクを明示し、通常より強い検証またはowner sign-off要否を確認する |

## Related Skills

- `github:gh-address-comments`: thread-awareなPRコメント取得と、合意済み修正の実装に使う。
- `commit`: 合意済み・検証済み差分のcommitに使う。
- `push`: commit済みブランチのpushに使う。
- `grill-me`: 方針が曖昧な場合に、一問ずつ設計判断を詰めるために使う。
