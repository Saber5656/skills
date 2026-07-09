# pr

Local GitHub PR publication workflow with Codex review gating.

## What It Does

- Creates a ready-for-review, non-draft PR only.
- Writes the PR title and body in English.
- Uses `[issue #N]` for issue-scoped PR titles when the primary issue is known, and does not use `[codex]` as a title marker.
- Applies existing repository labels to the PR and the primary linked issue.
- Assigns the current GitHub user.
- Verifies the pushed PR head and detects non-diagnostic Codex review results for the current head SHA.
- Uses `@codex review` only as an optional fallback/manual trigger when automatic review is not observed.
- Waits for Codex review feedback when feasible.
- Stops before review-driven code changes and asks the user to approve the fix plan.
- Pushes approved fixes before posting addressed/fixed replies to review threads.

## Typical Use

```text
PR作成して
```

```text
このブランチでPR作って。Codex review まで確認して
```

## Important Boundary

This skill does not merge PRs and does not implement Codex review feedback without human confirmation.
It also does not mirror Codex automatic-review settings locally; it observes GitHub PR review objects and
falls back to `@codex review` only when configured or explicitly requested.
If the user asks for a draft PR, the workflow stops before PR creation and asks whether to create a ready PR
or pause publication.
The workflow uses existing labels only and reports a blocker instead of inventing or creating labels silently.

See [SKILL.md](SKILL.md) for the full workflow.
