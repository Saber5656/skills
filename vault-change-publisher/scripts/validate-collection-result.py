#!/usr/bin/env python3
"""Validate collection output paths and hashes before publication privileges exist."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
SUMMARY_NAME = re.compile(r"^SUMMARY-IT-NEWS-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ADVISORY_NAME = re.compile(r"^Personal-Vulnerability-Advisory-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")

class ValidationError(RuntimeError):
    """Represent a collection result that must block publication."""


def digest_fd(descriptor: int) -> tuple[str, bytes]:
    """Hash and retain bounded bytes from one already-open descriptor."""
    hasher = hashlib.sha256()
    content = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        hasher.update(chunk)
        content.extend(chunk)
    return hasher.hexdigest(), bytes(content)


def validate_artifact(
    path_value: str,
    expected_hash: str,
    staging_root: Path,
    earliest_mtime: int,
    expected_date: str,
    role: str,
) -> None:
    """Require a same-run regular non-symlink file below staging root."""
    path = Path(path_value)
    if not path.is_absolute():
        raise ValidationError("artifact is not an absolute regular non-symlink file")
    root = Path(os.path.abspath(staging_root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValidationError("artifact escapes run staging root") from exc
    if not relative.parts or ".." in relative.parts:
        raise ValidationError("artifact escapes run staging root")
    expected_pattern = SUMMARY_NAME if role == "summary" else ADVISORY_NAME
    match = expected_pattern.fullmatch(path.name)
    if not match or "-".join(match.groups()) != expected_date:
        raise ValidationError("artifact filename does not match current JST run date")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    opened = []
    directory_fd = os.open(root, directory_flags)
    opened.append(directory_fd)
    try:
        for component in relative.parts[:-1]:
            directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened.append(directory_fd)
        descriptor = os.open(relative.name, flags, dir_fd=directory_fd)
        opened.append(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidationError("artifact is not a regular file")
        if metadata.st_size <= 0 or metadata.st_size > MAX_ARTIFACT_BYTES:
            raise ValidationError("artifact size is outside the allowed range")
        if metadata.st_mtime < earliest_mtime:
            raise ValidationError("artifact predates this collection run")
        actual_hash, content = digest_fd(descriptor)
        if actual_hash != expected_hash:
            raise ValidationError("artifact SHA-256 mismatch")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("artifact is not UTF-8 text") from exc
    finally:
        for opened_descriptor in reversed(opened):
            os.close(opened_descriptor)


def main(argv: list[str]) -> int:
    """Validate one collection result and return a fail-closed status."""
    if len(argv) != 5:
        print(
            "usage: validate-collection-result.py RESULT STAGING_ROOT RUN_ID START_EPOCH",
            file=sys.stderr,
        )
        return 64
    try:
        result = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        if result.get("daily_pipeline_status") != "complete":
            raise ValidationError("daily pipeline is not complete")
        if result.get("vault_artifacts_complete") is not True:
            raise ValidationError("artifact set is incomplete")
        if result.get("run_id") != argv[3]:
            raise ValidationError("run ID mismatch")
        run_match = re.match(r"^(\d{4})(\d{2})(\d{2})T", argv[3])
        if not run_match:
            raise ValidationError("run ID does not contain a JST date")
        expected_date = "-".join(run_match.groups())
        earliest_mtime = int(argv[4])
        validate_artifact(
            result["summary_path"],
            result["summary_sha256"],
            Path(argv[2]),
            earliest_mtime,
            expected_date,
            "summary",
        )
        validate_artifact(
            result["advisory_path"],
            result["advisory_sha256"],
            Path(argv[2]),
            earliest_mtime,
            expected_date,
            "advisory",
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"collection validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
