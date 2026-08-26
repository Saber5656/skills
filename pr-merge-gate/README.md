# pr-merge-gate

Thin, fail-closed adapter for a Saihai-authorized GitHub PR merge.

It activates for an explicit `/pr-merge-gate` or typed Saihai envelope request, then accepts execution only
with a trusted versioned contract reference, finalized manifest digest, and matching unexpired one-shot authorization. Saihai validates policy readiness and atomically consumes the authorization
while merging; this skill never recomputes policy and never falls back to direct `gh pr merge` or connector
mutation.

Opaque artifacts remain inside catalog-approved roots and bearer-sensitive authorization values are redacted.
After an uncertain result, only the contract-fixed read-only Saihai reconciliation route may run; the mutation
executor is never retried with either the same or a new authorization.

Generic “merge” requests, PR URLs alone, local repository merges, GitHub `mergeable`/`CLEAN`, review
acknowledgements, and comments are not authorization.

See [SKILL.md](SKILL.md) for the envelope, typed blockers, replay rules, and output contract.
