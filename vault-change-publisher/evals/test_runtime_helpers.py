#!/usr/bin/env python3
"""Integration tests for runtime context, collection validation, and artifact install."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
PUSH_SPEC = importlib.util.spec_from_file_location(
    "push_committed_heads", SCRIPTS / "push-committed-heads.py"
)
assert PUSH_SPEC and PUSH_SPEC.loader
PUSH_MODULE = importlib.util.module_from_spec(PUSH_SPEC)
PUSH_SPEC.loader.exec_module(PUSH_MODULE)
REVIEW_SPEC = importlib.util.spec_from_file_location(
    "validate_publication_review", SCRIPTS / "validate-publication-review.py"
)
assert REVIEW_SPEC and REVIEW_SPEC.loader
REVIEW_MODULE = importlib.util.module_from_spec(REVIEW_SPEC)
REVIEW_SPEC.loader.exec_module(REVIEW_MODULE)
FINALIZER_SPEC = importlib.util.spec_from_file_location(
    "commit_push_publication_evidence",
    SCRIPTS / "commit-push-publication-evidence.py",
)
assert FINALIZER_SPEC and FINALIZER_SPEC.loader
FINALIZER_MODULE = importlib.util.module_from_spec(FINALIZER_SPEC)
FINALIZER_SPEC.loader.exec_module(FINALIZER_MODULE)
FETCH_SPEC = importlib.util.spec_from_file_location(
    "fetch_vault_main", SCRIPTS / "fetch-vault-main.py"
)
assert FETCH_SPEC and FETCH_SPEC.loader
FETCH_MODULE = importlib.util.module_from_spec(FETCH_SPEC)
FETCH_SPEC.loader.exec_module(FETCH_MODULE)
SCHEMA_SPEC = importlib.util.spec_from_file_location(
    "prepare_codex_output_schema", SCRIPTS / "prepare-codex-output-schema.py"
)
assert SCHEMA_SPEC and SCHEMA_SPEC.loader
SCHEMA_MODULE = importlib.util.module_from_spec(SCHEMA_SPEC)
SCHEMA_SPEC.loader.exec_module(SCHEMA_MODULE)
CANONICAL_SPEC = importlib.util.spec_from_file_location(
    "validate_canonical_result", SCRIPTS / "validate-canonical-result.py"
)
assert CANONICAL_SPEC and CANONICAL_SPEC.loader
CANONICAL_MODULE = importlib.util.module_from_spec(CANONICAL_SPEC)
CANONICAL_SPEC.loader.exec_module(CANONICAL_MODULE)
DIRTY_STAGER_SPEC = importlib.util.spec_from_file_location(
    "stage_dirty_review_inputs", SCRIPTS / "stage-dirty-review-inputs.py"
)
assert DIRTY_STAGER_SPEC and DIRTY_STAGER_SPEC.loader
DIRTY_STAGER_MODULE = importlib.util.module_from_spec(DIRTY_STAGER_SPEC)
DIRTY_STAGER_SPEC.loader.exec_module(DIRTY_STAGER_MODULE)
COMMITTER_SPEC = importlib.util.spec_from_file_location(
    "commit_reviewed_publication", SCRIPTS / "commit-reviewed-publication.py"
)
assert COMMITTER_SPEC and COMMITTER_SPEC.loader
COMMITTER_MODULE = importlib.util.module_from_spec(COMMITTER_SPEC)
COMMITTER_SPEC.loader.exec_module(COMMITTER_MODULE)
EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "prepare_publication_evidence", SCRIPTS / "prepare-publication-evidence.py"
)
assert EVIDENCE_SPEC and EVIDENCE_SPEC.loader
EVIDENCE_MODULE = importlib.util.module_from_spec(EVIDENCE_SPEC)
EVIDENCE_SPEC.loader.exec_module(EVIDENCE_MODULE)
CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "capture_vault_state", SCRIPTS / "capture-vault-state.py"
)
assert CAPTURE_SPEC and CAPTURE_SPEC.loader
CAPTURE_MODULE = importlib.util.module_from_spec(CAPTURE_SPEC)
CAPTURE_SPEC.loader.exec_module(CAPTURE_MODULE)


class RuntimeHelperTests(unittest.TestCase):
    """Exercise helper boundaries without machine-specific paths."""

    def setUp(self) -> None:
        """Create isolated fake catalog, Vaults, workdir, and staging."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.saihai = self.root / "saihai"
        self.agents = self.root / "agents"
        self.user = self.root / "user"
        self.skills = self.root / "skills"
        self.workdir = self.root / "automation"
        for directory in (
            self.saihai,
            self.agents,
            self.user,
            self.skills,
            self.workdir,
        ):
            directory.mkdir()
        for repo in (self.agents, self.user):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.origins = {}
        for repo, key in ((self.agents, "agents"), (self.user, "user")):
            origin = self.root / f"{key}-origin.git"
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
                check=True,
            )
            self.origins[key] = origin
        module = f'''
def load_environment(*, checkout_root, environ, require_catalog):
    environ.update({{
        "AGENTS_VAULT_ROOT": {str(self.agents)!r},
        "USER_VAULT_ROOT": {str(self.user)!r},
        "SKILLS_REPO_ROOT": {str(self.skills)!r},
    }})
    return {{"status": "loaded"}}
'''
        (self.saihai / "directory_paths.py").write_text(module, encoding="utf-8")
        self.config = self.workdir / "automation.local.env"
        (self.agents / "tasks").mkdir()
        (self.agents / "tasks" / "standing.md").write_text(
            "# Standing\n\n### Vault Publication Evidence\n\n"
            "| Run ID | Push status |\n|---|---|\n\n## Reviews\n\nstanding\n",
            encoding="utf-8",
        )
        authorization = self.agents / "tasks" / "auth.md"
        authorization.write_text("approved\n", encoding="utf-8")
        authorization_hash = hashlib.sha256(authorization.read_bytes()).hexdigest()
        self.fake_gitleaks = self.workdir / "fake-gitleaks"
        self.fake_gitleaks.write_text(
            "#!/bin/sh\n[ \"$1\" = version ] && echo 'fixture-gitleaks 1.0'\nexit 0\n",
            encoding="utf-8",
        )
        self.fake_gitleaks.chmod(0o755)
        self.config.write_text(
            "\n".join(
                (
                    f"SAIHAI_CHECKOUT_ROOT={self.saihai}",
                    "CODEX_BIN=/usr/bin/true",
                    f"GITLEAKS_BIN={self.fake_gitleaks}",
                    "IT_NEWS_ARCHIVE_RELATIVE=10_Prompt",
                    "ADVISORY_ARCHIVE_RELATIVE=03-Contexts/Reports/Security",
                    "STANDING_TASK_ID=TSK-STANDING",
                    "STANDING_TASK_RELATIVE=tasks/standing.md",
                    "AUTHORIZATION_TASK_ID=TSK-AUTH",
                    "AUTHORIZATION_TASK_RELATIVE=tasks/auth.md",
                    f"AUTHORIZATION_TASK_SHA256={authorization_hash}",
                    "PUBLISHER_GIT_NAME='Fixture Publisher'",
                    "PUBLISHER_GIT_EMAIL=publisher@example.invalid",
                )
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        """Remove the isolated fixture."""
        self.temp_dir.cleanup()

    def test_codex_schema_projection_preserves_canonical_contract(self) -> None:
        source_path = SKILL_ROOT / "references" / "publication-review-result.schema.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        projected = SCHEMA_MODULE.compatible_schema(source)
        encoded = json.dumps(projected, sort_keys=True)

        self.assertIn("allOf", source)
        self.assertNotIn('"allOf"', encoded)
        self.assertNotIn('"if"', encoded)
        self.assertNotIn('"then"', encoded)
        self.assertNotIn('"else"', encoded)
        self.assertNotIn('"uniqueItems"', encoded)
        self.assertNotIn('"oneOf"', encoded)
        self.assertIn('"anyOf"', encoded)
        self.assertNotIn("approvedManifest", projected["$defs"])
        self.assertEqual(
            projected["$defs"]["taskChangeManifest"]["properties"]
            ["validation_evidence"]["properties"]["file_guard"]["type"],
            "string",
        )
        self.assertEqual(
            json.loads(source_path.read_text(encoding="utf-8")), source
        )

    def test_codex_schema_projection_rejects_open_root(self) -> None:
        with self.assertRaises(SCHEMA_MODULE.SchemaProjectionError):
            SCHEMA_MODULE.compatible_schema(
                {"type": "object", "additionalProperties": True}
            )
        with self.assertRaises(SCHEMA_MODULE.SchemaProjectionError):
            SCHEMA_MODULE.compatible_schema(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "not": {"type": "null"},
                }
            )
        with self.assertRaises(SCHEMA_MODULE.SchemaProjectionError):
            SCHEMA_MODULE.compatible_schema(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "allOf": [{"not": {"type": "null"}}],
                }
            )
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            invalid_schema = {
                "type": "object",
                "additionalProperties": False,
                "allOf": [{"not": {"type": "object"}}],
            }
            CANONICAL_MODULE.validate({}, invalid_schema, invalid_schema)

    def test_canonical_validator_rejects_projected_only_result(self) -> None:
        schema = json.loads(
            (SKILL_ROOT / "references" / "collection-result.schema.json")
            .read_text(encoding="utf-8")
        )
        result = {
            "daily_pipeline_status": "complete",
            "run_id": "fixture",
            "summary_path": "summary.md",
            "summary_sha256": "0" * 64,
            "advisory_path": "advisory.md",
            "advisory_sha256": "1" * 64,
            "notification_result": None,
            "vault_artifacts_complete": True,
            "next_action": None,
        }
        CANONICAL_MODULE.validate(result, schema, schema)
        result["next_action"] = "canonical contract requires null"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(result, schema, schema)
        result["next_action"] = None
        result["notification_result"] = 17
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(result, schema, schema)

    def test_standing_task_snapshot_is_exclusive_and_nofollow(self) -> None:
        staging = self.workdir / "snapshot-staging"
        staging.mkdir()
        source = self.agents / "standing-source.md"
        source.write_text("# Standing task\n", encoding="utf-8")
        runtime = self.workdir / "snapshot-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "standing_task_path": str(source),
                }
            ),
            encoding="utf-8",
        )
        destination = staging / "standing-task.md"
        command = [
            str(SCRIPTS / "stage-standing-task.py"),
            str(runtime),
            str(staging),
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "# Standing task\n")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)

        destination.unlink()
        linked = self.agents / "standing-link.md"
        linked.symlink_to(source)
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "standing_task_path": str(linked),
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)
        self.assertFalse(destination.exists())

        outside = self.workdir / "outside-standing"
        outside.mkdir()
        (outside / "task.md").write_text("outside\n", encoding="utf-8")
        linked_parent = self.agents / "linked-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "standing_task_path": str(linked_parent / "task.md"),
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)
        self.assertFalse(destination.exists())

    def test_authorization_task_snapshot_uses_distinct_destination(self) -> None:
        review_input = self.workdir / "review-input"
        review_input.mkdir()
        source = self.agents / "authorization-source.md"
        source.write_text("# Approved authorization\n", encoding="utf-8")
        pinned_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        runtime = self.workdir / "authorization-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "authorization_task_path": str(source),
                    "authorization_task_sha256": pinned_digest,
                }
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(SCRIPTS / "stage-standing-task.py"),
                str(runtime),
                str(review_input),
                "authorization",
            ],
            check=False,
        )
        destination = review_input / "authorization-task.md"
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "# Approved authorization\n")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

        destination.unlink()
        source.write_text("# Changed after resolver\n", encoding="utf-8")
        self.assertEqual(
            subprocess.run(
                [
                    str(SCRIPTS / "stage-standing-task.py"),
                    str(runtime),
                    str(review_input),
                    "authorization",
                ],
                check=False,
            ).returncode,
            75,
        )
        self.assertFalse(destination.exists())

    def test_publication_validator_hashes_authorization_snapshot_nofollow(self) -> None:
        snapshot = self.workdir / "authorization-task.md"
        snapshot.write_text("approved\n", encoding="utf-8")
        expected = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        self.assertEqual(REVIEW_MODULE.sha256_regular_nofollow(snapshot), expected)

        link = self.workdir / "authorization-link.md"
        link.symlink_to(snapshot)
        with self.assertRaises(OSError):
            REVIEW_MODULE.sha256_regular_nofollow(link)

    def test_dirty_review_inputs_are_bound_to_captured_git_blobs(self) -> None:
        git_environment = DIRTY_STAGER_MODULE.clean_git_environment()
        self.assertEqual(git_environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(git_environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(git_environment["GIT_OPTIONAL_LOCKS"], "0")
        content = b"captured dirty bytes\n"
        oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        agents_git_dir = subprocess.check_output(
            ["git", "-C", str(self.agents), "rev-parse", "--absolute-git-dir"],
            text=True,
        ).strip()
        user_git_dir = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "--absolute-git-dir"],
            text=True,
        ).strip()
        runtime = self.workdir / "dirty-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_git_dir": agents_git_dir,
                    "user_git_dir": user_git_dir,
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                }
            ),
            encoding="utf-8",
        )
        pre = self.workdir / "dirty-pre.json"
        pre.write_text(
            json.dumps(
                {
                    "agents_vault": {
                        "local_commits": [],
                        "dirty_entries": [
                            {
                                "path": "tasks/dirty.md",
                                "git_blob_oid": oid,
                                "mode": "100644",
                            }
                        ]
                    },
                    "user_vault": {"local_commits": [], "dirty_entries": []},
                }
            ),
            encoding="utf-8",
        )
        review_input = self.workdir / "dirty-review-input"
        review_input.mkdir()
        (self.agents / "tasks" / "dirty.md").write_bytes(content)
        command = [
            str(SCRIPTS / "stage-dirty-review-inputs.py"),
            str(runtime),
            str(pre),
            str(review_input),
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        self.assertEqual(review_input.stat().st_mode & 0o777, 0o000)
        review_input.chmod(0o700)
        snapshot = review_input / "dirty-snapshots" / "agents" / "0000.blob"
        self.assertEqual(snapshot.read_bytes(), content)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        manifest_path = review_input / "dirty-snapshots.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["vaults"]["agents_vault"][0]["git_blob_oid"], oid)
        context = {
            "dirty_snapshot_manifest_file": str(manifest_path),
            "dirty_snapshot_manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
        }
        REVIEW_MODULE.validate_dirty_snapshots(context, json.loads(pre.read_text()))

        snapshot.write_text("substituted\n", encoding="utf-8")
        with self.assertRaises(REVIEW_MODULE.ReviewError):
            REVIEW_MODULE.validate_dirty_snapshots(context, json.loads(pre.read_text()))

        snapshot.write_bytes(content)
        agents_directory = snapshot.parent
        real_agents_directory = agents_directory.with_name("real-agents")
        agents_directory.rename(real_agents_directory)
        agents_directory.symlink_to(real_agents_directory, target_is_directory=True)
        with self.assertRaises(OSError):
            REVIEW_MODULE.validate_dirty_snapshots(context, json.loads(pre.read_text()))

    def test_network_git_keeps_process_cwd_outside_vault(self) -> None:
        """Use explicit Git metadata/work-tree arguments for transport commands."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr=""
        )
        for module in (PUSH_MODULE, FINALIZER_MODULE):
            with mock.patch.object(module.subprocess, "run", return_value=completed) as run:
                remote = module.remote_head(
                    "/vault/worktree", "ssh://git@example.invalid/repo", "/local/gitdir"
                )
            self.assertEqual(remote, "a" * 40)
            command = run.call_args.args[0]
            self.assertEqual(
                command[:3],
                [
                    "git",
                    "--git-dir=/local/gitdir",
                    "--work-tree=/vault/worktree",
                ],
            )
            self.assertNotIn("-C", command)

    def test_capture_and_review_snapshot_local_ahead_then_diverged(self) -> None:
        """Allow reviewable local-ahead history but classify divergence separately."""
        for repo in (self.agents, self.user):
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
                check=True,
            )
            (repo / "base.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)

        base = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()
        (self.user / "local.md").write_text("local\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", "local.md"], check=True)
        subprocess.run(
            ["git", "-C", str(self.user), "commit", "-q", "-m", "local only"],
            check=True,
        )
        local_head = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()
        runtime = self.workdir / "history-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "agents_git_dir": str(self.agents / ".git"),
                    "user_vault_root": str(self.user),
                    "user_git_dir": str(self.user / ".git"),
                }
            ),
            encoding="utf-8",
        )
        pre = self.workdir / "history-pre.json"
        with pre.open("w", encoding="utf-8") as output:
            subprocess.run(
                [
                    str(SCRIPTS / "capture-vault-state.py"),
                    "--include-local-history",
                    str(runtime),
                ],
                check=True,
                stdout=output,
                text=True,
            )
        state = json.loads(pre.read_text())
        user_state = state["user_vault"]
        self.assertEqual(user_state["history_relation"], "local_ahead")
        self.assertEqual(user_state["remote_head"], base)
        self.assertEqual(user_state["local_head"], local_head)
        self.assertEqual(
            [item["commit"] for item in user_state["local_commits"]], [local_head]
        )

        review_input = self.workdir / "history-review-input"
        review_input.mkdir()
        self.assertEqual(
            subprocess.run(
                [
                    str(SCRIPTS / "stage-dirty-review-inputs.py"),
                    str(runtime),
                    str(pre),
                    str(review_input),
                ],
                check=False,
            ).returncode,
            0,
        )
        review_input.chmod(0o700)
        manifest_path = review_input / "dirty-snapshots.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(
            manifest["local_commits"]["user_vault"][0]["commit"], local_head
        )
        REVIEW_MODULE.validate_dirty_snapshots(
            {
                "dirty_snapshot_manifest_file": str(manifest_path),
                "dirty_snapshot_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
            },
            state,
        )
        patch_path = review_input / "commit-snapshots" / "user" / "0000.patch"
        patch_path.write_bytes(patch_path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "local commit patch digest mismatch"
        ):
            REVIEW_MODULE.validate_dirty_snapshots(
                {
                    "dirty_snapshot_manifest_file": str(manifest_path),
                    "dirty_snapshot_manifest_sha256": hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                },
                state,
            )

        peer = self.root / "history-peer"
        subprocess.run(
            ["git", "clone", "-q", str(self.origins["user"]), str(peer)],
            check=True,
        )
        subprocess.run(["git", "-C", str(peer), "config", "user.name", "Peer"], check=True)
        subprocess.run(
            ["git", "-C", str(peer), "config", "user.email", "peer@example.invalid"],
            check=True,
        )
        (peer / "remote.md").write_text("remote\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(peer), "add", "remote.md"], check=True)
        subprocess.run(
            ["git", "-C", str(peer), "commit", "-q", "-m", "remote only"],
            check=True,
        )
        subprocess.run(["git", "-C", str(peer), "push", "-q", "origin", "main"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.user), "fetch", "origin",
                "refs/heads/main:refs/remotes/origin/main",
            ],
            check=True,
        )
        with pre.open("w", encoding="utf-8") as output:
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(runtime)],
                check=True,
                stdout=output,
                text=True,
            )
        diverged = json.loads(pre.read_text())["user_vault"]
        self.assertEqual(diverged["history_relation"], "diverged")
        self.assertEqual(diverged["local_commits"], [])

    def test_lightweight_capture_does_not_materialize_local_commit_patches(self) -> None:
        """Keep expensive local-only history work behind publication preflight."""
        for repo in (self.agents, self.user):
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
                check=True,
            )
            (repo / "base.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
        (self.user / "huge-history.bin").write_bytes(b"local history")
        subprocess.run(["git", "-C", str(self.user), "add", "huge-history.bin"], check=True)
        subprocess.run(["git", "-C", str(self.user), "commit", "-q", "-m", "local ahead"], check=True)

        with mock.patch.object(
            CAPTURE_MODULE,
            "local_commit_metadata",
            side_effect=AssertionError("history materialization ran before collection"),
        ) as history:
            state = CAPTURE_MODULE.capture(str(self.user))
        history.assert_not_called()
        self.assertEqual(state["history_relation"], "local_ahead")
        self.assertEqual(state["local_commits"], [])

    def test_local_committer_uses_explicit_git_paths_and_no_network_overrides(self) -> None:
        """Keep local mutation independent of a Vault process cwd or ambient Git config."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="main\n", stderr=""
        )
        with mock.patch.dict(
            os.environ,
            {"GIT_DIR": "/attacker/git", "GIT_ASKPASS": "/attacker/askpass"},
        ), mock.patch.object(COMMITTER_MODULE.subprocess, "run", return_value=completed) as run:
            COMMITTER_MODULE.git(
                "/vault/worktree",
                "/vault/gitdir",
                "branch",
                "--show-current",
                publisher_identity=("Fixture Publisher", "publisher@example.invalid"),
            )
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(
            command[:3],
            ["git", "--git-dir=/vault/gitdir", "--work-tree=/vault/worktree"],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], "/")
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("GIT_ASKPASS", environment)
        self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(environment["GIT_AUTHOR_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_AUTHOR_EMAIL"],
            "publisher@example.invalid",
        )
        self.assertEqual(environment["GIT_COMMITTER_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_COMMITTER_EMAIL"],
            "publisher@example.invalid",
        )

    def test_evidence_finalizer_uses_same_verified_publisher_identity(self) -> None:
        """Attribute the final evidence commit to the same GitHub account."""
        with mock.patch.dict(
            os.environ,
            {
                "GIT_AUTHOR_NAME": "Attacker",
                "GIT_AUTHOR_EMAIL": "attacker@example.invalid",
                "GIT_COMMITTER_NAME": "Attacker",
                "GIT_COMMITTER_EMAIL": "attacker@example.invalid",
            },
        ):
            environment = FINALIZER_MODULE.clean_environment(
                ("Fixture Publisher", "publisher@example.invalid")
            )
        self.assertEqual(environment["GIT_AUTHOR_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_AUTHOR_EMAIL"],
            "publisher@example.invalid",
        )
        self.assertEqual(environment["GIT_COMMITTER_NAME"], "Fixture Publisher")
        self.assertEqual(
            environment["GIT_COMMITTER_EMAIL"],
            "publisher@example.invalid",
        )

    def test_committers_reject_tampered_runtime_identity(self) -> None:
        """Apply the resolver's email grammar again immediately before mutation."""
        tampered = {
            "publisher_git_name": "Fixture Publisher",
            "publisher_git_email": "publisher@@example.invalid",
        }
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validated_publisher_identity(tampered)
        with self.assertRaises(FINALIZER_MODULE.FinalizationError):
            FINALIZER_MODULE.validated_publisher_identity(tampered)

    def test_finalizer_rejects_valid_identity_substitution_after_review(self) -> None:
        """Bind finalization identity to the reviewed publication context digest."""
        runtime = {
            "publisher_git_name": "Fixture Publisher",
            "publisher_git_email": "publisher@example.invalid",
        }
        pre = {"agents_vault": {"local_head": "a" * 40}}
        context_bytes = json.dumps(
            {"runtime": runtime, "pre_collection_state": pre},
            sort_keys=True,
        ).encode("utf-8")
        substituted = dict(runtime)
        substituted["publisher_git_email"] = "other@example.invalid"
        with self.assertRaises(FINALIZER_MODULE.FinalizationError):
            FINALIZER_MODULE.context_bound_inputs(
                substituted,
                pre,
                context_bytes,
                hashlib.sha256(context_bytes).hexdigest(),
            )

    def test_local_committer_creates_github_attributed_commit_metadata(self) -> None:
        """Write the configured identity into an actual isolated commit object."""
        repo = self.workdir / "identity-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "artifact.md").write_text("reviewed artifact\n", encoding="utf-8")
        COMMITTER_MODULE.git(str(repo), str(repo / ".git"), "add", "artifact.md")
        COMMITTER_MODULE.git(
            str(repo),
            str(repo / ".git"),
            "commit",
            "-q",
            "-m",
            "fixture",
            publisher_identity=("Fixture Publisher", "publisher@example.invalid"),
        )
        identity = COMMITTER_MODULE.git(
            str(repo),
            str(repo / ".git"),
            "show",
            "-s",
            "--format=%an%x00%ae%x00%cn%x00%ce",
            "HEAD",
        ).stdout.strip().split("\0")
        self.assertEqual(
            identity,
            [
                "Fixture Publisher",
                "publisher@example.invalid",
                "Fixture Publisher",
                "publisher@example.invalid",
            ],
        )

    def test_evidence_finalizer_creates_github_attributed_commit_metadata(self) -> None:
        """Write the final evidence identity into an actual isolated commit object."""
        repo = self.workdir / "evidence-identity-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "evidence.md").write_text("reviewed evidence\n", encoding="utf-8")
        FINALIZER_MODULE.git(str(repo), "add", "evidence.md")
        FINALIZER_MODULE.git(
            str(repo),
            "commit",
            "-q",
            "-m",
            "fixture evidence",
            publisher_identity=("Fixture Publisher", "publisher@example.invalid"),
        )
        identity = FINALIZER_MODULE.git(
            str(repo),
            "show",
            "-s",
            "--format=%an%x00%ae%x00%cn%x00%ce",
            "HEAD",
        ).stdout.strip().split("\0")
        self.assertEqual(
            identity,
            [
                "Fixture Publisher",
                "publisher@example.invalid",
                "Fixture Publisher",
                "publisher@example.invalid",
            ],
        )

    def test_local_committer_binds_inputs_and_complete_installed_scope(self) -> None:
        """Reject standalone JSON substitution and manifest-external dirty paths."""
        runtime = {"runtime": "reviewed"}
        pre = {"pre": "reviewed"}
        collection = {"collection": "reviewed"}
        plan = {"plan": "reviewed"}
        context = {
            "runtime": runtime,
            "pre_collection_state": pre,
            "verified_collection": collection,
            "artifact_plan": plan,
        }
        self.assertEqual(
            COMMITTER_MODULE.reviewed_inputs(
                context, runtime, pre, collection, plan
            ),
            (runtime, pre, collection, plan),
        )
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.reviewed_inputs(
                context, runtime, pre, {"collection": "substituted"}, plan
            )

        fixed = {
            "repo_root": "vault",
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "operation_in_progress": False,
            "git_control_sha256": "b" * 64,
        }
        empty = {
            **fixed,
            "dirty_lines": [],
            "dirty_paths": [],
            "dirty_entries": [],
            "dirty_digest": hashlib.sha256(b"").hexdigest(),
            "diff_snapshot_sha256": hashlib.sha256(b"").hexdigest(),
        }
        current = {
            **empty,
            "dirty_lines": ["?? artifact.md"],
            "dirty_paths": ["artifact.md"],
            "dirty_entries": [
                {"path": "artifact.md", "git_blob_oid": "c" * 40, "mode": "100644"}
            ],
        }
        COMMITTER_MODULE.validate_installed_scope(
            {"agents_vault": empty, "user_vault": empty},
            {"agents_vault": current, "user_vault": current},
            {"agents_vault": "artifact.md", "user_vault": "artifact.md"},
        )
        external = json.loads(json.dumps(current))
        external["dirty_paths"].append("external.md")
        external["dirty_entries"].append(
            {"path": "external.md", "git_blob_oid": "d" * 40, "mode": "100644"}
        )
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validate_installed_scope(
                {"agents_vault": empty, "user_vault": empty},
                {"agents_vault": external, "user_vault": current},
                {"agents_vault": "artifact.md", "user_vault": "artifact.md"},
            )

    def test_local_committer_rejects_unsafe_paths(self) -> None:
        """Reject absolute, traversal, and Obsidian-control paths."""
        for path in (
            "/absolute.md",
            "../escape.md",
            "a/../escape.md",
            ".obsidian/config",
        ):
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.safe_path(path)
        self.assertEqual(
            COMMITTER_MODULE.safe_path("reports/advisory.md"),
            "reports/advisory.md",
        )

    def test_local_committer_scans_binary_blobs_and_allows_deletion(self) -> None:
        """Reject a home path in raw bytes without treating deletion as a blob."""
        repo = self.agents
        git_dir = str(repo / ".git")
        (repo / "delete.md").write_text("delete me\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "delete.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "base",
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            binary_index = str(Path(temporary) / "binary.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=binary_index
            )
            binary_oid = COMMITTER_MODULE.write_blob(
                str(repo), git_dir, b"\x00" + os.fsencode(str(Path.home()))
            )
            COMMITTER_MODULE.update_index_entry(
                str(repo),
                git_dir,
                "binary.dat",
                ("100644", binary_oid),
                index_file=binary_index,
            )
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.scan_staged(
                    str(self.fake_gitleaks),
                    str(repo),
                    git_dir,
                    ["binary.dat"],
                    binary_index,
                )

            deletion_index = str(Path(temporary) / "deletion.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=deletion_index
            )
            COMMITTER_MODULE.update_index_entry(
                str(repo),
                git_dir,
                "delete.md",
                None,
                index_file=deletion_index,
            )
            COMMITTER_MODULE.scan_staged(
                str(self.fake_gitleaks),
                str(repo),
                git_dir,
                ["delete.md"],
                deletion_index,
            )

    def test_local_committer_allows_historical_home_path_but_rejects_new_text_path(
        self,
    ) -> None:
        """Only newly added home paths block reviewed text candidates."""
        repo = self.agents
        git_dir = str(repo / ".git")
        historical = repo / "historical.md"
        historical.write_text(
            f"historical evidence: {Path.home()}/old.log\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(repo), "add", "historical.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-q",
                "-m",
                "historical path fixture",
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            historical_index = str(Path(temporary) / "historical.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=historical_index
            )
            historical.write_text(
                historical.read_text(encoding="utf-8") + "reviewed update\n",
                encoding="utf-8",
            )
            COMMITTER_MODULE.git(
                str(repo),
                git_dir,
                "add",
                "--",
                "historical.md",
                index_file=historical_index,
            )
            COMMITTER_MODULE.scan_staged(
                str(self.fake_gitleaks),
                str(repo),
                git_dir,
                ["historical.md"],
                historical_index,
            )

            added_index = str(Path(temporary) / "added.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=added_index
            )
            historical.write_text(
                historical.read_text(encoding="utf-8")
                + f"new evidence: {Path.home()}/new.log\n",
                encoding="utf-8",
            )
            COMMITTER_MODULE.git(
                str(repo),
                git_dir,
                "add",
                "--",
                "historical.md",
                index_file=added_index,
            )
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.scan_staged(
                    str(self.fake_gitleaks),
                    str(repo),
                    git_dir,
                    ["historical.md"],
                    added_index,
                )

    def test_local_committer_malformed_input_emits_blocked_result(self) -> None:
        """Convert malformed early input into status 75 and structured JSON."""
        invalid = self.workdir / "invalid-committer-runtime.json"
        invalid.write_bytes(b"\xff")
        placeholder = self.workdir / "committer-placeholder.json"
        placeholder.write_text("{}", encoding="utf-8")
        output = self.workdir / "committer-result.json"
        result = subprocess.run(
            [
                str(SCRIPTS / "commit-reviewed-publication.py"),
                str(invalid),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "/missing-installer",
                "/missing-capture",
                "review-digest",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertEqual(json.loads(output.read_text())["outcome"], "blocked")
        self.assertNotIn("Traceback", result.stderr)

        runtime = {
            "agents_vault_root": str(self.agents),
            "agents_git_dir": str(self.agents / ".git"),
            "user_vault_root": str(self.user),
            "user_git_dir": str(self.user / ".git"),
        }
        invalid.write_text(json.dumps(runtime), encoding="utf-8")
        malformed_pre = self.workdir / "committer-list-pre.json"
        malformed_pre.write_text("[]", encoding="utf-8")
        result = subprocess.run(
            [
                str(SCRIPTS / "commit-reviewed-publication.py"),
                str(invalid),
                str(malformed_pre),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "/missing-installer",
                "/missing-capture",
                hashlib.sha256(placeholder.read_bytes()).hexdigest(),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertEqual(json.loads(output.read_text())["outcome"], "blocked")
        self.assertNotIn("Traceback", result.stderr)

    def test_local_committer_rejects_staged_only_before_head_changes(self) -> None:
        """Fail before commit when reviewed index bytes differ from the worktree."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        staged = repo / "staged.md"
        staged.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        staged.write_text("reviewed staged bytes\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        reviewed_oid = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", ":staged.md"], text=True
        ).strip()
        staged.write_text("base\n", encoding="utf-8")
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [
                {
                    "path": "staged.md",
                    "git_blob_oid": reviewed_oid,
                    "mode": "100644",
                }
            ],
            "commit_groups": [
                {
                    "message": "approved staged",
                    "paths": ["staged.md", "artifact.md"],
                }
            ],
        }
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validate_final_worktree(
                str(repo),
                str(repo / ".git"),
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
            )
        after = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertEqual(after, before)

        deleted = repo / "deleted.md"
        deleted.write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "deleted.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "tracked deletion"],
            check=True,
        )
        deletion_head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "-C", str(repo), "rm", "-q", "deleted.md"], check=True)
        deleted.write_text("tracked\n", encoding="utf-8")
        deletion_manifest = {
            "approved_dirty_entries": [
                {"path": "deleted.md", "git_blob_oid": None, "mode": None}
            ],
            "commit_groups": [
                {
                    "message": "approved deletion",
                    "paths": ["deleted.md", "artifact.md"],
                }
            ],
        }
        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.validate_final_worktree(
                str(repo),
                str(repo / ".git"),
                deletion_manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            deletion_head,
        )

    def test_local_committer_barrier_failure_keeps_head_unchanged(self) -> None:
        """Build commit objects first but never advance HEAD after a scope race."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        base = repo / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [],
            "commit_groups": [
                {"message": "publish artifact", "paths": ["artifact.md"]}
            ],
        }

        def reject_race() -> None:
            raise COMMITTER_MODULE.CommitError("fixture scope race")

        with self.assertRaises(COMMITTER_MODULE.CommitError):
            COMMITTER_MODULE.commit_groups(
                str(repo),
                str(repo / ".git"),
                str(self.fake_gitleaks),
                {"local_head": before, "dirty_digest": hashlib.sha256(b"").hexdigest()},
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
                self.workdir,
                before_update=reject_race,
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            before,
        )

    def test_fixed_fetch_pins_remote_refspec_and_environment(self) -> None:
        """Reject mutable remote/config behavior in the preflight transport."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with mock.patch.dict(os.environ, {"GIT_DIR": "/attacker/git"}), mock.patch.object(
            FETCH_MODULE.subprocess, "run", return_value=completed
        ) as run:
            FETCH_MODULE.fetch_main(
                "/vault/worktree", "/local/gitdir", "ssh://git@example.invalid/repo"
            )
        command = run.call_args.args[0]
        self.assertEqual(
            command[:3],
            ["git", "--git-dir=/local/gitdir", "--work-tree=/vault/worktree"],
        )
        self.assertIn("core.hooksPath=/dev/null", command)
        self.assertEqual(
            command[-2:],
            [
                "ssh://git@example.invalid/repo",
                "refs/heads/main:refs/remotes/origin/main",
            ],
        )
        self.assertNotIn("origin", command)
        self.assertNotIn("GIT_DIR", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(
            run.call_args.kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull
        )

    def test_fixed_fetch_invalid_utf8_uses_standard_failure_contract(self) -> None:
        """Convert malformed runtime bytes into status 75 without a traceback."""
        invalid = self.workdir / "invalid-fetch-runtime.json"
        invalid.write_bytes(b"\xff")
        result = subprocess.run(
            [str(SCRIPTS / "fetch-vault-main.py"), str(invalid)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("fixed fetch blocked:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_resolver_uses_catalog_and_relative_config(self) -> None:
        """Resolve roots and Git directories without tracked personal paths."""
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        context = json.loads(result.stdout)
        self.assertEqual(context["agents_vault_root"], str(self.agents.resolve()))
        self.assertEqual(context["standing_task_id"], "TSK-STANDING")
        self.assertEqual(context["publisher_git_name"], "Fixture Publisher")
        self.assertEqual(context["publisher_git_email"], "publisher@example.invalid")
        self.assertTrue(Path(context["agents_git_dir"]).is_absolute())

    def test_resolver_rejects_invalid_private_publisher_identity(self) -> None:
        """Fail closed before runtime context creation for malformed identity."""
        invalid = self.workdir / "invalid-publisher.local.env"
        invalid.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "PUBLISHER_GIT_EMAIL=publisher@example.invalid",
                "PUBLISHER_GIT_EMAIL=not-an-email",
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(invalid),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("invalid publisher Git email", result.stderr)

    def test_resolver_accepts_bound_absolute_gitdir_file(self) -> None:
        """Support the Vault layout while binding its indirection file."""
        marker = self.agents / ".git"
        detached = self.root / "agents-detached.git"
        marker.rename(detached)
        marker.write_text(f"gitdir: {detached}\n", encoding="utf-8")
        marker.chmod(0o644)
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        context = json.loads(result.stdout)
        self.assertEqual(context["agents_git_dir"], str(detached.resolve()))
        push_before = PUSH_MODULE.git_control_digest(str(self.agents))
        final_before = FINALIZER_MODULE.control_digest(str(self.agents))
        self.assertEqual(push_before, final_before)
        marker.chmod(0o600)
        push_after = PUSH_MODULE.git_control_digest(str(self.agents))
        final_after = FINALIZER_MODULE.control_digest(str(self.agents))
        self.assertEqual(push_after, final_after)
        self.assertNotEqual(push_before, push_after)

        marker.unlink()
        marker.symlink_to(detached, target_is_directory=True)
        rejected = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 78)
        self.assertIn("must not be a symlink", rejected.stderr)

        marker.unlink()
        for invalid_content in ("gitdir: relative.git\n", "not-a-gitdir\n"):
            marker.write_text(invalid_content, encoding="utf-8")
            rejected = subprocess.run(
                [
                    str(SCRIPTS / "resolve-runtime-context.py"),
                    str(self.config),
                    str(self.workdir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 78)

    def test_resolver_rejects_symlink_standing_task(self) -> None:
        """Reject a deferred evidence target that cannot be safely updated."""
        standing = self.agents / "tasks" / "standing.md"
        standing.unlink()
        standing.symlink_to("auth.md")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("standing task", result.stderr)

    def test_resolver_import_failure_returns_standard_status(self) -> None:
        """Convert missing catalog loader modules into the status-78 contract."""
        module = self.saihai / "directory_paths.py"
        module.rename(self.saihai / "directory_paths.missing")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("runtime context resolution failed", result.stderr)

    def test_resolver_malformed_config_returns_standard_status(self) -> None:
        """Convert shlex parse failures into the status-78 contract."""
        malformed = self.workdir / "malformed.local.env"
        malformed.write_text("SAIHAI_CHECKOUT_ROOT='unterminated\n", encoding="utf-8")
        result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(malformed),
                str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 78)
        self.assertIn("runtime context resolution failed", result.stderr)

    def test_capture_includes_staged_only_path_and_index_blob(self) -> None:
        """Bind an index-only change even when the worktree matches HEAD."""
        for repo in (self.agents, self.user):
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "config",
                    "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
            (repo / "tracked.md").write_text("head\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "branch", "-M", "main"], check=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
        tracked = self.agents / "tracked.md"
        tracked.write_text("staged\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.agents), "add", "tracked.md"], check=True
        )
        tracked.write_text("head\n", encoding="utf-8")
        context = self.workdir / "capture-context.json"
        context.write_text(
            json.dumps(
                {
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(SCRIPTS / "capture-vault-state.py"), str(context)],
            check=True,
            capture_output=True,
            text=True,
        )
        state = json.loads(result.stdout)["agents_vault"]
        expected_oid = subprocess.check_output(
            ["git", "-C", str(self.agents), "rev-parse", ":tracked.md"],
            text=True,
        ).strip()
        self.assertEqual(state["dirty_paths"], ["tracked.md"])
        self.assertEqual(state["dirty_entries"][0]["git_blob_oid"], expected_oid)
        dangling = self.agents / "dangling-link"
        dangling.symlink_to("missing-target")
        result = subprocess.run(
            [str(SCRIPTS / "capture-vault-state.py"), str(context)],
            check=True,
            capture_output=True,
            text=True,
        )
        state = json.loads(result.stdout)["agents_vault"]
        link_entry = next(
            entry for entry in state["dirty_entries"] if entry["path"] == "dangling-link"
        )
        expected_link_oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=b"missing-target",
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        self.assertEqual(link_entry["mode"], "120000")
        self.assertEqual(link_entry["git_blob_oid"], expected_link_oid)

        hook = self.agents / ".git" / "hooks" / "fixture"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o644)
        before_mode = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        hook.chmod(0o755)
        after_mode = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )["agents_vault"]["git_control_sha256"]
        self.assertNotEqual(before_mode, after_mode)

    def test_capture_invalid_utf8_context_fails_closed(self) -> None:
        """Return status 75 instead of leaking a decode traceback."""
        context = self.workdir / "invalid-context.json"
        context.write_bytes(b"\xff")
        result = subprocess.run(
            [str(SCRIPTS / "capture-vault-state.py"), str(context)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("Vault state capture failed", result.stderr)

    def test_collection_validator_checks_hash_and_staging(self) -> None:
        """Accept verified staged files and reject a hash mismatch."""
        staging = self.workdir / "staging"
        staging.mkdir()
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text("summary", encoding="utf-8")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        advisory.write_text(
            f"- 入力ニュース: {summary.name} "
            f"(same-run SHA-256: {digest(summary)})\n",
            encoding="utf-8",
        )
        result_path = self.workdir / "collection.json"
        payload = {
            "daily_pipeline_status": "complete",
            "run_id": "20260731T040000+0900",
            "summary_path": str(summary),
            "summary_sha256": digest(summary),
            "advisory_path": str(advisory),
            "advisory_sha256": digest(advisory),
            "notification_result": "none",
            "vault_artifacts_complete": True,
            "next_action": None,
        }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        command = [
            str(SCRIPTS / "validate-collection-result.py"),
            str(result_path),
            str(staging),
            "20260731T040000+0900",
            "0",
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        payload["summary_sha256"] = "0" * 64
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)

    def test_collection_validator_rejects_unsafe_advisory_references(self) -> None:
        """Reject private paths, inconsistent hashes/names, and duplicate fields."""
        staging = self.workdir / "staging"
        staging.mkdir()
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text("summary", encoding="utf-8")
        summary_hash = hashlib.sha256(summary.read_bytes()).hexdigest()
        result_path = self.workdir / "collection.json"
        payload = {
            "daily_pipeline_status": "complete",
            "run_id": "20260731T040000+0900",
            "summary_path": str(summary),
            "summary_sha256": summary_hash,
            "advisory_path": str(advisory),
            "advisory_sha256": "",
            "vault_artifacts_complete": True,
        }
        command = [
            str(SCRIPTS / "validate-collection-result.py"),
            str(result_path),
            str(staging),
            payload["run_id"],
            "0",
        ]

        unsafe_references = {
            "private home": f"- 入力ニュース: {Path.home()}/news.md\n",
            "staging path": f"- 入力ニュース: {summary}\n",
            "wrong basename": (
                "- 入力ニュース: SUMMARY-IT-NEWS-2026-07-30.md "
                f"(same-run SHA-256: {summary_hash})\n"
            ),
            "wrong digest": (
                f"- 入力ニュース: {summary.name} "
                f"(same-run SHA-256: {'0' * 64})\n"
            ),
            "duplicate field": (
                f"- 入力ニュース: {summary.name} "
                f"(same-run SHA-256: {summary_hash})\n"
                f"- 入力ニュース: {summary.name} "
                f"(same-run SHA-256: {summary_hash})\n"
            ),
        }
        for label, content in unsafe_references.items():
            with self.subTest(label=label):
                advisory.write_text(content, encoding="utf-8")
                payload["advisory_sha256"] = hashlib.sha256(
                    advisory.read_bytes()
                ).hexdigest()
                result_path.write_text(json.dumps(payload), encoding="utf-8")
                validation = subprocess.run(
                    command, check=False, capture_output=True, text=True
                )
                self.assertEqual(validation.returncode, 75)
                self.assertIn("collection validation failed", validation.stderr)

    def test_installer_places_only_declared_roles(self) -> None:
        """Install summary and advisory below their configured Vault roots."""
        staging = self.workdir / "staging"
        staging.mkdir()
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text("summary", encoding="utf-8")
        advisory.write_text("advisory", encoding="utf-8")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        context = {
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "it_news_archive_relative": "10_Prompt",
            "advisory_archive_relative": "03-Contexts/Reports/Security",
        }
        collection = {
            "summary_path": str(summary),
            "summary_sha256": digest(summary),
            "advisory_path": str(advisory),
            "advisory_sha256": digest(advisory),
        }
        context_path = self.workdir / "context.json"
        collection_path = self.workdir / "collection.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        collection_path.write_text(json.dumps(collection), encoding="utf-8")
        plan_path = self.workdir / "plan.json"
        with plan_path.open("w", encoding="utf-8") as output:
            subprocess.run(
                [
                    str(SCRIPTS / "install-verified-artifacts.py"),
                    "--plan",
                    str(context_path),
                    str(collection_path),
                ],
                check=True,
                stdout=output,
                text=True,
            )
        result = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        installed = json.loads(result.stdout)
        self.assertTrue(Path(installed["summary_target"]).is_file())
        self.assertTrue(Path(installed["advisory_target"]).is_file())

        context["it_news_archive_relative"] = ""
        context_path.write_text(json.dumps(context), encoding="utf-8")
        rejected = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                "--plan",
                str(context_path),
                str(collection_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 75)

        invalid_context = self.workdir / "invalid-install-context.json"
        invalid_context.write_bytes(b"\xff")
        rejected = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                "--plan",
                str(invalid_context),
                str(collection_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 75)

    def test_evidence_preparer_invalid_utf8_fails_closed(self) -> None:
        """Convert malformed runtime JSON into the status-75 contract."""
        invalid = self.workdir / "invalid-evidence-runtime.json"
        invalid.write_bytes(b"\xff")
        placeholder = self.workdir / "placeholder.json"
        placeholder.write_text("{}", encoding="utf-8")
        output = self.workdir / "evidence-plan.json"
        result = subprocess.run(
            [
                str(SCRIPTS / "prepare-publication-evidence.py"),
                str(invalid),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "run-id",
                "2026-07-31T04:00:00+09:00",
                hashlib.sha256(placeholder.read_bytes()).hexdigest(),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("evidence preparation failed", result.stderr)

    def test_evidence_preparer_rejects_changed_review_bytes(self) -> None:
        """Bind evidence generation to the exact review already used for push."""
        placeholder = self.workdir / "evidence-placeholder.json"
        placeholder.write_text("{}", encoding="utf-8")
        output = self.workdir / "changed-review-plan.json"
        result = subprocess.run(
            [
                str(SCRIPTS / "prepare-publication-evidence.py"),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                str(placeholder),
                "run-id",
                "2026-07-31T04:00:00+09:00",
                hashlib.sha256(b"different review").hexdigest(),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 75)
        self.assertIn("approved review digest mismatch", result.stderr)

    def test_evidence_preparer_rejects_correlated_context_substitution(self) -> None:
        """Keep a valid alternate runtime+context pair outside the approved run."""
        approved_context = json.dumps(
            {
                "runtime": {
                    "publisher_git_name": "Fixture Publisher",
                    "publisher_git_email": "publisher@example.invalid",
                }
            },
            sort_keys=True,
        ).encode("utf-8")
        review = {
            "publication_context_sha256": hashlib.sha256(
                approved_context
            ).hexdigest()
        }
        substituted_context = approved_context.replace(
            b"publisher@example.invalid", b"other-pub@example.invalid"
        )
        with self.assertRaises(EVIDENCE_MODULE.EvidenceError):
            EVIDENCE_MODULE.approved_context_digest(review, substituted_context)

    def test_evidence_block_is_inserted_inside_canonical_section(self) -> None:
        """Place evidence before the next peer heading instead of at task EOF."""
        task = self.agents / "tasks" / "standing.md"
        marker = b"<!-- vault-change-publisher:fixture -->"
        block = "\n#### Daily publication evidence — fixture\n".encode() + marker + b"\n"
        EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
            self.agents, Path("tasks/standing.md"), block
        )
        text = task.read_text(encoding="utf-8")
        self.assertLess(text.index("Daily publication evidence"), text.index("## Reviews"))
        self.assertEqual(text.count("vault-change-publisher:fixture"), 1)

    def test_evidence_block_requires_one_canonical_section(self) -> None:
        """Fail closed when the insertion anchor is absent or ambiguous."""
        task = self.agents / "tasks" / "standing.md"
        marker = b"<!-- vault-change-publisher:fixture -->"
        block = "\n#### Daily publication evidence — fixture\n".encode() + marker + b"\n"
        for content in (
            "# Standing\n",
            "### Vault Publication Evidence\n### Vault Publication Evidence\n",
        ):
            with self.subTest(content=content):
                task.write_text(content, encoding="utf-8")
                with self.assertRaises(EVIDENCE_MODULE.EvidenceError):
                    EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                        self.agents, Path("tasks/standing.md"), block
                    )

    def test_evidence_block_ignores_headings_inside_fences(self) -> None:
        """Treat ATX-like lines in backtick and tilde fences as content."""
        task = self.agents / "tasks" / "standing.md"
        task.write_text(
            "# Standing\n\n### Vault Publication Evidence\n\n"
            "```md\n## example inside fence\n```\n"
            "~~~\n### another example\n~~~\n"
            "section tail\n\n## Reviews\n",
            encoding="utf-8",
        )
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
            self.agents, Path("tasks/standing.md"), block
        )
        text = task.read_text(encoding="utf-8")
        self.assertLess(text.index("~~~\nsection tail"), text.index("Daily publication"))
        self.assertLess(text.index("Daily publication"), text.index("## Reviews"))

    def test_evidence_block_rejects_unterminated_fences_without_mutation(self) -> None:
        """Do not insert evidence into an unterminated backtick or tilde fence."""
        task = self.agents / "tasks" / "standing.md"
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        for fence in ("```md", "~~~"):
            with self.subTest(fence=fence):
                original = (
                    "# Standing\n\n### Vault Publication Evidence\n\n"
                    f"{fence}\nunclosed example\n## Reviews\n"
                )
                task.write_text(original, encoding="utf-8")
                with self.assertRaisesRegex(
                    EVIDENCE_MODULE.EvidenceError, "unterminated fenced block"
                ):
                    EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                        self.agents, Path("tasks/standing.md"), block
                    )
                self.assertEqual(task.read_text(encoding="utf-8"), original)

    def test_evidence_target_content_guards_fail_closed(self) -> None:
        """Reject malformed, oversized, growing, and oversized-output targets."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()

        task.write_bytes(b"\xff")
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "UTF-8"):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )

        with task.open("wb") as output:
            output.truncate(EVIDENCE_MODULE.MAX_TASK_BYTES + 1)
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "size"):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )

        task.write_text("### Vault Publication Evidence\n", encoding="utf-8")
        one_megabyte = b"x" * (1024 * 1024)
        with mock.patch.object(
            EVIDENCE_MODULE.os, "read", side_effect=[one_megabyte] * 11
        ):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "grew"):
                EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                    self.agents, relative, block
                )

        prefix = "### Vault Publication Evidence\n"
        padding = "x" * (
            EVIDENCE_MODULE.MAX_TASK_BYTES - len(prefix.encode()) - 1
        )
        task.write_text(prefix + padding, encoding="utf-8")
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "updated"):
            EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                self.agents, relative, block
            )

    def test_evidence_atomic_failure_preserves_original_target(self) -> None:
        """Leave the canonical task unchanged after partial or zero writes."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        original = task.read_bytes()
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        for side_effect in ([55, OSError("disk full")], [0]):
            with self.subTest(side_effect=side_effect):
                task.write_bytes(original)
                with mock.patch.object(
                    EVIDENCE_MODULE.os, "write", side_effect=side_effect
                ):
                    with self.assertRaises((OSError, EVIDENCE_MODULE.EvidenceError)):
                        EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                            self.agents, relative, block
                        )
                self.assertEqual(task.read_bytes(), original)
                self.assertEqual(list(task.parent.glob("*.evidence-*.tmp")), [])

    def test_evidence_concurrent_mutation_is_not_overwritten(self) -> None:
        """Detect a target edit before replacement and preserve that edit."""
        task = self.agents / "tasks" / "standing.md"
        relative = Path("tasks/standing.md")
        original = task.read_text(encoding="utf-8")
        concurrent = original + "concurrent edit\n"
        block = (
            "\n#### Daily publication evidence — fixture\n"
            "<!-- vault-change-publisher:fixture -->\n"
        ).encode()
        real_stat = EVIDENCE_MODULE.os.stat

        def mutate_then_stat(*args, **kwargs):
            task.write_text(concurrent, encoding="utf-8")
            return real_stat(*args, **kwargs)

        with mock.patch.object(
            EVIDENCE_MODULE.os, "stat", side_effect=mutate_then_stat
        ):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "changed"):
                EVIDENCE_MODULE.insert_under_evidence_section_no_follow(
                    self.agents, relative, block
                )
        self.assertEqual(task.read_text(encoding="utf-8"), concurrent)

    def test_fixed_pusher_validates_and_pushes_exact_heads(self) -> None:
        """Push both validated local main heads outside the Codex process."""
        runtime = {
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_remote_url": str(self.origins["agents"]),
            "user_remote_url": str(self.origins["user"]),
            "gitleaks_bin": str(self.fake_gitleaks),
            "gitleaks_version": "fixture-gitleaks 1.0",
        }
        for repo, key in ((self.agents, "agents"), (self.user, "user")):
            (repo / "initial.md").write_text("initial\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "config",
                    "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )
        runtime_path = self.workdir / "fixed-runtime.json"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        pre_path = self.workdir / "fixed-pre.json"
        with pre_path.open("w", encoding="utf-8") as output:
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(runtime_path)],
                check=True,
                stdout=output,
                text=True,
            )
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        commits = {}
        empty_digest = hashlib.sha256(b"").hexdigest()
        for key, repo in (("agents_vault", self.agents), ("user_vault", self.user)):
            (repo / "published.md").write_text("published\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "published.md"], check=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "publish"],
                check=True,
            )
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            commits[key] = {
                "commit_status": "complete",
                "commit_hashes": [head],
                "pre_local_head": pre[key]["local_head"],
                "local_head": head,
                "pre_dirty_digest": pre[key]["dirty_digest"],
                "post_dirty_digest": empty_digest,
                "clean": True,
            }
        commit_result = {
            "outcome": "ready_to_push",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": str(self.user / "published.md"),
            "advisory_path": str(self.agents / "published.md"),
            "notification_result": "none",
            "agents_vault": commits["agents_vault"],
            "user_vault": commits["user_vault"],
            "evidence_finalization_commit": None,
            "next_action": None,
        }
        commit_path = self.workdir / "fixed-commit.json"
        final_path = self.workdir / "fixed-final.json"
        context_path = self.workdir / "fixed-context.json"
        review_path = self.workdir / "fixed-review.json"
        plan_path = self.workdir / "fixed-plan.json"
        context_path.write_text("{}", encoding="utf-8")
        context_digest = hashlib.sha256(context_path.read_bytes()).hexdigest()
        artifact_hash = hashlib.sha256(b"published\n").hexdigest()
        manifest = lambda root: {
            "repo_root": str(root),
            "task_id": "TSK-AUTH",
            "owned_paths": ["published.md"],
            "excluded_paths": [],
            "approved_diff_snapshot_sha256": hashlib.sha256(b"").hexdigest(),
            "approved_existing_commits": [],
            "approved_dirty_entries": [],
            "reviewed_artifacts": [
                {
                    "role": "agents_security_advisory"
                    if root == self.agents
                    else "user_it_news_summary",
                    "source_sha256": artifact_hash,
                    "target_path": "published.md",
                }
            ],
            "validation_evidence": {
                "file_guard": "passed",
                "secret_scan": "passed",
                "secret_scan_tool": "gitleaks",
                "secret_scan_tool_version": "fixture-gitleaks 1.0",
                "reviewed_snapshot_sha256": hashlib.sha256(b"").hexdigest(),
                "reviewed_history_sha256": pre[
                    "agents_vault" if root == self.agents else "user_vault"
                ]["history_snapshot_sha256"],
            },
            "review_or_validation_status": "quality_ok",
            "commit_required": True,
            "unrelated_dirty_paths": [],
            "commit_groups": [{"message": "publish", "paths": ["published.md"]}],
            "evidence_finalization": None,
        }
        review_payload = {
            "outcome": "approved",
            "publication_context_sha256": context_digest,
            "agents_vault": manifest(self.agents),
            "user_vault": manifest(self.user),
            "next_action": None,
        }
        plan_path.write_text(
            json.dumps(
                {
                    "summary_target": str(self.user / "published.md"),
                    "advisory_target": str(self.agents / "published.md"),
                }
            ),
            encoding="utf-8",
        )
        commit_path.write_text(json.dumps(commit_result), encoding="utf-8")
        command = [
            str(SCRIPTS / "push-committed-heads.py"),
            str(runtime_path),
            str(pre_path),
            str(commit_path),
            str(final_path),
            "0",
            str(context_path),
            str(review_path),
            str(plan_path),
            "review-digest-placeholder",
        ]
        rejected_review = json.loads(json.dumps(review_payload))
        for key in ("agents_vault", "user_vault"):
            rejected_review[key]["commit_groups"][0]["paths"] = ["approved.md"]
        review_path.write_text(json.dumps(rejected_review), encoding="utf-8")
        command[-1] = hashlib.sha256(review_path.read_bytes()).hexdigest()
        rejected = subprocess.run(command, check=False)
        self.assertEqual(rejected.returncode, 75)
        for key, repo in (("agents_vault", self.agents), ("user_vault", self.user)):
            remote = subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            self.assertEqual(remote, pre[key]["remote_head"])

        wrong_paths = json.loads(json.dumps(commit_result))
        wrong_paths["summary_path"] = str(self.user / "wrong.md")
        commit_path.write_text(json.dumps(wrong_paths), encoding="utf-8")
        review_path.write_text(json.dumps(review_payload), encoding="utf-8")
        command[-1] = hashlib.sha256(review_path.read_bytes()).hexdigest()
        rejected = subprocess.run(command, check=False)
        self.assertEqual(rejected.returncode, 75)
        for key, repo in (("agents_vault", self.agents), ("user_vault", self.user)):
            remote = subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            self.assertEqual(remote, pre[key]["remote_head"])

        bad_status = list(command)
        bad_status[5] = "not-a-number"
        rejected = subprocess.run(bad_status, check=False)
        self.assertEqual(rejected.returncode, 75)
        self.assertTrue(final_path.is_file())

        commit_path.write_text(json.dumps(commit_result), encoding="utf-8")
        result = subprocess.run(command, check=False)
        self.assertEqual(result.returncode, 0)
        final = json.loads(final_path.read_text(encoding="utf-8"))
        self.assertEqual(final["outcome"], "partial_publication")
        self.assertEqual(
            final["agents_vault"]["local_head"],
            final["agents_vault"]["remote_head"],
        )

    def test_pusher_scans_remote_to_final_and_rejects_remote_race(self) -> None:
        """Fix both secret-scan coverage and the pre-push remote race gate."""
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(PUSH_MODULE.subprocess, "run", return_value=completed) as run:
            PUSH_MODULE.scan_commits("/tools/gitleaks", "/vault", "a" * 40, "b" * 40)
        self.assertIn(
            f"{'a' * 40}..{'b' * 40}",
            run.call_args.args[0],
        )
        pre = {
            "agents_vault": {"remote_head": "a" * 40},
            "user_vault": {"remote_head": "b" * 40},
        }
        PUSH_MODULE.require_unchanged_remote_heads("a" * 40, "b" * 40, pre)
        with self.assertRaisesRegex(PUSH_MODULE.PushError, "remote main moved"):
            PUSH_MODULE.require_unchanged_remote_heads("c" * 40, "b" * 40, pre)

    def test_review_rejects_forbidden_path_in_local_only_history(self) -> None:
        """Apply the .obsidian guard to commits that already exist locally."""
        state = {
            "repo_root": str(self.agents),
            "branch": "main",
            "upstream": "origin/main",
            "history_relation": "local_ahead",
            "operation_in_progress": False,
            "local_commits": [
                {
                    "commit": "a" * 40,
                    "parents": ["b" * 40],
                    "tree": "c" * 40,
                    "message": "unsafe",
                    "changed_paths": [".obsidian/workspace.json"],
                    "patch_sha256": "d" * 64,
                }
            ],
            "dirty_paths": [],
        }
        with self.assertRaisesRegex(REVIEW_MODULE.ReviewError, "forbidden .obsidian"):
            REVIEW_MODULE.validate_manifest(
                {
                    "repo_root": str(self.agents),
                    "task_id": "TSK-AUTH",
                    "approved_existing_commits": state["local_commits"],
                },
                state,
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "e" * 64,
                    "target_path": str(self.agents / "advisory.md"),
                },
                None,
                "fixture-gitleaks 1.0",
            )

    def test_partial_evidence_keeps_initial_and_finalization_commits(self) -> None:
        """Do not discard already-published local-ahead hashes on partial recovery."""
        initial_hash = "1" * 40
        finalization_hash = "2" * 40
        initial = {
            "agents_vault": {
                "commit_hashes": [initial_hash],
                "local_head": initial_hash,
                "remote_head": initial_hash,
            }
        }
        git_results = [
            subprocess.CompletedProcess([], 0, finalization_hash + "\n", ""),
            subprocess.CompletedProcess([], 0, finalization_hash + "\n", ""),
        ]
        with mock.patch.object(FINALIZER_MODULE, "git", side_effect=git_results), \
             mock.patch.object(FINALIZER_MODULE, "dirty_status", return_value=(True, "")), \
             mock.patch.object(FINALIZER_MODULE, "remote_head", return_value=initial_hash):
            result = FINALIZER_MODULE.partial_result(
                {
                    "agents_vault_root": str(self.agents),
                    "agents_remote_url": "fixture",
                    "agents_git_dir": str(self.agents / ".git"),
                },
                {},
                initial,
                "fixture failure",
            )
        self.assertEqual(
            result["agents_vault"]["commit_hashes"],
            [initial_hash, finalization_hash],
        )

    def test_review_rejects_forbidden_obsidian_scope(self) -> None:
        """Reject forbidden Vault metadata before any write-capable phase."""
        state = {
            "repo_root": str(self.agents),
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "operation_in_progress": False,
            "dirty_paths": [".obsidian/workspace.json"],
            "dirty_entries": [
                {
                    "path": ".obsidian/workspace.json",
                    "git_blob_oid": "b" * 40,
                    "mode": "100644",
                }
            ],
            "diff_snapshot_sha256": "c" * 64,
        }
        with self.assertRaises(REVIEW_MODULE.ReviewError):
            REVIEW_MODULE.validate_manifest(
                {"repo_root": str(self.agents), "task_id": "TSK-AUTH"},
                state,
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "d" * 64,
                    "target_path": str(self.agents / "advisory.md"),
                },
                str(self.agents / "tasks" / "standing.md"),
                "fixture-gitleaks 1.0",
            )

    def test_review_rejects_standing_task_as_manifest_identity(self) -> None:
        """Keep recurring evidence ownership distinct from authorization."""
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "Task Change Manifest identity mismatch"
        ):
            REVIEW_MODULE.validate_manifest(
                {"repo_root": str(self.agents), "task_id": "TSK-STANDING"},
                {},
                str(self.agents),
                "TSK-AUTH",
                {},
                None,
                "fixture-gitleaks 1.0",
            )

    def test_review_prompt_selects_authorization_task_for_both_manifests(self) -> None:
        """Expose the validator's identity contract to the read-only reviewer."""
        prompt = (SKILL_ROOT / "assets" / "daily-it-news.review.prompt.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("publication_context.authorization_task_id", prompt)
        self.assertIn("both manifests", prompt)
        self.assertIn("must not replace the authorization identity", prompt)

    def test_review_canonicalizes_both_containment_paths(self) -> None:
        """Accept a target expressed through a symlinked Vault path."""
        real_root = self.root / "real-vault"
        real_root.mkdir()
        alias = self.root / "vault-alias"
        alias.symlink_to(real_root, target_is_directory=True)
        self.assertEqual(
            REVIEW_MODULE.relative_target(
                str(real_root), str(alias / "reports" / "artifact.md")
            ),
            "reports/artifact.md",
        )

    def test_staged_evidence_digest_rebinds_reviewed_bytes(self) -> None:
        """Detect a race that substitutes evidence bytes during staging."""
        repo = self.agents
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        target = repo / "evidence.md"
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True
        )
        target.write_text("reviewed\n", encoding="utf-8")
        reviewed = FINALIZER_MODULE.diff_digest(str(repo), "evidence.md")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.md"], check=True)
        self.assertEqual(
            FINALIZER_MODULE.cached_diff_digest(str(repo), "evidence.md"),
            reviewed,
        )
        subprocess.run(
            ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", "evidence.md"],
            check=True,
        )
        target.write_text("substituted\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.md"], check=True)
        self.assertNotEqual(
            FINALIZER_MODULE.cached_diff_digest(str(repo), "evidence.md"),
            reviewed,
        )

    def test_scope_validator_rejects_group_split_and_symlink_artifact(self) -> None:
        """Reject split meaning units and non-regular committed artifacts."""
        repo = self.agents
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True
        )
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        commits = []
        for filename in ("a.md", "b.md"):
            (repo / filename).write_text(filename + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "approved group"],
                check=True,
            )
            commits.append(
                subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
                ).strip()
            )
        manifest = {
            "commit_groups": [
                {"message": "approved group", "paths": ["a.md", "b.md"]}
            ],
            "reviewed_artifacts": [],
            "approved_existing_commits": [],
            "approved_dirty_entries": [],
        }
        with self.assertRaises(PUSH_MODULE.PushError):
            PUSH_MODULE.validate_scope(
                str(repo),
                {"remote_head": before, "local_head": before, "local_commits": []},
                {"local_head": commits[-1], "commit_hashes": commits},
                manifest,
            )
        second_before = commits[-1]
        (repo / "artifact.md").symlink_to("outside-target")
        subprocess.run(["git", "-C", str(repo), "add", "artifact.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "artifact"],
            check=True,
        )
        symlink_commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        symlink_manifest = {
            "commit_groups": [
                {"message": "artifact", "paths": ["artifact.md"]}
            ],
            "reviewed_artifacts": [
                {
                    "target_path": "artifact.md",
                    "source_sha256": hashlib.sha256(b"outside-target").hexdigest(),
                }
            ],
            "approved_existing_commits": [],
            "approved_dirty_entries": [],
        }
        with self.assertRaises(PUSH_MODULE.PushError):
            PUSH_MODULE.validate_scope(
                str(repo),
                {
                    "remote_head": second_before,
                    "local_head": second_before,
                    "local_commits": [],
                },
                {
                    "local_head": symlink_commit,
                    "commit_hashes": [symlink_commit],
                },
                symlink_manifest,
            )
        third_before = symlink_commit
        dirty_path = repo / "approved-dirty.md"
        dirty_path.write_text("reviewed bytes\n", encoding="utf-8")
        approved_oid = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "hash-object",
                "--path=approved-dirty.md",
                "--",
                "approved-dirty.md",
            ],
            text=True,
        ).strip()
        dirty_path.write_text("substituted bytes\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "approved-dirty.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "dirty"],
            check=True,
        )
        substituted_commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        dirty_manifest = {
            "commit_groups": [
                {"message": "dirty", "paths": ["approved-dirty.md"]}
            ],
            "reviewed_artifacts": [],
            "approved_existing_commits": [],
            "approved_dirty_entries": [
                {
                    "path": "approved-dirty.md",
                    "git_blob_oid": approved_oid,
                    "mode": "100644",
                }
            ],
        }
        with self.assertRaises(PUSH_MODULE.PushError):
            PUSH_MODULE.validate_scope(
                str(repo),
                {
                    "remote_head": third_before,
                    "local_head": third_before,
                    "local_commits": [],
                },
                {
                    "local_head": substituted_commit,
                    "commit_hashes": [substituted_commit],
                },
                dirty_manifest,
            )

    def test_dedicated_runner_completes_separated_publication(self) -> None:
        """Complete collection, two reviews, local commits, and fixed pushes."""
        runtime = self.root / "runtime"
        runtime.mkdir()
        for source in (
            SKILL_ROOT / "assets" / "run-daily-it-news-vulnerability-check.sh",
            SKILL_ROOT / "assets" / "daily-it-news.collect.prompt.md",
            SKILL_ROOT / "assets" / "daily-it-news.review.prompt.md",
            SKILL_ROOT / "assets" / "daily-it-news.publish.prompt.md",
            SKILL_ROOT / "assets" / "daily-it-news.evidence-review.prompt.md",
            SKILL_ROOT / "references" / "collection-result.schema.json",
            SKILL_ROOT / "references" / "publication-review-result.schema.json",
            SKILL_ROOT / "references" / "publication-commit-result.schema.json",
            SKILL_ROOT / "references" / "evidence-review-result.schema.json",
            SKILL_ROOT / "references" / "automation-result.schema.json",
            SCRIPTS / "resolve-runtime-context.py",
            SCRIPTS / "fetch-vault-main.py",
            SCRIPTS / "capture-vault-state.py",
            SCRIPTS / "validate-collection-result.py",
            SCRIPTS / "install-verified-artifacts.py",
            SCRIPTS / "commit-reviewed-publication.py",
            SCRIPTS / "validate-publication-review.py",
            SCRIPTS / "push-committed-heads.py",
            SCRIPTS / "prepare-publication-evidence.py",
            SCRIPTS / "commit-push-publication-evidence.py",
            SCRIPTS / "git_diff_digest.py",
            SCRIPTS / "prepare-codex-output-schema.py",
            SCRIPTS / "validate-canonical-result.py",
            SCRIPTS / "stage-standing-task.py",
            SCRIPTS / "stage-dirty-review-inputs.py",
            SCRIPTS / "interpret-automation-result.sh",
        ):
            shutil.copy2(source, runtime / source.name)

        finalizer = runtime / "commit-push-publication-evidence.py"
        real_finalizer = runtime / "commit-push-publication-evidence.real.py"
        finalizer.rename(real_finalizer)
        finalizer.write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
real=Path(__file__).with_name("commit-push-publication-evidence.real.py")
completed=subprocess.run([str(real),*sys.argv[1:]],check=False)
if completed.returncode == 0 and os.environ.get("FAKE_CANONICAL_INVALID_FINAL") == "1":
    output=Path(sys.argv[6])
    result=json.loads(output.read_text())
    result["unexpected_property"]=True
    output.write_text(json.dumps(result))
raise SystemExit(completed.returncode)
""",
            encoding="utf-8",
        )
        finalizer.chmod(0o755)

        for repo, key in ((self.agents, "agents"), (self.user, "user")):
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
            (repo / "initial.md").write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "initial.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)

        fake_codex = runtime / "fake-codex.py"
        fake_codex.write_text(
            """#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys
from pathlib import Path
args=sys.argv[1:]
output=Path(args[args.index("--output-last-message")+1])
context=json.loads(args[-1].split("Runtime context JSON:\\n",1)[1])
schema=Path(args[args.index("--output-schema")+1]).name
schema_document=json.loads(Path(args[args.index("--output-schema")+1]).read_text())
schema_encoded=json.dumps(schema_document,sort_keys=True)
assert all(f'"{keyword}"' not in schema_encoded for keyword in ("allOf","if","then","else","oneOf","uniqueItems"))
with (output.parent/"invocations.log").open("a") as log:
    stage="collection" if "--search" in args else ("review" if schema=="publication-review-result.schema.json" else ("evidence_review" if schema=="evidence-review-result.schema.json" else "publication"))
    log.write(stage+"\\n")
if "--search" in args:
    staging=Path(context["collection_output_root"])
    standing=Path(context["standing_task"])
    assert standing.parent == staging
    assert standing.name == "standing-task.md"
    assert standing.read_text()
    run_date=context["started_at"][:10]
    summary=staging/f"SUMMARY-IT-NEWS-{run_date}.md"
    advisory=staging/f"Personal-Vulnerability-Advisory-{run_date}.md"
    summary.write_text("summary "+context["run_id"])
    digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    advisory.write_text(f"- 入力ニュース: {summary.name} (same-run SHA-256: {digest(summary)})\\n")
    result={"daily_pipeline_status":"complete","run_id":context["run_id"],"summary_path":str(summary),"summary_sha256":digest(summary),"advisory_path":str(advisory),"advisory_sha256":digest(advisory),"notification_result":"none","vault_artifacts_complete":True,"next_action":None}
    if os.environ.get("FAKE_CANONICAL_INVALID_COLLECTION") == "1":
        result["next_action"]="must be null for a complete result"
elif stage=="review":
    publication=context["publication_context"]
    authorization=Path(publication["authorization_task"])
    assert authorization.name == "authorization-task.md"
    assert authorization.parent.name == "review-input"
    assert hashlib.sha256(authorization.read_bytes()).hexdigest() == publication["authorization_task_sha256"]
    dirty_manifest=Path(publication["dirty_snapshot_manifest_file"])
    assert hashlib.sha256(dirty_manifest.read_bytes()).hexdigest() == publication["dirty_snapshot_manifest_sha256"]
    snapshot_manifest=json.loads(dirty_manifest.read_text())
    assert snapshot_manifest["version"] == 2
    runtime_context=publication["runtime"]
    pre=publication["pre_collection_state"]
    plan=context["artifact_plan"]
    artifacts=publication["publication_manifest"]["artifact_manifest"]
    def rel(root,target):
        return os.path.relpath(target,root)
    def manifest(kind):
        if kind=="agents":
            state=pre["agents_vault"]; root=runtime_context["agents_vault_root"]; item=artifacts["advisory"]; target=plan["advisory_target"]
            evidence=rel(root,publication["standing_task"])
        else:
            state=pre["user_vault"]; root=runtime_context["user_vault_root"]; item=artifacts["summary"]; target=plan["summary_target"]
            evidence=None
        target_rel=rel(root,target)
        initial=sorted(set(state["dirty_paths"]+[target_rel]))
        existing_paths=[path for commit in state["local_commits"] for path in commit["changed_paths"]]
        owned=sorted(set(initial+existing_paths+([evidence] if evidence else [])))
        return {"repo_root":root,"task_id":publication["authorization_task_id"],"owned_paths":owned,"excluded_paths":[],"approved_diff_snapshot_sha256":state["diff_snapshot_sha256"],"approved_existing_commits":state["local_commits"],"approved_dirty_entries":state["dirty_entries"],"reviewed_artifacts":[{"role":item["role"],"source_sha256":item["sha256"],"target_path":target_rel}],"validation_evidence":{"file_guard":"passed","secret_scan":"passed","secret_scan_tool":"gitleaks","secret_scan_tool_version":runtime_context["gitleaks_version"],"reviewed_snapshot_sha256":state["diff_snapshot_sha256"],"reviewed_history_sha256":state["history_snapshot_sha256"]},"review_or_validation_status":"quality_ok","commit_required":True,"unrelated_dirty_paths":[],"commit_groups":[{"message":"fixture publication","paths":initial}],"evidence_finalization":({"target_path":evidence,"template":"daily_publication_v1"} if evidence else None)}
    result={"outcome":"approved","publication_context_sha256":context["publication_context_sha256"],"agents_vault":manifest("agents"),"user_vault":manifest("user"),"next_action":None}
elif stage=="publication":
    publication=context["publication_context"]
    runtime_context=publication["runtime"]
    pre=publication["pre_collection_state"]
    approved=context["approved_review"]
    installed=json.loads(subprocess.check_output([publication["installer"],publication["runtime_context_file"],publication["collection_result_file"],publication["artifact_plan_file"]],text=True))
    def publish(key,root):
        manifest=approved[key]
        for group in manifest["commit_groups"]:
            subprocess.run(["git","-C",root,"add","--",*group["paths"]],check=True)
            subprocess.run(["git","-C",root,"commit","-q","-m",group["message"]],check=True)
        head=subprocess.check_output(["git","-C",root,"rev-parse","HEAD"],text=True).strip()
        state=pre[key]
        commits=subprocess.check_output(["git","-C",root,"rev-list","--reverse",f"{state['local_head']}..{head}"],text=True).splitlines()
        return {"commit_status":"complete","commit_hashes":commits,"pre_local_head":state["local_head"],"local_head":head,"pre_dirty_digest":state["dirty_digest"],"post_dirty_digest":hashlib.sha256(b"").hexdigest(),"clean":True}
    verified=publication["verified_collection"]
    result={"outcome":"ready_to_push","phase":"local_commit","daily_pipeline_status":"complete","summary_path":installed["summary_target"],"advisory_path":installed["advisory_target"],"notification_result":"none","agents_vault":publish("agents_vault",runtime_context["agents_vault_root"]),"user_vault":publish("user_vault",runtime_context["user_vault_root"]),"evidence_finalization_commit":None,"next_action":None}
else:
    plan=context["evidence_plan"]
    publication=context["publication_context"]
    evidence_diff=subprocess.check_output(
        ["git","-C",publication["runtime"]["agents_vault_root"],"diff","--",plan["target_path"]],
        text=True,
    )
    payload_lines=[line[1:] for line in evidence_diff.splitlines() if line.startswith('+{')]
    assert len(payload_lines) == 1
    evidence_payload=json.loads(payload_lines[0])
    assert set(evidence_payload) == {
        "run_id","publication_context_sha256","agents_vault","user_vault",
        "summary_repo_path","advisory_repo_path",
    }
    result={"outcome":"approved","target_path":plan["target_path"],"evidence_diff_sha256":plan["evidence_diff_sha256"],"publication_context_sha256":plan["publication_context_sha256"],"review_status":"quality_ok","next_action":None}
output.write_text(json.dumps(result))
""",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        (runtime / "automation.local.env").write_text(
            self.config.read_text(encoding="utf-8").replace(
                "CODEX_BIN=/usr/bin/true", f"CODEX_BIN={fake_codex}"
            ),
            encoding="utf-8",
        )
        ahead = self.root / "ahead-clone"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--branch",
                "main",
                str(self.origins["user"]),
                str(ahead),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(ahead), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(ahead),
                "config",
                "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        (ahead / "ahead.md").write_text("ahead\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(ahead), "add", "ahead.md"], check=True)
        subprocess.run(
            ["git", "-C", str(ahead), "commit", "-q", "-m", "ahead"], check=True
        )
        subprocess.run(["git", "-C", str(ahead), "push", "-q", "origin", "main"], check=True)

        blocked = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )
        self.assertEqual(blocked.returncode, 75)
        blocked_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(blocked_logs), 1)
        self.assertEqual(
            blocked_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection"],
        )
        self.assertIn(
            "phase=publication_preflight",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        subprocess.run(
            ["git", "-C", str(self.user), "merge", "--ff-only", "origin/main"],
            check=True,
            capture_output=True,
        )
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        invalid_result = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_CANONICAL_INVALID_COLLECTION": "1",
            },
        )
        self.assertEqual(invalid_result.returncode, 75)
        invalid_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(invalid_logs), 1)
        self.assertEqual(
            invalid_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection"],
        )
        self.assertIn(
            "phase=collection",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        (self.user / "local-ahead.md").write_text("local ahead\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.user), "add", "local-ahead.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.user), "commit", "-q", "-m", "local ahead"],
            check=True,
        )
        local_ahead_commit = subprocess.check_output(
            ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
        ).strip()

        result = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )
        self.assertEqual(result.returncode, 0)
        invocation_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(invocation_logs), 1)
        self.assertEqual(
            invocation_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection", "review", "evidence_review"],
        )
        status_files = list(runtime.glob("last-status.txt"))
        self.assertEqual(len(status_files), 1)
        self.assertIn("semantic_status=success", status_files[0].read_text())
        publication_results = list(runtime.glob("logs/**/publication-result.json"))
        self.assertEqual(len(publication_results), 1)
        publication_result = json.loads(publication_results[0].read_text())
        self.assertIn(
            local_ahead_commit, publication_result["user_vault"]["commit_hashes"]
        )
        self.assertEqual(
            publication_result["user_vault"]["local_head"],
            publication_result["user_vault"]["remote_head"],
        )

        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()
        invalid_final = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_CANONICAL_INVALID_FINAL": "1",
            },
        )
        self.assertEqual(invalid_final.returncode, 75)
        self.assertIn(
            "semantic_status=process_error",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
