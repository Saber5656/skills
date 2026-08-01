#!/usr/bin/env python3
"""Copy the validated standing task into the run staging boundary."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


MAX_TASK_BYTES = 2 * 1024 * 1024


class SnapshotError(RuntimeError):
    """Raised when the standing task cannot be snapshotted safely."""


def open_beneath(root: Path, relative: Path) -> tuple[int, list[int]]:
    """Open a regular file beneath root without following any path component."""
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise SnapshotError("standing task is not a safe Vault-relative path")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    opened = [os.open(root, directory_flags)]
    try:
        current = opened[-1]
        for component in relative.parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            opened.append(current)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=current)
        return descriptor, opened
    except BaseException:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise


def read_regular_beneath(root: Path, relative: Path) -> bytes:
    descriptor, opened = open_beneath(root, relative)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SnapshotError("standing task is not a regular file")
        if metadata.st_size > MAX_TASK_BYTES:
            raise SnapshotError("standing task exceeds size limit")
        chunks: list[bytes] = []
        remaining = MAX_TASK_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_TASK_BYTES:
            raise SnapshotError("standing task exceeds size limit")
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
            raise SnapshotError("standing task changed while being read")
        content.decode("utf-8")
        return content
    except UnicodeDecodeError as exc:
        raise SnapshotError("standing task is not UTF-8") from exc
    finally:
        os.close(descriptor)
        for directory_descriptor in reversed(opened):
            os.close(directory_descriptor)


def write_exclusive(staging_root: Path, content: bytes) -> None:
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    staging_descriptor = os.open(staging_root, directory_flags)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("standing-task.md", flags, 0o600, dir_fd=staging_descriptor)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(staging_descriptor)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: stage-standing-task.py RUNTIME_CONTEXT DESTINATION", file=sys.stderr)
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        source = Path(runtime["standing_task_path"])
        agents_root = Path(runtime["agents_vault_root"])
        staging_root = Path(argv[2])
        if not source.is_absolute() or not agents_root.is_absolute() or not staging_root.is_absolute():
            raise SnapshotError("source and destination must be absolute")
        try:
            relative = source.relative_to(agents_root)
        except ValueError as exc:
            raise SnapshotError("standing task is outside Agents Vault") from exc
        content = read_regular_beneath(agents_root, relative)
        write_exclusive(staging_root, content)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, SnapshotError) as exc:
        print(f"standing task snapshot failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
