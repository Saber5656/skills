#!/usr/bin/env python3
"""Append deterministic, personal-path-free evidence after initial pushes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

from git_diff_digest import git_diff_digest


class EvidenceError(RuntimeError):
    """Represent an unsafe or incomplete evidence preparation."""


def read_regular_nofollow(path: Path) -> bytes:
    """Read stable bytes from one regular file without following its final link."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("input is not a regular file")
        content = b""
        while chunk := os.read(descriptor, 1024 * 1024):
            content += chunk
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise EvidenceError("input changed while being read")
        return content
    finally:
        os.close(descriptor)


def relative_path(root: str, absolute: str) -> str:
    """Convert an installed artifact path to a normalized repo-relative path."""
    try:
        relative = Path(absolute).resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise EvidenceError("published artifact path escapes its Vault") from exc
    if not relative.parts or ".." in relative.parts:
        raise EvidenceError("invalid published artifact path")
    return str(relative)


def append_no_follow(root: Path, relative: PurePosixPath, content: bytes) -> None:
    """Append through descriptor-relative no-follow traversal and fsync."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    opened = []
    directory_fd = os.open(root, flags)
    opened.append(directory_fd)
    try:
        for component in relative.parts[:-1]:
            directory_fd = os.open(component, flags, dir_fd=directory_fd)
            opened.append(directory_fd)
        file_flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        file_fd = os.open(relative.name, file_flags, dir_fd=directory_fd)
        opened.append(file_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise EvidenceError("evidence target is not a regular file")
        existing = b""
        while chunk := os.read(file_fd, 1024 * 1024):
            existing += chunk
        marker = next(
            line for line in content.splitlines() if b"vault-change-publisher:" in line
        )
        if marker in existing:
            raise EvidenceError("run evidence marker already exists")
        os.lseek(file_fd, 0, os.SEEK_END)
        prefix = b"" if not existing or existing.endswith(b"\n") else b"\n"
        os.write(file_fd, prefix + content)
        os.fsync(file_fd)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def main(argv: list[str]) -> int:
    """Prepare one deterministic evidence hunk and emit its review plan."""
    if len(argv) != 9:
        print(
            "usage: prepare-publication-evidence.py RUNTIME CONTEXT REVIEW "
            "INITIAL_RESULT RUN_ID STARTED_AT REVIEW_SHA256 PLAN_OUTPUT",
            file=sys.stderr,
        )
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        context_path = Path(argv[2])
        review_bytes = read_regular_nofollow(Path(argv[3]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[7]:
            raise EvidenceError("approved review digest mismatch")
        review = json.loads(review_bytes.decode("utf-8"))
        initial = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        if not all(
            initial[key]["push_status"] in {"complete", "not_required"}
            and initial[key]["local_head"] == initial[key]["remote_head"]
            for key in ("agents_vault", "user_vault")
        ):
            raise EvidenceError("initial pushes are not complete")
        finalization = review["agents_vault"]["evidence_finalization"]
        if finalization["template"] != "daily_publication_v1":
            raise EvidenceError("unsupported evidence template")
        target_relative = PurePosixPath(finalization["target_path"])
        if target_relative.is_absolute() or ".." in target_relative.parts:
            raise EvidenceError("invalid evidence target")
        context_digest = hashlib.sha256(context_path.read_bytes()).hexdigest()
        payload = {
            "run_id": argv[5],
            "started_at": argv[6],
            "publication_context_sha256": context_digest,
            "agents_vault": {
                "commit_hashes": initial["agents_vault"]["commit_hashes"],
                "push_status": initial["agents_vault"]["push_status"],
                "local_remote_equal": True,
            },
            "user_vault": {
                "commit_hashes": initial["user_vault"]["commit_hashes"],
                "push_status": initial["user_vault"]["push_status"],
                "local_remote_equal": True,
            },
            "summary_repo_path": relative_path(
                runtime["user_vault_root"], initial["summary_path"]
            ),
            "advisory_repo_path": relative_path(
                runtime["agents_vault_root"], initial["advisory_path"]
            ),
        }
        marker = f"vault-change-publisher:{argv[5]}"
        block = (
            f"\n## Daily publication evidence — {argv[5]}\n"
            f"<!-- {marker} -->\n\n"
            "```json\n"
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n"
            "```\n"
        ).encode("utf-8")
        append_no_follow(
            Path(runtime["agents_vault_root"]), target_relative, block
        )
        plan = {
            "template": "daily_publication_v1",
            "target_path": str(target_relative),
            "evidence_diff_sha256": git_diff_digest(
                runtime["agents_vault_root"], str(target_relative)
            ),
            "marker": marker,
        }
        Path(argv[8]).write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )
    except (
        EvidenceError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"evidence preparation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
