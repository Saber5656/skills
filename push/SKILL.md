---
name: push
description: >
  Git pushの可否判定と実行を担当するスキル。push_required true の
  Publication Manifest を受け取ったとき、またはユーザーが task context 上で
  「pushして」「自動pushして」と依頼したときに使う。default branch への push は
  main-push-repos.md の whitelist にある repo だけ許可し、それ以外は working branch のみ push する。
user-invocable: true
allowed-tools: Bash, Read, Grep
category: Dev
created: 2026-06-02
status: active
purpose: repo profile と branch policy に基づき安全な git push だけを実行する
argument-hint: "[Git Publication Manifest or repo path]"
---

# Git Push

`push` は `git push` だけを担当する道具スキルである。
commit、PR 作成、branch 作成、GitHub ruleset 設定は担当しない。

## Task Context Precondition

人間起点で呼ばれた場合でも、task context または Publication Manifest が必要。
未起票・scope 不明の依頼では `task_scope_missing` として停止する。
外部フロー上の承認、routing、publication 判断はこのスキルでは決めず、必要な場合は `configure-organization` から渡された manifest を使う。

## When I Activate

- ✅ `push_required: true` の handoff を受け取ったとき
- ✅ task context 上でユーザーが `push` を明示したとき
- ✅ commit 後の branch を remote に反映する必要があるとき
- ❌ commit を作る必要があるとき
- ❌ force push が必要なとき
- ❌ GitHub branch protection / ruleset を設定するとき

## Policy References

default branch push 許可の正本は `references/main-push-repos.md` とする。
repo 種別と補助情報は `references/repo-profiles.md` を参照する。
default branch push を拒否する明示 pattern は `references/default-branch-deny-patterns.md` を参照する。

`main-push-repos.md` と deny pattern に含まれる `${...}` aliases は、比較前に必ず解決する。
alias source は environment variables、ignored `.directory-path.local.md`、deterministic local fallback の順に扱う。
`${SKILLS_REPO}` は env がなくても、この `skills/push/SKILL.md` を含む repository root から解決できる場合は解決する。
target repository がその skills repository ではない場合、target repository の `git rev-parse --show-toplevel` を `${SKILLS_REPO}` の fallback として使ってはならない。
未解決 alias を literal path として比較してはならない。
default branch 判定で必要な alias が解決できない場合は `push_status: blocked`, `blocked_reason: whitelist_alias_unresolved` として停止する。

whitelist に記載されていない repository は、repo_kind に関係なく次を既定にする。

| Branch kind | Auto push |
|---|---|
| default branch | false |
| working branch | true |

default branch は `main` / `master` / `origin/HEAD` が指す branch を含む。
working branch は default branch と protected branch を除く通常の作業 branch とする。
`${DEV_REPO_ROOT}/*` は source repository の既定 pattern として default branch push を拒否する。
この pattern に一致する repository は、task-specific working branch / worktree で push 自動化を扱う。

task context または Publication Manifest があり、preflight と branch policy が allow の場合、push ごとの追加人間確認は不要とする。
runtime sandbox や network 権限の承認は環境制約であり、policy 上の push 確認とは分けて扱う。

whitelist に記載された repository は、default branch push を許可できる。
これは default branch push permission であり、default branch で通常タスクを進める許可や task worktree 作成の免除ではない。
main-push repository の通常 publication flow は、task-specific branch / worktree で作業し、完了時に default branch へ統合してから default branch を push する。
その場合の branch / worktree 準備と main 統合は task planning / publication flow と `git-workspace-prep` が扱い、`push` は実行時 branch の push 可否だけを見る。
deny pattern と whitelist が衝突している場合は、勝手に片方を優先せず `push_status: blocked` として policy conflict を記録する。

## Preflight

次を確認する。

```bash
git rev-parse --show-toplevel
git status --porcelain
git branch --show-current
git remote -v
git rev-parse --abbrev-ref --symbolic-full-name @{u}
# When upstream is missing and initial upstream creation may be eligible:
git ls-remote --heads origin <current_branch>
```

| Check | Required |
|---|---|
| repo profile exists or safe default is applied | Yes |
| main-push whitelist aliases resolved before comparison | Yes when default branch |
| main-push whitelist checked for default branch | Yes when default branch |
| default-branch deny patterns checked | Yes |
| task-owned paths are clean after approved commit | Yes |
| unrelated dirty paths are recorded when repo-wide status is dirty | When applicable |
| current branch is known | Yes |
| upstream branch exists | Yes, unless initial working branch upstream creation is allowed |
| initial upstream creation eligibility checked | Yes when upstream is missing |
| remote branch absence checked before initial upstream creation | Yes when upstream is missing |
| current branch is allowed by profile | Yes |
| remote is allowed by profile | Yes |
| command is not force push | Yes |

## Branch Policy

| Branch kind | Policy |
|---|---|
| default branch in `main-push-repos.md` | allowed when task-owned paths are clean, upstream exists, remote is allowed, and unrelated dirty paths are recorded |
| default branch matching `default-branch-deny-patterns.md` | denied unless policy files are intentionally updated together; conflict blocks |
| default branch not in `main-push-repos.md` | denied |
| working branch | allowed when task-owned paths are clean, remote is allowed, branch is not protected, unrelated dirty paths are recorded, and either upstream exists or initial upstream creation is explicitly eligible |

Blocked branch examples:

- `main` unless the repo is listed in `main-push-repos.md`
- `master` unless the repo is listed in `main-push-repos.md`
- default branch resolved from `origin/HEAD`
- `${DEV_REPO_ROOT}/*` repository default branch
- `release/*`
- `production/*`
- `staging`

Allowed working branch examples:

- `codex/*`
- `codex-*`
- `feature/*`
- `fix/*`
- `chore/*`
- `docs/*`
- `task/*`
- `wip/*`
- other non-default, non-protected task branches

## Initial Upstream Creation

New task branches often have no upstream before their first publication.
For that case only, first verify that the remote branch does not already exist:

```bash
git ls-remote --heads origin <current_branch>
```

The command must return no matching ref. After that absence check passes,
`push` may execute:

```bash
git push -u origin <current_branch>
```

This is allowed without another policy-level human confirmation when all checks
below pass.

| Check | Required |
|---|---|
| task context or Publication Manifest exists | Yes |
| `push_required` or explicit task-context push request | Yes |
| current branch is a working branch | Yes |
| current branch is not `main`, `master`, `origin/HEAD`, `release/*`, `production/*`, or `staging` | Yes |
| branch name matches an allowed working branch pattern such as `codex/*`, `feature/*`, `fix/*`, `chore/*`, `docs/*`, `task/*`, or `wip/*` | Yes |
| task-owned paths are clean after approved commit | Yes |
| unrelated dirty paths are absent or already recorded | Yes |
| remote is allowed by profile, default `origin` | Yes |
| `git ls-remote --heads origin <current_branch>` returns no refs | Yes |
| remote branch argument exactly matches the current branch | Yes |
| command is exactly `git push -u origin <current_branch>` | Yes |
| command is not force push | Yes |

If the remote branch already exists while local upstream is missing, stop with
`blocked_reason: remote_branch_exists_without_upstream`. If any other condition
is not true, missing upstream remains a block with `blocked_reason:
upstream_missing`.

Runtime sandbox, network, and host-level external-transfer approval are
environment constraints. They are not policy-level push confirmations.

## Workflow

1. Resolve repository root and current branch.
2. Classify the branch as `default`, `working`, or `protected`.
3. If branch is default, resolve aliases and check both `references/main-push-repos.md` and `references/default-branch-deny-patterns.md`.
4. Confirm current branch and upstream, or validate initial upstream creation eligibility including remote branch absence.
5. Reject dirty task-owned paths. Repo-wide dirty state is allowed only when every dirty path is outside the approved task scope and is recorded as `unrelated_dirty_paths`.
6. Reject default branch not in whitelist, default branch matching deny pattern, and protected branches.
7. Execute plain `git push` for branches with upstream, or `git push -u origin <current_branch>` for eligible first working branch publication.
8. Record `Push Result` with status, remote branch, policy decision, and failure reason if any.

## Stop Rules

| 状況 | 対応 |
|---|---|
| dirty task-owned paths | `push_status: blocked`, reason `task_owned_dirty_paths` |
| unrelated dirty paths present but not recorded | `push_status: blocked`, reason `unrelated_dirty_paths_missing` |
| no upstream and initial upstream creation is not eligible | `push_status: blocked`, reason `upstream_missing` |
| no upstream and remote branch already exists | `push_status: blocked`, reason `remote_branch_exists_without_upstream` |
| default branch whitelist alias cannot be resolved | `push_status: blocked`, reason `whitelist_alias_unresolved` |
| default branch not in `main-push-repos.md` | `push_status: blocked`, reason `default_branch_not_whitelisted` |
| default branch matches `${DEV_REPO_ROOT}/*` deny pattern | `push_status: blocked`, reason `default_branch_push_denied_for_dev_repo` |
| whitelist and deny pattern conflict | `push_status: blocked`, reason `default_branch_policy_conflict` |
| protected branch | `push_status: blocked`, reason `protected_branch_push_denied` |
| force push requested | `push_status: blocked`, reason `force_push_forbidden` |
| non-fast-forward rejection | stop; do not pull/rebase automatically |
| auth/network failure | stop and record error |

## Push Result

| Field | Required | 内容 |
|---|---:|---|
| `push_status` | Yes | `complete` / `not_required` / `blocked` |
| `repo_root` | Yes | 対象 repo |
| `current_branch` | Yes | 実行時 branch |
| `branch_kind` | Yes | `default` / `working` / `protected` |
| `remote_branch` | When complete | push 先 |
| `push_command` | When complete | `git push` or `git push -u origin <branch>` |
| `initial_upstream_created` | When applicable | `true` when first working branch publication used `git push -u` |
| `remote_branch_absent` | When initial upstream creation is checked | `true` only when `git ls-remote --heads` found no existing remote branch |
| `repo_kind` | Yes | profile 判定 |
| `main_push_whitelisted` | Yes when default branch | default branch push whitelist 判定 |
| `default_branch_deny_pattern` | Yes when matched | deny pattern と理由 |
| `policy_decision` | Yes | allow / deny と理由 |
| `blocked_reason` | When blocked | 停止理由 |
| `task_owned_dirty_paths` | When blocked | push 前に残っている task-owned dirty paths |
| `unrelated_dirty_paths` | When repo-wide dirty | push 対象外として許容した dirty paths |

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** Network push usually requires approval

- **Filesystem**: repo read/write
- **Network**: `git push`
- **Configuration**: remote must already exist. Upstream may be created only for eligible first working branch publication after confirming the remote branch does not already exist.

## Related References

- `references/repo-profiles.md`
- `references/main-push-repos.md`
- `references/default-branch-deny-patterns.md`
- `references/github-guards.md`
