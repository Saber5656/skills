---
name: configure-organization
description: >
  AI エージェント組織の運用設定、task context、manifest、runtime facade を確認・適用・実行するときに必ず使う。COMMON-AGENTS.md、
  Hook、No Task gate、fast/strict mode、Agent-Teams-Viewer、Agent-Vault policy、
  Team Role 定義、ITB/ITD/archive-shutdown runtime、組織運用の enabled/disabled/maintenance 切替、組織フローの詰まりや高速化に
  言及されたらこのスキルを使う。通常の実装作業だけで組織運用を変更しない場合は使わない。
user-invocable: true
allowed-tools: Read, Grep, Bash, Write, Edit
category: Operation
created: 2026-06-15
status: active
purpose: Agent-Teams-Viewer を正本として組織運用設定、task context、runtime facade を扱う唯一の skills-repo 入口
argument-hint: "[prompt or organization change request]"
---

# Configure Organization

Agent-Teams-Viewer を AI 組織運用知識の正本として扱い、skills-repo における
組織運用の唯一の public interface として動く。

`co` / `configure-organization` は、単なる設定判定ではなく、次をまとめて扱う facade である。

- 現在の作業を `fast` / `strict` / `maintenance` のどれで扱うかを決める
- utility skills が読む task context / manifest / policy decision を解決する
- ITB / ITD / archive shutdown などの organization runtime を発見・実行する
- Hook、mode、policy、role、runtime の正本を Agent-Teams-Viewer に寄せる

## Source Of Truth

| Item | Canonical Source |
|---|---|
| Organization settings | `Agent-Teams-Viewer/organization/settings.json` |
| Policies | `Agent-Teams-Viewer/organization/policies/` |
| Team Role definitions | `Agent-Teams-Viewer/organization/roles/` |
| Runtime registry snapshot | `Agent-Teams-Viewer/organization/runtime/` |
| ITB executable runtime | `Agent-Teams-Viewer/organization/runtime/infra-team-bootstrap/` |
| ITD monitor runtime | `Agent-Teams-Viewer/organization/runtime/infra-task-dispatcher/` |
| Task context and evidence | configured task/evidence vault (`AGENTS_VAULT_ROOT`) |

Resolve the Agent-Teams-Viewer root in this order:

1. `AGENT_TEAMS_VIEWER_ROOT` when it is set and non-empty
2. A task-specific worktree explicitly provided by the user
3. `${DEV_REPO_ROOT}/Agent-Teams-Viewer` when `DEV_REPO_ROOT` is set
4. `${HOME}/dev/Agent-Teams-Viewer`

Do not treat an unset or empty alias as a usable path. If none of these
candidates exists, stop with a missing-root diagnostic instead of guessing an
organization runtime location.

## Workflow

1. Read `organization/settings.json`.
2. Run the decision CLI when available:
   ```sh
   python3 <Agent-Teams-Viewer>/scripts/configure_organization.py classify --prompt "<user prompt>"
   ```
3. Discover runtime paths through the same CLI when organization runtime is
   needed:
   ```sh
   python3 <Agent-Teams-Viewer>/scripts/configure_organization.py runtime-paths
   ```
4. Run ITB / ITD runtime through the Agent-Teams-Viewer CLI rather than calling
   skills-repo runtime files directly:
   ```sh
   python3 <Agent-Teams-Viewer>/scripts/configure_organization.py itb <builder args>
   python3 <Agent-Teams-Viewer>/scripts/configure_organization.py itd-monitor <monitor args>
   ```
5. Run archive shutdown through the same runtime facade:
   ```sh
   python3 <Agent-Teams-Viewer>/scripts/configure_organization.py itb archive-shutdown <args>
   ```
6. Treat every work item as task-backed. `fast` still requires a lightweight task
   record and Vault update.
7. Use `maintenance` when changing organization infrastructure itself
   (`COMMON-AGENTS.md`, Hook, ITB, Gate, Agent-Teams-Viewer, role policy).
   Maintenance keeps task/evidence requirements but disables strict-flow
   self-blocking so the organization can be repaired.
8. Use `strict` for normal implementation, policy, model, Hook, permission,
   review, commit, publication, security, or multi-step work.
9. Use `fast` only for obviously simple, low-risk work.

## Current Facade Surface

| Contract | Required fields |
|---|---|
| `classify` | `organization_state`, `organization_flow_enabled`, `mode`, `task_required`, `main_agent_can_execute`, `role_dispatch_required`, `review_required`, `hook_policy`, `reason`, `missing_information` |
| `status` | settings snapshot, role count, policy count, repository root, generated time |
| `runtime-paths` | ITB runtime root, ITB builder, ITB hooks, ITD monitor, role root, runtime registry, existence flags |
| `itb ...` | delegated ITB runtime result as JSON or native runtime output |
| `itd-monitor ...` | delegated ITD monitor result as JSON or native runtime output |

Archive shutdown is currently executed through the ITB facade:

```sh
python3 <Agent-Teams-Viewer>/scripts/configure_organization.py itb archive-shutdown <args>
```

## Required Next Contracts

| Contract | Purpose |
|---|---|
| `task-context` / manifest | Return task id, repo root, owned paths, review requirements, publication requirement, Branch Plan requirement, policy decision, and blocked reason when missing |
| `archive-shutdown ...` | Optional top-level alias for delegated archive shutdown with dry-run/state/tmux safety fields |

Until `task-context` / manifest exists in the ATV CLI, utility skills must consume
explicit task context supplied by the caller and must not infer role routing on their own.
Until a top-level archive shutdown alias exists, the canonical command is `itb archive-shutdown`.

## Operating Rules

- Team Role skills that have been mirrored into Agent-Teams-Viewer may be
  removed from skills-repo once the deletion scope is task-approved. Keep
  utility skills in skills-repo unless explicitly migrated elsewhere.
- Utility skills in skills-repo must not hard-code organization role names,
  routing rules, approval owners, mode policy, or publication ownership. They
  should accept task context, manifests, and policy decisions supplied through
  this skill / Agent-Teams-Viewer, then execute only their local utility
  responsibility.
- ITB / ITD organization runtime is owned by Agent-Teams-Viewer. Do not keep
  organization member roles or runtime shims under skills-repo; invoke them
  through `configure_organization.py` in Agent-Teams-Viewer.
- New organization runtime entrypoints must be added to this facade first.
  Existing user-facing wrappers, such as archive shutdown aliases, are
  compatibility wrappers only; they must delegate to this facade and must not
  own runtime behavior or organization policy.
- Do not let Hook failure, stale queue, or provider unavailability hard-block
  ordinary assistance. Hooks are observers/advisors.
- Keep organization knowledge in Agent-Teams-Viewer. Keep task history,
  decisions, evidence, and reusable context in the configured task/evidence vault.
- If Agent-Teams-Viewer config is missing, fall back to conservative `strict`
  and report the missing file.
- If the user explicitly disables organization operation, keep task recording
  but allow main-agent execution.

## Output

When asked to explain or decide mode, return:

| Field | Meaning |
|---|---|
| `organization_state` | `enabled`, `disabled`, or `maintenance` |
| `mode` | `fast` or `strict` |
| `task_required` | Always `true` |
| `main_agent_can_execute` | Whether direct execution is allowed |
| `hook_policy` | Must be observer and non-hard-blocking |
| `reason` | Short decision reason |
| `runtime_facade` | Runtime commands must be resolved/executed through Agent-Teams-Viewer |
| `manifest_contract` | Utility skills receive context/manifest; they do not decide role routing |

For implementation work, apply the decision quietly and proceed with the task.
