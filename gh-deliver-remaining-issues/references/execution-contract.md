# Parallel Issue Delivery Contract

Read this reference before dispatching implementation or review workers.

## Manifest

Maintain one coordinator-owned manifest. Values may come from explicit user instructions, repository policy, GitHub evidence, or caller-supplied Saihai task context. Record the source for every approval or organization decision.

```yaml
manifest_version: "1"
task_id: "<stable task id>"
repository:
  root: "<absolute path>"
  remote: "owner/repo"
  default_branch: "main"
  verified_base_sha: "<sha>"
issue_scope:
  selector: "explicit | parent | milestone | project | task-record"
  selector_value: "<id/url>"
  snapshot_at: "<ISO-8601>"
authorization:
  implement: true
  push: true
  create_ready_pr: true
  merge: false
  release: false
  source: "<user message or caller artifact>"
coordination:
  coordinator: "<caller-assigned role/provider>"
  ambiguity_owner: "<user or caller>"
  approval_owner: "<user or caller>"
  publication_owner: "<caller-assigned role/provider>"
  concurrency_limit: 3
  reviewer_capacity_reserved: 1
issues:
  - number: 123
    url: "https://github.com/owner/repo/issues/123"
    title: "<title>"
    status: "ready"
    acceptance_criteria: []
    non_goals: []
    dependencies: []
    dependency_evidence: []
    owned_paths: []
    owned_symbols: []
    interface_impacts: []
    exclusive_resources: []
    risk_domains: []
    clarification_status: "clear"
    branch_plan:
      base_branch: "main"
      base_sha: "<sha>"
      working_branch: "codex/issue-123-short-slug"
      worktree_path: "<absolute path>"
      workspace_mode: "task_worktree"
      publication_flow: "create_pr_from_task_branch"
      base_verification:
        after_prepare_head: "<must equal base_sha>"
        before_dispatch_head: "<must equal base_sha>"
    implementer_assignment:
      role: "<caller-assigned>"
      provider: "<caller-assigned>"
    integration_review:
      required: false
      assignment: null
      snapshot_digest: null
      evidence: null
    units: []
    publication:
      approved: true
      ready_pr: true
      base: "main"
      stacked: false
      labels: []
waves:
  - id: "wave-1"
    issue_numbers: [123]
    base_sha: "<same verified SHA for the wave>"
    independence_evidence: []
```

Do not dispatch when any required organization decision is missing or either immutable base check differs from `branch_plan.base_sha`. Return:

```yaml
status: parallel_issue_delivery_context_missing
missing_fields: []
affected_issues: []
discoverable_fields_checked: []
question_owner: caller | user
next_action: "<smallest question or upstream artifact update>"
```

Route later requirement, review, security, and publication decisions through the owners declared in the manifest:

```yaml
status: waiting_owner_decision
decision_type: requirement | review_finding | security_finding | publication
decision_owner: caller | user
affected_issues: []
evidence: []
options: []
recommended_option: "<id>"
next_action: "return_to_caller | ask_user"
```

Ask the user directly only when `decision_owner` is `human` or `user`. Never treat a direct user question as a substitute when the caller owns the gate.

## Unit contract

Add units as execution proceeds rather than inventing them before reading the code.

```yaml
unit_id: "issue-123-u1"
objective: "<one independently meaningful behavior change>"
owned_paths: []
excluded_paths: []
acceptance_criteria: []
required_checks: []
review_focus: "api-contract"
review_assignment:
  role: "<caller/user-confirmed role>"
  provider: "<caller/user-confirmed provider>"
  rationale: "<why this assignment covers the focus>"
security_review_assignment:
  role: "<caller/user-confirmed security role>"
  provider: "<caller/user-confirmed provider>"
  rationale: "<why this assignment covers the unit risk>"
state: planned
diff_snapshot:
  version: "1"
  base_sha: "<immutable sha>"
  content_manifest: []
  binary_patch_sha256: "<sha256>"
  snapshot_digest: "<sha256 of canonical payload>"
validation_evidence: []
review_evidence: null
security_review_evidence: null
commit_hash: null
```

Use these state transitions:

```text
planned
→ implemented
→ locally_validated
→ review_pending
→ waiting_owner_finding_policy (only when needed)
→ reimplemented
→ locally_revalidated
→ rereview_pending
→ reviewed_snapshot_approved
→ committed
```

## Immutable review snapshot

Bind review approval to the exact intended commit bytes, independent of whether a path is currently staged. Build one canonical payload containing:

```yaml
snapshot_version: "1"
base_sha: "<full immutable sha>"
content_manifest:
  - path: "<sorted repository-relative path>"
    status: "added | modified | deleted"
    mode: "<git mode or null for deletion>"
    content_sha256: "<exact bytes, or null for deletion>"
binary_patch_sha256: "<sha256 of the complete base-to-intended --binary --full-index patch>"
```

Include every task-owned staged, unstaged, previously untracked, binary, and deleted path in the intended tree. Sort `content_manifest` by bytewise repository-relative path, serialize the payload as canonical UTF-8 JSON with sorted keys and no insignificant whitespace, and set `snapshot_digest` to its SHA-256. Store the complete binary patch and exact new-file bytes as reviewer artifacts; a digest alone is not reviewable evidence.

Before commit, stage only approved paths/hunks, derive the same canonical payload from the index, and require its digest to equal the approved `snapshot_digest`. Also require no unstaged or untracked task-owned remainder. Stop on any mismatch, extra task-owned path, missing deletion, or changed mode/content. Any change creates a new digest and invalidates technical, security, and integration review evidence.

## Review focus and assignment

Identify the narrow technical focus from the unit, but do not choose an organization role or provider. Accept assignments only from caller context or explicit user confirmation.

| Change | Suggested `review_focus` | Review concern |
|---|---|---|
| auth, permission, secret handling | `security-threat-model` | trust boundaries, privilege, leakage |
| API, schema, shared type | `api-compatibility` | contract correctness, consumers, breaking change |
| database, migration, persistence | `data-integrity-migration` | ordering, rollback, loss, idempotency |
| async, queue, state machine | `concurrency-reliability` | races, retries, ordering, failure recovery |
| UI or interaction | `ux-accessibility` | user path, states, keyboard, semantics |
| dependency, CI, build | `reproducibility-supply-chain` | lock state, provenance, deterministic build |
| performance-sensitive path | `performance` | workload, regression method, resource use |
| tests or test infrastructure | `regression-test-strategy` | failure proof, boundary coverage, flakiness |
| docs, runbooks, commands | `technical-writing-operator-ux` | accuracy, executable steps, reader failure modes |
| ordinary code | `correctness-maintainability` | behavior, errors, simplicity, regression |

If no assignment covers the focus, route this choice through `approval_owner`. Use the following user-facing form only when that owner is `human` or `user`:

```markdown
Issue #123 / unit u1 needs an `api-compatibility` review.

A. Use <role/provider candidate> — best contract coverage; <risk>.
B. Use <role/provider candidate> — faster, but <coverage gap>.
C. Pause this unit — no reviewer is assigned.

Recommended: A
```

## Reviewer input and output

Give the reviewer raw evidence, not the intended answer:

- issue body and acceptance criteria;
- repository guidance and unit scope;
- exact diff snapshot and base SHA;
- focused check output and known baseline failures;
- relevant adjacent implementation needed to assess the diff.

Require read-only output:

```yaml
unit_id: "issue-123-u1"
reviewer_role: "<assigned role>"
reviewer_provider: "<assigned provider>"
snapshot_digest: "<canonical snapshot digest>"
verdict: "approved | findings | insufficient_input"
scope_ok: true
acceptance_criteria_ok: true
findings:
  - id: "R1"
    priority: "P0 | P1 | P2 | P3"
    evidence: "<path:line, failing check, or concrete counterexample>"
    current_problem: "<impact if unchanged>"
    recommended_action: "<bounded correction>"
notes: []
```

A finding is actionable when it recommends a code, test, configuration, documentation, or behavior change. For every actionable finding, obtain a fix policy from `approval_owner` before editing. Pure observations may be recorded as notes only when they require no change and do not undermine acceptance criteria.

## Security Commit Review

Require a caller/user-confirmed security role and provider for every unit before review dispatch. Run it against the same raw snapshot artifacts and `snapshot_digest` as technical review.

```yaml
unit_id: "issue-123-u1"
security_reviewer_role: "<assigned role>"
security_reviewer_provider: "<assigned provider>"
snapshot_digest: "<canonical snapshot digest>"
max_priority: "P0 | P1 | P2 | P3 | none"
verdict: "security_clear | security_notes | security_blocked | security_insufficient_input"
findings: []
```

Route every actionable security finding through `approval_owner`. Do not commit on `security_blocked`, `security_insufficient_input`, a P0 finding, a missing assignment, or a digest mismatch. After any approved fix, generate a new digest and rerun technical and security review.

## Cumulative integration review

When interacting units or high-risk boundaries require a cumulative review, create an issue-level contract with a caller/user-confirmed separate reviewer assignment, the canonical digest of the full issue diff, raw unit/validation evidence, and the same read-only verdict schema. Route findings through `approval_owner`; invalidate and rerun the cumulative review whenever the issue diff changes. Self-review, a unit reviewer operating as implementer, or review of an unfixed/mismatched digest cannot satisfy this gate.

## Finding policy handoff

```markdown
### Issue #123 / unit u1 / finding R1

| Item | Detail |
|---|---|
| Evidence | `<path:line or check>` |
| Current problem | ... |
| Proposed response | ... |
| Validation after change | ... |
| Risk | ... |

A. Apply the proposed response, then revalidate and rereview. (Recommended)
B. Choose a different response; specify the behavior to preserve.
C. Reject/defer the finding with a recorded reason; leave this unit uncommitted if Done would be false.
```

Route the handoff through `approval_owner`; ask the displayed question only when that owner is `human` or `user`. Do not let one waiting issue stop unrelated workers. Never interpret silence as approval.

## Worker evidence return

Require each implementer to return, without editing the coordinator-owned record:

```yaml
issue: 123
worktree: "<absolute path>"
branch: "<branch>"
base_sha: "<sha>"
unit_id: "issue-123-u1"
status: "implemented | blocked | failed"
changed_paths: []
snapshot_digest: "<canonical digest>"
checks:
  - command: "<exact command>"
    result: "pass | fail | blocked"
    evidence: "<short output reference>"
ambiguities: []
unrelated_dirty_paths: []
next_action: "review | ask_user | diagnose"
```

## Coordinator status table

Keep this mapping current and include it in the final report:

| Issue | Planning state | Wave | Branch | Worktree | Implementer | Units/commits | Reviewer(s) | Checks | PR | Blocker |
|---|---|---|---|---|---|---|---|---|---|---|

Do not report the overall run complete while an issue is silently absent or while a required Vault update, review, commit, remote-head check, or user decision is missing.
