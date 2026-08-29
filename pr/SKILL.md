---
name: pr
description: >
  GitHub pull request publication workflow for local code changes. Use this skill when the user asks to
  create a PR, open a pull request, PR作成, PR出して, pushしてPR, publish changes, or wants a Codex-reviewed
  PR flow. This skill creates ready-for-review, non-draft PRs only, writes PR titles and bodies in English,
  applies existing repository labels to the PR and primary linked issue, assigns the current GitHub user, relies
  on repository-configured automatic Codex review, detects current-head GitHub review results, never posts a
  manual Codex review-trigger comment, waits for Codex review feedback when feasible, then stops at a human
  confirmation gate with a proposed fix
  plan. Do not use for merely summarizing an existing PR, fixing CI only, or addressing already-selected
  review comments.
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
category: Dev
created: 2026-06-21
status: active
purpose: GitHub PR作成から自動Codex review観測とHOTL修正方針確認までを標準化する
argument-hint: "[PR対象の説明、base/head、追加制約]"
---

# PR Publication Workflow

This skill publishes local changes as a GitHub PR and wires in the Codex review loop. It is intentionally
stricter than a simple `gh pr create`: branch contents are checked against the remote base, the pushed PR
head is verified, and any actionable Codex review feedback must pass through a human-in-the-loop
confirmation before code is changed. Codex automatic review settings live in Codex/GitHub, so this skill
does not mirror them locally. It observes whether a current-head Codex review appears and never posts a
comment-based fallback to trigger another Codex review.

## When I Activate

- User asks to create or open a PR from local changes.
- User asks to push a branch and create a PR.
- User wants the standard Codex review harness on a PR.
- User invokes `/pr`.

Do not activate for:

- A simple PR summary with no local publication work. Use GitHub triage instead.
- Fixing failing GitHub Actions checks. Use the CI-specific workflow.
- Addressing existing review comments after the user has already selected what to fix. Use the review-comment handler.
- Merging a PR or changing branch-protection/ruleset settings.

## Scope

This skill owns the publication and Codex review workflow:

1. Confirm the intended local diff and branch.
2. Commit and push only task-owned changes when needed.
3. Create a ready-for-review, non-draft PR. Draft PR creation is forbidden; if the user asks for a draft PR, stop before PR creation and ask whether to create a ready PR or pause publication.
4. Assign the current GitHub user.
5. Write the PR title and body in English, translating Japanese source context into concise English when needed.
6. Resolve a label plan from user-provided labels, task context, primary issue labels, or existing repository labels, then apply the final label set to the PR and primary linked issue when available.
7. Start a review attempt by recording the PR head SHA and review window start before PR creation or push.
8. Poll GitHub for a submitted `chatgpt-codex-connector[bot]` review whose `commit_id` matches the current PR head.
9. If no current-head Codex review appears in the polling window, report `review_pending` or
   `review_timeout` without posting a manual review-trigger comment or starting a comment-triggered attempt.
10. Summarize actionable feedback and ask the user to approve the fix plan before editing.
11. After approved review fixes are implemented, commit, push, and verify the remote PR head contains the fix before posting any GitHub reply that says feedback was addressed.

It does not merge PRs, bypass GitHub rulesets, implement review feedback without user confirmation, or mark review feedback fixed while the fix is still local-only.

## Preconditions

| Check | Required action |
|---|---|
| GitHub CLI | Run `gh --version` when available; use the GitHub Connector for supported operations when CLI is unavailable |
| GitHub auth | Run `gh auth status`; if unavailable, use the GitHub Connector for supported read/write operations and stop only when the requested operation is not connector-supported |
| Repository | Resolve `owner/repo` from `origin` or user-provided repo |
| Base branch | Use user-provided base, otherwise remote default branch |
| Worktree scope | Inspect `git status -sb` and staged/unstaged/untracked files before staging |
| Remote base | Fetch the base from GitHub and use the exact remote base ref/SHA, not a possibly divergent local branch |
| Branch ownership | Inspect `git log "$remote_base"..HEAD` and `git diff --stat "$remote_base"...HEAD`; ask when existing commits or changed paths are unrelated or ambiguous |
| Remote head readiness | Fetch upstream; stop on behind/diverged state unless a safe fast-forward is performed and all ownership checks are rerun; push before PR creation when the branch has no upstream or is ahead |
| Dirty mixed worktree | Stage only task-owned paths; ask if ownership is ambiguous |
| Existing PR | Reuse the current branch PR if it exists instead of creating a duplicate |
| Issue context | If a primary issue number is known from the user request, branch name, commit scope, PR body, or existing issue reference, include `[issue #N]` in the PR title |
| Label plan | Determine labels before final PR reporting. Use existing labels only; do not create labels unless the user explicitly asks |

## Publication Rules

### Branch, Commit, Push

- If on `main`, `master`, or the remote default branch, create or switch to `codex/{short-description}`.
- If uncommitted changes exist, use the commit workflow requirements: task record, approved scope, security scan, and explicit path staging.
- Do not use `git add -A` unless the entire worktree is confirmed in scope.
- Before creating a PR, verify the branch contents, not only the worktree:
  - Fetch the remote base first: `git fetch origin "$base"` and set `remote_base="origin/$base"` or the exact `baseRefOid` for an existing PR.
  - `git log "$remote_base"..HEAD --oneline` must contain only task-owned commits.
  - `git diff --stat "$remote_base"...HEAD` must contain only task-owned paths.
  - Do not use a local branch name such as `main` as the comparison base unless it has just been verified to match the remote base SHA.
  - If unrelated commits or ambiguous paths appear, stop and ask whether to create a clean branch or exclude the unrelated work.
- Push with tracking after commit: `git push -u origin <branch>`.
- `gh pr create --head` does not push the branch for you. If the branch has no upstream or is ahead of upstream, push before invoking `gh pr create --head`.
- If the branch is behind its upstream, stop or fast-forward with `git pull --ff-only` only when that is clearly safe for the task; after any fast-forward, rerun the remote-base ownership checks.
- If the branch is diverged from upstream, stop and ask. Do not create or reuse a PR against remote branch contents that were not inspected locally.
- Never push directly to a protected default branch unless the repository policy explicitly allows it and the user explicitly requested it.

### PR Creation

Create a ready PR. Never pass `--draft`, and never create a draft PR. If the user explicitly asks for a
draft PR, stop before PR creation and ask whether to create a ready PR or pause publication.

PR language rule:

- The PR title and PR body passed to `gh pr create` or `gh pr edit` must be written in English.
- If the issue, task note, commit message, or user request is in Japanese, translate the PR-facing summary,
  motivation, change list, and validation notes into concise English.
- The user-facing chat response may follow the active conversation language; this rule is only for GitHub
  PR creation/editing content.

PR title rule:

- If the PR primarily addresses a known GitHub issue, include `[issue #N]` at the start of the title, for example `[issue #2] Document provider architecture decisions`.
- Prefer a single primary issue number. If multiple issues are materially addressed, include the primary issue in the title and list the rest in the PR body.
- Do not include `[codex]` in the PR title. If no issue context is available, use a plain English title without a bracketed Codex marker.
- If the task is clearly issue-scoped but the issue number cannot be determined from the user request, branch name, commits, PR body draft, or linked task record, ask for the issue number instead of substituting `[codex]`.
- If the title was created without the issue marker and the issue context is discovered before final reporting, update the PR title before reporting completion.

Recommended CLI shape:

```bash
me="$(gh api user --jq .login)"
git fetch origin "$base"
remote_base="origin/$base"
git log "$remote_base"..HEAD --oneline
git diff --stat "$remote_base"...HEAD
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || true)"
if [ -n "$upstream" ]; then
  git fetch "${upstream%%/*}" "${upstream#*/}"
  read behind ahead <<EOF
$(git rev-list --left-right --count "$upstream"...HEAD)
EOF
  if [ "$behind" != "0" ] && [ "$ahead" != "0" ]; then
    echo "Branch diverged from upstream; stop and ask." >&2
    exit 1
  fi
  if [ "$behind" != "0" ]; then
    echo "Branch is behind upstream; fast-forward only if safe, then rerun ownership checks." >&2
    exit 1
  fi
fi
review_window_start="$(date -u +%Y-%m-%dT%H:%M:%SZ)" # capture before PR creation/push
if [ -z "$upstream" ] || [ "${ahead:-0}" != "0" ]; then
  git push -u origin "$head"
fi
gh pr create \
  --repo "$repo" \
  --base "$base" \
  --head "$head" \
  --title "$title" \
  --body-file "$body_file" \
  --assignee "$me"
```

If PR creation succeeds but assignee is missing, add and verify it:

```bash
gh pr edit "$pr" --repo "$repo" --add-assignee "$me"
```

### Issue And PR Labels

Every PR publication must have an explicit label plan. Apply labels to both the PR and the primary linked
issue when an issue is known.

Label source priority:

1. Labels explicitly requested by the user or supplied by task context / Publication Manifest.
2. Existing labels already present on the primary linked issue.
3. Repository label policy files such as `.github/labels.yml`, `.github/labels.json`, or task/project docs.
4. Existing repository labels that exactly match the task type or scope, such as `bug`, `enhancement`,
   `documentation`, `test`, `chore`, or a skill/domain label.

Rules:

- Use existing repository labels only. Do not create, rename, or recolor labels unless the user explicitly asks.
- Validate the final label names with `gh label list --repo "$repo"` before applying them.
- If the primary issue is known, add the final label set to that issue with `gh issue edit`.
- Add the same final label set to the PR with `gh pr edit`.
- For multiple linked issues, apply labels to the PR and the primary issue by default. Modify secondary
  issues only when the user explicitly asks or task context says they share the same label plan.
- If no safe label set can be determined from the sources above, ask the user for labels before reporting
  PR publication complete; do not invent labels.
- If label application fails because of permissions, missing labels, or GitHub API errors, report
  `label_status: blocked` with the exact reason. Do not claim the PR or issue is labeled until verification passes.

Recommended CLI shape:

```bash
available_labels="$(gh label list --repo "$repo" --json name --jq '.[].name')"
issue_labels="$(gh issue view "$primary_issue" --repo "$repo" --json labels --jq '.labels[].name' 2>/dev/null || true)"
label_csv="$(printf '%s\n' "$requested_labels" "$issue_labels" "$inferred_labels" | awk 'NF' | sort -u | paste -sd, -)"
if [ -n "$label_csv" ]; then
  missing_labels="$(
    comm -23 \
      <(printf '%s\n' "$label_csv" | tr ',' '\n' | awk 'NF' | sort -u) \
      <(printf '%s\n' "$available_labels" | sort -u)
  )"
  if [ -n "$missing_labels" ]; then
    echo "Label(s) do not exist: $missing_labels" >&2
    exit 1
  fi
  gh pr edit "$pr" --repo "$repo" --add-label "$label_csv"
  if [ -n "$primary_issue" ]; then
    gh issue edit "$primary_issue" --repo "$repo" --add-label "$label_csv"
  fi
fi
```

Verify:

- `gh pr view "$pr" --repo "$repo" --json labels` includes every label in the final label set.
- If `primary_issue` is known, `gh issue view "$primary_issue" --repo "$repo" --json labels` includes
  every label in the final label set.
- Final output includes `Label status` with applied labels or the blocker.

### Automatic Codex Review Observation

After the PR exists, observe Codex review for the current PR head when feasible. Repository configuration
owns the normal review trigger. This skill must not mirror that setting locally, post a manual trigger
comment, or start a comment-triggered second review attempt.

Required outcomes:

1. A submitted, non-diagnostic PR review from `chatgpt-codex-connector[bot]` exists with
   `commit_id == headRefOid` for the current PR head.
2. If the submitted current-head review does not appear within the polling window, report `review_pending`
   or `review_timeout` and return a resumable observation state without posting a trigger comment.
3. A reviewer assignment, requested-reviewer entry, top-level acknowledgement comment, environment note, or
   review object whose only content is an environment/setup note is diagnostic only. It is not successful
   review evidence.

Recommended detection starts by finding candidate current-head reviews:

```bash
head_oid="$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid)"
gh api "repos/$repo/pulls/$pr/reviews" --jq \
  ".[] | select(.user.login == \"chatgpt-codex-connector[bot]\" and .commit_id == \"$head_oid\" and
    (.state == \"COMMENTED\" or .state == \"APPROVED\" or .state == \"CHANGES_REQUESTED\")) |
    {id, state, commit_id, submitted_at, html_url, body}"
```

Then inspect each candidate review body and associated review comments before treating it as successful:

```bash
gh api "repos/$repo/pulls/$pr/reviews/$review_id/comments" --jq \
  '.[] | {id, body, path, line, html_url}'
```

Accept a candidate when the review body contains the Codex review summary (for example `Codex Review` or
`Reviewed commit`) or when its associated review comments contain actionable review feedback. Reject
diagnostic-only candidates such as an environment setup note with no review suggestions.

Manual trigger comments are forbidden in this publication workflow. Even when the user asks for a
Codex-reviewed PR or automatic review is delayed, do not translate that request into a PR comment.

Direct reviewer requests remain optional compatibility behavior. They may be attempted when the repository
supports them or when the user explicitly asks, but failure to keep `chatgpt-codex-connector[bot]` in
`reviewRequests` is not itself a workflow failure. GitHub can remove requested reviewers after they submit
a review, and a reviewer assignment alone is never successful review evidence.

Verify:

- PR is open and non-draft.
- Current GitHub user is assigned.
- PR labels are applied and verified, or `label_status` explains why labeling is blocked.
- If a primary issue is linked, issue labels are applied and verified, or `label_status` explains why labeling is blocked.
- Remote PR head matches the intended pushed commit.
- `reviews` contains a submitted, non-diagnostic `chatgpt-codex-connector[bot]` review for the current `headRefOid`, or the workflow clearly reports `review_pending` / `review_timeout` without posting a trigger comment.
- No PR comment was used to trigger Codex review; a reviewer request, when explicitly used for compatibility, is not treated as review success.

### Codex Work PR monitor registration

The Codex Work setting “Pull Requestを監視して修正する” is an external watcher, not a property that can be inferred from GitHub `mergeable` or the automatic-merge toggle. After creating the PR or pushing a fix, verify an authenticated registration for the exact repository, PR number, current base/head SHA, review/comment trigger, and “continue until merged” state. Record the registration evidence with the task.

If the registration cannot be read through the available connector, report `pr_monitor_registration: unverified` and do not claim that the PR is being monitored or will be auto-remediated. Continue only with bounded, explicitly reported review observation; do not silently replace the missing watcher with a comment trigger. The automatic-merge toggle controls merge behavior only and is not review-completion evidence. When `gh` authentication is unavailable, use the GitHub Connector for the PR/review/thread state and report any watcher-registration limitation rather than storing credentials in the repository.

## Codex Review Feedback Intake

The Codex review may arrive asynchronously. Treat waiting as a resumable stage rather than an infinite block.

Default behavior:

- Poll for up to 10 minutes when the user asked for a complete PR creation flow in the current turn.
- If no Codex response arrives in time, report the PR URL and the exact command/context needed to resume.
- Capture `review_window_start` before the PR creation, ready-for-review transition, or push that should
  trigger automatic Codex review. If a direct reviewer request is attempted, keep its timestamp too.
- When a response appears, fetch top-level PR comments, reviews, and review threads created after the
  earliest relevant event timestamp: `review_window_start` or reviewer-request timestamp.
- Always inspect submitted `chatgpt-codex-connector[bot]` reviews whose `commit_id` matches the current
  `headRefOid`. Exclude diagnostic-only reviews from success status while still reporting them as diagnostics.

Feedback source priority:

1. Review threads and requested-change reviews.
2. Current-head PR reviews from `chatgpt-codex-connector[bot]`.
3. Top-level PR comments from `chatgpt-codex-connector[bot]` or comments that clearly respond to the current review attempt.
4. Other bot-authored PR feedback that references Codex review.

Classify feedback into:

| Class | Meaning | Next action |
|---|---|---|
| Blocking fix | Correctness, security, data loss, build failure, or ruleset blocker | Propose fix plan and ask user approval |
| Non-blocking improvement | Maintainability, style, docs, or tests | Include in optional plan |
| Clarification | Needs user or reviewer decision | Ask user before editing |
| No action | Praise, duplicate, stale, or already addressed | Record and do not edit |

## Human Confirmation Gate

Before making any review-driven code changes, stop and ask for approval with a concise plan.

Use this format:

```markdown
Codex review の指摘を受け取りました。実装前に方針確認です。

| ID | 種別 | 指摘 | 推奨対応 |
|---|---|---|---|
| R1 | Blocking fix | ... | ... |

推奨: A

A. 推奨対応を実装する
B. 一部だけ実装する
C. 実装せずコメント返信案だけ作る
```

Only implement after the user chooses a path. If the user chooses implementation, make the smallest scoped
changes, rerun relevant checks, commit, push, and report back to the same PR.

## Review Fix Publication Gate

A review-thread fix is not considered addressed until the fix commit is pushed and visible on the PR branch.
Use this order for any approved review-feedback implementation:

1. Implement the selected fix locally.
2. Run the relevant checks and record any known environment-only failures.
3. Commit only the scoped fix and test changes.
4. Push the PR branch.
5. Verify the pushed PR branch contains the fix commit by comparing the local fix commit to the remote PR head with `gh pr view --json headRefOid` or `git ls-remote origin "refs/heads/$head"`. Local-only checks such as `git log` or `git status -sb` are not sufficient.
6. Only after push verification, reply to review threads or top-level comments with language such as `fixed`, `addressed`, or `implemented`.

Rules:

- Do not post `fixed` / `addressed` replies, resolve review threads, submit a review, or request re-review while fixes are local-only.
- If push fails or remote verification is unavailable, report the local commit and blocker to the user; optionally draft the intended reply, but do not post it.
- If the user asks to reply before push, push and verify first, or explain the blocker if push cannot be completed.
- Review replies should include the pushed commit hash or clear verification context plus checks run.
- This gate also applies when review handling is delegated to `github:gh-address-comments` or another review-comment workflow.

## Failure Handling

| Failure | Required response |
|---|---|
| `gh` missing or unauthenticated | Use the GitHub Connector for supported PR/review/thread operations; report a blocker only for an operation unavailable through either path |
| No GitHub remote | Stop and ask for repo or remote setup |
| Worktree ownership ambiguous | Ask which paths belong in the PR |
| Commit/push rejected | Report exact error and do not create a misleading PR or post addressed/fixed review replies |
| Draft PR requested | Stop before PR creation and ask whether to create a ready PR or pause publication; do not pass `--draft` |
| No safe label set | Ask the user for labels before reporting PR publication complete |
| Label application blocked | Report the exact missing label, permission, or API error; do not claim labels were applied |
| Review reply requested before push | Commit, push, and verify the PR branch first; if blocked, draft but do not post the reply |
| PR already exists | Reuse it and apply assignee, remote-head verification, and automatic current-head review observation |
| Local base diverges from remote base | Fetch and compare against the remote base; stop if the branch contents cannot be proven task-owned |
| Local branch behind or diverged from upstream | Stop or safely fast-forward, then rerun remote-base ownership checks before PR create/reuse |
| Codex review cannot be verified | Report resumable `review_pending` only when no submitted current-head Codex review exists after the polling window; do not post a trigger comment |
| Codex review times out | Report resumable state; do not fabricate review results |

## Output

Final response must include:

| Field | Required |
|---|---|
| PR URL | Yes |
| Branch | Yes |
| Commit(s) | When created in this run |
| Assignee | Yes |
| PR title/body language | English |
| Label status | Applied labels for PR and primary issue, skipped only with reason, or blocked |
| Codex review status | current-head review observed, review pending, timed out, or not requested |
| Codex review intake status | Responded, timed out, or not requested |
| PR monitor registration | `pr_monitor_registration=verified` for exact PR/head and continue-until-merged state, or `pr_monitor_registration=unverified` with the connector limitation |
| Checks run | Yes |
| Fix push status | Required when review feedback was implemented |
| Review reply status | Required when posting replies after pushed fixes |
| Next user decision | Required when actionable review feedback exists |

Also record the publication, Codex review status, review feedback summary, and user confirmation state in
the task record or Agent-Vault context required by the active project instructions.

## Examples

### Standard publication

User: `PR作成して`

Expected behavior:

- Inspect local branch and diff.
- Commit/push task-owned changes if needed.
- Create a ready PR.
- Write the PR title and body in English.
- Because the task is issue-scoped, use a title such as `[issue #2] Document provider architecture decisions`; do not include `[codex]`.
- Resolve labels from user/task/issue/repo context and apply them to the PR and primary issue.
- Assign current GitHub user.
- Record the review attempt before PR creation/push.
- Detect a current-head Codex review from GitHub PR reviews.
- Never post a manual Codex review-trigger comment; repository automation owns normal review creation.
- Wait briefly for Codex review and stop at fix-plan confirmation if feedback arrives.

### Existing pushed branch

User: `このブランチでPRだけ作って。Codex reviewも付けて`

Expected behavior:

- Do not create a duplicate branch.
- Create or reuse the PR.
- Apply assignee, remote-head verification, and automatic current-head Codex review observation.

### Review feedback returns

User: `Codex reviewが返ってきていたら見て`

Expected behavior:

- Fetch new Codex review feedback.
- Classify actionable items.
- Present a fix plan and ask before editing.
- After approved fixes, commit and push before posting any addressed/fixed review-thread replies.

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** Requires escalation for git writes and GitHub network operations

- **Filesystem**: Reads repo state; writes git index/refs when committing or pushing.
- **Network**: Uses GitHub via `gh` for PR creation, optional reviewer requests, label/assignee updates, feedback replies, and review polling.
- **Configuration**: Requires authenticated `gh` with sufficient repository permissions.

## Related Skills

- `commit`: Use for scoped commits before publication when changes are uncommitted.
- `push`: Use when repository push policy or default-branch restrictions need explicit checking.
- `github:gh-address-comments`: Use after the user approves implementing selected review feedback.
- `github:gh-fix-ci`: Use when the PR problem is specifically failing GitHub Actions checks.
