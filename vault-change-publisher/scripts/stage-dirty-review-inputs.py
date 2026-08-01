#!/usr/bin/env python3
"""Materialize captured dirty Git blobs for the no-network publication review."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath


MAX_BLOB_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
OID_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class DirtySnapshotError(RuntimeError):
    """Raised when captured dirty state cannot be materialized safely."""


def clean_git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def read_regular_beneath(root: Path, relative: PurePosixPath) -> bytes:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    opened = [os.open(root, directory_flags)]
    try:
        current = opened[-1]
        for component in relative.parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            opened.append(current)
        descriptor = os.open(
            relative.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BLOB_BYTES:
                raise DirtySnapshotError("dirty worktree input is not a bounded regular file")
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
                raise DirtySnapshotError("dirty worktree input changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def read_blob(git_dir: str, worktree: str, relative: str, oid: str) -> bytes:
    if OID_PATTERN.fullmatch(oid) is None:
        raise DirtySnapshotError("dirty entry has an invalid Git object ID")
    completed = subprocess.run(
        ["git", f"--git-dir={git_dir}", "cat-file", "blob", oid],
        check=False,
        capture_output=True,
        cwd="/",
        env=clean_git_environment(),
    )
    if completed.returncode == 0:
        content = completed.stdout
    else:
        content = read_regular_beneath(Path(worktree), PurePosixPath(relative))
    hashed = subprocess.run(
        ["git", f"--git-dir={git_dir}", "hash-object", "--stdin"],
        input=content,
        check=True,
        capture_output=True,
        cwd="/",
        env=clean_git_environment(),
    )
    if hashed.stdout.decode("ascii").strip() != oid:
        raise DirtySnapshotError("dirty blob bytes do not match captured object ID")
    if len(content) > MAX_BLOB_BYTES:
        raise DirtySnapshotError("dirty blob exceeds per-file size limit")
    return content


def mkdir_exclusive(parent_descriptor: int, name: str) -> int:
    os.mkdir(name, 0o700, dir_fd=parent_descriptor)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_descriptor)


def write_exclusive(directory_descriptor: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_descriptor)
    try:
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_relative(value: object) -> str:
    if not isinstance(value, str):
        raise DirtySnapshotError("dirty path is not a string")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\x00" in value:
        raise DirtySnapshotError("dirty path is not safely repo-relative")
    return value


def materialize(
    runtime: dict[str, object], pre_state: dict[str, object], destination: Path
) -> dict[str, object]:
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_descriptor = os.open(destination, root_flags)
    total_bytes = 0
    manifest: dict[str, object] = {"version": 1, "vaults": {}}
    try:
        snapshots_descriptor = mkdir_exclusive(destination_descriptor, "dirty-snapshots")
        try:
            for label, state_key, git_key in (
                ("agents", "agents_vault", "agents_git_dir"),
                ("user", "user_vault", "user_git_dir"),
            ):
                vault_descriptor = mkdir_exclusive(snapshots_descriptor, label)
                try:
                    entries = pre_state[state_key]["dirty_entries"]
                    if not isinstance(entries, list):
                        raise DirtySnapshotError("dirty entries are not a list")
                    seen: set[str] = set()
                    output_entries: list[dict[str, object]] = []
                    for index, entry in enumerate(entries):
                        relative = validate_relative(entry["path"])
                        if relative in seen:
                            raise DirtySnapshotError("dirty path is duplicated")
                        seen.add(relative)
                        mode = entry["mode"]
                        oid = entry["git_blob_oid"]
                        if mode is None and oid is None:
                            output_entries.append(
                                {
                                    "path": relative,
                                    "git_blob_oid": None,
                                    "mode": None,
                                    "snapshot": None,
                                    "sha256": None,
                                }
                            )
                            continue
                        if mode not in {"100644", "100755"} or not isinstance(oid, str):
                            raise DirtySnapshotError("dirty entry mode is not publishable")
                        content = read_blob(
                            str(runtime[git_key]),
                            str(runtime[f"{label}_vault_root"]),
                            relative,
                            oid,
                        )
                        total_bytes += len(content)
                        if total_bytes > MAX_TOTAL_BYTES:
                            raise DirtySnapshotError("dirty snapshots exceed total size limit")
                        filename = f"{index:04d}.blob"
                        write_exclusive(vault_descriptor, filename, content)
                        output_entries.append(
                            {
                                "path": relative,
                                "git_blob_oid": oid,
                                "mode": mode,
                                "snapshot": f"dirty-snapshots/{label}/{filename}",
                                "sha256": hashlib.sha256(content).hexdigest(),
                            }
                        )
                    manifest["vaults"][state_key] = output_entries
                finally:
                    os.close(vault_descriptor)
        finally:
            os.close(snapshots_descriptor)
        encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n").encode()
        write_exclusive(destination_descriptor, "dirty-snapshots.json", encoded)
    finally:
        os.close(destination_descriptor)
    return manifest


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: stage-dirty-review-inputs.py RUNTIME_CONTEXT PRE_STATE DESTINATION",
            file=sys.stderr,
        )
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre_state = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        destination = Path(argv[3])
        if not destination.is_absolute():
            raise DirtySnapshotError("destination must be absolute")
        materialize(runtime, pre_state, destination)
        os.chmod(destination, 0o000)
    except (
        DirtySnapshotError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"dirty review snapshot failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
