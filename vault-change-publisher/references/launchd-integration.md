# launchd Integration

`launchd`は04:00にdaily専用runnerを起動するだけとする。汎用automation ID dispatcherへdaily publication権限を追加しない。

```text
launchd 04:00
  -> daily dedicated runner
  -> deterministic standing-task snapshot into run staging
  -> collection Codex process (Web/search, run staging write only)
  -> deterministic artifact validation
  -> publication review Codex process (read-only, no search/network)
  -> local commit Codex process (Vault/Git write, no search/network)
  -> initial fixed runner pushes (manifest-bound object IDs -> refs/heads/main)
  -> deterministic push evidence hunk
  -> evidence review Codex process (read-only, no search/network)
  -> deterministic evidence commit + fixed Agents main push
```

## Runtime Files

| Tracked source | Runtime destination |
|---|---|
| `assets/run-daily-it-news-vulnerability-check.sh` | daily automation workdir |
| `assets/daily-it-news.collect.prompt.md` | same workdir |
| `assets/daily-it-news.review.prompt.md` | same workdir |
| `assets/daily-it-news.publish.prompt.md` | same workdir |
| `assets/daily-it-news.evidence-review.prompt.md` | same workdir |
| `assets/automation.env.example` | copy manually to ignored `automation.local.env` and fill local values |
| `references/collection-result.schema.json` | same workdir |
| `references/publication-review-result.schema.json` | same workdir |
| `references/publication-commit-result.schema.json` | same workdir |
| `references/evidence-review-result.schema.json` | same workdir |
| `references/automation-result.schema.json` | same workdir |
| `scripts/resolve-runtime-context.py` | same workdir |
| `scripts/fetch-vault-main.py` | same workdir |
| `scripts/capture-vault-state.py` | same workdir |
| `scripts/validate-collection-result.py` | same workdir |
| `scripts/install-verified-artifacts.py` | same workdir |
| `scripts/validate-publication-review.py` | same workdir |
| `scripts/push-committed-heads.py` | same workdir |
| `scripts/prepare-publication-evidence.py` | same workdir |
| `scripts/commit-push-publication-evidence.py` | same workdir |
| `scripts/git_diff_digest.py` | same workdir |
| `scripts/prepare-codex-output-schema.py` | same workdir |
| `scripts/validate-canonical-result.py` | same workdir |
| `scripts/stage-standing-task.py` | same workdir |
| `scripts/interpret-automation-result.sh` | same workdir |

Tracked sourceがmainへmergeされた後に配備し、各source/destinationのSHA-256一致を確認する。task worktreeをproduction runtime pathとして参照しない。

正本のresult schemaはstate-dependent constraintを保持する。runnerは各run配下へCodex Structured Outputs対応subsetを生成してAPIへ渡し、生成結果は各phaseのdeterministic validatorで正本契約に照合する。互換schemaだけをpublication可否の判定に使わない。

collection Codex processへiCloud上のstanding taskを直接読ませない。resolverが検証した正本taskをrunner側の専用helperでrun stagingへ0600・exclusive createし、collection contextにはsnapshot pathだけを渡す。これによりcollectionのVault非アクセス境界を維持し、launchd配下のNode/CodexにVault全体のTCC権限を要求しない。

## Local Configuration

personal absolute paths、Vault names、machine layoutはtracked fileへ書かない。`automation.local.env`にはSaihai primary checkout、relative destination、承認taskのSHA-256 pinだけを置き、runnerは`directory_paths.load_environment(checkout_root=..., environ={}, require_catalog=True)`でcanonical rootsを解決する。承認taskが変わった場合は自動追従せず、内容を人間が再確認してpinを更新する。

File Provider配下のVaultでnetwork Git transportを起動するときは、transport subprocessのcurrent working directoryをVaultへ移さない。resolverが検証したGit directoryとworktree rootをそれぞれ`--git-dir` / `--work-tree`へ明示し、fetch、`ls-remote`、fixed pushを実行する。remote URL、object ID、`refs/heads/main`の固定契約とnon-force制約は変更しない。

## Deployment Gate

1. source PRがreview・merge済み。
2. runtime filesとplistをbackup。
3. tracked filesをcopyしchecksum一致。
4. ignored local configを人間が確認。
5. `zsh -n`、Python tests、JSON parse、`plutil -lint`。
6. TCC復旧後に実Vault E2Eを1回実施。
