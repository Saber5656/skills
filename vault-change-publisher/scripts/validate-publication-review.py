#!/usr/bin/env python3
"""Validate a digest-bound read-only publication review result."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Optional


class ReviewError(RuntimeError):
    """Represent a review result that cannot authorize mutation."""


def sha256(path: Path) -> str:
    """Hash one runner-owned context file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_regular_nofollow(path: Path) -> bytes:
    """Read a stable regular snapshot without following a final-component symlink."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReviewError("authorization snapshot is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
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
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256_regular_nofollow(path: Path) -> str:
    """Hash one stable regular snapshot."""
    return hashlib.sha256(read_regular_nofollow(path)).hexdigest()


def read_regular_beneath(root: Path, relative: PurePosixPath) -> bytes:
    """Read a stable regular file without following any relative path component."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    opened = [os.open(root, flags)]
    try:
        current = opened[-1]
        for component in relative.parts[:-1]:
            current = os.open(component, flags, dir_fd=current)
            opened.append(current)
        descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ReviewError("dirty snapshot is not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 65536):
                chunks.append(chunk)
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
                raise ReviewError("dirty snapshot changed while being validated")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def validate_dirty_snapshots(
    context: dict[str, object], pre: dict[str, object]
) -> None:
    """Bind staged dirty blobs to the runner-captured pre-collection entries."""
    manifest_path = Path(context["dirty_snapshot_manifest_file"])
    expected_manifest_digest = context["dirty_snapshot_manifest_sha256"]
    manifest_bytes = read_regular_nofollow(manifest_path)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_digest:
        raise ReviewError("dirty snapshot manifest digest mismatch")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("version") != 2 or set(manifest.get("vaults", {})) != {
        "agents_vault",
        "user_vault",
    } or set(manifest.get("local_commits", {})) != {
        "agents_vault",
        "user_vault",
    }:
        raise ReviewError("dirty snapshot manifest shape mismatch")
    for label, state_key in (("agents", "agents_vault"), ("user", "user_vault")):
        actual_entries = manifest["vaults"][state_key]
        expected_entries = pre[state_key]["dirty_entries"]
        if len(actual_entries) != len(expected_entries):
            raise ReviewError("dirty snapshot entry count mismatch")
        for index, (actual, expected) in enumerate(zip(actual_entries, expected_entries)):
            if {
                "path": actual.get("path"),
                "git_blob_oid": actual.get("git_blob_oid"),
                "mode": actual.get("mode"),
            } != expected:
                raise ReviewError("dirty snapshot identity mismatch")
            if expected["mode"] is None and expected["git_blob_oid"] is None:
                if actual.get("snapshot") is not None or actual.get("sha256") is not None:
                    raise ReviewError("deleted dirty entry unexpectedly has snapshot bytes")
                continue
            expected_relative = f"dirty-snapshots/{label}/{index:04d}.blob"
            if actual.get("snapshot") != expected_relative:
                raise ReviewError("dirty snapshot path mismatch")
            snapshot_bytes = read_regular_beneath(
                manifest_path.parent, PurePosixPath(expected_relative)
            )
            if hashlib.sha256(snapshot_bytes).hexdigest() != actual.get("sha256"):
                raise ReviewError("dirty snapshot content digest mismatch")
        actual_commits = manifest["local_commits"][state_key]
        expected_commits = pre[state_key].get("local_commits", [])
        if len(actual_commits) != len(expected_commits):
            raise ReviewError("local commit snapshot count mismatch")
        for index, (actual, expected) in enumerate(
            zip(actual_commits, expected_commits)
        ):
            actual_identity = {
                key: actual.get(key)
                for key in (
                    "commit", "parents", "tree", "message", "changed_paths",
                    "patch_sha256",
                )
            }
            if actual_identity != expected:
                raise ReviewError("local commit snapshot identity mismatch")
            expected_relative = f"commit-snapshots/{label}/{index:04d}.patch"
            if actual.get("snapshot") != expected_relative:
                raise ReviewError("local commit snapshot path mismatch")
            snapshot_bytes = read_regular_beneath(
                manifest_path.parent, PurePosixPath(expected_relative)
            )
            digest = hashlib.sha256(snapshot_bytes).hexdigest()
            if digest != actual.get("sha256") or digest != expected["patch_sha256"]:
                raise ReviewError("local commit patch digest mismatch")


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
        or state.get("history_relation") not in {"equal", "local_ahead"}
        or state["operation_in_progress"] is not False
    ):
        raise ReviewError("pre-publication Git state is not safe")
    local_commits = state.get("local_commits", [])
    if (
        state["history_relation"] == "equal" and local_commits
    ) or (
        state["history_relation"] == "local_ahead" and not local_commits
    ):
        raise ReviewError("pre-publication local history metadata is inconsistent")
    if manifest["approved_existing_commits"] != local_commits:
        raise ReviewError("approved local-only commits do not match pre-state")
    artifact_target = relative_target(repo_root, artifact["target_path"])
    initial_paths = sorted(set(state["dirty_paths"]) | {artifact_target})
    existing_paths = sorted(
        {
            path
            for commit in local_commits
            for path in commit["changed_paths"]
        }
    )
    if any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in [*initial_paths, *existing_paths]
    ):
        raise ReviewError("publication scope contains a forbidden .obsidian path")
    expected_owned = set(initial_paths) | set(existing_paths)
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
        "reviewed_history_sha256": state["history_snapshot_sha256"],
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
    if len(argv) != 7:
        print(
            "usage: validate-publication-review.py REVIEW CONTEXT PRE_STATE PLAN AUTH_SHA REVIEW_SHA",
            file=sys.stderr,
        )
        return 64
    try:
        review_bytes = read_regular_nofollow(Path(argv[1]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[6]:
            raise ReviewError("publication review file digest mismatch")
        review = json.loads(review_bytes)
        context = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        if pre != context["pre_collection_state"] or plan != context["artifact_plan"]:
            raise ReviewError("mutable publication inputs differ from reviewed context")
        if review.get("outcome") != "approved":
            raise ReviewError("publication review is not approved")
        if review["publication_context_sha256"] != sha256(Path(argv[2])):
            raise ReviewError("publication context digest mismatch")
        if context["authorization_task_sha256"] != argv[5]:
            raise ReviewError("authorization evidence digest mismatch")
        if sha256_regular_nofollow(Path(context["authorization_task"])) != argv[5]:
            raise ReviewError("authorization snapshot digest mismatch")
        validate_dirty_snapshots(context, pre)
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
