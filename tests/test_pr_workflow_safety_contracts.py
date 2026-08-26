from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_skill(name: str) -> str:
    return (ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def read_pr_contract() -> str:
    return read_skill("pr") + "\n" + (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")


def read_remaining_contract() -> str:
    return read_skill("gh-deliver-remaining-issues") + "\n" + (ROOT / "gh-deliver-remaining-issues" / "references" / "execution-contract.md").read_text(encoding="utf-8")


def read_pr_cli_block() -> str:
    section = read_skill("pr").split("Recommended CLI shape:", 1)[1].split("After creation or reuse", 1)[0]
    return section.split("```bash", 1)[1].split("```", 1)[0].strip()


def read_expected_assignees_filter() -> str:
    match = re.search(
        r'expected_assignees_json="\$\(jq -ce \'(.*?)\' "\$publication_manifest"\)"',
        read_pr_cli_block(),
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("expected_assignees jq filter not found")
    return match.group(1)


def read_current_user_default_block() -> str:
    contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
    section = contract.split("### Current-user default materialization", 1)[1].split("### Exact-set reconciliation", 1)[0]
    return section.split("```bash", 1)[1].split("```", 1)[0].strip()


class PrPublicationSafetyTest(unittest.TestCase):
    def test_exact_assignee_postcondition_is_fail_closed(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "expected_assignees",
            "observed_assignees",
            "exact set",
            "publication_incomplete",
            "assignee_set_mismatch",
        ]:
            self.assertIn(phrase, text)

    def test_assignee_commands_reconcile_the_trusted_set(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "expected_assignees_json",
            "assignee_args+=(--assignee",
            "assignee_edit_args+=(--add-assignee",
            "assignee_edit_args+=(--remove-assignee",
            "$observed - $expected",
        ]:
            self.assertIn(phrase, text)
        self.assertNotIn('--assignee "$me"', text)
        self.assertNotIn('--add-assignee "$me"', text)

    def test_assignee_examples_and_evals_do_not_restore_unconditional_current_user(self) -> None:
        skill = read_skill("pr")
        self.assertNotIn("- Assign current GitHub user.", skill)
        self.assertNotIn("- Apply assignee, remote-head verification", skill)
        evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))
        first = next(item for item in evals["evals"] if item["id"] == 1)
        self.assertIn("expected_assignees", first["expected_output"])
        self.assertNotIn("assigns the current user", first["expected_output"].lower())
        self.assertFalse(any("current GitHub user" in item for item in first["expectations"]))

    def test_expected_assignee_manifest_filter_fails_closed_before_mutation(self) -> None:
        skill = read_skill("pr")
        jq_filter = read_expected_assignees_filter()
        invalid_payloads = [
            "{",
            json.dumps({}),
            json.dumps({"expected_assignees": None}),
            json.dumps({"expected_assignees": "Saber5656"}),
            json.dumps({"expected_assignees": []}),
            json.dumps({"expected_assignees": [1]}),
            json.dumps({"expected_assignees": [""]}),
            json.dumps({"expected_assignees": ["-invalid"]}),
            json.dumps({"expected_assignees": ["invalid-"]}),
            json.dumps({"expected_assignees": ["invalid_name"]}),
            json.dumps({"expected_assignees": ["a--b"]}),
            json.dumps({"expected_assignees": ["a---b"]}),
            json.dumps({"expected_assignees": ["a" * 40]}),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                result = subprocess.run(
                    ["jq", "-ce", jq_filter],
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, result.returncode)
        valid = subprocess.run(
            ["jq", "-ce", jq_filter],
            input=json.dumps({"expected_assignees": ["release-owner", "a-b", "a" * 39, "Saber5656", "Saber5656"]}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, valid.returncode, valid.stderr)
        self.assertEqual(["Saber5656", "a-b", "a" * 39, "release-owner"], json.loads(valid.stdout))
        cli_block = read_pr_cli_block()
        gate_position = cli_block.index('if ! expected_assignees_json="$(jq -ce')
        for mutation in ['git fetch origin "$base"', 'git push -u origin "$head"', "gh pr create"]:
            self.assertLess(gate_position, cli_block.index(mutation))
        self.assertIn("expected_assignees_invalid", skill)

    def test_invalid_assignee_manifest_invokes_no_git_or_gh_command(self) -> None:
        cli_block = read_pr_cli_block()
        invalid_payloads = [
            "{",
            json.dumps({}),
            json.dumps({"expected_assignees": None}),
            json.dumps({"expected_assignees": "Saber5656"}),
            json.dumps({"expected_assignees": []}),
            json.dumps({"expected_assignees": [1]}),
            json.dumps({"expected_assignees": [""]}),
            json.dumps({"expected_assignees": ["-invalid"]}),
            json.dumps({"expected_assignees": ["invalid-"]}),
            json.dumps({"expected_assignees": ["invalid_name"]}),
            json.dumps({"expected_assignees": ["a--b"]}),
            json.dumps({"expected_assignees": ["a---b"]}),
            json.dumps({"expected_assignees": ["a" * 40]}),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            bin_directory = temporary_root / "bin"
            bin_directory.mkdir()
            calls_path = temporary_root / "mutation-calls.log"
            stub = '#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$CALLS_FILE"\nexit 97\n'
            for command in ("git", "gh"):
                command_path = bin_directory / command
                command_path.write_text(stub, encoding="utf-8")
                command_path.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
            environment["CALLS_FILE"] = str(calls_path)
            script = 'set -euo pipefail\npublication_manifest="$1"\n' + cli_block
            manifest_path = temporary_root / "publication-manifest.json"
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    calls_path.unlink(missing_ok=True)
                    manifest_path.write_text(payload, encoding="utf-8")
                    result = subprocess.run(
                        ["bash", "-c", script, "assignee-preflight", str(manifest_path)],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=environment,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("publication_incomplete: expected_assignees_invalid", result.stderr)
                    self.assertFalse(calls_path.exists(), calls_path.read_text(encoding="utf-8") if calls_path.exists() else "")

    def test_explicit_current_user_default_materializes_authenticated_login(self) -> None:
        block = read_current_user_default_block()
        contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
        self.assertNotIn('expected_assignees=["$me"]', contract)
        self.assertIn("Never persist a symbolic `$me`", contract)
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            bin_directory = temporary_root / "bin"
            bin_directory.mkdir()
            calls_path = temporary_root / "gh-calls.log"
            gh_path = bin_directory / "gh"
            gh_path.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CALLS_FILE"\n'
                'if [ "${STUB_GH_FAIL:-0}" = "1" ]; then exit 41; fi\n'
                'if [ "$#" -eq 4 ] && [ "$1" = "api" ] && [ "$2" = "user" ] && [ "$3" = "--jq" ] && [ "$4" = ".login" ]; then\n'
                '  printf "%s\\n" "Saber5656"\n  exit 0\nfi\nexit 97\n',
                encoding="utf-8",
            )
            gh_path.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
            environment["CALLS_FILE"] = str(calls_path)
            script = 'set -euo pipefail\n' + block + '\nprintf "RESULT=%s\\n" "$current_user_expected_assignees_json"\n'
            result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["api user --jq .login"], calls_path.read_text(encoding="utf-8").splitlines())
            concrete = json.loads(next(line.removeprefix("RESULT=") for line in result.stdout.splitlines() if line.startswith("RESULT=")))
            self.assertEqual(["Saber5656"], concrete)
            validated = subprocess.run(
                ["jq", "-ce", read_expected_assignees_filter()],
                input=json.dumps({"expected_assignees": concrete}),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, validated.returncode, validated.stderr)
            calls_path.unlink()
            failing_environment = environment.copy()
            failing_environment["STUB_GH_FAIL"] = "1"
            failed = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, env=failing_environment)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("publication_incomplete: current_user_identity_unreadable", failed.stderr)

    def test_assignee_reconciliation_rereads_and_compares_exact_set(self) -> None:
        contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
        for phrase in [
            "assignee_state_unreadable",
            "assignee_edit_failed",
            "fresh_assignees_json",
            "$expected == $observed",
            "assignee_set_mismatch",
        ]:
            self.assertIn(phrase, contract)

    def test_coderabbit_trigger_is_configured_current_head_and_typed(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "external_reviewers.coderabbit.required",
            "@coderabbitai review",
            "at-most-once",
            "headRefOid",
            "coderabbit_policy_missing",
            "coderabbit_permission_blocked",
            "coderabbit_rate_limited",
            "coderabbit_delivery_failed",
            "coderabbit_claim_unavailable",
            "atomically compare-and-set",
            "delivery_unknown",
        ]:
            self.assertIn(phrase, text)
        claim_rows = [line for line in text.splitlines() if line.startswith("| `") and " | " in line]
        mutation_column = {row.split("|")[1].strip(): row.split("|")[2].strip() for row in claim_rows}
        self.assertEqual("no", mutation_column["`unclaimed`"])
        self.assertIn("no from persisted state", mutation_column["`reserved`"])
        self.assertIn("no from persisted or reloaded state", mutation_column["`posting` / `delivery_unknown`"])
        self.assertEqual("no", mutation_column["`delivered` / `acknowledged` / `rate_limited` / terminal"])

    def test_coderabbit_posting_crash_is_reconciliation_only(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "pre-existing `reserved`, `posting`, `delivery_unknown`",
            "perform reconciliation only",
            "A persisted state never grants permission",
            "uninterrupted execution",
            "non-replayable in-memory",
            "including after restart",
            "never issue another comment mutation",
            "coderabbit_trigger_state_unknown",
        ]:
            self.assertIn(phrase, text)
        evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))
        crash = next(item for item in evals["evals"] if item["id"] == 32)
        self.assertIn("pre-existing posting state as reconciliation-only", crash["expected_output"])
        self.assertIn("no second comment mutation", crash["expected_output"])

    def test_required_check_producer_and_untrusted_payload_are_bound(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "trusted producer",
            "app_id",
            "required-workflow identity/path/ref",
            "incomplete pagination",
            "required_check_producer_mismatch",
            "untrusted data",
            "Never execute",
            "authenticated structured GitHub metadata",
        ]:
            self.assertIn(phrase, text)

    def test_review_absence_and_provenance_states_are_distinct(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "review_count_zero",
            "review_threads_absent",
            "unresolved_thread_count_zero",
            "review_timeout",
            "review_provenance_missing",
            "effective_model",
            "reviewer_role",
        ]:
            self.assertIn(phrase, text)
        self.assertIn("never post a manual Codex review-trigger command", text)

    def test_required_checks_do_not_use_mergeable_as_readiness(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "required_check_inventory_unknown",
            "checks_pending",
            "checks_failed",
            "mergeStateStatus",
            "policy merge readiness",
        ]:
            self.assertIn(phrase, text)


class ReviewFixSafetyTest(unittest.TestCase):
    def test_current_head_and_provenance_are_required(self) -> None:
        text = read_skill("pr-review-fix-policy")
        for phrase in [
            "current_head_sha",
            "review_head_sha",
            "old_head_review_invalid",
            "review_provenance_missing",
            "effective_model",
            "reviewer_role",
        ]:
            self.assertIn(phrase, text)

    def test_timeout_and_absence_are_not_collapsed(self) -> None:
        text = read_skill("pr-review-fix-policy")
        for phrase in [
            "review_count_zero",
            "review_threads_absent",
            "unresolved_thread_count_zero",
            "review_timeout",
            "not a pass",
        ]:
            self.assertIn(phrase, text)


class RemainingIssuesSafetyTest(unittest.TestCase):
    def test_required_frontmatter_is_present(self) -> None:
        text = read_skill("gh-deliver-remaining-issues")
        for field in [
            "user-invocable:",
            "allowed-tools:",
            "category:",
            "created:",
            "status:",
            "purpose:",
            "argument-hint:",
        ]:
            self.assertIn(field, text)
        allowed_line = next(line for line in text.splitlines() if line.startswith("allowed-tools:"))
        observed_tools = {item.strip() for item in allowed_line.split(":", 1)[1].split(",")}
        self.assertEqual({"Read", "Grep", "Glob", "Bash", "Agent"}, observed_tools)

    def test_review_provenance_carrier_is_complete(self) -> None:
        text = read_remaining_contract()
        for phrase in [
            "Canonical review evidence carrier",
            "effective_model",
            "request_id",
            "session_id",
            "reviewed_head_sha",
            "artifact_digest",
            "result_integrity",
            "review_provenance_incomplete",
            "terminal_result",
            "RFC 8785 JSON Canonicalization Scheme (JCS), UTF-8",
            "never self-referential",
        ]:
            self.assertIn(phrase, text)
        vector = {"findings": [], "status": "success", "verdict": "approved"}
        canonical = json.dumps(vector, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(
            "b84a8ab8c6b071531408bbcdd7253ce51025fcbb9d7d8ab51cdaf92b244ad800",
            hashlib.sha256(canonical).hexdigest(),
        )

    def test_pr_handoff_and_stop_boundary(self) -> None:
        text = read_skill("gh-deliver-remaining-issues")
        for phrase in [
            "expected_assignees",
            "external_reviewers",
            "external_reviewers.coderabbit.required",
            "pr_created_review_pending",
            "policy_merge_ready",
            "Stop after PR creation and its configured review intake",
            "Do not merge or release",
        ]:
            self.assertIn(phrase, text)


class MergeContextSafetyTest(unittest.TestCase):
    def test_mixed_context_performs_no_local_or_pr_mutation(self) -> None:
        text = read_skill("merge")
        for phrase in [
            "`mixed_context`としてfail closed",
            "local側を候補化せず",
            "fetch、commit、stash、`git merge`を含む全mutationを実行しない",
            "独立したtask/processとして明示的に再承認・再依頼",
            "直接handoffしない",
        ]:
            self.assertIn(phrase, text)
        evals = json.loads((ROOT / "merge" / "evals" / "evals.json").read_text(encoding="utf-8"))
        mixed = next(item for item in evals["evals"] if item["id"] == 10)
        self.assertIn("performs no fetch, commit, stash, local git merge, or PR handoff", mixed["expected_output"])
        self.assertNotIn("Preserves local merge behavior", mixed["expectations"])


class EvalInventoryTest(unittest.TestCase):
    def test_changed_skill_eval_ids_are_unique(self) -> None:
        minimums = {
            "pr": 34,
            "pr-review-fix-policy": 31,
            "merge": 10,
            "gh-deliver-remaining-issues": 28,
            "pr-merge-gate": 15,
        }
        for name, minimum in minimums.items():
            with self.subTest(skill=name):
                data = json.loads((ROOT / name / "evals" / "evals.json").read_text(encoding="utf-8"))
                ids = [item["id"] for item in data["evals"]]
                self.assertGreaterEqual(len(ids), minimum)
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(data["skill_name"], name)


if __name__ == "__main__":
    unittest.main()
