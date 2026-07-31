# Scheduled Vault Publication Contract

`summarize-it-news` を含む定期複合ワークフローが、明示承認された場合だけ使用する publication contract。

## Activation Boundary

次のすべてが揃う場合だけ実行する。

| Check | Required |
|---|---|
| standing task が daily Vault publication を scope に含む | Yes |
| scheduled automation prompt がこの contract と対象2 Vault を明記する | Yes |
| 人間が両 Vault の全未コミット差分の commit と `main` push を承認済み | Yes |
| ニュース、advisory、task evidence、許可済み通知 payload の保存が完了 | Yes |

手動の `summarize-it-news`、ニュース要約だけの依頼、read-only 実行では commit / push しない。

## Targets

Saihai primary checkout の `directory-path.env` を唯一の source とする。シェル環境変数からパスを解決しない。

```python
env = {}
directory_paths.load_environment(
    checkout_root=Path("/Users/takagiyasushi/dev/Saihai").expanduser(),
    environ=env,
    require_catalog=True,
)
```

`status=loaded` と read/write を確認し、次の2リポジトリだけを対象にする。

1. `env["AGENTS_VAULT_ROOT"]`
2. `env["USER_VAULT_ROOT"]`

`.obsidian/` は人間の個別依頼がない限り commit 対象から除外する。対象外差分が残る場合は「全差分を commit 済み」と扱わず停止する。

## Preflight

commit 前に2リポジトリの全項目を確認する。

| Check | Pass condition |
|---|---|
| Git root | 解決した Vault root 自体が repository root |
| Branch | `main` |
| Operation state | merge / rebase / cherry-pick / revert 中ではない |
| Remote | `origin` が存在し、push URL を記録 |
| Upstream | `main` が `origin/main` を追跡 |
| Remote freshness | `git fetch origin main` 後、local `main` が `origin/main` より behind でない |
| Worktree | staged / unstaged / untracked をすべて列挙可能 |

非 `main`、behind、conflict state、remote 不明、認証・network failure では停止する。自動 checkout、pull、merge、rebase、reset、stash、force push は行わない。

片方でも preflight に失敗した場合、どちらの commit / push も開始しない。

## Commit Phase

各 Vault について `commit` スキルを読み、standing task と今回の人間承認を caller-supplied task context として渡す。

1. `git status --short`、staged diff、unstaged diff、untracked files を取得する。
2. 現在の全未コミット path を列挙した Task Change Manifest を Vault ごとに作る。
3. 各 path / hunk を目的、対象 task、変更種別、リスク、レビュー線で分類する。
4. 独立して説明・レビュー・revert できる最小の意味単位へ分割する。同じ task の実装と対応テストは同一単位でよいが、別 task、別目的、別リスクは分ける。
5. 各単位の approved scope と approved diff snapshot を固定し、次の deterministic Security Commit Review を行う。
6. `P0`、scope 不明、snapshot 不一致は停止する。`P1` 以下は evidence を記録して続行できる。
7. 対象 path を明示して stage する。`git add .`、`git add -A`、`--no-verify` は使わない。
8. staged snapshot に対して `gitleaks git --staged --redact --report-format json` を実行する。`--report-path` は両 Vault 外の `/Users/takagiyasushi/AutomationWorkspaces/codex/daily-it-news-vulnerability-check/logs/<date>/<run-id>.security/<vault>.gitleaks.json` に固定する。scanner 名、version、exit code、redacted report path、SHA-256 を evidence に残す。report を Vault 内へ作らない。
9. staged path の file type、byte size、Git mode、symlink target を列挙する。Vault root 外へ出る symlink、実行形式、disk image、archive、10 MiB 超、読取不能、scan 対象外 binary は、既存 task の個別承認と検証 evidence がない限り `security_insufficient_input` で停止する。
10. Conventional Commits 形式で単位ごとに commit する。
11. commit 後に staged snapshot と commit tree が一致することを確認する。
12. 両 Vault の commit phase 完了後、`git status --porcelain` が空であることを確認する。

差分がない Vault は `commit_status: not_required` と記録する。片方の commit が失敗した場合、もう片方に作成済みの local commit は巻き戻さず、両方の push を停止して exact state を報告する。

## Push Phase

両 Vault の commit phase が `complete` または `not_required` で、両 worktree が clean の場合だけ開始する。

各 Vault について `push` スキルを読み、次を検証して plain push する。

| Field | Value |
|---|---|
| repo_kind | `vault` |
| current_branch | `main` |
| branch_kind | `default` |
| main_push_whitelisted | `true`（`push/references/main-push-repos.md`） |
| remote | `origin` |
| push_required | `true` |
| force | `false` |

remote が既に同じ commit の場合は `push_status: not_required` とする。non-fast-forward、認証、network failure は停止し、pull / rebase / force push で回避しない。

2リポジトリ間の push は transaction ではない。2つ目が失敗した場合は1つ目を巻き戻さず、remote / local の各 HEAD を記録して `partial_publication` として報告する。

## Evidence Finalization Phase

初回 push の結果は事前に確定できないため、push 後に Agents-Vault の standing task へ結果を追記する。この追記を未コミットのまま残さない。

1. 初回 push の2 Vault分の local / remote HEAD、status、failure を収集する。
2. standing task の `Execution Log` と `Automation Runs` に、下記 Required Evidence を一度だけ追記する。
3. 追記 hunk だけを finalization scope とし、Security Commit Review と staged snapshot 照合を再実行する。
4. `docs(automation): record daily vault publication` のような単一目的の finalization commit を Agents-Vault に作る。
5. Agents-Vault の `main` を non-force push する。
6. 以後 Vault を編集しない。両 Vault の worktree が clean、local HEAD と remote HEAD が一致することを確認してから、machine-readable final response を返す。

finalization commit または2回目の Agents-Vault push が失敗した場合、成功済み push は巻き戻さない。`partial_publication` と exact state を返し、成功扱いにしない。

## Required Evidence

standing task の `Execution Log` と `Automation Runs` に次を追記する。

| Vault | Dirty paths | Commit groups | Commit hashes | Local HEAD | Remote HEAD | Push status | Residual risk |
|---|---|---|---|---|---|---|---|
| Agents-Vault |  |  |  |  |  |  |  |
| User-Vault |  |  |  |  |  |  |  |

最終応答にはニュース要約、advisory、通知結果に加え、この表の要約と、停止した場合の手動次アクションを含める。

最終応答は automation runner が指定する JSON Schema に一致させる。`outcome` は全工程と evidence finalization が完了した場合だけ `success`、mutation 前停止は `blocked`、remote またはlocal stateが片側だけ進んだ場合は `partial_publication` とする。
