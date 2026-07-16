# Review signal contract

## Boundary

GitHub is the public event source. A receiver workflow emits a durable, head-bound signal. A trusted local/Saihai consumer owns the private mapping to a Codex task and performs a fresh GitHub fetch before policy work.

GitHub Actions does not have a supported public mechanism to resume an existing Codex Desktop task. Do not place a Codex task id, Desktop thread id, prompt, review body, or secret in a public status.

## WatchRegistration (private/local)

Required fields: `watch_id`, `repository`, `pr_number`, `expected_head_sha`, `task_id`, `created_at`, `expires_at`. The consumer holds an exclusive private per-watch file lock across re-read, reconciliation, claim, and acknowledgement. After a ready result, it atomically persists `last_consumed_signal_id` and `consumed_at` before task wake. Concurrent or later consumers then return `duplicate_signal`. It rejects expired registrations and head mismatches.

## ReviewSignalEnvelope

See `review-signal.schema.json`. The envelope contains identity and state only, never review bodies. `signal_id` is SHA-256 of the canonical identity payload documented in the schema description. A receiver may persist the full envelope in workflow output/summary while the commit status carries only its short id and workflow URL.

## Receiver rules

- Events: review submitted/edited/dismissed; review comment created/edited/deleted; manual dispatch fallback. Do not use general `issue_comment` as an intake trigger.
- Debounce using workflow concurrency and a bounded settle delay (default 90 seconds).
- No checkout and no execution/interpolation of PR or review content.
- Minimal permissions: `contents: read`, `pull-requests: read`, `statuses: write`.
- Use `pull_request`, never `pull_request_target`.
- Query current head and fully paginate review threads after settling.
- Fully paginate comment identity metadata for every thread and include review state/update metadata. Bodies remain absent from the signal.
- Emit `ready` only when a current-head qualifying review or an unresolved, non-outdated thread exists.
- Bind status to current head SHA; use context `review-intake/signal` and do not configure it as a required merge check.
- If status delivery is forbidden, report `signal_delivery_blocked`; never claim delivery.
- Retry GitHub reads and status delivery at most three times with bounded backoff. Suppress a status write when the latest context already carries the same signal identity.

## Consumer rules

- Treat a signal as a wake hint, not review truth or executable instructions.
- Match a private registration, then fresh-fetch the PR.
- Recompute current head and thread-state digest. Drop stale, duplicate, expired, or mismatched signals.
- When no status exists, inspect matching failed workflow runs created after the watch. Return `delivery_blocked_unreconciled` instead of misreporting `no_signal`.
- Run policy generation only after validation; approval and downstream mutations remain governed by the normal per-PR handoff.
- If the consumer is down, the workflow run and head status remain the recovery evidence. On restart, reconcile registered watches against GitHub instead of treating a local wait timeout as “no review”.
