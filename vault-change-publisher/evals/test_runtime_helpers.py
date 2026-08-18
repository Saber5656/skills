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
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parent
SOURCE_CATALOG = REPO_ROOT / "summarize-it-news" / "references" / "it-news-sources.json"
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
MODE_SPEC = importlib.util.spec_from_file_location(
    "determine_publication_modes", SCRIPTS / "determine-publication-modes.py"
)
assert MODE_SPEC and MODE_SPEC.loader
MODE_MODULE = importlib.util.module_from_spec(MODE_SPEC)
MODE_SPEC.loader.exec_module(MODE_MODULE)
COLLECTION_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_collection_result", SCRIPTS / "validate-collection-result.py"
)
assert COLLECTION_VALIDATOR_SPEC and COLLECTION_VALIDATOR_SPEC.loader
COLLECTION_VALIDATOR_MODULE = importlib.util.module_from_spec(COLLECTION_VALIDATOR_SPEC)
COLLECTION_VALIDATOR_SPEC.loader.exec_module(COLLECTION_VALIDATOR_MODULE)
import isolated_git_transport as TRANSPORT_MODULE


def source_coverage_markdown() -> str:
    """Build a valid complete source-audit table from the tracked catalog."""
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    lines = [
        "## 確認済みサイト一覧",
        "",
        "| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |",
        "|---|---:|---|---|---|---:|---|",
    ]
    for index, source in enumerate(catalog["sources"]):
        url = source["feed_url"] or source["page_url"]
        method = "RSS" if source["feed_url"] else "公開ページ"
        if index == 0:
            status, count, reason = "取得済み", 1, "fixture記事確認"
        elif index == len(catalog["sources"]) - 1:
            status, count, reason = "アクセス制約", 0, "robotsで取得禁止"
        else:
            status, count, reason = "対象期間記事なし", 0, "fixture確認"
        lines.append(
            f"| {source['name']} | {source['tier']} | {status} | "
            f"{method} | {url} | {count} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def write_source_manifest(root: Path) -> Path:
    """Create deterministic collector evidence matching the positive fixture."""
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    source_root = root / "source-inputs"
    source_root.mkdir()
    sources = []
    for index, source in enumerate(catalog["sources"]):
        url = source["feed_url"] or source["page_url"]
        method = "rss" if source["feed_url"] else "public_page"
        if index == len(catalog["sources"]) - 1:
            sources.append({
                "name": source["name"], "tier": source["tier"],
                "status": "access_constraint",
                "method": method,
                "requested_url": url,
                "final_url": url,
                "constraint": "robots",
                "http_status": None,
                "robots_url": f"https://{url.split('/')[2]}/robots.txt",
                "robots_sha256": "a" * 64,
                "attempts": [
                    {
                        "method": attempt_method,
                        "url": attempt_url,
                        "status": "access_constraint",
                        "reason": "robots_disallowed",
                        "requested_url": attempt_url,
                        "final_url": attempt_url,
                        "constraint": "robots",
                        "http_status": None,
                        "robots_url": f"https://{attempt_url.split('/')[2]}/robots.txt",
                        "robots_sha256": "a" * 64,
                    }
                    for attempt_method, attempt_url in (
                        ("rss", source["feed_url"]),
                        ("public_page", source["page_url"]),
                    )
                    if attempt_url
                ],
            })
        else:
            extract_file = f"source-{index}.extract.json"
            published = "2026-07-31T00:00:00Z" if index == 0 else "2026-07-01T00:00:00Z"
            (source_root / extract_file).write_text(
                json.dumps({
                    "format": "feed" if method == "rss" else "html_links",
                    "entries": [{"url": f"{url}?fixture={index}", "published": published}],
                }),
                encoding="utf-8",
            )
            evidence = {
                "name": source["name"], "tier": source["tier"], "status": "fetched",
                "method": method, "final_url": url, "attempts": [],
                "extract_file": extract_file, "extracted_entry_count": 1,
                "jst_window_start": "2026-07-25",
                "jst_window_end": "2026-07-31",
                "jst_window_item_count": 1 if index == 0 else 0,
            }
            sources.append(evidence)
    path = source_root / "source-manifest.json"
    path.write_text(json.dumps({
        "catalog_sha256": hashlib.sha256(SOURCE_CATALOG.read_bytes()).hexdigest(),
        "sources": sources,
    }), encoding="utf-8")
    return path


def write_verified_resolutions(root: Path) -> Path:
    """Create an empty verified fallback set for the robots-only fixture."""
    path = root / "verified-source-resolutions.json"
    path.write_text(json.dumps({"version": 1, "resolutions": [], "date_evidence": []}), encoding="utf-8")
    return path


def write_minimal_coverage_fixture(
    root: Path,
    *,
    extract_entries: list[dict[str, object]],
    evidence_updates: Optional[dict[str, object]] = None,
    resolutions: Optional[list[dict[str, object]]] = None,
    date_evidence: Optional[list[dict[str, object]]] = None,
    status: str = "取得済み",
    method: str = "RSS",
    count: int = 1,
    confirmed_url: Optional[str] = None,
    reason: str = "fixture",
    run_date: date = date(2026, 7, 31),
) -> tuple[str, Path, Path, Path]:
    """Create one-source sealed coverage evidence for focused validator tests."""
    catalog = root / "catalog.json"
    manifest = root / "source-manifest.json"
    verified = root / "verified.json"
    extract = root / "source.extract.json"
    source_url = "https://example.test/feed"
    catalog.write_text(
        json.dumps({
            "version": 1,
            "sources": [{
                "name": "Fixture News",
                "tier": 1,
                "feed_url": source_url,
                "page_url": "https://example.test/news",
            }],
        }),
        encoding="utf-8",
    )
    extract.write_text(
        json.dumps({
            "format": "feed" if method == "RSS" else "html_links",
            "entries": extract_entries,
        }),
        encoding="utf-8",
    )
    evidence: dict[str, object] = {
        "name": "Fixture News",
        "tier": 1,
        "status": "fetched",
        "method": "rss",
        "final_url": source_url,
        "extract_file": extract.name,
        "extracted_entry_count": len(extract_entries),
        "attempts": [],
        "jst_window_start": (run_date - timedelta(days=6)).isoformat(),
        "jst_window_end": run_date.isoformat(),
        "jst_window_item_count": sum(
            run_date - timedelta(days=6) <= published <= run_date
            for published in (
                COLLECTION_VALIDATOR_MODULE.parse_publication_date(item.get("published"))
                for item in extract_entries
            )
            if published is not None
        ),
    }
    evidence.update(evidence_updates or {})
    manifest.write_text(
        json.dumps({
            "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "sources": [evidence],
        }),
        encoding="utf-8",
    )
    verified.write_text(
        json.dumps({
            "version": 1,
            "resolutions": resolutions or [],
            "date_evidence": date_evidence or [],
        }),
        encoding="utf-8",
    )
    summary = (
        "## 確認済みサイト一覧\n\n"
        "| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |\n"
        "|---|---:|---|---|---|---:|---|\n"
        f"| Fixture News | 1 | {status} | {method} | {confirmed_url or source_url} | {count} | {reason} |\n"
    )
    return summary, catalog, manifest, verified


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
            projected["$defs"]["validationEvidence"]["properties"]
            ["file_guard"]["enum"],
            ["passed", "blocked"],
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

    def test_publication_review_root_next_action_tracks_blocked_mode(self) -> None:
        """Deferred cleanup alone keeps an approved review terminally actionable."""
        review = {
            "outcome": "approved",
            "agents_vault": {"publication_mode": "sweep"},
            "user_vault": {"publication_mode": "own_only"},
            "next_action": None,
        }
        schema = json.loads(
            (SKILL_ROOT / "references" / "publication-review-result.schema.json")
            .read_text(encoding="utf-8")
        )
        mode_contract = schema["allOf"][1]
        CANONICAL_MODULE.validate(review, mode_contract, schema)
        REVIEW_MODULE.validate_root_contract(review)

        review["next_action"] = "remediate deferred residual later"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(review, mode_contract, schema)
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "unexpected next action"
        ):
            REVIEW_MODULE.validate_root_contract(review)

        review["user_vault"]["publication_mode"] = "blocked"
        CANONICAL_MODULE.validate(review, mode_contract, schema)
        REVIEW_MODULE.validate_root_contract(review)
        review["next_action"] = None
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(review, mode_contract, schema)
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "requires a concrete next action"
        ):
            REVIEW_MODULE.validate_root_contract(review)

    def test_canonical_publication_schemas_enforce_terminal_state(self) -> None:
        """Reject success/ready labels that do not contain publishable commits."""
        automation_schema = json.loads(
            (SKILL_ROOT / "references" / "automation-result.schema.json")
            .read_text(encoding="utf-8")
        )
        deferred = {"agents_vault": [], "user_vault": []}
        published_vault = {
            "commit_status": "complete",
            "commit_hashes": ["a" * 40],
            "push_status": "complete",
            "local_head": "a" * 40,
            "remote_head": "a" * 40,
            "clean": False,
            "publication_mode": "own_only",
            "deferred_cleanup": [],
        }
        success = {
            "outcome": "success",
            "phase": "evidence_finalization",
            "daily_pipeline_status": "complete",
            "summary_path": "summary.md",
            "advisory_path": "advisory.md",
            "notification_result": None,
            "agents_vault": published_vault,
            "user_vault": published_vault,
            "publication_mode": {
                "agents_vault": "own_only", "user_vault": "own_only"
            },
            "deferred_cleanup": deferred,
            "evidence_finalization_commit": "b" * 40,
            "next_action": None,
        }
        CANONICAL_MODULE.validate(success, automation_schema, automation_schema)
        invalid_success = json.loads(json.dumps(success))
        invalid_success["user_vault"]["commit_hashes"] = []
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                invalid_success, automation_schema, automation_schema
            )

        commit_schema = json.loads(
            (SKILL_ROOT / "references" / "publication-commit-result.schema.json")
            .read_text(encoding="utf-8")
        )
        ready_vault = {
            "commit_status": "complete",
            "commit_hashes": ["c" * 40],
            "pre_local_head": "d" * 40,
            "local_head": "c" * 40,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": "f" * 64,
            "clean": False,
            "publication_mode": "own_only",
            "deferred_cleanup": [],
        }
        blocked_vault = {
            "commit_status": "not_started",
            "commit_hashes": [],
            "pre_local_head": "1" * 40,
            "local_head": "1" * 40,
            "pre_dirty_digest": "2" * 64,
            "post_dirty_digest": "2" * 64,
            "clean": False,
            "publication_mode": "blocked",
            "deferred_cleanup": [],
        }
        ready = {
            "outcome": "ready_to_push",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": None,
            "advisory_path": "advisory.md",
            "notification_result": None,
            "agents_vault": ready_vault,
            "user_vault": blocked_vault,
            "publication_mode": {
                "agents_vault": "own_only", "user_vault": "blocked"
            },
            "deferred_cleanup": deferred,
            "evidence_finalization_commit": None,
            "next_action": "repair blocked User Vault",
        }
        CANONICAL_MODULE.validate(ready, commit_schema, commit_schema)
        invalid_ready = json.loads(json.dumps(ready))
        invalid_ready["agents_vault"] = blocked_vault
        invalid_ready["publication_mode"]["agents_vault"] = "blocked"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(invalid_ready, commit_schema, commit_schema)

        no_op_ready = json.loads(json.dumps(ready))
        for key in ("agents_vault", "user_vault"):
            no_op_ready[key] = {
                **blocked_vault,
                "commit_status": "not_required",
                "publication_mode": "own_only",
            }
            no_op_ready["publication_mode"][key] = "own_only"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(no_op_ready, commit_schema, commit_schema)

        no_op_success = json.loads(json.dumps(success))
        for key in ("agents_vault", "user_vault"):
            no_op_success[key]["commit_status"] = "not_required"
            no_op_success[key]["commit_hashes"] = []
            no_op_success[key]["push_status"] = "not_required"
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(
                no_op_success, automation_schema, automation_schema
            )
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
        self.assertEqual(git_environment["LC_ALL"], "C")
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

    def test_residual_guard_defers_added_home_path_but_allows_historical_path(
        self,
    ) -> None:
        """Constrain unsafe residuals before review without flagging unchanged history."""
        for repo in (self.agents, self.user):
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(repo), "config", "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
        historical = self.agents / "historical.md"
        historical.write_text(
            f"historical evidence: {Path.home()}/old.log\n"
            f"historical CR evidence:\r{Path.home()}/old-cr.log\n",
            encoding="utf-8",
        )
        peer = self.user / "peer.md"
        peer.write_text("peer base\n", encoding="utf-8")
        for repo, path in ((self.agents, historical), (self.user, peer)):
            subprocess.run(["git", "-C", str(repo), "add", path.name], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "base"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"],
                check=True,
            )

        historical.write_bytes(historical.read_bytes() + b"safe reviewed update\n")
        unsafe = self.agents / "unsafe.md"
        unsafe.write_text(
            f"++ new machine evidence:\r{Path.home()}/new.log\n", encoding="utf-8"
        )
        unsafe_binary = self.agents / "unsafe.bin"
        unsafe_binary.write_bytes(b"\0++" + os.fsencode(str(Path.home())) + b"/binary")
        peer.write_text("peer base\npeer safe update\n", encoding="utf-8")
        pre = {
            "agents_vault": CAPTURE_MODULE.capture(str(self.agents)),
            "user_vault": CAPTURE_MODULE.capture(str(self.user)),
        }
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        destination = self.workdir / "guarded-dirty-review"
        destination.mkdir()
        manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        agents_entries = {
            entry["path"]: entry for entry in manifest["vaults"]["agents_vault"]
        }
        self.assertEqual(
            agents_entries["historical.md"]["materialization_status"], "available"
        )
        self.assertEqual(
            agents_entries["unsafe.md"]["materialization_status"], "deferred"
        )
        self.assertEqual(
            agents_entries["unsafe.md"]["materialization_reason"],
            "dirty_entry_added_machine_home_path",
        )
        self.assertIsNone(agents_entries["unsafe.md"]["snapshot"])
        self.assertEqual(
            agents_entries["unsafe.bin"]["materialization_status"], "deferred"
        )
        self.assertEqual(
            agents_entries["unsafe.bin"]["materialization_reason"],
            "dirty_entry_added_machine_home_path",
        )
        self.assertIsNone(agents_entries["unsafe.bin"]["snapshot"])
        self.assertEqual(
            manifest["vaults"]["user_vault"][0]["materialization_status"],
            "available",
        )
        manifest_path = destination / "dirty-snapshots.json"
        REVIEW_MODULE.validate_dirty_snapshots(
            {
                "dirty_snapshot_manifest_file": str(manifest_path),
                "dirty_snapshot_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
            },
            pre,
        )

    def test_residual_guard_defers_gitleaks_rejection(self) -> None:
        """Convert a deterministic residual secret rejection into deferred input."""
        content = b"++prefix\rnew residual secret fixture\n"
        oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        path = self.agents / "secret.md"
        path.write_bytes(content)
        head = subprocess.run(
            ["git", "-C", str(self.agents), "mktree"],
            input="",
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(self.agents), "commit-tree", head],
            input="empty\n",
            text=True,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "Fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            },
        ).stdout.strip()
        pre = {
            "agents_vault": {
                "local_head": commit,
                "dirty_entries": [
                    {"path": "secret.md", "git_blob_oid": oid, "mode": "100644"}
                ],
                "local_commits": [],
            },
            "user_vault": {"dirty_entries": [], "local_commits": []},
        }
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        destination = self.workdir / "secret-guard-review"
        destination.mkdir()
        with mock.patch.object(
            DIRTY_STAGER_MODULE, "gitleaks_rejects", return_value=True
        ) as scanner:
            manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        self.assertIn(
            b"++prefix\rnew residual secret fixture", scanner.call_args.args[1]
        )
        entry = manifest["vaults"]["agents_vault"][0]
        self.assertEqual(entry["materialization_status"], "deferred")
        self.assertEqual(
            entry["materialization_reason"], "dirty_entry_secret_scan_rejected"
        )

    def test_network_git_keeps_process_cwd_outside_vault(self) -> None:
        """Use an isolated Git directory rather than the Vault control plane."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr=""
        )
        for module in (PUSH_MODULE, FINALIZER_MODULE):
            with mock.patch.object(
                module, "run_transport", return_value=completed
            ) as run:
                remote = module.remote_head(
                    "/vault/worktree", "ssh://git@example.invalid/repo", "/local/gitdir"
                )
            self.assertEqual(remote, "a" * 40)
            command = run.call_args.args
            self.assertEqual(
                command[:2],
                (
                    "/local/gitdir",
                    "ls-remote",
                ),
            )
            self.assertNotIn("/vault/worktree", command)

    def test_transport_timeout_kills_the_process_group(self) -> None:
        """Bound every network Git call and reap its complete process group."""
        transport = object.__new__(TRANSPORT_MODULE.IsolatedGitTransport)
        transport.git_dir = Path("/isolated/transport.git")
        transport.environment = {}
        transport.timeout = 1
        process = mock.MagicMock()
        process.pid = 4242
        process.returncode = -9
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["git", "push"], 1),
            ("", ""),
        ]
        with mock.patch.object(
            TRANSPORT_MODULE.subprocess, "Popen", return_value=process
        ) as popen, mock.patch.object(
            TRANSPORT_MODULE.os, "killpg"
        ) as killpg:
            with self.assertRaisesRegex(
                TRANSPORT_MODULE.TransportError, "exceeded 1 second"
            ):
                transport.run("push", "remote", "a" * 40 + ":refs/heads/main")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertIn("core.fsmonitor=false", popen.call_args.args[0])
        self.assertIn("credential.helper=", popen.call_args.args[0])
        killpg.assert_called_once_with(4242, TRANSPORT_MODULE.signal.SIGKILL)

    def test_local_git_helpers_disable_repo_fsmonitor(self) -> None:
        """Do not execute repo-local fsmonitor through Git or pinned gitleaks."""
        tracked = self.user / "initial.md"
        tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", "initial.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.user),
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "base",
            ],
            check=True,
        )
        marker = self.workdir / "fsmonitor-executed"
        monitor = self.workdir / "malicious-fsmonitor.sh"
        monitor.write_text(
            f"#!/bin/sh\n/usr/bin/touch '{marker}'\nexit 1\n",
            encoding="utf-8",
        )
        monitor.chmod(0o755)
        subprocess.run(
            [
                "git", "-C", str(self.user), "config", "--local",
                "core.fsmonitor", str(monitor),
            ],
            check=True,
        )
        PUSH_MODULE.dirty_digest(str(self.user))
        tracked.write_text("changed\n", encoding="utf-8")
        FINALIZER_MODULE.diff_digest(str(self.user), "initial.md")

        gitleaks_git = self.workdir / "gitleaks-that-invokes-git"
        gitleaks_git.write_text(
            "#!/bin/sh\n"
            "[ -z \"${GITLEAKS_CONFIG+x}\" ] || exit 99\n"
            "repo=\n"
            "for argument in \"$@\"; do repo=$argument; done\n"
            "/usr/bin/git -C \"$repo\" status --porcelain >/dev/null\n",
            encoding="utf-8",
        )
        gitleaks_git.chmod(0o755)
        index_file = str(self.user / ".git" / "index")
        with mock.patch.dict(
            os.environ, {"GITLEAKS_CONFIG": "/attacker/gitleaks.toml"}
        ):
            COMMITTER_MODULE.scan_staged(
                str(gitleaks_git), str(self.user), str(self.user / ".git"),
                ["initial.md"], index_file,
            )
            FINALIZER_MODULE.scan_staged(
                str(gitleaks_git), str(self.user), index_file
            )
            head = subprocess.check_output(
                ["git", "-C", str(self.user), "rev-parse", "HEAD"], text=True
            ).strip()
            PUSH_MODULE.scan_commits(str(gitleaks_git), str(self.user), head, head)
        self.assertFalse(marker.exists())

    def test_unmaterializable_dirty_entry_defers_only_its_vault(self) -> None:
        """Keep a symlink residual inert while another Vault remains reviewable."""
        agents_link = self.agents / "unsafe-link"
        agents_link.symlink_to("outside-target")
        user_file = self.user / "reviewable.md"
        user_file.write_text("reviewable\n", encoding="utf-8")
        link_oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "--stdin"],
            input=b"outside-target",
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        user_oid = subprocess.run(
            ["git", "-C", str(self.user), "hash-object", "--stdin"],
            input=user_file.read_bytes(),
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        runtime = self.workdir / "deferred-runtime.json"
        runtime.write_text(
            json.dumps(
                {
                    "agents_git_dir": str(self.agents / ".git"),
                    "user_git_dir": str(self.user / ".git"),
                    "agents_vault_root": str(self.agents),
                    "user_vault_root": str(self.user),
                }
            ),
            encoding="utf-8",
        )
        state_digest = hashlib.sha256(b"").hexdigest()
        pre_value = {
            "agents_vault": {
                "repo_root": str(self.agents),
                "history_relation": "equal",
                "local_commits": [],
                "dirty_paths": ["unsafe-link"],
                "dirty_entries": [
                    {"path": "unsafe-link", "git_blob_oid": link_oid, "mode": "120000"}
                ],
                "diff_snapshot_sha256": state_digest,
                "history_snapshot_sha256": state_digest,
            },
            "user_vault": {
                "repo_root": str(self.user),
                "history_relation": "equal",
                "local_commits": [],
                "dirty_paths": ["reviewable.md"],
                "dirty_entries": [
                    {"path": "reviewable.md", "git_blob_oid": user_oid, "mode": "100644"}
                ],
                "diff_snapshot_sha256": state_digest,
                "history_snapshot_sha256": state_digest,
            },
        }
        pre = self.workdir / "deferred-pre.json"
        pre.write_text(json.dumps(pre_value), encoding="utf-8")
        destination = self.workdir / "deferred-review"
        destination.mkdir()
        self.assertEqual(
            subprocess.run(
                [
                    str(SCRIPTS / "stage-dirty-review-inputs.py"),
                    str(runtime), str(pre), str(destination),
                ],
                check=False,
            ).returncode,
            0,
        )
        destination.chmod(0o700)
        materialized = json.loads(
            (destination / "dirty-snapshots.json").read_text(encoding="utf-8")
        )
        agents_materialized = materialized["vaults"]["agents_vault"]
        user_materialized = materialized["vaults"]["user_vault"]
        self.assertEqual(agents_materialized[0]["materialization_status"], "deferred")
        self.assertEqual(user_materialized[0]["materialization_status"], "available")

        artifact = self.agents / "artifact.md"
        common = {
            "repo_root": str(self.agents),
            "task_id": "TSK-AUTH",
            "core_review_status": "quality_ok",
            "approved_diff_snapshot_sha256": state_digest,
            "approved_existing_commits": [],
            "reviewed_artifacts": [
                {"role": "agents_security_advisory", "source_sha256": "a" * 64, "target_path": "artifact.md"}
            ],
            "validation_evidence": {
                "file_guard": "passed",
                "secret_scan": "passed",
                "secret_scan_tool": "gitleaks",
                "secret_scan_tool_version": "fixture",
                "reviewed_snapshot_sha256": state_digest,
                "reviewed_history_sha256": state_digest,
            },
            "review_or_validation_status": "quality_ok",
            "evidence_finalization": None,
        }
        own_only = {
            **common,
            "publication_mode": "own_only",
            "residual_review_status": "deferred",
            "owned_paths": ["artifact.md"],
            "excluded_paths": ["unsafe-link"],
            "deferred_cleanup": [
                {"path": "unsafe-link", "reason": "snapshot unavailable"}
            ],
            "approved_dirty_entries": [],
            "commit_required": True,
            "unrelated_dirty_paths": ["unsafe-link"],
            "commit_groups": [{"message": "publish", "paths": ["artifact.md"]}],
        }
        REVIEW_MODULE.validate_manifest(
            own_only,
            pre_value["agents_vault"],
            str(self.agents),
            "TSK-AUTH",
            {
                "role": "agents_security_advisory",
                "source_sha256": "a" * 64,
                "target_path": str(artifact),
            },
            None,
            "fixture",
            {"required_mode": "sweep"},
            agents_materialized,
            [],
        )
        residual_blocked = {
            **own_only,
            "publication_mode": "blocked",
            "residual_review_status": "blocked",
            "commit_required": False,
            "commit_groups": [],
        }
        REVIEW_MODULE.validate_manifest(
            residual_blocked,
            pre_value["agents_vault"],
            str(self.agents),
            "TSK-AUTH",
            {
                "role": "agents_security_advisory",
                "source_sha256": "a" * 64,
                "target_path": str(artifact),
            },
            None,
            "fixture",
            {"required_mode": "sweep"},
            agents_materialized,
            [],
        )
        invalid_history_deferred = json.loads(json.dumps(residual_blocked))
        invalid_history_deferred["deferred_cleanup"].append(
            {
                "path": "local-ahead-only.md",
                "reason": "unsafe local history belongs in next_action",
            }
        )
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError,
            "blocked manifest does not preserve the exact non-actionable scope",
        ):
            REVIEW_MODULE.validate_manifest(
                invalid_history_deferred,
                pre_value["agents_vault"],
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "a" * 64,
                    "target_path": str(artifact),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                agents_materialized,
                [],
            )
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "core review status fields disagree"
        ):
            REVIEW_MODULE.validate_manifest(
                {**residual_blocked, "review_or_validation_status": "blocked"},
                pre_value["agents_vault"],
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "a" * 64,
                    "target_path": str(artifact),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                agents_materialized,
                [],
            )
        sweep = {
            **own_only,
            "publication_mode": "sweep",
            "residual_review_status": "quality_ok",
            "owned_paths": ["artifact.md", "unsafe-link"],
            "excluded_paths": [],
            "deferred_cleanup": [],
            "approved_dirty_entries": pre_value["agents_vault"]["dirty_entries"],
            "unrelated_dirty_paths": [],
            "commit_groups": [
                {"message": "publish", "paths": ["artifact.md", "unsafe-link"]}
            ],
        }
        with self.assertRaisesRegex(
            REVIEW_MODULE.ReviewError, "unreviewable dirty residual"
        ):
            REVIEW_MODULE.validate_manifest(
                sweep,
                pre_value["agents_vault"],
                str(self.agents),
                "TSK-AUTH",
                {
                    "role": "agents_security_advisory",
                    "source_sha256": "a" * 64,
                    "target_path": str(artifact),
                },
                None,
                "fixture",
                {"required_mode": "sweep"},
                agents_materialized,
                [],
            )

    def test_oversize_dirty_entry_is_deferred_without_suppressing_peer(self) -> None:
        """Apply snapshot size limits per Vault instead of aborting both reviews."""
        large = self.agents / "large.md"
        small = self.user / "small.md"
        large.write_bytes(b"12345")
        small.write_bytes(b"ok")
        oid = lambda repo, content: subprocess.run(
            ["git", "-C", str(repo), "hash-object", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        pre = {
            "agents_vault": {
                "dirty_entries": [
                    {"path": "large.md", "git_blob_oid": oid(self.agents, b"12345"), "mode": "100644"}
                ],
                "local_commits": [],
            },
            "user_vault": {
                "dirty_entries": [
                    {"path": "small.md", "git_blob_oid": oid(self.user, b"ok"), "mode": "100644"}
                ],
                "local_commits": [],
            },
        }
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(self.user / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(self.user),
        }
        destination = self.workdir / "oversize-review"
        destination.mkdir()
        with mock.patch.object(DIRTY_STAGER_MODULE, "MAX_BLOB_BYTES", 4):
            manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        self.assertEqual(
            manifest["vaults"]["agents_vault"][0]["materialization_status"],
            "deferred",
        )
        self.assertEqual(
            manifest["vaults"]["user_vault"][0]["materialization_status"],
            "available",
        )

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
        self.assertEqual(manifest["version"], 4)
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
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            [  # noqa: S607 -- controlled Git fixture command
                "git", "clone", "-q", "--branch", "main",
                str(self.origins["user"]), str(peer),
            ],
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

        with mock.patch.object(
            CAPTURE_MODULE,
            "commit_patch",
            side_effect=AssertionError("capture materialized a local commit patch"),
        ) as patch_reader:
            reviewed = CAPTURE_MODULE.capture(
                str(self.user), include_local_history=True
            )
        patch_reader.assert_not_called()
        self.assertEqual(reviewed["history_capture_status"], "available")
        self.assertEqual(len(reviewed["local_commits"]), 1)

    def test_binary_dirty_content_does_not_break_lightweight_capture(self) -> None:
        """Hash invalid-UTF-8 worktree bytes without decoding the file content."""
        subprocess.run(["git", "-C", str(self.user), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(self.user), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (self.user / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.user), "add", "base.md"], check=True)
        subprocess.run(["git", "-C", str(self.user), "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(self.user), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(self.user), "push", "-q", "-u", "origin", "main"], check=True)
        binary = self.user / "binary-dirty.bin"
        binary.write_bytes(b"changed-\xff\n")
        state = CAPTURE_MODULE.capture(str(self.user))
        self.assertIn("binary-dirty.bin", state["dirty_paths"])
        entry = next(
            item for item in state["dirty_entries"]
            if item["path"] == "binary-dirty.bin"
        )
        self.assertIsNotNone(entry["git_blob_oid"])
        self.assertEqual(state["capture_status"], "available")

    def test_oversize_local_commit_patch_blocks_only_residual_sweep(self) -> None:
        """Materialize local history under a hard bound after collection only."""
        repo = self.user
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
        (repo / "large.md").write_text("12345\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "large.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "large"], check=True)
        state = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        destination = self.workdir / "bounded-local-history"
        destination.mkdir()
        runtime = {
            "agents_git_dir": str(self.agents / ".git"),
            "user_git_dir": str(repo / ".git"),
            "agents_vault_root": str(self.agents),
            "user_vault_root": str(repo),
        }
        pre = {
            "agents_vault": {"dirty_entries": [], "local_commits": []},
            "user_vault": state,
        }
        with mock.patch.object(DIRTY_STAGER_MODULE, "MAX_BLOB_BYTES", 4):
            manifest = DIRTY_STAGER_MODULE.materialize(runtime, pre, destination)
        self.assertEqual(
            manifest["local_commits"]["user_vault"][0]["materialization_status"],
            "blocked",
        )

    def test_local_commit_patch_helpers_disable_repository_textconv(self) -> None:
        """Do not execute repository-configured textconv while hashing reviewed patches."""
        repo = self.user
        marker = self.root / "textconv-executed"
        helper = self.root / "textconv.sh"
        helper.write_text(
            f"#!/bin/sh\ntouch {marker}\ncat \"$1\"\n",
            encoding="utf-8",
        )
        helper.chmod(0o755)
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.name", "Fixture"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "diff.fixture.textconv", str(helper)],  # noqa: S607
            check=True,
        )
        (repo / ".gitattributes").write_text("*.txt diff=fixture\n", encoding="utf-8")
        target = repo / "reviewed.txt"
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "add", ".gitattributes", "reviewed.txt"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"],  # noqa: S607
            check=True,
        )
        parent = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()
        target.write_text("after\n", encoding="utf-8")
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "add", "reviewed.txt"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "commit", "-q", "-m", "change"],  # noqa: S607
            check=True,
        )
        commit = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()

        patch = CAPTURE_MODULE.commit_patch(str(repo), commit, [parent])
        DIRTY_STAGER_MODULE.read_commit_patch(
            str(repo / ".git"), commit, [parent], hashlib.sha256(patch).hexdigest()
        )
        self.assertEqual(
            PUSH_MODULE.commit_patch_sha256(str(repo), commit, [parent]),
            hashlib.sha256(patch).hexdigest(),
        )
        self.assertFalse(marker.exists())

    def test_empty_existing_commit_message_is_captured_and_schema_valid(self) -> None:
        """Represent valid local-only commits whose Git message is empty."""
        repo = self.user
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.name", "Fixture"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],  # noqa: S607
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "add", "base.md"],  # noqa: S607
            check=True,
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "commit", "-q", "-m", "base"],  # noqa: S607
            check=True,
        )
        base = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            [  # noqa: S607 -- controlled Git fixture command
                "git", "-C", str(repo), "commit", "-q", "--allow-empty",
                "--allow-empty-message", "-m", "",
            ],
            check=True,
        )
        head = subprocess.check_output(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True  # noqa: S607
        ).strip()
        commits = CAPTURE_MODULE.local_commit_metadata(str(repo), base, head)
        self.assertEqual(commits[0]["message"], "")
        schema = json.loads(
            (SKILL_ROOT / "references" / "publication-review-result.schema.json").read_text()
        )
        self.assertNotIn("minLength", schema["$defs"]["existingCommit"]["properties"]["message"])

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

    def test_evidence_commit_ignores_replace_refs_and_matches_raw_parent(self) -> None:
        """Prevent a replace ref from hiding extra paths in the committed tree."""
        repo = self.workdir / "replace-ref-evidence-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "base.md").write_text("base\n", encoding="utf-8")
        target = repo / "evidence.md"
        target.write_text(
            "# Task\n\n### Vault Publication Evidence\n\n## Reviews\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "base.md", "evidence.md"],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        base = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        (repo / "hidden.md").write_text("hidden\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "hidden.md"], check=True)
        replacement_tree = subprocess.check_output(
            ["git", "-C", str(repo), "write-tree"], text=True
        ).strip()
        replacement = subprocess.check_output(
            ["git", "-C", str(repo), "commit-tree", replacement_tree, "-p", base],
            input="replacement\n",
            text=True,
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", "hidden.md"],
            check=True,
        )
        (repo / "hidden.md").unlink()
        subprocess.run(["git", "-C", str(repo), "replace", base, replacement], check=True)
        before = target.read_bytes()
        candidate = before.replace(
            b"## Reviews\n", b"#### Daily evidence\n\n## Reviews\n"
        )
        head_candidate = self.workdir / "replace-head-candidate"
        index_candidate = self.workdir / "replace-index-candidate"
        patch_path = self.workdir / "replace-evidence.patch"
        head_candidate.write_bytes(candidate)
        index_candidate.write_bytes(candidate)
        patch = FINALIZER_MODULE.canonical_patch("evidence.md", before, candidate)
        patch_path.write_bytes(patch)
        runtime = {
            "agents_vault_root": str(repo),
            "agents_git_dir": str(repo / ".git"),
            "gitleaks_bin": str(self.fake_gitleaks),
        }
        plan = {
            "target_path": "evidence.md",
            "base_head": base,
            "head_source_sha256": hashlib.sha256(before).hexdigest(),
            "head_candidate_path": str(head_candidate),
            "head_candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "index_candidate_path": str(index_candidate),
            "index_candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            "review_patch_path": str(patch_path),
            "evidence_diff_sha256": hashlib.sha256(patch).hexdigest(),
        }
        commit, _head_blob, _index_blob, _index_bytes = FINALIZER_MODULE.isolated_evidence_commit(
            runtime,
            plan,
            ("Fixture", "fixture@example.invalid"),
            self.workdir,
        )
        environment = FINALIZER_MODULE.clean_environment()
        raw_paths = subprocess.check_output(
            [
                "git", "-C", str(repo), "diff", "--name-only", "--no-renames",
                base, commit,
            ],
            text=True,
            env=environment,
        ).splitlines()
        self.assertEqual(raw_paths, ["evidence.md"])

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
            "dirty_metadata": [],
            "staged_paths": [],
            "index_entries": [],
            "index_sha256": "0" * 64,
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
            "dirty_metadata": [
                {
                    "path": "artifact.md", "exists": True, "size": 1,
                    "mtime_ns": 1, "st_mode": 0o100644,
                }
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
        external["dirty_metadata"].append(
            {
                "path": "external.md", "exists": True, "size": 1,
                "mtime_ns": 1, "st_mode": 0o100644,
            }
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

    def test_owned_artifact_rollback_is_exact_and_refuses_drift(self) -> None:
        """Undo only an unchanged O_EXCL artifact after a pre-commit failure."""
        target = self.workdir / "owned-artifact.md"
        target.write_text("verified\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = COMMITTER_MODULE.installed_artifact_receipt(target, digest)
        COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertFalse(target.exists())

        target.write_text("verified\n", encoding="utf-8")
        receipt = COMMITTER_MODULE.installed_artifact_receipt(target, digest)
        target.write_text("other task\n", encoding="utf-8")
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "rollback refused"
        ):
            COMMITTER_MODULE.rollback_owned_artifact(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "other task\n")

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
            f"historical evidence: {Path.home()}/old.log\n"
            f"historical CR evidence:\r{Path.home()}/old-cr.log\n",
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
            historical.write_bytes(historical.read_bytes() + b"reviewed update\n")
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

            safe_candidate = historical.read_bytes()
            for index, prefix in enumerate(("", "+", "++", "+++")):
                with self.subTest(prefix=prefix):
                    added_index = str(Path(temporary) / f"added-{index}.index")
                    COMMITTER_MODULE.git(
                        str(repo), git_dir, "read-tree", "HEAD", index_file=added_index
                    )
                    historical.write_bytes(
                        safe_candidate
                        + f"{prefix}new evidence: {Path.home()}/new.log\n".encode(
                            "utf-8"
                        )
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
            cr_index = str(Path(temporary) / "added-cr.index")
            COMMITTER_MODULE.git(
                str(repo), git_dir, "read-tree", "HEAD", index_file=cr_index
            )
            historical.write_bytes(
                safe_candidate
                + f"new CR evidence:\r{Path.home()}/new-cr.log\n".encode("utf-8")
            )
            COMMITTER_MODULE.git(
                str(repo),
                git_dir,
                "add",
                "--",
                "historical.md",
                index_file=cr_index,
            )
            with self.assertRaises(COMMITTER_MODULE.CommitError):
                COMMITTER_MODULE.scan_staged(
                    str(self.fake_gitleaks),
                    str(repo),
                    git_dir,
                    ["historical.md"],
                    cr_index,
                )

    def test_unified_diff_parser_uses_only_lf_as_line_boundary(self) -> None:
        """Preserve standalone CR and plus-prefixed content inside one Git hunk."""
        patch_bytes = (
            b"diff --git a/file b/file\n"
            b"--- a/file\n"
            b"+++ b/file\n"
            b"@@ -0,0 +1 @@\n"
            b"+++prefix\rsecret-after-cr\n"
        )
        self.assertEqual(
            DIRTY_STAGER_MODULE.unified_diff_added_content(patch_bytes),
            b"++prefix\rsecret-after-cr\n",
        )
        patch_text = patch_bytes.decode("utf-8")
        self.assertEqual(
            DIRTY_STAGER_MODULE.unified_diff_added_content(patch_text),
            "++prefix\rsecret-after-cr\n",
        )

    def test_local_committer_preserves_reviewed_markdown_whitespace(self) -> None:
        """Reviewed Vault content is committed byte-for-byte, including hard breaks."""
        repo = self.agents
        git_dir = str(repo / ".git")
        base = repo / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
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
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        reviewed = repo / "reviewed.md"
        reviewed.write_text("reviewed line  \n", encoding="utf-8")
        reviewed_oid = COMMITTER_MODULE.write_blob(
            str(repo), git_dir, reviewed.read_bytes()
        )
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [
                {
                    "path": "reviewed.md",
                    "git_blob_oid": reviewed_oid,
                    "mode": "100644",
                }
            ],
            "commit_groups": [
                {"message": "approved content", "paths": ["reviewed.md"]},
                {"message": "publish artifact", "paths": ["artifact.md"]},
            ],
        }

        final_state = {
            "commit_status": "complete",
            "commit_hashes": ["1" * 40, "2" * 40],
            "pre_local_head": before,
            "local_head": "2" * 40,
            "pre_dirty_digest": hashlib.sha256(b"").hexdigest(),
            "post_dirty_digest": hashlib.sha256(b"").hexdigest(),
            "clean": True,
        }
        with mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=final_state
        ):
            result = COMMITTER_MODULE.commit_groups(
                str(repo),
                git_dir,
                str(self.fake_gitleaks),
                {
                    "local_head": before,
                    "dirty_digest": hashlib.sha256(b"").hexdigest(),
                },
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
                self.workdir,
            )

        self.assertEqual(result["commit_status"], "complete")
        self.assertTrue(result["clean"])
        self.assertEqual(reviewed.read_bytes(), b"reviewed line  \n")

    def test_fixed_pusher_validates_unquoted_unicode_paths(self) -> None:
        """Tree validation handles spaces and non-ASCII paths without Git quoting."""
        repo = self.agents
        relative = "日本語 フォルダ/要約.md"
        target = repo / relative
        target.parent.mkdir(parents=True)
        target.write_text("reviewed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", relative], check=True)
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
                "unicode path",
            ],
            check=True,
        )
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        oid = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", f"{head}:{relative}"], text=True
        ).strip()

        PUSH_MODULE.validate_dirty_entry(
            str(repo),
            head,
            {"path": relative, "mode": "100644", "git_blob_oid": oid},
        )
        PUSH_MODULE.validate_blob_mode(str(repo), head, relative)

    def test_local_committer_rejects_reviewed_conflict_markers(self) -> None:
        """Whitespace exceptions must not disable Git's conflict-marker guard."""
        repo = self.agents
        git_dir = str(repo / ".git")
        base = repo / "base.md"
        base.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
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
        before = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        reviewed = repo / "reviewed.md"
        reviewed.write_text("<<<<<<< ours\nvalue\n=======\nother\n>>>>>>> theirs\n")
        reviewed_oid = COMMITTER_MODULE.write_blob(
            str(repo), git_dir, reviewed.read_bytes()
        )
        artifact = repo / "artifact.md"
        artifact.write_text("artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [
                {
                    "path": "reviewed.md",
                    "git_blob_oid": reviewed_oid,
                    "mode": "100644",
                }
            ],
            "commit_groups": [
                {
                    "message": "approved content",
                    "paths": ["reviewed.md", "artifact.md"],
                }
            ],
        }

        with self.assertRaises(subprocess.CalledProcessError):
            COMMITTER_MODULE.commit_groups(
                str(repo),
                git_dir,
                str(self.fake_gitleaks),
                {
                    "local_head": before,
                    "dirty_digest": hashlib.sha256(b"").hexdigest(),
                },
                manifest,
                "artifact.md",
                hashlib.sha256(b"artifact\n").hexdigest(),
                self.workdir,
            )
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip(),
            before,
        )

    def test_local_committer_main_resumes_after_agents_commit(self) -> None:
        """A sealed partial result resumes the uncommitted User Vault only."""
        resume_root = self.workdir / "resume-main"
        resume_root.mkdir()
        agents_root = resume_root / "agents"
        user_root = resume_root / "user"
        agents_root.mkdir()
        user_root.mkdir()
        advisory = agents_root / "advisory.md"
        summary = user_root / "summary.md"
        advisory.write_text("advisory\n", encoding="utf-8")
        summary.write_text("summary\n", encoding="utf-8")
        runtime = {
            "agents_vault_root": str(agents_root),
            "agents_git_dir": str(agents_root / ".git"),
            "user_vault_root": str(user_root),
            "user_git_dir": str(user_root / ".git"),
            "gitleaks_bin": str(self.fake_gitleaks),
            "publisher_git_name": "Fixture",
            "publisher_git_email": "fixture@example.invalid",
        }
        agents_before = "a" * 40
        agents_after = "b" * 40
        user_before = "c" * 40
        clean_digest = hashlib.sha256(b"").hexdigest()
        pre = {
            "agents_vault": {
                "local_head": agents_before,
                "dirty_digest": "d" * 64,
                "dirty_paths": [],
                "dirty_entries": [],
                "dirty_metadata": [],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
            },
            "user_vault": {
                "local_head": user_before,
                "dirty_digest": "e" * 64,
                "dirty_paths": [],
                "dirty_entries": [],
                "dirty_metadata": [],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
            },
        }
        collection = {
            "daily_pipeline_status": "complete",
            "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "advisory_sha256": hashlib.sha256(advisory.read_bytes()).hexdigest(),
            "notification_result": "none",
        }
        plan = {
            "summary_target": str(summary),
            "advisory_target": str(advisory),
        }
        context = {
            "runtime": runtime,
            "pre_collection_state": pre,
            "verified_collection": collection,
            "artifact_plan": plan,
        }
        review = {
            "outcome": "approved",
            "agents_vault": {"publication_mode": "sweep", "deferred_cleanup": []},
            "user_vault": {"publication_mode": "sweep", "deferred_cleanup": []},
            "next_action": None,
        }
        context_path = resume_root / "context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        review["publication_context_sha256"] = hashlib.sha256(
            context_path.read_bytes()
        ).hexdigest()
        review_path = resume_root / "review.json"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        input_paths = []
        for name, value in (
            ("runtime.json", runtime),
            ("pre.json", pre),
            ("collection.json", collection),
            ("plan.json", plan),
        ):
            path = resume_root / name
            path.write_text(json.dumps(value), encoding="utf-8")
            input_paths.append(path)
        output = resume_root / "commit-result.json"
        partial_result = {
            "outcome": "partial_publication",
            "phase": "local_commit",
            "publication_context_sha256": review["publication_context_sha256"],
            "agents_vault": {
                "commit_status": "failed",
                "commit_hashes": [agents_after],
                "pre_local_head": agents_before,
                "local_head": agents_after,
                "pre_dirty_digest": "d" * 64,
                "post_dirty_digest": clean_digest,
                "clean": True,
                "publication_mode": "sweep",
                "deferred_cleanup": [],
            },
            "user_vault": {
                "commit_status": "not_started",
                "commit_hashes": [],
                "pre_local_head": user_before,
                "local_head": user_before,
                "pre_dirty_digest": "e" * 64,
                "post_dirty_digest": "f" * 64,
                "clean": False,
                "publication_mode": "sweep",
                "deferred_cleanup": [],
            },
            "publication_mode": {"agents_vault": "sweep", "user_vault": "sweep"},
            "deferred_cleanup": {"agents_vault": [], "user_vault": []},
            "evidence_finalization_commit": None,
        }
        actual_agents = {
            "commit_status": "complete",
            "commit_hashes": [agents_after],
            "pre_local_head": agents_before,
            "local_head": agents_after,
            "pre_dirty_digest": "d" * 64,
            "post_dirty_digest": clean_digest,
            "clean": True,
            "publication_mode": "sweep",
            "deferred_cleanup": [],
        }
        user_result = {
            "commit_status": "complete",
            "commit_hashes": ["1" * 40],
            "pre_local_head": user_before,
            "local_head": "1" * 40,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": clean_digest,
            "clean": True,
            "publication_mode": "sweep",
            "deferred_cleanup": [],
        }
        captured = {
            "agents_vault": {
                "dirty_paths": [],
                "dirty_entries": [],
                "dirty_metadata": [],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
                "local_head": agents_after,
            },
            "user_vault": {
                "dirty_paths": ["summary.md"],
                "dirty_entries": [
                    {
                        "path": "summary.md",
                        "mode": "100644",
                        "git_blob_oid": "2" * 40,
                    }
                ],
                "dirty_metadata": [
                    {
                        "path": "summary.md",
                        "exists": True,
                        "size": summary.stat().st_size,
                        "mtime_ns": summary.stat().st_mtime_ns,
                        "st_mode": summary.stat().st_mode,
                    }
                ],
                "staged_paths": [],
                "index_entries": [],
                "index_sha256": "0" * 64,
                "local_head": user_before,
                "dirty_digest": "f" * 64,
            },
        }
        partial_result["resumable_state"] = captured
        completed_capture = {
            "agents_vault": captured["agents_vault"],
            "user_vault": {"dirty_paths": [], "local_head": "1" * 40},
        }
        output.write_text(json.dumps(partial_result), encoding="utf-8")

        with mock.patch.object(
            COMMITTER_MODULE, "validate_final_worktree"
        ), mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=actual_agents
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state",
            side_effect=[captured, captured, completed_capture]
        ), mock.patch.object(
            COMMITTER_MODULE, "validate_post_commit_state"
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups", return_value=user_result
        ) as resumed_commit:
            status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )

        self.assertEqual(status, 0)
        result = json.loads(output.read_text())
        self.assertEqual(result["outcome"], "ready_to_push")
        self.assertEqual(result["agents_vault"]["commit_status"], "complete")
        self.assertEqual(result["agents_vault"]["commit_hashes"], [agents_after])
        self.assertEqual(result["user_vault"], user_result)
        resumed_commit.assert_called_once()

        complete_partial = json.loads(json.dumps(partial_result))
        complete_partial["agents_vault"]["commit_status"] = "complete"
        output.write_text(json.dumps(complete_partial), encoding="utf-8")
        with mock.patch.object(
            COMMITTER_MODULE, "validate_final_worktree"
        ), mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=actual_agents
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state",
            side_effect=[captured, captured, completed_capture]
        ), mock.patch.object(
            COMMITTER_MODULE, "validate_post_commit_state"
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups", return_value=user_result
        ) as complete_resume_commit:
            complete_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(complete_status, 0)
        complete_result = json.loads(output.read_text())
        self.assertEqual(
            complete_result["agents_vault"]["commit_hashes"], [agents_after]
        )
        complete_resume_commit.assert_called_once()

        output.write_text(json.dumps(partial_result), encoding="utf-8")
        drifted_capture = json.loads(json.dumps(captured))
        drifted_capture["user_vault"]["dirty_digest"] = "9" * 64
        with mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=drifted_capture
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups"
        ) as user_drift_commit:
            user_drift = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(user_drift, 75)
        self.assertIn("Vaults no longer match", json.loads(output.read_text())["next_action"])
        user_drift_commit.assert_not_called()

        wrong_context = json.loads(json.dumps(partial_result))
        wrong_context["publication_context_sha256"] = "0" * 64
        output.write_text(json.dumps(wrong_context), encoding="utf-8")
        with mock.patch.object(COMMITTER_MODULE, "commit_groups") as context_commit:
            wrong_context_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(wrong_context_status, 75)
        self.assertIn("not a resumable", json.loads(output.read_text())["next_action"])
        context_commit.assert_not_called()

        missing_state = json.loads(json.dumps(partial_result))
        missing_state.pop("resumable_state")
        output.write_text(json.dumps(missing_state), encoding="utf-8")
        with mock.patch.object(COMMITTER_MODULE, "commit_groups") as state_commit:
            missing_state_status = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(missing_state_status, 75)
        self.assertIn("not a resumable", json.loads(output.read_text())["next_action"])
        state_commit.assert_not_called()

        output.write_text(json.dumps(partial_result), encoding="utf-8")
        drifted_agents = dict(actual_agents, local_head="9" * 40)
        with mock.patch.object(
            COMMITTER_MODULE, "validate_final_worktree"
        ), mock.patch.object(
            COMMITTER_MODULE, "current_state", return_value=drifted_agents
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=captured
        ), mock.patch.object(
            COMMITTER_MODULE, "commit_groups"
        ) as rejected_commit:
            rejected = COMMITTER_MODULE.main(
                [
                    "commit-reviewed-publication.py",
                    *(str(path) for path in input_paths),
                    str(context_path),
                    str(review_path),
                    "/unused-installer",
                    "/unused-capture",
                    hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    str(output),
                ]
            )
        self.assertEqual(rejected, 75)
        self.assertIn(
            "Agents Vault no longer matches",
            json.loads(output.read_text())["next_action"],
        )
        rejected_commit.assert_not_called()

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

    def test_partial_failure_without_context_is_valid_but_not_resumable(self) -> None:
        """Preserve early local progress without fabricating resume bindings."""
        before = {
            "agents_vault": {"local_head": "a" * 40, "dirty_digest": "d" * 64},
            "user_vault": {"local_head": "b" * 40, "dirty_digest": "e" * 64},
        }
        progressed = {
            "commit_status": "failed",
            "commit_hashes": ["c" * 40],
            "pre_local_head": "a" * 40,
            "local_head": "c" * 40,
            "pre_dirty_digest": "d" * 64,
            "post_dirty_digest": hashlib.sha256(b"").hexdigest(),
            "clean": True,
        }
        unchanged = {
            "commit_status": "not_started",
            "commit_hashes": [],
            "pre_local_head": "b" * 40,
            "local_head": "b" * 40,
            "pre_dirty_digest": "e" * 64,
            "post_dirty_digest": "e" * 64,
            "clean": False,
        }
        with mock.patch.object(
            COMMITTER_MODULE, "current_state", side_effect=[progressed, unchanged]
        ):
            partial = COMMITTER_MODULE.result_after_failure(
                {
                    "agents_vault_root": "/agents",
                    "agents_git_dir": "/agents/.git",
                    "user_vault_root": "/user",
                    "user_git_dir": "/user/.git",
                },
                before,
                {"daily_pipeline_status": "complete"},
                {"summary_target": "/user/summary.md", "advisory_target": "/agents/advisory.md"},
                "early failure",
            )
        self.assertEqual(partial["outcome"], "partial_publication")
        self.assertNotIn("publication_context_sha256", partial)
        self.assertNotIn("resumable_state", partial)
        schema = json.loads(
            (SKILL_ROOT / "references" / "publication-commit-result.schema.json").read_text()
        )
        CANONICAL_MODULE.validate(partial, schema, schema)
        incomplete_binding = dict(
            partial, publication_context_sha256="f" * 64
        )
        with self.assertRaises(CANONICAL_MODULE.CanonicalValidationError):
            CANONICAL_MODULE.validate(incomplete_binding, schema, schema)

        resumable_snapshot = {
            "agents_vault": {"local_head": "c" * 40},
            "user_vault": {"local_head": "b" * 40},
        }
        deferred = {
            "agents_vault": [],
            "user_vault": [
                {"path": ".codex-handoff/unsafe.md", "reason": "guard rejection"}
            ],
        }
        with mock.patch.object(
            COMMITTER_MODULE, "current_state", side_effect=[progressed, unchanged]
        ), mock.patch.object(
            COMMITTER_MODULE, "capture_state", return_value=resumable_snapshot
        ):
            resumable = COMMITTER_MODULE.result_after_failure(
                {
                    "agents_vault_root": "/agents",
                    "agents_git_dir": "/agents/.git",
                    "user_vault_root": "/user",
                    "user_git_dir": "/user/.git",
                },
                before,
                {"daily_pipeline_status": "complete"},
                {
                    "summary_target": "/user/summary.md",
                    "advisory_target": "/agents/advisory.md",
                },
                "user CAS race",
                publication_context_sha256="f" * 64,
                capture="/capture",
                runtime_file="/runtime",
                publication_modes={
                    "agents_vault": "sweep",
                    "user_vault": "own_only",
                },
                deferred_cleanup=deferred,
            )
        self.assertEqual(
            resumable["publication_mode"],
            {"agents_vault": "sweep", "user_vault": "own_only"},
        )
        self.assertEqual(resumable["deferred_cleanup"], deferred)
        self.assertEqual(resumable["resumable_state"], resumable_snapshot)
        self.assertEqual(
            resumable["agents_vault"]["publication_mode"], "sweep"
        )
        self.assertEqual(
            resumable["user_vault"]["publication_mode"], "own_only"
        )

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
        oid = "a" * 40
        transport = mock.MagicMock()
        transport.__enter__.return_value = transport
        transport.__exit__.return_value = None
        transport.run.side_effect = [
            subprocess.CompletedProcess([], 0, f"{oid}\trefs/heads/main\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, oid + "\n", ""),
            subprocess.CompletedProcess([], 0, f"{oid}\trefs/heads/main\n", ""),
        ]
        local_results = [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with mock.patch.dict(os.environ, {"GIT_DIR": "/attacker/git"}), mock.patch.object(
            FETCH_MODULE, "IsolatedGitTransport", return_value=transport
        ) as transport_factory, mock.patch.object(
            FETCH_MODULE.subprocess, "run", side_effect=local_results
        ) as local_run:
            FETCH_MODULE.fetch_main(
                "/vault/worktree", "/local/gitdir", "ssh://git@example.invalid/repo"
            )
        transport_factory.assert_called_once_with("/local/gitdir")
        fetch_call = transport.run.call_args_list[1]
        self.assertEqual(
            fetch_call.args[-2:],
            (
                "ssh://git@example.invalid/repo",
                "refs/heads/main:refs/remotes/origin/main",
            ),
        )
        update_command = local_run.call_args_list[-1].args[0]
        self.assertIn("core.fsmonitor=false", update_command)
        self.assertNotIn("origin", update_command[-2:])
        self.assertNotIn("GIT_DIR", local_run.call_args_list[-1].kwargs["env"])
        self.assertEqual(
            local_run.call_args_list[-1].kwargs["env"]["GIT_CONFIG_GLOBAL"],
            os.devnull,
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

    def test_resolver_rejects_git_transport_rewrite_and_include(self) -> None:
        """Reject repository-local config that can redirect fixed Git transport."""
        subprocess.run(
            [
                "git", "-C", str(self.agents), "config", "--local",
                "url.ssh://attacker.invalid/.insteadOf", "ssh://git@example.invalid/",
            ],
            check=True,
        )
        rewritten = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config), str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rewritten.returncode, 78)
        self.assertIn("unsafe repository-local Git config", rewritten.stderr)
        subprocess.run(
            [
                "git", "-C", str(self.agents), "config", "--local", "--unset-all",
                "url.ssh://attacker.invalid/.insteadOf",
            ],
            check=True,
        )
        included = self.workdir / "attacker-git-config"
        included.write_text("[core]\n\tsshCommand = attacker\n", encoding="utf-8")
        subprocess.run(
            [
                "git", "-C", str(self.agents), "config", "--local",
                "include.path", str(included),
            ],
            check=True,
        )
        include_result = subprocess.run(
            [
                str(SCRIPTS / "resolve-runtime-context.py"),
                str(self.config), str(self.workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(include_result.returncode, 78)
        self.assertIn("unsafe repository-local Git config", include_result.stderr)

    def test_resolver_rejects_git_execution_config(self) -> None:
        """Reject filters, attributes indirection, and fsmonitor before collection."""
        cases = (
            ("filter.fixture.clean", "sh -c 'exit 1'"),
            ("core.attributesFile", str(self.workdir / "attributes")),
            ("core.fsmonitor", "sh -c 'exit 1'"),
        )
        for key, value in cases:
            with self.subTest(key=key):
                subprocess.run(
                    [
                        "git", "-C", str(self.agents), "config", "--local",
                        key, value,
                    ],
                    check=True,
                )
                result = subprocess.run(
                    [
                        str(SCRIPTS / "resolve-runtime-context.py"),
                        str(self.config), str(self.workdir),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 78)
                self.assertIn("unsafe repository-local Git config", result.stderr)
                subprocess.run(
                    [
                        "git", "-C", str(self.agents), "config", "--local",
                        "--unset-all", key,
                    ],
                    check=True,
                )

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
        permissions = self.agents / "permissions.md"
        permissions.write_text("same bytes\n", encoding="utf-8")
        permissions.chmod(0o640)
        before_permissions_all = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        before_permissions = before_permissions_all["agents_vault"]
        before_metadata = next(
            item for item in before_permissions["dirty_metadata"]
            if item["path"] == "permissions.md"
        )
        permissions.chmod(0o600)
        after_permissions_all = json.loads(
            subprocess.run(
                [str(SCRIPTS / "capture-vault-state.py"), str(context)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        after_permissions = after_permissions_all["agents_vault"]
        after_metadata = next(
            item for item in after_permissions["dirty_metadata"]
            if item["path"] == "permissions.md"
        )
        self.assertNotEqual(before_metadata["st_mode"], after_metadata["st_mode"])
        self.assertNotEqual(
            before_permissions["dirty_worktree_sha256"],
            after_permissions["dirty_worktree_sha256"],
        )
        with self.assertRaisesRegex(
            COMMITTER_MODULE.CommitError, "dirty metadata changed"
        ):
            COMMITTER_MODULE.validate_installed_scope(
                before_permissions_all, after_permissions_all, {}
            )
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

    def test_mode_determiner_constrains_each_vault_independently(self) -> None:
        """Convert collection-time drift and staged state into own_only, not failure."""
        base = {
            "repo_root": "/vault",
            "branch": "main",
            "upstream": "origin/main",
            "local_head": "a" * 40,
            "history_relation": "equal",
            "operation_in_progress": False,
            "git_control_sha256": "1" * 64,
            "index_sha256": "2" * 64,
            "dirty_worktree_sha256": "3" * 64,
            "dirty_digest": "4" * 64,
            "diff_snapshot_sha256": "5" * 64,
            "dirty_paths": [],
            "staged_paths": [],
        }
        changed = json.loads(json.dumps(base))
        changed["dirty_worktree_sha256"] = "6" * 64
        changed["dirty_paths"] = ["other-task.md"]
        result = MODE_MODULE.vault_mode(base, changed, "summary.md")
        self.assertEqual(result["required_mode"], "own_only")
        self.assertTrue(result["state_changed"])
        stable = json.loads(json.dumps(base))
        stable["staged_paths"] = ["staged.md"]
        staged = MODE_MODULE.vault_mode(stable, stable, "advisory.md")
        self.assertEqual(staged["required_mode"], "own_only")
        self.assertIn("existing_staged_changes", staged["reasons"])
        conflicted = MODE_MODULE.vault_mode(
            base, base, "summary.md", target_conflict=True
        )
        self.assertEqual(conflicted["required_mode"], "blocked")
        self.assertIn(
            "planned_target_changed_before_publication", conflicted["reasons"]
        )
        self.assertEqual(conflicted["retry_disposition"], "replan")
        active = json.loads(json.dumps(base))
        active["operation_in_progress"] = True
        blocked_active = MODE_MODULE.vault_mode(base, active, "summary.md")
        self.assertEqual(blocked_active["required_mode"], "blocked")
        self.assertEqual(blocked_active["retry_disposition"], "none")
        self.assertIn("active_git_operation", blocked_active["reasons"])
        control_changed = json.loads(json.dumps(base))
        control_changed["git_control_sha256"] = "9" * 64
        blocked_control = MODE_MODULE.vault_mode(
            base, control_changed, "summary.md"
        )
        self.assertEqual(blocked_control["required_mode"], "blocked")
        self.assertIn("git_control_plane_changed", blocked_control["reasons"])

    def test_sealed_residual_guard_raises_only_affected_mode_floor(self) -> None:
        """Turn a deterministic dirty deferral into an own_only mode hint."""
        hint = {
            "version": 1,
            "agents_vault": {
                "required_mode": "sweep",
                "reasons": ["stable_sweep_candidate"],
            },
            "user_vault": {
                "required_mode": "sweep",
                "reasons": ["stable_sweep_candidate"],
            },
        }
        manifest = {
            "version": 4,
            "vaults": {
                "agents_vault": [
                    {
                        "path": "unsafe.md",
                        "materialization_status": "deferred",
                    }
                ],
                "user_vault": [
                    {"path": "safe.md", "materialization_status": "available"}
                ],
            },
        }
        guarded = MODE_MODULE.apply_residual_guards(hint, manifest)
        self.assertEqual(guarded["agents_vault"]["required_mode"], "own_only")
        self.assertEqual(
            guarded["agents_vault"]["guard_deferred_paths"], ["unsafe.md"]
        )
        self.assertIn(
            "sealed_residual_guard_deferred", guarded["agents_vault"]["reasons"]
        )
        self.assertNotIn(
            "stable_sweep_candidate", guarded["agents_vault"]["reasons"]
        )
        self.assertEqual(guarded["user_vault"]["required_mode"], "sweep")
        self.assertEqual(guarded["user_vault"]["guard_deferred_paths"], [])

    def test_own_only_commit_preserves_unrelated_staged_and_untracked_state(self) -> None:
        """Commit only the artifact while preserving unsafe residual bytes and metadata."""
        repo = self.user
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "staged.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
        (repo / "staged.md").write_text("other task staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "staged.md"], check=True)
        handoff = repo / ".codex-handoff" / "unsafe.md"
        handoff.parent.mkdir()
        handoff.write_text(f"unsafe existing path {Path.home()}\n", encoding="utf-8")
        handoff.chmod(0o640)
        before_stat = handoff.stat()
        before_bytes = handoff.read_bytes()
        staged_oid = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", ":staged.md"], text=True
        ).strip()
        pre = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        artifact = repo / "summary.md"
        artifact.write_text("verified artifact\n", encoding="utf-8")
        manifest = {
            "approved_dirty_entries": [],
            "commit_groups": [{"message": "docs: publish summary", "paths": ["summary.md"]}],
            "deferred_cleanup": [
                {"path": path, "reason": "existing unrelated change"}
                for path in pre["dirty_paths"]
            ],
        }
        result = COMMITTER_MODULE.commit_groups(
            str(repo), str(repo / ".git"), str(self.fake_gitleaks), pre,
            manifest, "summary.md", hashlib.sha256(artifact.read_bytes()).hexdigest(),
            self.workdir, ("Fixture Publisher", "publisher@example.invalid"),
            publication_mode="own_only",
        )
        after = CAPTURE_MODULE.capture(str(repo), include_local_history=True)
        COMMITTER_MODULE.validate_post_commit_state(pre, after, "summary.md", "own_only")
        self.assertEqual(result["publication_mode"], "own_only")
        self.assertFalse(result["clean"])
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", ":staged.md"], text=True
            ).strip(),
            staged_oid,
        )
        self.assertEqual(handoff.read_bytes(), before_bytes)
        after_stat = handoff.stat()
        self.assertEqual(after_stat.st_mode, before_stat.st_mode)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        committed_paths = subprocess.check_output(
            ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            text=True,
        ).splitlines()
        self.assertEqual(committed_paths, ["summary.md"])

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
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text(source_coverage_markdown(), encoding="utf-8")
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
            str(SOURCE_CATALOG),
            str(source_manifest),
            str(verified_resolutions),
        ]
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
        payload["summary_sha256"] = "0" * 64
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(subprocess.run(command, check=False).returncode, 75)

    def test_collection_validator_preserves_query_article_identity(self) -> None:
        """Count distinct query-addressed articles instead of collapsing their evidence."""
        root = self.workdir / "query-coverage"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/news?id=1", "published": "2026-07-31"},
                {"url": "https://example.test/news?id=2", "published": "2026-07-31"},
            ],
            count=2,
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_uses_inclusive_seven_jst_calendar_dates(self) -> None:
        """Include run_date-6 through run_date after timezone normalization."""
        root = self.workdir / "jst-calendar-window"
        root.mkdir()
        entries = [
            {
                "url": "https://example.test/too-old",
                "published": "2026-08-03T23:59:59+09:00",
            },
            {
                "url": "https://example.test/first-second",
                "published": "Mon, 03 Aug 2026 15:00:00 GMT",
            },
            {
                "url": "https://example.test/last-second",
                "published": "2026-08-10T23:59:59+09:00",
            },
            {
                "url": "https://example.test/too-new",
                "published": "2026-08-10T15:00:00Z",
            },
        ]
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=entries,
            count=2,
            run_date=date(2026, 8, 10),
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 8, 10)
        )

        wrong_summary = summary.replace("| 2 | fixture |", "| 3 | fixture |")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "item count does not match dated extract evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                wrong_summary, catalog, manifest, verified, date(2026, 8, 10)
            )

    def test_collection_validator_rederives_manifest_window_count(self) -> None:
        """Accept the trusted count hint only when sealed dates reproduce it."""
        root = self.workdir / "manifest-window-count"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/in", "published": "2026-08-10"},
                {"url": "https://example.test/out", "published": "2026-08-03"},
            ],
            evidence_updates={
                "jst_window_start": "2026-08-04",
                "jst_window_end": "2026-08-10",
                "jst_window_item_count": 1,
            },
            count=1,
            run_date=date(2026, 8, 10),
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 8, 10)
        )
        payload = json.loads(manifest.read_text())
        payload["sources"][0]["jst_window_item_count"] = 0
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "manifest JST window count is invalid",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 8, 10)
            )
        for invalid in (None, True):
            payload = json.loads(manifest.read_text())
            if invalid is None:
                payload["sources"][0].pop("jst_window_item_count", None)
            else:
                payload["sources"][0]["jst_window_item_count"] = invalid
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                COLLECTION_VALIDATOR_MODULE.ValidationError,
                "manifest JST window count is invalid",
            ):
                COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                    summary, catalog, manifest, verified, date(2026, 8, 10)
                )
        payload = json.loads(manifest.read_text())
        payload["sources"][0]["jst_window_item_count"] = 1
        payload["sources"][0].pop("jst_window_start", None)
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "manifest JST window count is invalid",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 8, 10)
            )

    def test_collection_validator_binds_supplemental_date_to_extract_url(self) -> None:
        """Reject same-host dates for articles absent from the sealed extract."""
        root = self.workdir / "unbound-date"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/news?id=1", "published": None}
            ],
            date_evidence=[{
                "name": "Fixture News",
                "requested_url": "https://example.test/news?id=2",
                "final_url": "https://example.test/news?id=2",
                "published_date": "2026-07-31",
            }],
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "not bound to an undated extract entry",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_rejects_supplemental_date_redirect(self) -> None:
        """Require verified date evidence to finish on the sealed article URL."""
        root = self.workdir / "redirected-date"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/news?id=1", "published": None}
            ],
            date_evidence=[{
                "name": "Fixture News",
                "requested_url": "https://example.test/news?id=1",
                "final_url": "https://example.test/news?id=2",
                "published_date": "2026-07-31",
            }],
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError, "redirected"
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_ignores_undated_navigation_entries(self) -> None:
        """Count sealed dated articles without treating undated navigation as articles."""
        root = self.workdir / "mixed-html-dates"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/article", "published": "2026-07-31"},
                {"url": "https://example.test/about", "published": None},
            ],
            evidence_updates={
                "method": "public_page",
                "final_url": "https://example.test/news",
            },
            method="公開ページ",
            confirmed_url="https://example.test/news",
            count=1,
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_requires_dates_for_every_feed_entry(self) -> None:
        """Do not treat undated RSS entries as HTML navigation links."""
        root = self.workdir / "undated-feed-entry"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[
                {"url": "https://example.test/article", "published": "2026-07-31"},
                {"url": "https://example.test/undated", "published": None},
            ],
            count=1,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "feed source lacks publication-date evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_sealed_extract_requires_a_filename(self) -> None:
        """Raise the validator's typed error instead of an unpacking ValueError."""
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "source extract filename is invalid",
        ):
            COLLECTION_VALIDATOR_MODULE.sealed_extract_entries(
                self.workdir / "source-manifest.json", {}
            )

    def test_collection_validator_requires_gate_attempt_transport_evidence(self) -> None:
        """Reject non-robots constraints without final URL and HTTP status evidence."""
        root = self.workdir / "incomplete-gate-attempt"
        root.mkdir()
        feed_url = "https://example.test/feed"
        page_url = "https://example.test/news"
        attempts = [
            {
                "method": attempt_method,
                "url": attempt_url,
                "requested_url": attempt_url,
                "status": "access_constraint",
                "constraint": "login",
            }
            for attempt_method, attempt_url in (
                ("rss", feed_url),
                ("public_page", page_url),
            )
        ]
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "access_constraint",
                "method": "public_page",
                "final_url": page_url,
                "constraint": "login",
                "attempts": attempts,
            },
            status="アクセス制約",
            method="公開ページ",
            count=0,
            confirmed_url=page_url,
            reason="login required",
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "sealed access-constraint evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_accepts_reviewed_www_gate_redirect(self) -> None:
        """Use the same reviewed www/non-www aliases as the collector."""
        root = self.workdir / "www-gate-redirect"
        root.mkdir()
        feed_url = "https://example.test/feed"
        page_url = "https://example.test/news"
        attempts = [
            {
                "method": attempt_method,
                "url": attempt_url,
                "requested_url": attempt_url,
                "final_url": attempt_url.replace("example.test", "www.example.test"),
                "http_status": 403,
                "status": "access_constraint",
                "constraint": "login",
            }
            for attempt_method, attempt_url in (
                ("rss", feed_url),
                ("public_page", page_url),
            )
        ]
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "access_constraint",
                "method": "public_page",
                "final_url": page_url.replace("example.test", "www.example.test"),
                "constraint": "login",
                "attempts": attempts,
            },
            status="アクセス制約",
            method="公開ページ",
            count=0,
            confirmed_url=page_url.replace("example.test", "www.example.test"),
            reason="login required",
        )
        COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
            summary, catalog, manifest, verified, date(2026, 7, 31)
        )

    def test_collection_validator_rejects_empty_fetched_extract(self) -> None:
        """Do not infer no recent articles from an empty parser result."""
        root = self.workdir / "empty-extract"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            status="対象期間記事なし",
            count=0,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "empty or inconsistent extract",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_requires_complete_fallback_dates(self) -> None:
        """Reject fallback counts when any extracted entry has no parseable date."""
        root = self.workdir / "fallback-dates"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "needs_search_fallback",
                "method": None,
                "final_url": None,
                "extract_file": None,
                "extracted_entry_count": 0,
                "attempts": [{"method": "rss", "status": "failed", "reason": "fixture"}],
            },
            resolutions=[{
                "name": "Fixture News",
                "status": "verified_fallback",
                "method": "site_search",
                "requested_url": "https://example.test/feed",
                "final_url": "https://example.test/feed",
                "extracted_entry_count": 1,
                "candidate_entry_count": 1,
                "date_evidence_count": 1,
                "published_dates": [None],
                "candidate_evidence": [{
                    "url": "https://example.test/article",
                    "provenance": "feed_entry",
                    "published": None,
                }],
            }],
            status="対象期間記事なし",
            method="サイト限定検索",
            count=0,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "complete publication-date evidence",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_rejects_mixed_robots_attempts_without_fallback(self) -> None:
        """One robots denial must not hide a generic failure on another endpoint."""
        root = self.workdir / "mixed-robots"
        root.mkdir()
        summary, catalog, manifest, verified = write_minimal_coverage_fixture(
            root,
            extract_entries=[],
            evidence_updates={
                "status": "needs_search_fallback",
                "method": None,
                "final_url": None,
                "extract_file": None,
                "extracted_entry_count": 0,
                "attempts": [
                    {"method": "rss", "status": "access_constraint", "reason": "robots_disallowed"},
                    {"method": "public_page", "status": "failed", "reason": "transient"},
                ],
            },
            status="アクセス制約",
            count=0,
        )
        with self.assertRaisesRegex(
            COLLECTION_VALIDATOR_MODULE.ValidationError,
            "verified fallback resolution",
        ):
            COLLECTION_VALIDATOR_MODULE.validate_source_coverage(
                summary, catalog, manifest, verified, date(2026, 7, 31)
            )

    def test_collection_validator_rejects_unsafe_advisory_references(self) -> None:
        """Reject private paths, inconsistent hashes/names, and duplicate fields."""
        staging = self.workdir / "staging"
        staging.mkdir()
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        summary.write_text(source_coverage_markdown(), encoding="utf-8")
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
            str(SOURCE_CATALOG),
            str(source_manifest),
            str(verified_resolutions),
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

    def test_collection_validator_rejects_unresolved_or_missing_sources(self) -> None:
        """Block complete publication when any catalog source lacks a resolved row."""
        staging = self.workdir / "staging"
        staging.mkdir()
        source_manifest = write_source_manifest(staging)
        verified_resolutions = write_verified_resolutions(staging)
        summary = staging / "SUMMARY-IT-NEWS-2026-07-31.md"
        advisory = staging / "Personal-Vulnerability-Advisory-2026-07-31.md"
        result_path = self.workdir / "collection.json"
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        valid = source_coverage_markdown()
        fabricated_rows = []
        for line in valid.splitlines():
            if line.startswith("|") and "https://" in line:
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                cells[2:7] = [
                    "対象期間記事なし", "公式代替URL",
                    "https://example.invalid/fake", "42", "根拠なし",
                ]
                line = "| " + " | ".join(cells) + " |"
            fabricated_rows.append(line)
        invalid_summaries = {
            "unresolved status": valid.replace("対象期間記事なし", "取得不可", 1),
            "missing source": "\n".join(valid.splitlines()[:-1]) + "\n",
            "generic access error": valid.replace(
                "対象期間記事なし | RSS",
                "アクセス制約 | RSS",
                1,
            ).replace("fixture確認", "HTTP 403", 1),
            "fabricated complete table": "\n".join(fabricated_rows) + "\n",
        }
        for label, content in invalid_summaries.items():
            with self.subTest(label=label):
                summary.write_text(content, encoding="utf-8")
                advisory.write_text(
                    f"- 入力ニュース: {summary.name} "
                    f"(same-run SHA-256: {digest(summary)})\n",
                    encoding="utf-8",
                )
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
                validation = subprocess.run(
                    [
                        str(SCRIPTS / "validate-collection-result.py"),
                        str(result_path),
                        str(staging),
                        payload["run_id"],
                        "0",
                        str(SOURCE_CATALOG),
                        str(source_manifest),
                        str(verified_resolutions),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(validation.returncode, 75)
                self.assertRegex(validation.stderr, r"source|access-control")

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
        existing_summary = (
            self.user
            / "10_Prompt"
            / "2026"
            / "07"
            / "31"
            / summary.name
        )
        existing_summary.parent.mkdir(parents=True)
        existing_summary.write_text("existing summary\n", encoding="utf-8")
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
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(Path(plan["summary_target"]).name, "SUMMARY-IT-NEWS-2026-07-31-2.md")
        blocked_summary = Path(plan["summary_target"])
        blocked_summary.parent.mkdir(parents=True, exist_ok=True)
        blocked_summary.write_text("concurrent user target\n", encoding="utf-8")
        agents_only = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "agents_security_advisory",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        agents_only_result = json.loads(agents_only.stdout)
        installed_advisory = Path(agents_only_result["advisory_target"])
        self.assertEqual(
            blocked_summary.read_text(encoding="utf-8"),
            "concurrent user target\n",
        )
        self.assertTrue(installed_advisory.is_file())
        blocked_summary.unlink()
        installed_advisory.unlink()

        summary_result = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "user_it_news_summary",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        advisory_result = subprocess.run(
            [
                str(SCRIPTS / "install-verified-artifacts.py"),
                str(context_path),
                str(collection_path),
                str(plan_path),
                "agents_security_advisory",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        installed = {
            **json.loads(summary_result.stdout),
            **json.loads(advisory_result.stdout),
        }
        self.assertTrue(Path(installed["summary_target"]).is_file())
        self.assertTrue(Path(installed["advisory_target"]).is_file())
        self.assertEqual(existing_summary.read_text(encoding="utf-8"), "existing summary\n")

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

    def test_evidence_inputs_are_bounded_before_allocation(self) -> None:
        """Reject oversized control files and Git blobs through streaming gates."""
        oversized = self.workdir / "oversized-evidence-input"
        with oversized.open("wb") as output:
            output.truncate(EVIDENCE_MODULE.MAX_TASK_BYTES + 1)
        with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "allowed size"):
            EVIDENCE_MODULE.read_regular_nofollow(oversized)
        with self.assertRaisesRegex(FINALIZER_MODULE.FinalizationError, "allowed size"):
            FINALIZER_MODULE.stable_regular_bytes(oversized)

        content = b"12345"
        oid = subprocess.run(
            ["git", "-C", str(self.agents), "hash-object", "-w", "--stdin"],
            input=content,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        with mock.patch.object(EVIDENCE_MODULE, "MAX_TASK_BYTES", 4):
            with self.assertRaisesRegex(EVIDENCE_MODULE.EvidenceError, "exceeds"):
                EVIDENCE_MODULE.git_bytes(
                    str(self.agents), str(self.agents / ".git"),
                    "cat-file", "blob", oid,
                )
        with mock.patch.object(FINALIZER_MODULE, "MAX_TASK_BYTES", 4):
            with self.assertRaisesRegex(
                FINALIZER_MODULE.FinalizationError, "exceeds"
            ):
                FINALIZER_MODULE.git_object_bytes(
                    str(self.agents), str(self.agents / ".git"), oid
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
                "publication_mode": "sweep",
                "deferred_cleanup": [],
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
            "publication_mode": {
                "agents_vault": "sweep", "user_vault": "sweep"
            },
            "deferred_cleanup": {"agents_vault": [], "user_vault": []},
            "evidence_finalization_commit": None,
            "next_action": None,
        }
        commit_path = self.workdir / "fixed-commit.json"
        final_path = self.workdir / "fixed-final.json"
        context_path = self.workdir / "fixed-context.json"
        review_path = self.workdir / "fixed-review.json"
        plan_path = self.workdir / "fixed-plan.json"
        artifact_hash = hashlib.sha256(b"published\n").hexdigest()
        manifest = lambda root: {
            "repo_root": str(root),
            "task_id": "TSK-AUTH",
            "publication_mode": "sweep",
            "core_review_status": "quality_ok",
            "residual_review_status": "quality_ok",
            "owned_paths": ["published.md"],
            "excluded_paths": [],
            "deferred_cleanup": [],
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
        plan_payload = {
            "summary_target": str(self.user / "published.md"),
            "advisory_target": str(self.agents / "published.md"),
        }
        plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
        context_path.write_text(
            json.dumps(
                {
                    "runtime": runtime,
                    "pre_collection_state": pre,
                    "artifact_plan": plan_payload,
                }
            ),
            encoding="utf-8",
        )
        context_digest = hashlib.sha256(context_path.read_bytes()).hexdigest()
        review_payload = {
            "outcome": "approved",
            "publication_context_sha256": context_digest,
            "agents_vault": manifest(self.agents),
            "user_vault": manifest(self.user),
            "next_action": None,
        }
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
            expected = (
                commit_result["agents_vault"]["local_head"]
                if key == "agents_vault"
                else pre[key]["remote_head"]
            )
            self.assertEqual(remote, expected)

        bad_status = list(command)
        bad_status[5] = "not-a-number"
        rejected = subprocess.run(bad_status, check=False)
        self.assertEqual(rejected.returncode, 75)
        self.assertTrue(final_path.is_file())

        remote_before_rejections = {
            key: subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            for key, repo in (("agents", self.agents), ("user", self.user))
        }
        failed_process = list(command)
        failed_process[5] = "75"
        self.assertEqual(subprocess.run(failed_process, check=False).returncode, 75)
        tampered_runtime = dict(runtime)
        tampered_runtime["user_remote_url"] = str(self.origins["agents"])
        tampered_runtime_path = self.workdir / "tampered-runtime.json"
        tampered_runtime_path.write_text(json.dumps(tampered_runtime), encoding="utf-8")
        tampered_command = list(command)
        tampered_command[1] = str(tampered_runtime_path)
        self.assertEqual(subprocess.run(tampered_command, check=False).returncode, 75)
        for key, repo in (("agents", self.agents), ("user", self.user)):
            observed = subprocess.check_output(
                ["git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"],
                text=True,
            ).split()[0]
            self.assertEqual(observed, remote_before_rejections[key])

        commit_path.write_text(json.dumps(commit_result), encoding="utf-8")
        result = subprocess.run(command, check=False)
        self.assertEqual(
            result.returncode,
            0,
            msg=final_path.read_text(encoding="utf-8") if final_path.exists() else "missing result",
        )
        final = json.loads(final_path.read_text(encoding="utf-8"))
        self.assertEqual(final["outcome"], "partial_publication")
        self.assertEqual(
            final["agents_vault"]["local_head"],
            final["agents_vault"]["remote_head"],
        )

    def test_pusher_scans_remote_to_final_and_isolates_remote_races(self) -> None:
        """Scan the full range and contain a remote race to the affected Vault."""
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(PUSH_MODULE.subprocess, "run", return_value=completed) as run:
            PUSH_MODULE.scan_commits("/tools/gitleaks", "/vault", "a" * 40, "b" * 40)
        self.assertIn(
            f"{'a' * 40}..{'b' * 40}",
            run.call_args.args[0],
        )
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="a" * 40
        ), mock.patch.object(
            PUSH_MODULE, "push_one", return_value=("complete", "b" * 40)
        ) as push:
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/vault", "remote", "b" * 40, "a" * 40, "own_only"
                ),
                ("complete", "b" * 40),
            )
            push.assert_called_once()
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="c" * 40
        ), mock.patch.object(PUSH_MODULE, "push_one") as push:
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/vault", "remote", "b" * 40, "a" * 40, "sweep"
                ),
                ("failed", "c" * 40),
            )
            push.assert_not_called()
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/vault", "remote", "a" * 40, "a" * 40, "blocked"
                ),
                ("not_started", "c" * 40),
            )
        with mock.patch.object(
            PUSH_MODULE,
            "remote_head",
            side_effect=TRANSPORT_MODULE.TransportError("deadline"),
        ):
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/agents", "remote", "b" * 40, "a" * 40, "own_only"
                ),
                ("failed", "a" * 40),
            )
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="a" * 40
        ), mock.patch.object(
            PUSH_MODULE, "push_one", return_value=("complete", "b" * 40)
        ):
            self.assertEqual(
                PUSH_MODULE.push_one_independently(
                    "/user", "remote", "b" * 40, "a" * 40, "own_only"
                ),
                ("complete", "b" * 40),
            )

    def test_fixed_push_is_non_force_and_bounded(self) -> None:
        """Retry a fixed refspec at most three times and stop on remote drift."""
        before = "a" * 40
        local = "b" * 40
        failed = subprocess.CompletedProcess([], 1, "", "rejected")
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value=before
        ), mock.patch.object(PUSH_MODULE, "git", return_value=failed) as git:
            self.assertEqual(
                PUSH_MODULE.push_one(
                    "/vault", "remote", local, True, before, "/vault/.git"
                ),
                ("failed", before),
            )
            self.assertEqual(git.call_count, 3)
            for call in git.call_args_list:
                self.assertEqual(
                    call.args[1:],
                    ("push", "remote", f"{local}:refs/heads/main"),
                )
                self.assertNotIn("--force", call.args)
        with mock.patch.object(
            PUSH_MODULE, "remote_head", return_value="c" * 40
        ), mock.patch.object(PUSH_MODULE, "git") as git:
            self.assertEqual(
                PUSH_MODULE.push_one(
                    "/vault", "remote", local, True, before, "/vault/.git"
                ),
                ("failed", "c" * 40),
            )
            git.assert_not_called()

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
                }
            ],
            "dirty_paths": [],
        }
        with self.assertRaisesRegex(REVIEW_MODULE.ReviewError, "forbidden .obsidian"):
            REVIEW_MODULE.validate_manifest(
                {
                    "repo_root": str(self.agents),
                    "task_id": "TSK-AUTH",
                    "publication_mode": "sweep",
                    "approved_existing_commits": [
                        {**state["local_commits"][0], "patch_sha256": "d" * 64}
                    ],
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
                {"required_mode": "sweep"},
                materialized_commits=[{"patch_sha256": "d" * 64}],
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
                {"repo_root": str(self.agents), "task_id": "TSK-AUTH", "publication_mode": "sweep"},
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
                {"required_mode": "sweep"},
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
                {"required_mode": "sweep"},
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
            SCRIPTS / "determine-publication-modes.py",
            SCRIPTS / "validate-collection-result.py",
            SCRIPTS / "install-verified-artifacts.py",
            SCRIPTS / "commit-reviewed-publication.py",
            SCRIPTS / "validate-publication-review.py",
            SCRIPTS / "push-committed-heads.py",
            SCRIPTS / "prepare-publication-evidence.py",
            SCRIPTS / "commit-push-publication-evidence.py",
            SCRIPTS / "evidence_hunk.py",
            SCRIPTS / "git_diff_digest.py",
            SCRIPTS / "isolated_git_transport.py",
            SCRIPTS / "prepare-codex-output-schema.py",
            SCRIPTS / "validate-canonical-result.py",
            SCRIPTS / "stage-standing-task.py",
            SCRIPTS / "stage-dirty-review-inputs.py",
            SCRIPTS / "interpret-automation-result.sh",
            REPO_ROOT / "summarize-it-news" / "scripts" / "collect-public-sources.py",
            SOURCE_CATALOG,
        ):
            shutil.copy2(source, runtime / source.name)

        installer = runtime / "install-verified-artifacts.py"
        real_installer = runtime / "install-verified-artifacts.real.py"
        installer.rename(real_installer)
        installer.write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
real=Path(__file__).with_name("install-verified-artifacts.real.py")
marker=Path(__file__).with_name("artifact-target-conflict-once.marker")
completed=subprocess.run([str(real),*sys.argv[1:]],check=False,capture_output=True,text=True)
if completed.returncode == 0 and len(sys.argv) > 1 and sys.argv[1] == "--plan" and os.environ.get("FAKE_PLAN_TARGET_CONFLICT_ONCE") == "1" and not marker.exists():
    plan=json.loads(completed.stdout)
    target=Path(plan["summary_target"])
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text("concurrent target must survive\\n")
    marker.write_text(str(target)+"\\n")
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
raise SystemExit(completed.returncode)
""",
            encoding="utf-8",
        )
        installer.chmod(0o755)

        (runtime / "collect-public-sources.py").write_text(
            """#!/usr/bin/env python3
import hashlib, json, os, sys
from datetime import date,timedelta
from pathlib import Path
if sys.argv[1] == "--verify-resolutions":
    request=json.loads(Path(sys.argv[4]).read_text())
    Path(sys.argv[5]).write_text(json.dumps({"version":1,"resolutions":request["resolutions"],"date_evidence":[]}))
    raise SystemExit(0)
catalog_path=Path(sys.argv[1]); output=Path(sys.argv[2]); output.mkdir()
catalog=json.loads(catalog_path.read_text())
run_date=date.fromisoformat(sys.argv[3][:10]); window_start=run_date-timedelta(days=6)
sources=[]
for index,source in enumerate(catalog["sources"]):
    url=source["feed_url"] or source["page_url"]
    method="rss" if source["feed_url"] else "public_page"
    extract_file=f"source-{index}.extract.json"
    (output/extract_file).write_text(json.dumps({"format":"feed" if method=="rss" else "html_links","entries":[{"url":f"{url}?fixture={index}","published":"2020-01-01"}]}))
    sources.append({"name":source["name"],"tier":source["tier"],"status":"fetched","method":method,"final_url":url,"extract_file":extract_file,"extracted_entry_count":1,"jst_window_start":window_start.isoformat(),"jst_window_end":run_date.isoformat(),"jst_window_item_count":0,"attempts":[]})
manifest={"catalog_sha256":hashlib.sha256(catalog_path.read_bytes()).hexdigest(),"sources":sources}
(output/"source-manifest.json").write_text(json.dumps(manifest))
print(json.dumps(manifest))
""",
            encoding="utf-8",
        )
        (runtime / "collect-public-sources.py").chmod(0o755)

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

        committer = runtime / "commit-reviewed-publication.py"
        real_committer = runtime / "commit-reviewed-publication.real.py"
        committer.rename(real_committer)
        committer.write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
real=Path(__file__).with_name("commit-reviewed-publication.real.py")
marker=Path(__file__).with_name("commit-head-drift-once.marker")
if os.environ.get("FAKE_COMMIT_HEAD_DRIFT_ONCE") == "1" and not marker.exists():
    runtime=json.loads(Path(sys.argv[1]).read_text())
    for key in ("agents_vault_root",):
        repo=runtime[key]
        original=subprocess.check_output(["git","-C",repo,"rev-parse","HEAD"],text=True).strip()
        tree=subprocess.check_output(["git","-C",repo,"show","-s","--format=%T",original],text=True).strip()
        drift=subprocess.check_output(
            ["git","-C",repo,"-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit-tree",tree,"-p",original],
            input="fixture persistent HEAD drift\\n",text=True,
        ).strip()
        subprocess.run(["git","-C",repo,"update-ref","HEAD",drift,original],check=True)
    marker.write_text("replanned\\n")
    completed=subprocess.run([str(real),*sys.argv[1:]],check=False)
    raise SystemExit(completed.returncode)
completed=subprocess.run([str(real),*sys.argv[1:]],check=False)
raise SystemExit(completed.returncode)
""",
            encoding="utf-8",
        )
        committer.chmod(0o755)

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
assert args[-1] == "-"
prompt=sys.stdin.read()
context=json.loads(prompt.split("Runtime context JSON:\\n",1)[1])
def contains_key(value,key):
    if isinstance(value,dict):
        return key in value or any(contains_key(item,key) for item in value.values())
    if isinstance(value,list):
        return any(contains_key(item,key) for item in value)
    return False
schema=Path(args[args.index("--output-schema")+1]).name
schema_document=json.loads(Path(args[args.index("--output-schema")+1]).read_text())
schema_encoded=json.dumps(schema_document,sort_keys=True)
assert all(f'"{keyword}"' not in schema_encoded for keyword in ("allOf","if","then","else","oneOf","uniqueItems"))
stage="collection" if "--search" in args else ("review" if schema=="publication-review-result.schema.json" else ("evidence_review" if schema=="evidence-review-result.schema.json" else "publication"))
log_path=output.parent/"invocations.log"
if stage=="review":
    log_path=Path(context["publication_context_file"]).parent.parent/"invocations.log"
with log_path.open("a") as log:
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
    catalog=json.loads(Path(context["source_catalog"]).read_text())
    manifest=json.loads(Path(context["source_manifest"]).read_text())
    assert len(manifest["sources"]) == len(catalog["sources"])
    (staging/"source-resolutions.json").write_text(json.dumps({"version":1,"resolutions":[],"date_evidence":[]}))
    lines=["summary "+context["run_id"],"","## 確認済みサイト一覧","","| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |","|---|---:|---|---|---|---:|---|"]
    for source in catalog["sources"]:
        url=source["feed_url"] or source["page_url"]
        method="RSS" if source["feed_url"] else "公開ページ"
        lines.append(f"| {source['name']} | {source['tier']} | 対象期間記事なし | {method} | {url} | 0 | fixture確認 |")
    summary.write_text("\\n".join(lines)+"\\n")
    digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    advisory.write_text(f"- 入力ニュース: {summary.name} (same-run SHA-256: {digest(summary)})\\n")
    result={"daily_pipeline_status":"complete","run_id":context["run_id"],"summary_path":str(summary),"summary_sha256":digest(summary),"advisory_path":str(advisory),"advisory_sha256":digest(advisory),"notification_result":"none","vault_artifacts_complete":True,"next_action":None}
    if os.environ.get("FAKE_CANONICAL_INVALID_COLLECTION") == "1":
        result["next_action"]="must be null for a complete result"
elif stage=="review":
    assert context["publication_context_projection"] == "review_without_index_entries_v1"
    assert not contains_key(context,"index_entries")
    publication=context["publication_context"]
    authorization=Path(publication["authorization_task"])
    assert authorization.name == "authorization-task.md"
    assert authorization.parent.name == "review-input"
    assert hashlib.sha256(authorization.read_bytes()).hexdigest() == publication["authorization_task_sha256"]
    dirty_manifest=Path(publication["dirty_snapshot_manifest_file"])
    assert hashlib.sha256(dirty_manifest.read_bytes()).hexdigest() == publication["dirty_snapshot_manifest_sha256"]
    snapshot_manifest=json.loads(dirty_manifest.read_text())
    assert snapshot_manifest["version"] == 4
    runtime_context=publication["runtime"]
    pre=publication["pre_collection_state"]
    assert all("index_entries" not in state for state in pre.values())
    mode_hint=publication["publication_mode_hint"]
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
        key="agents_vault" if kind=="agents" else "user_vault"
        mode=mode_hint[key]["required_mode"]
        dirty_materialization=snapshot_manifest["vaults"][key]
        commit_materialization=snapshot_manifest["local_commits"][key]
        assert all(entry["materialization_status"]!="blocked" for entry in commit_materialization)
        if any(entry["materialization_status"]=="deferred" for entry in dirty_materialization):
            assert mode=="own_only"
        initial=sorted(set(state["dirty_paths"]+[target_rel]))
        existing_paths=[path for commit in state["local_commits"] for path in commit["changed_paths"]]
        if mode=="sweep":
            owned=sorted(set(initial+existing_paths+([evidence] if evidence else [])))
            excluded=[]; deferred=[]; approved_dirty=state["dirty_entries"]; groups=[{"message":"fixture publication","paths":initial}]; residual="quality_ok"; commit_required=True; finalization=({"target_path":evidence,"template":"daily_publication_v1"} if evidence else None)
        elif mode=="own_only":
            owned=sorted(set([target_rel]+([evidence] if evidence else [])))
            excluded=state["dirty_paths"]; deferred=[{"path":path,"reason":"fixture deferred residual"} for path in excluded]; approved_dirty=[]; groups=[{"message":"fixture publication","paths":[target_rel]}]; residual="deferred"; commit_required=True; finalization=({"target_path":evidence,"template":"daily_publication_v1"} if evidence else None)
        else:
            owned=[target_rel]; excluded=state["dirty_paths"]; deferred=[{"path":path,"reason":"fixture blocked state"} for path in excluded]; approved_dirty=[]; groups=[]; residual="blocked"; commit_required=False; finalization=None
        approved_commits=[{**commit,"patch_sha256":material.get("patch_sha256")} for commit,material in zip(state["local_commits"],commit_materialization)]
        return {"repo_root":root,"task_id":publication["authorization_task_id"],"publication_mode":mode,"core_review_status":"quality_ok","residual_review_status":residual,"owned_paths":owned,"excluded_paths":excluded,"deferred_cleanup":deferred,"approved_diff_snapshot_sha256":state["diff_snapshot_sha256"],"approved_existing_commits":approved_commits,"approved_dirty_entries":approved_dirty,"reviewed_artifacts":[{"role":item["role"],"source_sha256":item["sha256"],"target_path":target_rel}],"validation_evidence":{"file_guard":"passed","secret_scan":"passed","secret_scan_tool":"gitleaks","secret_scan_tool_version":runtime_context["gitleaks_version"],"reviewed_snapshot_sha256":state["diff_snapshot_sha256"],"reviewed_history_sha256":state["history_snapshot_sha256"]},"review_or_validation_status":"quality_ok","commit_required":commit_required,"unrelated_dirty_paths":excluded,"commit_groups":groups,"evidence_finalization":finalization}
    agents_manifest=manifest("agents"); user_manifest=manifest("user")
    blocked_modes=agents_manifest["publication_mode"]=="blocked" or user_manifest["publication_mode"]=="blocked"
    both_blocked=agents_manifest["publication_mode"]=="blocked" and user_manifest["publication_mode"]=="blocked"
    result={"outcome":"blocked" if both_blocked else "approved","publication_context_sha256":context["publication_context_sha256"],"agents_vault":agents_manifest,"user_vault":user_manifest,"next_action":"fixture blocked Vault" if blocked_modes else None}
elif stage=="publication":
    publication=context["publication_context"]
    runtime_context=publication["runtime"]
    pre=publication["pre_collection_state"]
    approved=context["approved_review"]
    installed={}
    for role in ("agents_security_advisory","user_it_news_summary"):
        installed.update(json.loads(subprocess.check_output([publication["installer"],publication["runtime_context_file"],publication["collection_result_file"],publication["artifact_plan_file"],role],text=True)))
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
    assert context["publication_context_projection"] == "review_without_index_entries_v1"
    assert not contains_key(context,"index_entries")
    plan=context["evidence_plan"]
    publication=context["publication_context"]
    evidence_diff=Path(plan["review_patch_path"]).read_bytes()
    assert hashlib.sha256(evidence_diff).hexdigest() == plan["evidence_diff_sha256"]
    payload_lines=[line[1:] for line in evidence_diff.decode().splitlines() if line.startswith('+{')]
    assert len(payload_lines) == 1
    evidence_payload=json.loads(payload_lines[0])
    assert set(evidence_payload) == {
        "run_id","publication_context_sha256","agents_vault","user_vault",
        "summary_repo_path","advisory_repo_path","publication_mode",
        "deferred_cleanup",
    }
    if os.environ.get("FAKE_EVIDENCE_REVIEW_BLOCKED") == "1":
        result={"outcome":"blocked","target_path":plan["target_path"],"evidence_diff_sha256":plan["evidence_diff_sha256"],"publication_context_sha256":plan["publication_context_sha256"],"review_status":"blocked","next_action":"fixture evidence review rejection"}
    else:
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

        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            ["git", "-C", str(self.user), "branch", "--unset-upstream"],  # noqa: S607
            check=True,
        )
        missing_upstream = subprocess.run(  # noqa: S603 -- controlled fixture executable
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )
        self.assertEqual(missing_upstream.returncode, 75)
        missing_upstream_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(missing_upstream_logs), 1)
        self.assertEqual(
            missing_upstream_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection", "review"],
            msg=(runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "phase=publication",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        subprocess.run(  # noqa: S603 -- controlled Git fixture command
            [  # noqa: S607 -- controlled Git fixture command
                "git", "-C", str(self.user), "branch", "--set-upstream-to",
                "origin/main", "main",
            ],
            check=True,
        )
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

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
            ["collection", "review"],
        )
        self.assertIn(
            "phase=publication",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        subprocess.run(
            ["git", "-C", str(self.user), "merge", "--ff-only", "origin/main"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        invalid_result = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_CANONICAL_INVALID_COLLECTION": "1",
            },
        )
        self.assertEqual(invalid_result.returncode, 75)
        invalid_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(
            len(invalid_logs),
            1,
            msg=f"stdout={invalid_result.stdout}\nstderr={invalid_result.stderr}\nstatus={(runtime / 'last-status.txt').read_text(encoding='utf-8')}\ncollection_stderr={[path.read_text(encoding='utf-8') for path in (runtime / 'logs').rglob('collection.stderr.log')]}",
        )
        self.assertEqual(
            invalid_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection"],
        )
        self.assertIn(
            "phase=collection",
            (runtime / "last-status.txt").read_text(encoding="utf-8"),
        )
        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()

        standing = self.agents / "tasks" / "standing.md"
        standing_relative = "tasks/standing.md"
        standing_head_before = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f"HEAD:{standing_relative}"]
        )
        standing.write_bytes(standing.read_bytes() + b"staged parallel evidence\n")
        subprocess.run(
            ["git", "-C", str(self.agents), "add", standing_relative], check=True
        )
        standing_index_before = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f":{standing_relative}"]
        )
        standing.write_bytes(standing.read_bytes() + b"unstaged parallel evidence\n")
        standing_worktree_before = standing.read_bytes()
        standing_status_before = subprocess.check_output(
            ["git", "-C", str(self.agents), "status", "--porcelain=v1", "--", standing_relative],
            text=True,
        )

        (self.user / "local-ahead.md").write_text("local ahead\n", encoding="utf-8")
        unsafe_handoff = self.user / ".codex-handoff" / "unsafe.md"
        unsafe_handoff.parent.mkdir()
        unsafe_handoff.write_text(
            f"existing unsafe handoff {Path.home()}\n", encoding="utf-8"
        )
        unsafe_handoff.chmod(0o640)
        unsafe_before = unsafe_handoff.read_bytes()
        unsafe_stat_before = unsafe_handoff.stat()
        agents_unsafe = self.agents / ".codex-handoff" / "unsafe-agents.md"
        agents_unsafe.parent.mkdir()
        agents_unsafe.write_text(
            f"existing unsafe agents residual {Path.home()}\n", encoding="utf-8"
        )
        agents_unsafe.chmod(0o640)
        agents_unsafe_before = agents_unsafe.read_bytes()
        agents_unsafe_stat_before = agents_unsafe.stat()
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
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_COMMIT_HEAD_DRIFT_ONCE": "1",
                "FAKE_PLAN_TARGET_CONFLICT_ONCE": "1",
            },
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(runtime / "last-status.txt").read_text(encoding="utf-8")
            + "\n"
            + "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (runtime / "logs").rglob("*.stderr.log")
            )
            + "\nRESULTS\n"
            + "\n".join(
                path.name + ":" + path.read_text(encoding="utf-8", errors="replace")
                for path in (runtime / "logs").rglob("*result.json")
            ),
        )
        invocation_logs = list((runtime / "logs").rglob("invocations.log"))
        self.assertEqual(len(invocation_logs), 1)
        self.assertEqual(
            invocation_logs[0].read_text(encoding="utf-8").splitlines(),
            ["collection", "review", "review", "evidence_review"],
        )
        self.assertTrue((runtime / "commit-head-drift-once.marker").is_file())
        conflict_marker = runtime / "artifact-target-conflict-once.marker"
        self.assertTrue(conflict_marker.is_file())
        conflicted_target = Path(conflict_marker.read_text(encoding="utf-8").strip())
        self.assertEqual(
            conflicted_target.read_text(encoding="utf-8"),
            "concurrent target must survive\n",
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
        self.assertEqual(publication_result["publication_mode"]["user_vault"], "own_only")
        self.assertEqual(
            publication_result["publication_mode"]["agents_vault"], "own_only"
        )
        self.assertEqual(
            [item["path"] for item in publication_result["deferred_cleanup"]["user_vault"]],
            [
                ".codex-handoff/unsafe.md",
                str(conflicted_target.resolve().relative_to(self.user.resolve())),
            ],
        )
        self.assertEqual(
            [
                item["path"]
                for item in publication_result["deferred_cleanup"]["agents_vault"]
            ],
            [".codex-handoff/unsafe-agents.md", standing_relative],
        )
        self.assertEqual(unsafe_handoff.read_bytes(), unsafe_before)
        unsafe_stat_after = unsafe_handoff.stat()
        self.assertEqual(unsafe_stat_after.st_mode, unsafe_stat_before.st_mode)
        self.assertEqual(unsafe_stat_after.st_mtime_ns, unsafe_stat_before.st_mtime_ns)
        self.assertEqual(agents_unsafe.read_bytes(), agents_unsafe_before)
        agents_unsafe_stat_after = agents_unsafe.stat()
        self.assertEqual(
            agents_unsafe_stat_after.st_mode, agents_unsafe_stat_before.st_mode
        )
        self.assertEqual(
            agents_unsafe_stat_after.st_mtime_ns,
            agents_unsafe_stat_before.st_mtime_ns,
        )
        standing_head_after = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f"HEAD:{standing_relative}"]
        )
        standing_index_after = subprocess.check_output(
            ["git", "-C", str(self.agents), "show", f":{standing_relative}"]
        )
        standing_worktree_after = standing.read_bytes()
        standing_status_after = subprocess.check_output(
            ["git", "-C", str(self.agents), "status", "--porcelain=v1", "--", standing_relative],
            text=True,
        )
        self.assertNotIn(b"staged parallel evidence", standing_head_after)
        self.assertIn(b"vault-change-publisher:", standing_head_after)
        self.assertIn(b"staged parallel evidence", standing_index_after)
        self.assertNotIn(b"unstaged parallel evidence", standing_index_after)
        self.assertIn(b"vault-change-publisher:", standing_index_after)
        self.assertIn(b"staged parallel evidence", standing_worktree_after)
        self.assertIn(b"unstaged parallel evidence", standing_worktree_after)
        self.assertIn(b"vault-change-publisher:", standing_worktree_after)
        self.assertEqual(standing_status_after, standing_status_before)

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
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

        subprocess.run(["chmod", "-R", "u+w", str(runtime / "logs")], check=True)
        shutil.rmtree(runtime / "logs")
        (runtime / "last-status.txt").unlink()
        evidence_rejection_before = standing.read_bytes()
        evidence_rejection_stat = standing.stat()
        rejected_evidence = subprocess.run(
            [str(runtime / "run-daily-it-news-vulnerability-check.sh")],
            check=False,
            env={
                **os.environ,
                "PATH": os.environ["PATH"],
                "FAKE_EVIDENCE_REVIEW_BLOCKED": "1",
            },
        )
        self.assertEqual(rejected_evidence.returncode, 75)
        self.assertEqual(standing.read_bytes(), evidence_rejection_before)
        self.assertEqual(standing.stat().st_mode, evidence_rejection_stat.st_mode)
        self.assertEqual(
            standing.stat().st_mtime_ns, evidence_rejection_stat.st_mtime_ns
        )


if __name__ == "__main__":
    unittest.main()
