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
- GitHub PR URLs, PR-number merge requests, merge queues, and auto-merge are explicit negative triggers.
- Mixed local/PR requests fail closed with no fetch, commit, stash, local merge, or PR handoff; each process must be requested separately.
- GitHub PR merge is routed only to `pr-merge-gate` with a Saihai-finalized manifest and one-shot authorization.

See [SKILL.md](SKILL.md) for full workflow details.
