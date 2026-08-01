#!/usr/bin/env python3
"""Validate a digest-bound read-only publication review result."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Optional


class ReviewError(RuntimeError):
    """Represent a review result that cannot authorize mutation."""


def sha256(path: Path) -> str:
    """Hash one runner-owned context file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_regular_nofollow(path: Path) -> str:
    """Hash a regular snapshot without following a final-component symlink."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReviewError("authorization snapshot is not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 65536):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ReviewError("authorization snapshot changed while being validated")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def relative_target(root: str, target: str) -> str:
    """Return a normalized repo-relative planned target."""
    root_path = Path(root).resolve()
    raw_target = Path(target)
    if not raw_target.is_absolute():
        raise ReviewError("planned target is not absolute")
    target_path = raw_target.resolve()
    try:
        relative = target_path.relative_to(root_path)
    except ValueError as exc:
        raise ReviewError("planned target escapes its Vault") from exc
    if ".." in relative.parts or not relative.parts:
        raise ReviewError("invalid planned target")
    return str(relative)


def validate_manifest(
    manifest: dict[str, object],
    state: dict[str, object],
    repo_root: str,
    task_id: str,
    artifact: dict[str, str],
    evidence_target: Optional[str],
    gitleaks_version: str,
) -> None:
    """Bind one Task Change Manifest to state and one planned artifact."""
    if manifest["repo_root"] != repo_root or manifest["task_id"] != task_id:
        raise ReviewError("Task Change Manifest identity mismatch")
    if (
        state["repo_root"] != repo_root
        or state["branch"] != "main"
        or state["upstream"] != "origin/main"
        or state["local_head"] != state["remote_head"]
        or state["operation_in_progress"] is not False
    ):
        raise ReviewError("pre-publication Git state is not safe")
    artifact_target = relative_target(repo_root, artifact["target_path"])
    initial_paths = sorted(set(state["dirty_paths"]) | {artifact_target})
    if any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in initial_paths
    ):
        raise ReviewError("publication scope contains a forbidden .obsidian path")
    expected_owned = set(initial_paths)
    expected_evidence = None
    if evidence_target is not None:
        expected_evidence = relative_target(repo_root, evidence_target)
        if (
            expected_evidence == ".obsidian"
            or expected_evidence.startswith(".obsidian/")
        ):
            raise ReviewError("evidence target is a forbidden .obsidian path")
        expected_owned.add(expected_evidence)
    if sorted(manifest["owned_paths"]) != sorted(expected_owned):
        raise ReviewError("owned paths do not cover the exact publication scope")
    if manifest["excluded_paths"] or manifest["unrelated_dirty_paths"]:
        raise ReviewError("approved publication contains excluded dirty paths")
    if manifest["approved_diff_snapshot_sha256"] != state["diff_snapshot_sha256"]:
        raise ReviewError("approved diff snapshot digest mismatch")
    if any(
        entry["mode"] not in {"100644", "100755", None}
        for entry in state["dirty_entries"]
    ) or manifest["approved_dirty_entries"] != state["dirty_entries"]:
        raise ReviewError("approved dirty blobs/modes do not match pre-state")
    reviews = manifest["reviewed_artifacts"]
    if reviews != [
        {
            "role": artifact["role"],
            "source_sha256": artifact["source_sha256"],
            "target_path": artifact_target,
        }
    ]:
        raise ReviewError("reviewed artifact binding mismatch")
    if manifest["review_or_validation_status"] != "quality_ok":
        raise ReviewError("review did not return quality_ok")
    evidence = manifest["validation_evidence"]
    if evidence != {
        "file_guard": "passed",
        "secret_scan": "passed",
        "secret_scan_tool": "gitleaks",
        "secret_scan_tool_version": gitleaks_version,
        "reviewed_snapshot_sha256": state["diff_snapshot_sha256"],
    }:
        raise ReviewError("typed validation evidence mismatch")
    if manifest["commit_required"] is not True:
        raise ReviewError("planned artifact always requires a commit")
    grouped = [
        path
        for group in manifest["commit_groups"]
        for path in group["paths"]
    ]
    if len(grouped) != len(set(grouped)) or sorted(grouped) != initial_paths:
        raise ReviewError("commit groups do not partition initial commit paths")
    finalization = manifest["evidence_finalization"]
    if expected_evidence is None:
        if finalization is not None:
            raise ReviewError("User Vault must not have an evidence finalization")
    elif finalization != {
        "target_path": expected_evidence,
        "template": "daily_publication_v1",
    }:
        raise ReviewError("evidence finalization binding mismatch")


def main(argv: list[str]) -> int:
    """Validate the approved review and return a fail-closed status."""
    if len(argv) != 6:
        print(
            "usage: validate-publication-review.py REVIEW CONTEXT PRE_STATE PLAN AUTH_SHA",
            file=sys.stderr,
        )
        return 64
    try:
        review = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        context = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        if review.get("outcome") != "approved":
            raise ReviewError("publication review is not approved")
        if review["publication_context_sha256"] != sha256(Path(argv[2])):
            raise ReviewError("publication context digest mismatch")
        if context["authorization_task_sha256"] != argv[5]:
            raise ReviewError("authorization evidence digest mismatch")
        if sha256_regular_nofollow(Path(context["authorization_task"])) != argv[5]:
            raise ReviewError("authorization snapshot digest mismatch")
        manifest = context["publication_manifest"]["artifact_manifest"]
        gitleaks_version = context["runtime"]["gitleaks_version"]
        validate_manifest(
            review["agents_vault"],
            pre["agents_vault"],
            context["runtime"]["agents_vault_root"],
            context["authorization_task_id"],
            {
                "role": manifest["advisory"]["role"],
                "source_sha256": manifest["advisory"]["sha256"],
                "target_path": plan["advisory_target"],
            },
            context["standing_task"],
            gitleaks_version,
        )
        validate_manifest(
            review["user_vault"],
            pre["user_vault"],
            context["runtime"]["user_vault_root"],
            context["authorization_task_id"],
            {
                "role": manifest["summary"]["role"],
                "source_sha256": manifest["summary"]["sha256"],
                "target_path": plan["summary_target"],
            },
            None,
            gitleaks_version,
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ReviewError,
    ) as exc:
        print(f"publication review validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
