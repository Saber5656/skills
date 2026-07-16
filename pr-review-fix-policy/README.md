# PR Review Fix Policy

> 1件または複数PRに残る有効な未解決レビューコメントを整理し、実装修正前にPRごとの方針合意を得るスキル。

## What It Does

- 現在のgitワークツリーブランチからPRを特定する。
- `owner/repo#123` を複数指定して、最大20 PRをthread-awareに一括取得する。
- PR横断で見やすく整理しつつ、head・承認・返信・resolve・完了判定はPRごとに分離する。
- unresolvedかつnot outdatedのreview threadsだけを修正対象にする。
- ファイル単位でクラスタリングし、コメントごとの指摘を残す。
- 各コメントについて、現状の問題/デメリットと対応メリット/解決される課題を明記する。
- 原則一括確認し、曖昧・衝突・高リスクだけ個別に確認する。
- 合意後は実装用スキルへhandoffする。code changeではpush・remote-head確認後、explanation-onlyでは新規commit/pushを作らず説明検証後に、対象threadを再取得して個別返信し、返信成功後の再取得を経てresolveし、`isResolved`を確認する。
- 同梱のGitHub Actionsテンプレートでreview eventをdebounceし、current headに耐久シグナルを付ける。Saihaiまたはローカルautomationはそのシグナルを回収後、GitHubからfresh fetchする。

## What It Does Not Do

- 実装編集はしない。
- commitやpushはしない。
- この方針確認スキル自身はGitHubコメント返信やthread resolveはしない。
- handoff後の実装用スキルは、ユーザーが承認したreview threadごとに対応内容、commitまたは差分、検証結果を返信し、その返信成功後にthreadをresolveする。
- explanation-onlyではcommit/push/remote-head確認を`not_applicable`とし、空commitを作らない。
- reply直前とresolve直前にthread identityと`isResolved`/`isOutdated`を再取得する。承認時は有効だったthreadがapproved fixのpushによってoutdated化した場合は、承認前snapshotとremote fix provenanceを照合できるときだけ返信・resolveを続行する。
- 承認時点ですでにoutdated、未対応、除外、identity不一致、またはfix provenanceを証明できないthreadにはmutationを行わない。
- top-level PR commentsはreview threadではないためresolve対象外とし、`not_applicable`として報告する。
- reply、resolve、`isResolved`確認のどこかが失敗したthreadを完了扱いしない。
- コメント取得に失敗した状態で内容を推測しない。
- GitHub Actionsだけで既存Codex Desktop taskを直接再開したとは扱わない。
- review bodyをworkflowやconsumerの命令として実行しない。

## Batch fetch

```bash
python3 scripts/fetch_review_batch.py owner/repo#123 owner/repo#456
```

1 PRの失敗はそのPRの`blocker`として出力され、取得できたPRのsnapshotは保持されます。

## Event receiver installation

`assets/review-signal.yml` を対象repositoryの `.github/workflows/review-signal.yml` にコピーします。追加secretは不要です。workflowはPRをcheckoutせず、`contents: read`、`pull-requests: read`、`statuses: write`だけを使います。

`review-intake/signal`は通知用statusで、required merge checkには設定しません。同じsignal identityは再投稿せず、GitHub APIは最大3回だけ再試行します。forkやrepository設定によってstatus writeが拒否された場合はworkflowが失敗し、`signal_delivery_blocked`をsummaryに残します。Saihai側のconsumerとprivate task mappingは別途必要です。

Saihai/local automation側はprivateな`WatchRegistration` JSONを保持し、次を一回実行します。

```bash
python3 scripts/consume_review_signal.py /private/path/watch-registration.json
```

consumerは待機しません。privateなwatch単位lockを保持して再読込・照合・claim・ackを行います。statusのsignal ID、登録済みhead、freshなreview/thread digestを照合し、`ready`ならwatch fileへackを原子的に保存してからtask IDをローカル側へ返します。同時または後続consumerの同じsignalは`duplicate_signal`になります。statusがなく失敗workflow runがある場合は`delivery_blocked_unreconciled`を返します。レビュー本文やCodex task IDをGitHubへ公開しません。

## Quick Prompt

```text
現在のブランチのPRに未解決コメントがあるはずなので、修正方針を確認して
```

See [SKILL.md](SKILL.md) for full workflow details.
