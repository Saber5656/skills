---
name: vault-change-publisher
description: >
  承認済みの定期バッチまたはユーザーの明示依頼に含まれる Publication Manifest に従い、
  AGENTS_VAULT_ROOT と USER_VAULT_ROOT の全差分を最小の意味単位でcommitし、両mainを
  non-force pushする。通常のVault編集、ニュース要約、read-only調査、承認情報のない依頼では
  使用しない。自動推測や暗黙起動をせず、呼び出し元がこのスキルを明示指定した場合だけ使う。
allowed-tools: Read, Grep, Glob, Bash
---

# Vault Change Publisher

ニュース生成やadvisory判断から独立し、完成済みVault成果物のGit publicationだけを担当する。

## Activation Boundary

次の Publication Manifest がすべて揃う場合だけ実行する。

| Field | Required |
|---|---|
| `task_id` | 登録済みtask |
| `authorization_source` | 人間承認またはstanding taskの記録 |
| `source_type` | `automation_daily_run` または `explicit_user_request` |
| `targets` | `AGENTS_VAULT_ROOT`, `USER_VAULT_ROOT` のみ |
| `artifacts_complete` | `true` |
| `main_push_authorized` | `true` |
| `force` | `false` |
| `evidence_target` | Agents-Vault内のstanding task |

不足時は `publication_status: blocked_missing_manifest` を返し、Git操作を始めない。control plane、承認者、対象repoを探索・推測しない。

## Path Resolution

Saihai primary checkout の `directory-path.env` だけをsourceにし、空mapping `env={}` でcatalogを読み込む。`status=loaded` とread/writeを確認し、catalogの2 Vaultだけを対象にする。シェル環境変数やManifest内の任意pathへ置き換えない。

## Preflight Barrier

両repoをmutation前に検査する。

| Check | Pass condition |
|---|---|
| repository root | 解決したVault root自身 |
| branch | `main` |
| operation | merge/rebase/cherry-pick/revert中でない |
| upstream | `origin/main` |
| freshness | fetch後、今回のpublication開始前のlocal `main`と`origin/main`が完全一致（ahead/behind/divergedでない） |
| remote | remote名・host・repositoryをcredentialなしで記録可能 |
| changes | staged/unstaged/untrackedを全列挙可能 |

片方でも失敗したら、どちらもcommit/pushしない。既存のlocal-ahead commitを今回の承認対象へ暗黙に含めない。checkout、pull、merge、rebase、reset、stashで回避しない。

## Commit Phase

各Vaultで `commit` スキルを読み、Manifestをcaller-supplied task contextとして渡す。

1. 全path/hunkを目的、task、変更種別、risk、review lineで分類する。
2. 独立して説明・レビュー・revertできる最小の意味単位へ分ける。
3. approved scopeとstaged snapshotを単位ごとに固定する。
4. 明示pathだけをstageする。`git add .`、`git add -A`、`--no-verify`は禁止。
5. `gitleaks git --staged --redact --report-format json` を実行する。reportはVault外のautomation run log配下へ保存する。
6. file type、size、Git mode、symlink targetを検査する。Vault外symlink、実行形式、archive、disk image、10MiB超、読取不能、scan不能binaryは個別承認がなければ停止する。
7. Conventional Commitsでcommitし、staged snapshotとcommit treeの一致を確認する。

成果物差分なしは `not_required`。両Vaultが成果物差分なしでも、Evidence Finalizationの実行証跡commitは省略しない。片方のcommit失敗時、作成済みlocal commitは巻き戻さず、両pushを停止する。

remote evidenceはURL userinfo、password/token、query、fragmentを必ず除去する。sanitized `remote name + host + repository path` またはそのfingerprintだけを記録する。

## Push Phase

両commit phaseが`complete`または`not_required`で、両worktreeがcleanの場合だけ `push` スキルを使う。

- `origin main`へのplain non-force pushだけを許可する。
- remote同一は`not_required`。
- rejection、認証、network failureをpull/rebase/forceで回避しない。
- 2つ目のpush失敗時は成功済みpushを巻き戻さず`partial_publication`にする。

## Evidence Finalization

初回push後、Agents-Vaultのstanding taskへ両Vaultのdirty paths、commit groups/hashes、local/remote HEAD、push status、scan evidence、residual riskを追記する。

追記hunkだけを再レビュー・commitし、Agents-Vault `main`をもう一度non-force pushする。その後Vaultを編集せず、両worktree cleanとlocal/remote HEAD一致を確認する。finalization失敗は`partial_publication`。

## Output Contract

| Outcome | Meaning |
|---|---|
| `success` | finalizationを含む全工程完了 |
| `blocked` | remote/localを進めず停止 |
| `partial_publication` | localまたはremoteが片側だけ進んだ |

Vaultごとにcommit status/hashes、push status、local/remote HEAD、cleanを返し、停止時は手動next actionを含める。

## Forbidden Actions

- force push、自動pull/merge/rebase/reset/stash
- credential・secretの生成、変更、登録
- Manifest外repoのcommit/push
- `.obsidian/` の無断変更
- 成功済みcommit/pushの巻き戻し

## Related Skills

- `commit`: snapshot-bound minimal commit
- `push`: repository push policy
