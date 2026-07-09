# merge

> Resolve dirty/diverged managed repos: commit-first (or stash) then safe merge.

Companion to the [`pull`](../pull/) skill. `pull` blocks dirty repos with remote
updates; `merge` resolves them.

## Quick Use

```bash
python3 skills/merge/scripts/merge_managed_repos.py --dry-run
python3 skills/merge/scripts/merge_managed_repos.py --execute
python3 skills/merge/scripts/merge_managed_repos.py --execute --stash
```

## Trigger

- `マージして`
- `mergeして`
- `dirty な repo をマージして`
- `commit してからマージして`
- `pull がブロックした repo をマージして`

## Safety

- No push.
- No force.
- No `git reset --hard`.
- No deletion or cleanup.
- Local work is preserved commit-first (default) or by stash before merging.
- Merge conflicts are aborted and reported, never auto-resolved.

See [SKILL.md](SKILL.md) for full workflow details.
