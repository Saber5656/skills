import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_decision_manifest.py"
SPEC = importlib.util.spec_from_file_location("manifest_validator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_manifest():
    digest = "a" * 64
    proposal = {
        "bundle_id": "p1", "repository": "owner/repo", "base_sha": "b" * 40,
        "evidence_binding": "bound_repository", "generated_at": "2026-07-16T00:00:00Z",
        "source_manifest_digest": digest,
        "artifacts": [{"artifact_id": "design", "media_type": "text/markdown", "resolver": "repository_relative_file", "locator": "docs/design.md", "exact_bytes_sha256": digest}],
    }
    proposal["bundle_digest"] = MODULE.canonical_bundle_digest(proposal)
    return {
        "schema_version": "1", "manifest_id": "m1", "mode": "approved-apply",
        "repository": {"root": "/repo", "owner_repo": "owner/repo", "base_sha": "b" * 40, "observed_at": "2026-07-16T00:00:00Z", "issue_snapshot_digest": digest},
        "proposal": proposal,
        "approval": {
            "owner": "user", "approved_at": "2026-07-16T00:00:00Z", "source": {"kind": "explicit_user_instruction", "locator": "turn-1"},
            "approved_decision_ids": ["d1"], "approved_mutation_ids": ["m1"],
        },
        "decisions": [{"id": "d1", "category": "v1_scope", "statement": "ship x", "rationale": "goal", "source": "turn-1", "approved": True}],
        "constraints": {
            "exact_paths": ["docs/v1.md"], "excluded_paths": [], "github_actions_allowed": [],
            "no_product_code": True, "no_commit_push_pr_merge_release": True,
        },
        "mutations": [{
            "id": "m1", "action": "create_doc", "exact_target": "docs/v1.md",
            "allowed": True, "decision_ids": ["d1"], "expected_before": {"state": "absent", "digest": None},
            "proposed_after_digest": digest,
        }],
    }


class DecisionManifestTests(unittest.TestCase):
    def test_accepts_exact_approved_scope(self):
        self.assertEqual([], MODULE.validate(valid_manifest()))

    def test_rejects_unapproved_mutation(self):
        data = valid_manifest()
        data["approval"]["approved_mutation_ids"] = []
        self.assertIn("approval mutation IDs must exactly match mutation objects", MODULE.validate(data))

    def test_rejects_wildcard_target(self):
        data = valid_manifest()
        data["mutations"][0]["exact_target"] = "docs/*"
        self.assertTrue(any("exact_target must be exact" in error for error in MODULE.validate(data)))

    def test_requires_distinct_issue_authorization(self):
        data = valid_manifest()
        data["mutations"][0].update(action="create_issue", exact_target="owner/repo#new")
        self.assertTrue(any("distinct authorization" in error for error in MODULE.validate(data)))

    def test_requires_schema_fields_not_only_digests(self):
        data = valid_manifest()
        del data["approval"]["approved_at"]
        self.assertIn("approval.approved_at is required", MODULE.validate(data))

    def test_rejects_artifact_change_without_new_bundle_digest(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = "c" * 64
        self.assertIn("proposal.bundle_digest does not match canonical preimage", MODULE.validate(data))

    def test_rejects_doc_update_without_present_digest(self):
        data = valid_manifest()
        data["mutations"][0]["action"] = "update_doc"
        self.assertIn("update_doc requires a present digest precondition", MODULE.validate(data))

    def test_accepts_doc_update_with_present_digest(self):
        data = valid_manifest()
        data["mutations"][0]["action"] = "update_doc"
        data["mutations"][0]["expected_before"] = {"state": "present", "digest": "c" * 64}
        self.assertEqual([], MODULE.validate(data))

    def test_rejects_changed_locator_payload(self):
        data = valid_manifest()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "docs" / "design.md"
            target.parent.mkdir()
            target.write_bytes(b"approved")
            data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = hashlib.sha256(b"approved").hexdigest()
            data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
            self.assertEqual([], MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))
            target.write_bytes(b"changed after approval")
            self.assertIn("proposal.artifacts[0] payload digest mismatch", MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))

    def test_rejects_artifact_locator_escape(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["locator"] = "../outside.md"
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn("proposal.artifacts[0] locator escapes repository.root", MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))

    def test_rejects_absolute_artifact_locator(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["locator"] = "/tmp/outside.md"
        self.assertIn("proposal.artifacts[0] locator must be repository-relative", MODULE.validate_artifact_payloads(data["proposal"], Path("/repo")))

    def test_rejects_symlink_escape_and_missing_payload(self):
        data = valid_manifest()
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            link = Path(temp) / "docs"
            link.symlink_to(Path(outside), target_is_directory=True)
            data["proposal"]["artifacts"][0]["locator"] = "docs/design.md"
            self.assertIn("proposal.artifacts[0] locator escapes repository.root", MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))
            link.unlink()
            self.assertTrue(any("payload is unreadable" in item for item in MODULE.validate_artifact_payloads(data["proposal"], Path(temp))))

    def test_rejects_unsupported_resolver_and_unexpected_property(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["resolver"] = "url"
        self.assertIn("proposal.artifacts[0].resolver is unsupported", MODULE.validate(data))
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["extra"] = "not canonical"
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertIn("proposal.artifacts[0] has unexpected or missing properties", MODULE.validate(data))

    def test_target_state_gate_is_idempotent_or_fail_closed(self):
        mutation = valid_manifest()["mutations"][0]
        self.assertEqual("apply", MODULE.classify_target_state(mutation, "absent", None))
        self.assertEqual("idempotent_skip", MODULE.classify_target_state(mutation, "present", "a" * 64))
        self.assertEqual("stale_target", MODULE.classify_target_state(mutation, "present", "c" * 64))

    def test_cli_returns_structured_error_for_malformed_artifacts(self):
        data = valid_manifest()
        data["proposal"]["artifacts"] = None
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(json.dumps(data))
            completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["valid"])
        self.assertIn("proposal.artifacts must be a non-empty array", payload["errors"])
        self.assertNotIn("Traceback", completed.stderr)

    def test_rejects_proposal_bound_to_another_base(self):
        data = valid_manifest()
        data["proposal"]["base_sha"] = "c" * 40
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertIn("proposal.base_sha must match repository.base_sha", MODULE.validate(data))

    def test_rejects_proposal_bound_to_another_repository(self):
        data = valid_manifest()
        data["proposal"]["repository"] = "other/repo"
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertIn("proposal.repository must match the apply repository identity", MODULE.validate(data))

    def test_accepts_explicit_local_repository_identity(self):
        data = valid_manifest()
        data["repository"]["owner_repo"] = None
        data["proposal"]["repository"] = "local:/repo"
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertEqual([], MODULE.validate(data))

    def test_unbound_proposal_is_noncanonical_and_not_applicable(self):
        contract = (Path(__file__).parents[1] / "SKILL.md").read_text()
        self.assertIn("evidence_binding: unbound_concept", contract)
        self.assertIn("require a new bound proposal/audit before approved-apply", contract)

    def test_audit_and_apply_still_require_immutable_repository(self):
        contract = (Path(__file__).parents[1] / "SKILL.md").read_text()
        self.assertIn("audit/apply lacks repository, explicit mode, immutable base", contract)


if __name__ == "__main__":
    unittest.main()
