---
name: merge
description: >
  Git merge resolution workflow for managed local repositories that the `pull` skill
  blocked because their worktree was dirty while remote updates existed. Use this skill
  when the user says "マージして", "mergeして", "dirty な repo をマージして",
  "commit してからマージして", "pull がブロックした repo をマージして", or asks to
  resolve dirty/diverged managed repos. It preserves local work commit-first (default)
  or by stash, then merges upstream with --no-edit. It must not push, force, reset hard,
  delete files, or auto-resolve conflicts; on conflict it aborts and reports.
user-invocable: true
allowed-tools: Bash, Read, Grep
category: Dev
created: 2026-06-11
status: active
purpose: pull がブロックした dirty/diverged な管理 repo を commit-first で安全に merge する
argument-hint: "[--dry-run | --execute] [--stash] [--message <text>]"
---

# Git Merge Managed Repositories

`merge` は、`pull` と対になる道具スキルである。
`pull` は worktree が dirty かつリモート更新がある repo を `dirty_worktree` で
意図的にブロックする。`merge` はその repo を引き取り、ローカル作業を先に保全してから
安全に merge する。

ユーザーの短縮依頼「マージして」は、`pull` がブロックした管理 repo の
**commit-first → merge --no-edit → 衝突時 abort** を意味する。

## Task Context Precondition

人間起点で呼ばれた場合でも、task record と実行 scope を確認する。
実行 scope と決定済みの policy を含む caller-supplied Saihai task context が、呼び出し元から明示的に渡されていなければならない。
外部フロー上の起票、承認、routing、role 選択はこのスキル内で決めず、受け取った context の検証と merge 実行だけを行う。
task context がない場合は、git 操作をせず `task_scope_missing` として停止する。

次の境界を常に守る。

| Operation | Policy |
|---|---|
| fetch | allowed |
| commit (local work) | allowed (commit-first default) |
| stash | allowed only with `--stash` |
| merge | allowed by safe policy below |
| push | forbidden |
| force push | forbidden |
| `git reset --hard` | forbidden |
| delete / clean | forbidden |
| conflict auto-resolution | forbidden |

## When I Activate

- ユーザーが「マージして」「mergeして」と入力したとき。
- ユーザーが「dirty な repo をマージして」「commit してからマージして」と言ったとき。
- ユーザーが「pull がブロックした repo をマージして」と依頼したとき。
- `pull` skill が `dirty_worktree` でブロックした repo の解消を明示されたとき。

## What I Do

1. 共有タスク vault の managed repositories policy を正本として扱う。
2. 実行時は `references/managed-repositories.local.md` があればそれを優先し、なければ公開用雛形の `references/managed-repositories.md` から対象 repo を読む。
3. 各 repo の存在、current branch、upstream、dirty state、unmerged paths を確認する。
4. `git fetch --all --prune` を実行する。
5. fetch 後の ahead / behind を確認する。
6. safe merge policy に従って保全と merge を決める。
7. 衝突時は `git merge --abort` で原状復帰し、衝突ファイルを報告する。
8. 結果を task record に記録する。

## Safe Merge Policy

| Repo state | Action |
|---|---|
| No upstream | fallback to `<remote>/<default_branch>`; block if unavailable |
| No remote updates (behind 0) | no merge (`not_needed`) |
| Clean and behind | merge upstream with `--no-edit` |
| Clean and diverged | merge upstream with `--no-edit`; abort and report on conflict |
| Dirty and behind/diverged | commit local work first (or stash with `--stash`), then merge |
| Unmerged paths already exist | block before fetch/merge |
| Merge conflict | abort merge, restore pre-merge state, report `merge_conflict` + files |
| Stash pop conflict | leave stash intact, report `stash_pop_conflict` + files |

Commit-first is the default because managed repos here are user work products
(Obsidian vault notes, dotfiles) that belong in history rather than a volatile stash.

## Command

Dry run:

```bash
python3 skills/merge/scripts/merge_managed_repos.py --dry-run
```

Execute (commit-first):

```bash
python3 skills/merge/scripts/merge_managed_repos.py --execute
```

Stash instead of commit:

```bash
python3 skills/merge/scripts/merge_managed_repos.py --execute --stash
```

Custom preserve message / JSON output:

```bash
python3 skills/merge/scripts/merge_managed_repos.py --execute --message "chore(vault): pre-merge save"
python3 skills/merge/scripts/merge_managed_repos.py --execute --json
```

## Result Fields

| Field | Meaning |
|---|---|
| `repo` | managed repository name |
| `fetch_status` | `success`, `dry_run`, `skipped`, or `failed` |
| `local_change` | `none`, `committed`, `stashed`, `stash_restored`, or `stash_pop_conflict` |
| `commit_hash` | hash of the pre-merge commit when commit-first ran |
| `merge_status` | `merged`, `not_needed`, `blocked`, `dry_run`, `failed`, or `conflict_aborted` |
| `ahead` / `behind` | post-fetch relationship to upstream |
| `conflict_files` | files in conflict when a merge or stash pop was aborted |
| `reason` | block, conflict, or failure reason |

## Relationship to `pull`

| Skill | Scope |
|---|---|
| `pull` | fetch + safe merge for clean repos; blocks dirty repos with remote updates |
| `merge` | resolves the repos `pull` blocked: commit-first (or stash) then merge |

Run `pull` first; if it reports `dirty_worktree` blockers, run `merge` to resolve them.

## Review Requirements

- If task policy requires review, reviewer selection is supplied by task context.
- This skill records repo inventory, no-push boundary, command output, and Vault/task evidence.
- Completion record must include Git Publication Result or publication-not-required reason when a publication flow exists.

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** fetch / merge may require approval

- Filesystem: writes Git refs, FETCH_HEAD, commits, merge commits, and task records.
- Network: fetch requires remote access.
- Configuration: each repo must already have remotes and upstreams.

## Related Files

- `references/managed-repositories.md`
- `references/managed-repositories.local.md` (ignored local override)
- `scripts/merge_managed_repos.py`
- `tests/test_merge_managed_repos.py`
- Sibling skill: `skills/pull/`
- Vault policy: `03-Contexts/Policies/Managed-Local-Repositories.md`
