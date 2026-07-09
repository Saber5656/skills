# GitHub CLI Automation Policy

This reference defines how the skill may use `gh` and GitHub REST API for repository hardening.

## Principle

Use manual GitHub UI for high-risk settings unless automation materially reduces error and the user explicitly approves the mutation. Read-only inspection is allowed. Mutations require a dry-run manifest.

Do not use broad stored `gh` credentials for repository mutation by default. Prefer:

1. Manual/browser UI.
2. Short-lived fine-grained PAT supplied as `GH_TOKEN` for a selected repository.
3. GitHub App installation token for repeated multi-repository automation.

Classic PAT is a last resort.

## Never Do

- Do not run `gh auth token`.
- Do not run `gh auth status --show-token`.
- Do not print `GH_TOKEN`, `GITHUB_TOKEN`, Authorization headers, or secret values.
- Do not store tokens in Vault, issue bodies, PR descriptions, shell scripts, logs, or generated reports.
- Do not approve fork PR workflow runs automatically.
- Do not create, update, or delete secrets without explicit user action.
- Do not publish releases or packages from this skill.

## Read-only Inspection Allowlist

These commands are acceptable in `audit` mode.

```bash
gh auth status -h github.com
gh repo view OWNER/REPO --json nameWithOwner,owner,visibility,isPrivate,defaultBranchRef
gh api repos/OWNER/REPO/rulesets
gh api repos/OWNER/REPO/actions/permissions
gh api repos/OWNER/REPO/actions/permissions/workflow
gh api repos/OWNER/REPO/actions/permissions/fork-pr-contributor-approval
gh api -i repos/OWNER/REPO/vulnerability-alerts
gh secret list --repo OWNER/REPO
```

`gh auth status` is not a complete token audit. It cannot reliably prove fine-grained PAT selected repositories, permission matrix, expiration, or org approval state.

## Mutation Approval Manifest

Before any `POST`, `PUT`, `PATCH`, or `DELETE`, present this manifest and wait for explicit approval.

```markdown
**gh mutation dry-run**

| Field | Value |
|---|---|
| Target repository | OWNER/REPO |
| Endpoint | METHOD /path |
| Auth source | manual UI / temporary GH_TOKEN / stored gh credential |
| Required permission | e.g. Administration: write |
| Change summary | ... |
| Reversible | yes/no |
| Rollback | ... |
| Token hygiene | No token values printed; revoke temporary token after use |
```

If auth source is broad stored `gh` credential, stop and recommend manual UI or a fine-grained PAT.

## Mutation Categories

| Category | Default Handling |
|---|---|
| Generate local files | Allowed in `prepare` |
| Create draft PR | Approval recommended |
| Enable vulnerability alerts | Explicit approval |
| Create disabled ruleset payload | Explicit approval if API mutation is used |
| Activate ruleset | Explicit approval; verify CI/signing first |
| Enable or require merge queue | Explicit approval; verify required checks and `merge_group` CI first |
| Change Actions permissions | Explicit approval |
| Change fork PR workflow approval | Explicit approval |
| Add/update/delete secrets | Manual only by default |
| Add bypass actors | Manual only or explicit high-risk approval |
| Release/package publish | Out of scope |
| Repository transfer/delete/visibility change | Manual only unless user explicitly requests that exact action |

## Fine-grained PAT Profiles

Use separate short-lived tokens for separate tasks.

| Profile | Repository Permissions |
|---|---|
| Inspect | Metadata: read |
| File scaffold PR | Contents: write, Pull requests: write |
| Repository settings | Administration: write |
| Actions settings | Administration: write and Actions access as required by endpoint |
| Security alerts | Administration: write for vulnerability alerts endpoint |

Keep expiration short. Select only the target repository.

## Example: Read-only Audit

```bash
scripts/audit-gh-repo.sh OWNER/REPO
```

## Example: Ruleset Dry-run Payload

Use `templates/main-ruleset-solo.json` as a starting point. Replace `OWNER/REPO` only in the endpoint, not in the JSON.

```bash
gh api \
  --method POST \
  repos/OWNER/REPO/rulesets \
  --input templates/main-ruleset-solo.json
```

Do not run this until the user approves the dry-run manifest.

## Example: Default Branch Ruleset Script

Prefer the bundled helper when creating or updating the standard solo OSS default-branch ruleset.

Dry-run:

```bash
python3 scripts/apply-default-branch-ruleset.py --repo OWNER/REPO
```

Apply with a short-lived fine-grained PAT:

```bash
printf 'GitHub fine-grained PAT: '
IFS= read -r -s GH_TOKEN
printf '\n'
export GH_TOKEN
python3 scripts/apply-default-branch-ruleset.py \
  --repo OWNER/REPO \
  --mode apply \
  --yes \
  --payload-in /path/to/reviewed-ruleset.json
unset GH_TOKEN
```

The token must be selected to the target repository and have `Administration: write`.
Do not print the token, put it in the command line, or leave it in the shell environment after use.

The script refuses apply mode without `GH_TOKEN` unless `--allow-stored-gh-auth` is explicitly passed.
That override is for deliberate permission tests or exceptional cases only.
Read access to `repos/OWNER/REPO/rulesets` does not prove write access to `PUT /repos/OWNER/REPO/rulesets/{ruleset_id}`; a stored token may read rulesets and still fail mutation with HTTP 403.
Use `--payload-in` for apply so the reviewed dry-run payload is the exact payload sent to GitHub.
If the endpoint is `PUT`, `--replace-existing` is also required because replacing a ruleset can drop rules that were added outside this helper.

## Useful Official References

- GitHub REST API repository rulesets: https://docs.github.com/en/rest/repos/rules
- GitHub merge queue management: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- GitHub Actions `merge_group` event: https://docs.github.com/actions/using-workflows/events-that-trigger-workflows
- GitHub Actions repository permissions: https://docs.github.com/en/rest/actions/permissions
- GitHub Actions secure use: https://docs.github.com/en/actions/reference/security/secure-use
- GitHub token authentication in workflows: https://docs.github.com/en/actions/tutorials/authenticate-with-github_token
- Fine-grained PAT permissions: https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
