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
from typing import Any, Optional


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
) -> dict[str, object]:
    """Bind staged dirty blobs to the runner-captured pre-collection entries."""
    manifest_path = Path(context["dirty_snapshot_manifest_file"])
    expected_manifest_digest = context["dirty_snapshot_manifest_sha256"]
    manifest_bytes = read_regular_nofollow(manifest_path)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_digest:
        raise ReviewError("dirty snapshot manifest digest mismatch")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("version") != 4 or set(manifest.get("vaults", {})) != {
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
                deleted_not_required = (
                    actual.get("materialization_status") == "not_required"
                    and actual.get("materialization_reason") is None
                )
                deleted_deferred = (
                    actual.get("materialization_status") == "deferred"
                    and isinstance(actual.get("materialization_reason"), str)
                    and bool(actual["materialization_reason"])
                )
                if (
                    actual.get("snapshot") is not None
                    or actual.get("sha256") is not None
                    or not (deleted_not_required or deleted_deferred)
                ):
                    raise ReviewError("deleted dirty entry unexpectedly has snapshot bytes")
                continue
            status = actual.get("materialization_status")
            if status == "deferred":
                if (
                    actual.get("snapshot") is not None
                    or actual.get("sha256") is not None
                    or not isinstance(actual.get("materialization_reason"), str)
                    or not actual["materialization_reason"]
                ):
                    raise ReviewError("deferred dirty entry has invalid materialization evidence")
                continue
            if (
                status != "available"
                or actual.get("materialization_reason") is not None
                or expected["mode"] not in {"100644", "100755"}
            ):
                raise ReviewError("dirty snapshot materialization status is invalid")
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
                )
            }
            if actual_identity != expected:
                raise ReviewError("local commit snapshot identity mismatch")
            status = actual.get("materialization_status")
            if status == "blocked":
                if (
                    actual.get("snapshot") is not None
                    or actual.get("sha256") is not None
                    or not isinstance(actual.get("materialization_reason"), str)
                    or not actual["materialization_reason"]
                ):
                    raise ReviewError("blocked local commit has invalid materialization evidence")
                continue
            if status != "available" or actual.get("materialization_reason") is not None:
                raise ReviewError("local commit materialization status is invalid")
            expected_relative = f"commit-snapshots/{label}/{index:04d}.patch"
            if actual.get("snapshot") != expected_relative:
                raise ReviewError("local commit snapshot path mismatch")
            snapshot_bytes = read_regular_beneath(
                manifest_path.parent, PurePosixPath(expected_relative)
            )
            digest = hashlib.sha256(snapshot_bytes).hexdigest()
            if digest != actual.get("sha256") or digest != actual.get("patch_sha256"):
                raise ReviewError("local commit patch digest mismatch")
    return manifest


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
    mode_hint: dict[str, object],
    materialized_dirty: Optional[list[dict[str, object]]] = None,
    materialized_commits: Optional[list[dict[str, object]]] = None,
) -> None:
    """Bind one layered review manifest to state and one planned artifact."""
    if manifest["repo_root"] != repo_root or manifest["task_id"] != task_id:
        raise ReviewError("Task Change Manifest identity mismatch")
    if state["repo_root"] != repo_root:
        raise ReviewError("pre-publication repository identity mismatch")
    declared_mode = manifest.get("publication_mode")
    if declared_mode not in {"sweep", "own_only", "blocked"}:
        raise ReviewError("publication mode is missing or invalid")
    if declared_mode == "sweep" and any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in state.get("dirty_paths", [])
    ):
        raise ReviewError("sweep scope contains a forbidden .obsidian path")
    local_commits = state.get("local_commits", [])
    history_capture_status = state.get("history_capture_status", "available")
    if (
        state["history_relation"] == "equal" and local_commits
    ) or (
        state["history_relation"] == "local_ahead"
        and history_capture_status == "available"
        and not local_commits
    ):
        raise ReviewError("pre-publication local history metadata is inconsistent")
    materialized_commits = materialized_commits or []
    if len(materialized_commits) != len(local_commits):
        raise ReviewError("local commit materialization count mismatch")
    expected_approved_commits = [
        {**commit, "patch_sha256": materialized.get("patch_sha256")}
        for commit, materialized in zip(local_commits, materialized_commits)
    ]
    if manifest["approved_existing_commits"] != expected_approved_commits:
        raise ReviewError("reviewed local-only commits do not match pre-state")
    artifact_target = relative_target(repo_root, artifact["target_path"])
    dirty_paths = sorted(state["dirty_paths"])
    initial_paths = sorted(set(dirty_paths) | {artifact_target})
    existing_paths = sorted(
        {
            path
            for commit in local_commits
            for path in commit["changed_paths"]
        }
    )
    if declared_mode == "sweep" and any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in [*initial_paths, *existing_paths]
    ):
        raise ReviewError("sweep scope contains a forbidden .obsidian path")
    if artifact_target == ".obsidian" or artifact_target.startswith(".obsidian/"):
        raise ReviewError("artifact target is a forbidden .obsidian path")
    expected_evidence = None
    if evidence_target is not None:
        expected_evidence = relative_target(repo_root, evidence_target)
        if (
            expected_evidence == ".obsidian"
            or expected_evidence.startswith(".obsidian/")
        ):
            raise ReviewError("evidence target is a forbidden .obsidian path")
    if manifest["approved_diff_snapshot_sha256"] != state["diff_snapshot_sha256"]:
        raise ReviewError("approved diff snapshot digest mismatch")
    reviews = manifest["reviewed_artifacts"]
    if reviews != [
        {
            "role": artifact["role"],
            "source_sha256": artifact["source_sha256"],
            "target_path": artifact_target,
        }
    ]:
        raise ReviewError("reviewed artifact binding mismatch")
    evidence = manifest["validation_evidence"]
    expected_validation_evidence = {
        "secret_scan_tool": "gitleaks",
        "secret_scan_tool_version": gitleaks_version,
        "reviewed_snapshot_sha256": state["diff_snapshot_sha256"],
        "reviewed_history_sha256": state["history_snapshot_sha256"],
    }
    if any(evidence.get(key) != value for key, value in expected_validation_evidence.items()) or evidence.get(
        "file_guard"
    ) not in {"passed", "blocked"} or evidence.get("secret_scan") not in {
        "passed", "blocked"
    }:
        raise ReviewError("typed validation evidence mismatch")
    mode = declared_mode
    required_mode = mode_hint["required_mode"]
    rank = {"sweep": 0, "own_only": 1, "blocked": 2}
    if mode not in rank or required_mode not in rank or rank[mode] < rank[required_mode]:
        raise ReviewError("review mode weakens the deterministic mode hint")
    if materialized_dirty is None:
        materialized_dirty = [
            {
                **entry,
                "materialization_status": (
                    "not_required" if entry.get("mode") is None else "available"
                ),
            }
            for entry in state.get("dirty_entries", [])
        ]
    if materialized_commits is None:
        materialized_commits = [
            {**entry, "materialization_status": "available"}
            for entry in state.get("local_commits", [])
        ]
    unavailable_dirty = [
        entry for entry in materialized_dirty
        if entry.get("materialization_status") == "deferred"
    ]
    unavailable_commits = [
        entry for entry in materialized_commits
        if entry.get("materialization_status") != "available"
    ]
    if unavailable_commits and mode != "blocked":
        raise ReviewError("unreviewable local-only history requires blocked mode")
    if unavailable_dirty and mode == "sweep":
        raise ReviewError("unreviewable dirty residual cannot use sweep mode")
    if manifest["core_review_status"] == "quality_ok" and manifest[
        "review_or_validation_status"
    ] != "quality_ok":
        raise ReviewError("core review status fields disagree")
    deferred = manifest["deferred_cleanup"]
    deferred_paths = [item["path"] for item in deferred]
    if len(deferred_paths) != len(set(deferred_paths)):
        raise ReviewError("deferred cleanup paths are duplicated")
    grouped = [
        path
        for group in manifest["commit_groups"]
        for path in group["paths"]
    ]
    if len(grouped) != len(set(grouped)):
        raise ReviewError("commit groups contain duplicate paths")
    finalization = manifest["evidence_finalization"]
    expected_finalization = (
        None
        if expected_evidence is None or mode == "blocked"
        else {"target_path": expected_evidence, "template": "daily_publication_v1"}
    )
    if finalization != expected_finalization:
        raise ReviewError("evidence finalization binding mismatch")

    if mode == "sweep":
        expected_owned = set(initial_paths) | set(existing_paths)
        if expected_evidence is not None:
            expected_owned.add(expected_evidence)
        if any(
            path == ".obsidian" or path.startswith(".obsidian/")
            for path in expected_owned
        ):
            raise ReviewError("sweep scope contains a forbidden .obsidian path")
        if sorted(manifest["owned_paths"]) != sorted(expected_owned):
            raise ReviewError("sweep owned paths do not cover the exact scope")
        if (
            manifest["excluded_paths"]
            or manifest["unrelated_dirty_paths"]
            or deferred
            or manifest["residual_review_status"] != "quality_ok"
            or manifest["core_review_status"] != "quality_ok"
            or evidence["file_guard"] != "passed"
            or evidence["secret_scan"] != "passed"
            or manifest["commit_required"] is not True
        ):
            raise ReviewError("sweep review contains a deferred or blocked scope")
        if any(
            entry["mode"] not in {"100644", "100755", None}
            for entry in state["dirty_entries"]
        ) or manifest["approved_dirty_entries"] != state["dirty_entries"]:
            raise ReviewError("sweep dirty blobs/modes do not match pre-state")
        if sorted(grouped) != initial_paths:
            raise ReviewError("sweep groups do not partition dirty paths and artifact")
    elif mode == "own_only":
        expected_owned = {artifact_target}
        if expected_evidence is not None:
            expected_owned.add(expected_evidence)
        if sorted(manifest["owned_paths"]) != sorted(expected_owned):
            raise ReviewError("own_only contains a non-owned path")
        if (
            manifest["approved_dirty_entries"]
            or sorted(manifest["excluded_paths"]) != dirty_paths
            or sorted(manifest["unrelated_dirty_paths"]) != dirty_paths
            or sorted(deferred_paths) != dirty_paths
            or manifest["residual_review_status"] != "deferred"
            or manifest["core_review_status"] != "quality_ok"
            or evidence["file_guard"] != "passed"
            or evidence["secret_scan"] != "passed"
            or manifest["commit_required"] is not True
            or grouped != [artifact_target]
        ):
            raise ReviewError("own_only manifest does not defer the exact residual scope")
    else:
        expected_owned = {artifact_target}
        if (
            sorted(manifest["owned_paths"]) != sorted(expected_owned)
            or manifest["approved_dirty_entries"]
            or sorted(manifest["excluded_paths"]) != dirty_paths
            or sorted(manifest["unrelated_dirty_paths"]) != dirty_paths
            or sorted(deferred_paths) != dirty_paths
            or manifest["commit_required"] is not False
            or manifest["commit_groups"]
            or manifest["residual_review_status"] == "quality_ok"
        ):
            raise ReviewError(
                "blocked manifest does not preserve the exact non-actionable scope"
            )
        if manifest["core_review_status"] == "blocked" and manifest[
            "review_or_validation_status"
        ] != "blocked":
            raise ReviewError("blocked core status fields disagree")


def validate_root_contract(review: dict[str, Any]) -> None:
    """Bind root outcome and next action to the two per-Vault modes."""
    modes = {
        review["agents_vault"]["publication_mode"],
        review["user_vault"]["publication_mode"],
    }
    if "blocked" in modes:
        if not isinstance(review.get("next_action"), str) or not review[
            "next_action"
        ]:
            raise ReviewError("blocked Vault requires a concrete next action")
    elif review.get("next_action") is not None:
        raise ReviewError("successful per-Vault review has an unexpected next action")
    if review["outcome"] == "approved" and modes == {"blocked"}:
        raise ReviewError("approved review cannot block both Vaults")
    if review["outcome"] == "blocked" and modes != {"blocked"}:
        raise ReviewError("root blocked outcome requires both Vaults blocked")


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
        if review.get("outcome") not in {"approved", "blocked"}:
            raise ReviewError("publication review outcome is invalid")
        if review["publication_context_sha256"] != sha256(Path(argv[2])):
            raise ReviewError("publication context digest mismatch")
        if context["authorization_task_sha256"] != argv[5]:
            raise ReviewError("authorization evidence digest mismatch")
        if sha256_regular_nofollow(Path(context["authorization_task"])) != argv[5]:
            raise ReviewError("authorization snapshot digest mismatch")
        materialization = validate_dirty_snapshots(context, pre)
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
            context["publication_mode_hint"]["agents_vault"],
            materialization["vaults"]["agents_vault"],
            materialization["local_commits"]["agents_vault"],
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
            context["publication_mode_hint"]["user_vault"],
            materialization["vaults"]["user_vault"],
            materialization["local_commits"]["user_vault"],
        )
        validate_root_contract(review)
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
