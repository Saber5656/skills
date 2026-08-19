#!/usr/bin/env python3
"""Install reviewed artifacts and create only the approved local commits."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from atomic_file_ops import rename_no_replace, verify_rename_no_replace
from git_diff_digest import unified_diff_added_content
from isolated_git_transport import LOCAL_COMMAND_TIMEOUT_SECONDS, run_local_command
from trusted_gitleaks import trusted_scan_invocation


CLEAN_DIGEST = hashlib.sha256(b"").hexdigest()
SCAN_TIMEOUT_SECONDS = 120


class CommitError(RuntimeError):
    """Represent a publication mutation that must fail closed."""


def validated_publisher_identity(runtime: dict[str, object]) -> tuple[str, str]:
    """Revalidate the private, context-bound Git identity at mutation time."""
    name = runtime.get("publisher_git_name")
    email = runtime.get("publisher_git_email")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 128
        or any(character in name for character in "\0\r\n<>")
        or not isinstance(email, str)
        or not email
        or len(email) > 254
        or re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email) is None
    ):
        raise CommitError("publisher Git identity is invalid")
    return name, email


def clean_environment(
    publisher_identity: tuple[str, str] | None = None,
) -> dict[str, str]:
    """Remove ambient Git overrides and prohibit lazy object retrieval."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("GITLEAKS_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
        }
    )
    if publisher_identity is not None:
        name, email = publisher_identity
        environment.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            }
        )
    return environment


def git(
    repo: str,
    git_dir: str,
    *arguments: str,
    check: bool = True,
    index_file: str | None = None,
    input_text: str | None = None,
    publisher_identity: tuple[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run local-only Git with explicit metadata and work-tree paths."""
    environment = clean_environment(publisher_identity)
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    return run_local_command(
        [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={repo}",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "commit.gpgSign=false",
            *arguments,
        ],
        cwd="/",
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
        env=environment,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def git_bytes(
    repo: str,
    git_dir: str,
    *arguments: str,
    index_file: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run local-only Git while preserving byte-exact patch line boundaries."""
    environment = clean_environment()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    return run_local_command(
        [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={repo}",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "commit.gpgSign=false",
            *arguments,
        ],
        cwd="/",
        check=True,
        capture_output=True,
        env=environment,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def safe_path(value: object) -> str:
    """Require a normalized repo-relative path outside .obsidian."""
    if not isinstance(value, str):
        raise CommitError("approved path is not a string")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.parts[0] == ".obsidian"
        or str(path) != value
    ):
        raise CommitError("approved path is unsafe")
    return value


def current_state(repo: str, git_dir: str, pre: dict[str, object]) -> dict[str, object]:
    """Capture local progress in the canonical commit-result shape."""
    head = git(repo, git_dir, "rev-parse", "HEAD").stdout.strip()
    status = git(
        repo, git_dir, "status", "--porcelain=v1", "--untracked-files=all"
    ).stdout
    commits = git(
        repo,
        git_dir,
        "rev-list",
        "--reverse",
        f"{pre['local_head']}..{head}",
    ).stdout.splitlines()
    return {
        "commit_status": "complete" if commits else "not_started",
        "commit_hashes": commits,
        "pre_local_head": pre["local_head"],
        "local_head": head,
        "pre_dirty_digest": pre["dirty_digest"],
        "post_dirty_digest": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "clean": not status,
    }


def capture_exact(capture: str, runtime_file: str, expected: dict[str, object]) -> None:
    """Require the current two-Vault state to equal the immutable pre-state."""
    completed = run_local_command(
        [capture, "--include-local-history", runtime_file],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    if json.loads(completed.stdout) != expected:
        raise CommitError("Vault state changed after the approved review")


def capture_state(capture: str, runtime_file: str) -> dict[str, object]:
    """Capture the complete current state for both Vaults."""
    completed = run_local_command(
        [capture, "--include-local-history", runtime_file],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return json.loads(completed.stdout)


def validate_installed_scope(
    before: dict[str, object],
    current: dict[str, object],
    artifact_paths: dict[str, str],
) -> None:
    """Allow only the two installed artifacts beyond the reviewed dirty scope."""
    mutable_fields = {
        "dirty_lines",
        "dirty_paths",
        "dirty_entries",
        "dirty_metadata",
        "staged_paths",
        "index_sha256",
        "index_entries",
        "dirty_worktree_sha256",
        "dirty_digest",
        "diff_snapshot_sha256",
    }
    for key in ("agents_vault", "user_vault"):
        expected_state = before[key]
        current_state_value = current[key]
        if {
            field: value
            for field, value in current_state_value.items()
            if field not in mutable_fields
        } != {
            field: value
            for field, value in expected_state.items()
            if field not in mutable_fields
        }:
            raise CommitError("Vault control state changed during artifact installation")
        artifact = artifact_paths.get(key)
        expected_paths = sorted(
            [*expected_state["dirty_paths"], *([artifact] if artifact else [])]
        )
        if current_state_value["dirty_paths"] != expected_paths:
            raise CommitError("manifest-external path changed during artifact installation")
        expected_entries = {
            entry["path"]: entry for entry in expected_state["dirty_entries"]
        }
        current_entries = {
            entry["path"]: entry for entry in current_state_value["dirty_entries"]
        }
        if any(current_entries.get(path) != entry for path, entry in expected_entries.items()):
            raise CommitError("reviewed dirty entry changed during artifact installation")
        expected_metadata = {
            entry["path"]: entry for entry in expected_state["dirty_metadata"]
        }
        current_metadata = {
            entry["path"]: entry for entry in current_state_value["dirty_metadata"]
        }
        if any(
            current_metadata.get(path) != entry
            for path, entry in expected_metadata.items()
        ):
            raise CommitError("reviewed dirty metadata changed during artifact installation")
        if (
            current_state_value["staged_paths"] != expected_state["staged_paths"]
            or current_state_value["index_entries"] != expected_state["index_entries"]
            or current_state_value["index_sha256"] != expected_state["index_sha256"]
        ):
            raise CommitError("existing staged state changed during artifact installation")
        if artifact is not None:
            artifact_entry = current_entries.get(artifact)
            if artifact_entry is None or artifact_entry.get("mode") != "100644" or not artifact_entry.get(
                "git_blob_oid"
            ):
                raise CommitError("installed artifact is not the only approved regular addition")


def capture_installed_scope(
    capture: str,
    runtime_file: str,
    before: dict[str, object],
    artifact_paths: dict[str, str],
) -> dict[str, object]:
    """Recapture both complete Vaults after installation and before any commit."""
    current = capture_state(capture, runtime_file)
    validate_installed_scope(before, current, artifact_paths)
    return current


def reviewed_inputs(
    context: dict[str, object],
    runtime: object,
    pre: object,
    collection: object,
    plan: object,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    """Return only context-bound publication inputs or reject mutable substitutes."""
    bound = (
        context["runtime"],
        context["pre_collection_state"],
        context["verified_collection"],
        context["artifact_plan"],
    )
    if (runtime, pre, collection, plan) != bound:
        raise CommitError("mutable publication inputs differ from reviewed context")
    return bound


def write_bound_json(directory: Path, name: str, value: object) -> tuple[Path, bytes]:
    """Create one context-derived, no-follow control input for child helpers."""
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path = directory / name
    if path.exists():
        if stable_regular_bytes(path) != content:
            raise CommitError("existing bound publication input differs from context")
        return path, content
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CommitError("could not write bound publication input")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path, content


def require_bound_bytes(path: Path, content: bytes) -> None:
    """Reject any change to a context-derived helper input."""
    if stable_regular_bytes(path) != content:
        raise CommitError("bound publication input changed during helper execution")


def require_filter_free(repo: str, git_dir: str, paths: list[str]) -> None:
    """Reject path-specific external clean filters before staging."""
    completed = git(repo, git_dir, "check-attr", "-z", "filter", "--", *paths)
    fields = completed.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3:
        raise CommitError("could not parse Git attributes")
    for index in range(0, len(fields), 3):
        if fields[index + 1] != "filter" or fields[index + 2] not in {
            "unspecified",
            "unset",
        }:
            raise CommitError("approved path has an active Git filter")


def scan_staged(
    gitleaks_bin: str,
    repo: str,
    git_dir: str,
    paths: list[str],
    index_file: str,
) -> None:
    """Reject secrets and newly added machine-specific home paths before commit."""
    environment = clean_environment()
    environment["GIT_INDEX_FILE"] = index_file
    try:
        with trusted_scan_invocation() as (scan_prefix, pass_fds):
            completed = run_local_command(
                [
                    gitleaks_bin,
                    *scan_prefix,
                    "git",
                    "--staged",
                    repo,
                ],
                cwd="/",
                check=False,
                capture_output=True,
                env=environment,
                timeout=SCAN_TIMEOUT_SECONDS,
                pass_fds=pass_fds,
            )
    except subprocess.TimeoutExpired as exc:
        raise CommitError("gitleaks staged scan exceeded its deadline") from exc
    if completed.returncode != 0:
        raise CommitError("gitleaks rejected the staged publication")
    patch = git_bytes(
        repo,
        git_dir,
        "diff",
        "--cached",
        "--no-color",
        "--unified=0",
        "--",
        *paths,
        index_file=index_file,
    ).stdout
    home_bytes = os.fsencode(str(Path.home()))
    if home_bytes and home_bytes in unified_diff_added_content(patch):
        raise CommitError("staged publication adds a machine-specific home path")
    if home_bytes:
        for path in paths:
            listed = git(
                repo,
                git_dir,
                "ls-files",
                "--stage",
                "-z",
                "--",
                path,
                index_file=index_file,
            ).stdout
            if not listed:
                continue
            candidate = run_local_command(
                [
                    "git",
                    f"--git-dir={git_dir}",
                    f"--work-tree={repo}",
                    "show",
                    f":{path}",
                ],
                cwd="/",
                check=True,
                capture_output=True,
                env=environment,
                timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
            ).stdout
            numstat = git(
                repo,
                git_dir,
                "diff",
                "--cached",
                "--numstat",
                "-z",
                "--",
                path,
                index_file=index_file,
            ).stdout
            if numstat.startswith("-\t-\t") and home_bytes in candidate:
                raise CommitError("candidate blob contains a machine-specific home path")


def stable_regular_bytes(path: Path) -> bytes:
    """Read stable, no-follow regular-file bytes."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CommitError("installed artifact is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise CommitError("installed artifact changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def installed_artifact_receipt(path: Path, expected_sha256: str) -> dict[str, object]:
    """Bind bytes and stable inode identity through one descriptor."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CommitError("newly installed artifact is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino)
        content_contract = (before.st_size, before.st_mode)
        if identity != (after.st_dev, after.st_ino) or content_contract != (
            after.st_size,
            after.st_mode,
        ):
            raise CommitError("newly installed artifact changed while being read")
    finally:
        os.close(descriptor)
    if hashlib.sha256(b"".join(chunks)).hexdigest() != expected_sha256:
        raise CommitError("newly installed artifact differs from verified staging bytes")
    return {
        "path": str(path),
        "sha256": expected_sha256,
        "identity": identity,
        "size": content_contract[0],
        "mode": content_contract[1],
    }


def validated_installer_receipt(
    value: object, expected_path: Path, expected_sha256: str
) -> dict[str, object]:
    """Accept only the identity sealed by the installer's O_EXCL descriptor."""
    if not isinstance(value, dict) or set(value) != {
        "path",
        "sha256",
        "identity",
        "size",
        "mode",
    }:
        raise CommitError("installer artifact receipt is malformed")
    identity = value.get("identity")
    if (
        value.get("path") != str(expected_path)
        or value.get("sha256") != expected_sha256
        or not isinstance(identity, list)
        or len(identity) != 2
        or any(not isinstance(field, int) or isinstance(field, bool) for field in identity)
        or not isinstance(value.get("size"), int)
        or isinstance(value.get("size"), bool)
        or int(value["size"]) < 0
        or not isinstance(value.get("mode"), int)
        or isinstance(value.get("mode"), bool)
    ):
        raise CommitError("installer artifact receipt differs from the approved artifact")
    receipt = {
        "path": str(expected_path),
        "sha256": expected_sha256,
        "identity": tuple(identity),
        "size": int(value["size"]),
        "mode": int(value["mode"]),
    }
    require_owned_artifact(receipt)
    return receipt


def descriptor_matches_owned_artifact(
    descriptor: int, receipt: dict[str, object]
) -> bool:
    """Check stable inode/content fields without treating timestamps as identity."""
    before = os.fstat(descriptor)
    identity = (before.st_dev, before.st_ino)
    content_contract = (before.st_size, before.st_mode)
    if (
        not stat.S_ISREG(before.st_mode)
        or identity != tuple(receipt["identity"])
        or content_contract != (receipt["size"], receipt["mode"])
    ):
        return False
    os.lseek(descriptor, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        hasher.update(chunk)
    after = os.fstat(descriptor)
    return (
        identity == (after.st_dev, after.st_ino)
        and content_contract == (after.st_size, after.st_mode)
        and hasher.hexdigest() == receipt["sha256"]
    )


def require_owned_artifact(receipt: dict[str, object]) -> None:
    """Verify bytes through the installer inode while ignoring timestamp churn."""
    path = Path(str(receipt["path"]))
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not descriptor_matches_owned_artifact(descriptor, receipt):
            raise CommitError("installer-owned artifact changed before publication")
    finally:
        os.close(descriptor)


def rollback_owned_artifact(receipt: dict[str, object]) -> None:
    """Atomically quarantine, verify, and remove only this run's artifact."""
    path = Path(str(receipt["path"]))
    parent_descriptor = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor = -1
    quarantine_descriptor = -1
    quarantine_name = f".vault-publisher-rollback-{secrets.token_hex(16)}"
    quarantined = False
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if not descriptor_matches_owned_artifact(descriptor, receipt):
            raise CommitError("newly installed artifact changed; rollback refused")
        os.mkdir(quarantine_name, 0o700, dir_fd=parent_descriptor)
        quarantine_descriptor = os.open(
            quarantine_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        verify_rename_no_replace(quarantine_descriptor)
        os.rename(
            path.name,
            "artifact",
            src_dir_fd=parent_descriptor,
            dst_dir_fd=quarantine_descriptor,
        )
        quarantined = True
        quarantined_identity = os.stat(
            "artifact", dir_fd=quarantine_descriptor, follow_symlinks=False
        )
        if (quarantined_identity.st_dev, quarantined_identity.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            try:
                rename_no_replace(
                    quarantine_descriptor,
                    "artifact",
                    parent_descriptor,
                    path.name,
                )
                quarantined = False
            except FileExistsError:
                pass
            raise CommitError(
                "newly installed artifact was replaced; rollback quarantined it"
            )
        if not descriptor_matches_owned_artifact(descriptor, receipt):
            try:
                rename_no_replace(
                    quarantine_descriptor,
                    "artifact",
                    parent_descriptor,
                    path.name,
                )
                quarantined = False
                raise CommitError(
                    "newly installed artifact changed during rollback; rollback refused"
                )
            except FileExistsError:
                raise CommitError(
                    "newly installed artifact changed during rollback; "
                    "rollback quarantined it"
                )
        os.unlink("artifact", dir_fd=quarantine_descriptor)
        quarantined = False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if quarantine_descriptor >= 0:
            os.close(quarantine_descriptor)
        if not quarantined:
            try:
                os.rmdir(quarantine_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def write_blob(
    repo: str,
    git_dir: str,
    content: bytes,
    expected_oid: str | None = None,
) -> str:
    """Write one already-verified blob without filters or a shell."""
    completed = run_local_command(
        [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={repo}",
            "hash-object",
            "-w",
            "--stdin",
        ],
        cwd="/",
        check=True,
        input=content,
        capture_output=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    oid = completed.stdout.decode("ascii").strip()
    if not oid or (expected_oid is not None and oid != expected_oid):
        raise CommitError("reviewed bytes do not match the approved Git blob")
    return oid


def ensure_reviewed_blob(
    repo: str, git_dir: str, path: str, expected_oid: str
) -> None:
    """Make an approved dirty blob available without accepting other bytes."""
    available = git(repo, git_dir, "cat-file", "-e", f"{expected_oid}^{{blob}}", check=False)
    if available.returncode == 0:
        return
    write_blob(
        repo,
        git_dir,
        stable_regular_bytes(Path(repo) / path),
        expected_oid,
    )


def update_index_entry(
    repo: str,
    git_dir: str,
    path: str,
    entry: tuple[str, str] | None,
    *,
    index_file: str | None = None,
) -> None:
    """Set or remove one exact index entry without reading worktree bytes."""
    if entry is None:
        git(
            repo,
            git_dir,
            "update-index",
            "--force-remove",
            "--",
            path,
            index_file=index_file,
        )
        return
    mode, oid = entry
    if git(repo, git_dir, "cat-file", "-t", oid).stdout.strip() != "blob":
        raise CommitError("approved Git object is not an available blob")
    git(
        repo,
        git_dir,
        "update-index",
        "--add",
        "--cacheinfo",
        f"{mode},{oid},{path}",
        index_file=index_file,
    )


def validate_final_worktree(
    repo: str,
    git_dir: str,
    manifest: dict[str, object],
    artifact_path: str,
    artifact_source_sha256: str,
) -> None:
    """Prove every final approved entry matches the worktree before any commit."""
    entries = {
        safe_path(entry["path"]): (
            None
            if entry["git_blob_oid"] is None
            else (str(entry["mode"]), str(entry["git_blob_oid"]))
        )
        for entry in manifest.get("approved_dirty_entries", [])
    }
    for path, entry in entries.items():
        if entry is not None:
            ensure_reviewed_blob(repo, git_dir, path, entry[1])
    artifact_path = safe_path(artifact_path)
    artifact_content = stable_regular_bytes(Path(repo) / artifact_path)
    if hashlib.sha256(artifact_content).hexdigest() != artifact_source_sha256:
        raise CommitError("installed artifact digest differs from review")
    entries[artifact_path] = ("100644", write_blob(repo, git_dir, artifact_content))
    grouped_paths = [
        safe_path(path)
        for group in manifest.get("commit_groups", [])
        for path in group.get("paths", [])
    ]
    if sorted(grouped_paths) != sorted(entries):
        raise CommitError("approved groups do not cover the exact final worktree")
    for path, entry in entries.items():
        worktree_path = Path(repo) / path
        if entry is None:
            try:
                os.lstat(worktree_path)
            except FileNotFoundError:
                continue
            raise CommitError("approved deletion remains in the worktree")
        metadata = os.lstat(worktree_path)
        if not stat.S_ISREG(metadata.st_mode):
            raise CommitError("approved final path is not a regular file")
        actual_mode = "100755" if metadata.st_mode & 0o111 else "100644"
        if actual_mode != entry[0]:
            raise CommitError("worktree mode differs from the approved final mode")
        write_blob(
            repo,
            git_dir,
            stable_regular_bytes(worktree_path),
            entry[1],
        )


def commit_groups(
    repo: str,
    git_dir: str,
    gitleaks_bin: str,
    pre: dict[str, object],
    manifest: dict[str, object],
    artifact_path: str,
    artifact_source_sha256: str,
    temporary_directory: Path,
    publisher_identity: tuple[str, str] | None = None,
    before_update: Callable[[], None] | None = None,
    publication_mode: str = "sweep",
    mutation_tracker: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create the ordered, minimal commits declared by the approved manifest."""
    if publication_mode not in {"sweep", "own_only"}:
        raise CommitError("blocked Vault cannot create publication commits")
    if git(repo, git_dir, "branch", "--show-current").stdout.strip() != "main":
        raise CommitError("Vault is not on main")
    if git(repo, git_dir, "rev-parse", "HEAD").stdout.strip() != pre["local_head"]:
        raise CommitError("Vault HEAD changed after review")
    groups = manifest.get("commit_groups")
    if not isinstance(groups, list) or not groups:
        raise CommitError("approved commit groups are missing")
    entries = {
        safe_path(entry["path"]): (
            None
            if entry["git_blob_oid"] is None
            else (str(entry["mode"]), str(entry["git_blob_oid"]))
        )
        for entry in manifest.get("approved_dirty_entries", [])
    }
    for path, entry in entries.items():
        if entry is not None:
            ensure_reviewed_blob(repo, git_dir, path, entry[1])
    artifact_path = safe_path(artifact_path)
    artifact_content = stable_regular_bytes(Path(repo) / artifact_path)
    if hashlib.sha256(artifact_content).hexdigest() != artifact_source_sha256:
        raise CommitError("installed artifact digest differs from review")
    artifact_oid = write_blob(
        repo,
        git_dir,
        artifact_content,
    )
    entries[artifact_path] = ("100644", artifact_oid)
    current_head = str(pre["local_head"])
    for group in groups:
        message = group.get("message")
        paths = [safe_path(value) for value in group.get("paths", [])]
        if (
            not isinstance(message, str)
            or not message.strip()
            or "\n" in message
            or not paths
            or any(path not in entries for path in paths)
        ):
            raise CommitError("approved commit group is invalid")
        require_filter_free(repo, git_dir, paths)
        descriptor, temporary_index = tempfile.mkstemp(
            prefix="publication-index-", dir=str(temporary_directory)
        )
        os.close(descriptor)
        os.unlink(temporary_index)
        try:
            git(
                repo,
                git_dir,
                "read-tree",
                current_head,
                index_file=temporary_index,
            )
            for path in paths:
                update_index_entry(
                    repo,
                    git_dir,
                    path,
                    entries[path],
                    index_file=temporary_index,
                )
            git(
                repo,
                git_dir,
                "-c",
                # Reviewed Markdown bytes are immutable and may intentionally end
                # with approved blank lines as well as Markdown hard breaks.
                "core.whitespace=-blank-at-eol,-blank-at-eof",
                "diff",
                "--cached",
                "--check",
                index_file=temporary_index,
            )
            scan_staged(gitleaks_bin, repo, git_dir, paths, temporary_index)
            tree = git(
                repo, git_dir, "write-tree", index_file=temporary_index
            ).stdout.strip()
            commit = git(
                repo,
                git_dir,
                "commit-tree",
                tree,
                "-p",
                current_head,
                input_text=message + "\n",
                publisher_identity=publisher_identity,
            ).stdout.strip()
        finally:
            try:
                os.unlink(temporary_index)
            except FileNotFoundError:
                pass
        actual = sorted(
            value
            for value in git(
                repo,
                git_dir,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "--no-renames",
                "-r",
                "-z",
                commit,
            ).stdout.split("\0")
            if value
        )
        if actual != sorted(paths):
            raise CommitError("created commit differs from its approved path group")
        if git(repo, git_dir, "show", "-s", "--format=%s", commit).stdout.strip() != message:
            raise CommitError("created commit message differs from approval")
        current_head = commit
    if mutation_tracker is not None:
        mutation_tracker["candidate_head"] = current_head
        mutation_tracker["candidate_commits"] = [
            value
            for value in git(
                repo,
                git_dir,
                "rev-list",
                "--reverse",
                f"{pre['local_head']}..{current_head}",
            ).stdout.splitlines()
            if value
        ]
    if before_update is not None:
        before_update()
    git(repo, git_dir, "update-ref", "HEAD", current_head, str(pre["local_head"]))
    if mutation_tracker is not None:
        mutation_tracker["head_updated"] = True
    for path, entry in entries.items():
        update_index_entry(repo, git_dir, path, entry)
    result = current_state(repo, git_dir, pre)
    if len(result["commit_hashes"]) != len(groups):
        raise CommitError("approved local commit count differs from review")
    if publication_mode == "sweep" and not result["clean"]:
        raise CommitError("sweep commits did not leave a clean Vault")
    if publication_mode == "own_only" and result["post_dirty_digest"] != pre[
        "dirty_digest"
    ]:
        raise CommitError("own_only changed the residual porcelain state")
    result["commit_status"] = "complete"
    result["publication_mode"] = publication_mode
    result["deferred_cleanup"] = list(manifest.get("deferred_cleanup", []))
    return result


def validate_post_commit_state(
    before: dict[str, object],
    after: dict[str, object],
    artifact_path: str,
    mode: str,
) -> None:
    """Prove sweep cleanliness or exact preservation of every residual path."""
    if mode == "sweep":
        if after["dirty_paths"] or after["staged_paths"]:
            raise CommitError("sweep publication left residual changes")
        return
    if mode != "own_only":
        raise CommitError("invalid post-commit publication mode")
    for field in (
        "dirty_lines",
        "dirty_paths",
        "dirty_entries",
        "dirty_metadata",
        "staged_paths",
        "dirty_worktree_sha256",
        "dirty_digest",
        "diff_snapshot_sha256",
        "git_control_sha256",
        "branch",
        "upstream",
        "operation_in_progress",
        "remote_head",
    ):
        if after.get(field) != before.get(field):
            raise CommitError(f"own_only changed residual state field: {field}")
    before_index = [
        entry for entry in before["index_entries"] if entry["path"] != artifact_path
    ]
    after_index = [
        entry for entry in after["index_entries"] if entry["path"] != artifact_path
    ]
    if after_index != before_index:
        raise CommitError("own_only changed a non-owned index entry")


def unchanged_vault_result(
    repo: str,
    git_dir: str,
    pre: dict[str, object],
    manifest: dict[str, object],
) -> dict[str, object]:
    """Return an explicit blocked result without mutating the Vault."""
    result = current_state(repo, git_dir, pre)
    if (
        result["local_head"] != pre["local_head"]
        or result["post_dirty_digest"] != pre["dirty_digest"]
    ):
        raise CommitError("blocked Vault changed during publication")
    result["commit_status"] = "not_started"
    result["publication_mode"] = "blocked"
    result["deferred_cleanup"] = list(manifest.get("deferred_cleanup", []))
    return result


def result_after_failure(
    runtime: dict[str, object],
    pre: dict[str, object],
    collection: dict[str, object],
    plan: dict[str, object],
    reason: str,
    publication_context_sha256: Optional[str] = None,
    capture: Optional[str] = None,
    runtime_file: Optional[str] = None,
    publication_modes: Optional[dict[str, str]] = None,
    deferred_cleanup: Optional[dict[str, list[dict[str, str]]]] = None,
) -> dict[str, object]:
    """Report actual local progress without hiding partially applied mutation."""
    results: dict[str, dict[str, object]] = {}
    changed = False
    zero_head = "0" * 40
    zero_digest = "0" * 64
    modes = publication_modes or {
        "agents_vault": "blocked",
        "user_vault": "blocked",
    }
    deferred = deferred_cleanup or {
        "agents_vault": [],
        "user_vault": [],
    }
    for key, prefix in (("agents_vault", "agents"), ("user_vault", "user")):
        try:
            state = current_state(
                str(runtime[f"{prefix}_vault_root"]),
                str(runtime[f"{prefix}_git_dir"]),
                pre[key],
            )
        except (KeyError, TypeError, OSError, subprocess.SubprocessError):
            before = pre.get(key, {}) if isinstance(pre, dict) else {}
            state = {
                "commit_status": "failed",
                "commit_hashes": [],
                "pre_local_head": before.get("local_head", zero_head),
                "local_head": zero_head,
                "pre_dirty_digest": before.get("dirty_digest", zero_digest),
                "post_dirty_digest": zero_digest,
                "clean": False,
            }
        if state["commit_hashes"]:
            state["commit_status"] = "failed"
        state["publication_mode"] = modes.get(key, "blocked")
        state["deferred_cleanup"] = list(deferred.get(key, []))
        changed = changed or state["local_head"] != state["pre_local_head"] or (
            state["post_dirty_digest"] != state["pre_dirty_digest"]
        )
        results[key] = state
    resumable_state = None
    if capture and runtime_file:
        try:
            resumable_state = capture_state(capture, runtime_file)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            resumable_state = None
    result = {
        "outcome": "partial_publication" if changed else "blocked",
        "phase": "local_commit",
        "daily_pipeline_status": collection.get("daily_pipeline_status", "blocked"),
        "summary_path": plan.get("summary_target"),
        "advisory_path": plan.get("advisory_target"),
        "notification_result": collection.get("notification_result"),
        "agents_vault": results["agents_vault"],
        "user_vault": results["user_vault"],
        "publication_mode": modes,
        "deferred_cleanup": deferred,
        "evidence_finalization_commit": None,
        "next_action": reason,
    }
    if changed and publication_context_sha256 is not None:
        result["publication_context_sha256"] = publication_context_sha256
        result["resumable_state"] = resumable_state
    return result


def capture_one(capture: str, runtime_file: str, key: str) -> dict[str, object]:
    """Capture only the requested Vault contract from the canonical pair."""
    return capture_state(capture, runtime_file)[key]


def capture_one_exact(
    capture: str,
    runtime_file: str,
    key: str,
    expected: dict[str, object],
) -> None:
    """Bind one Vault without allowing its peer to suppress publication."""
    if capture_one(capture, runtime_file, key) != expected:
        raise CommitError(f"{key} state changed after approved review")


def validate_installed_vault(
    before: dict[str, object], current: dict[str, object], artifact: str
) -> None:
    """Allow one newly installed artifact while preserving one Vault residual."""
    mutable_fields = {
        "dirty_lines", "dirty_paths", "dirty_entries", "dirty_metadata",
        "staged_paths", "index_sha256", "index_entries",
        "dirty_worktree_sha256", "dirty_digest", "diff_snapshot_sha256",
    }
    if {
        field: value for field, value in current.items() if field not in mutable_fields
    } != {
        field: value for field, value in before.items() if field not in mutable_fields
    }:
        raise CommitError("Vault control state changed during artifact installation")
    if current["dirty_paths"] != sorted([*before["dirty_paths"], artifact]):
        raise CommitError("manifest-external path changed during artifact installation")
    before_entries = {entry["path"]: entry for entry in before["dirty_entries"]}
    current_entries = {entry["path"]: entry for entry in current["dirty_entries"]}
    if any(current_entries.get(path) != entry for path, entry in before_entries.items()):
        raise CommitError("reviewed dirty entry changed during artifact installation")
    before_metadata = {entry["path"]: entry for entry in before["dirty_metadata"]}
    current_metadata = {entry["path"]: entry for entry in current["dirty_metadata"]}
    if any(current_metadata.get(path) != entry for path, entry in before_metadata.items()):
        raise CommitError("reviewed dirty metadata changed during artifact installation")
    if (
        current["staged_paths"] != before["staged_paths"]
        or current["index_entries"] != before["index_entries"]
        or current["index_sha256"] != before["index_sha256"]
    ):
        raise CommitError("existing staged state changed during artifact installation")
    artifact_entry = current_entries.get(artifact)
    if (
        artifact_entry is None
        or artifact_entry.get("mode") != "100644"
        or not artifact_entry.get("git_blob_oid")
    ):
        raise CommitError("installed artifact is not the only approved regular addition")


def vault_result_from_snapshot(
    snapshot: dict[str, object],
    pre: dict[str, object],
    *,
    commit_status: str,
    commit_hashes: list[str],
    publication_mode: str,
    deferred_cleanup: list[dict[str, str]],
) -> dict[str, object]:
    """Build a result from actual state without attributing third-party commits."""
    return {
        "commit_status": commit_status,
        "commit_hashes": commit_hashes,
        "pre_local_head": pre["local_head"],
        "local_head": snapshot["local_head"],
        "pre_dirty_digest": pre["dirty_digest"],
        "post_dirty_digest": snapshot["dirty_digest"],
        "clean": not snapshot["dirty_lines"],
        "publication_mode": publication_mode,
        "deferred_cleanup": deferred_cleanup,
    }


def rollback_uncommitted_artifact(
    repo: str,
    git_dir: str,
    artifact: str,
    receipt: dict[str, object],
) -> None:
    """Remove an exact own artifact only while HEAD/index still omit its path."""
    if git(repo, git_dir, "ls-tree", "-z", "HEAD", "--", artifact).stdout:
        raise CommitError("artifact entered HEAD; rollback refused")
    if git(repo, git_dir, "ls-files", "--stage", "-z", "--", artifact).stdout:
        raise CommitError("artifact entered the shared index; rollback refused")
    rollback_owned_artifact(receipt)


def snapshot_shared_index(
    git_dir: str, temporary_directory: Path, label: str
) -> dict[str, object]:
    """Seal the exact shared index bytes before publication mutation."""
    index_path = Path(git_dir) / "index"
    descriptor = os.open(index_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    destination_fd, destination_name = tempfile.mkstemp(
        prefix=f"{label}-index-backup-", dir=str(temporary_directory)
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CommitError("Git index is not a regular file")
        hasher = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            hasher.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise CommitError("could not seal Git index backup")
                view = view[written:]
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise CommitError("Git index changed while backup was sealed")
        os.fchmod(destination_fd, stat.S_IMODE(before.st_mode))
        os.fsync(destination_fd)
        return {
            "index_path": str(index_path),
            "backup_path": destination_name,
            "sha256": hasher.hexdigest(),
            "mode": stat.S_IMODE(before.st_mode),
            "atime_ns": before.st_atime_ns,
            "mtime_ns": before.st_mtime_ns,
        }
    except Exception:
        try:
            os.unlink(destination_name)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
        os.close(destination_fd)


def restore_completed_publication(
    *,
    handle: dict[str, object],
    capture: str,
    runtime_file: str,
    key: str,
) -> dict[str, object]:
    """Compensate one unpushed owned commit so the pair can be re-reviewed."""
    repo = str(handle["repo"])
    git_dir = str(handle["git_dir"])
    before = handle["before"]
    after = handle["after"]
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise CommitError("publication rollback state is malformed")
    if capture_one(capture, runtime_file, key) != after:
        raise CommitError("completed Vault changed before compensating rollback")
    artifact = str(handle["artifact"])
    artifact_absolute = Path(str(handle["artifact_absolute"]))
    receipt = handle.get("artifact_receipt")
    if (
        not isinstance(receipt, dict)
        or receipt.get("path") != str(artifact_absolute)
        or receipt.get("sha256") != str(handle["artifact_sha256"])
    ):
        raise CommitError("publication rollback receipt is malformed")
    require_owned_artifact(receipt)
    backup = handle["index_backup"]
    if not isinstance(backup, dict):
        raise CommitError("publication index backup is malformed")
    index_path = Path(str(backup["index_path"]))
    backup_path = Path(str(backup["backup_path"]))
    if hashlib.sha256(stable_regular_bytes(backup_path)).hexdigest() != backup["sha256"]:
        raise CommitError("publication index backup changed")
    lock_path = index_path.with_name(index_path.name + ".lock")
    lock_fd = os.open(
        lock_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        int(backup["mode"]),
    )
    rollback_head = str(before["local_head"])
    committed_head = str(after["local_head"])
    head_restored = False
    try:
        source_fd = os.open(
            backup_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(lock_fd, view)
                    if written <= 0:
                        raise CommitError("could not prepare index rollback")
                    view = view[written:]
        finally:
            os.close(source_fd)
        os.fsync(lock_fd)
        if capture_one(capture, runtime_file, key) != after:
            raise CommitError("completed Vault changed while rollback was prepared")
        git(repo, git_dir, "update-ref", "HEAD", rollback_head, committed_head)
        head_restored = True
        os.close(lock_fd)
        lock_fd = -1
        os.replace(lock_path, index_path)
        os.chmod(index_path, int(backup["mode"]), follow_symlinks=False)
        os.utime(
            index_path,
            ns=(int(backup["atime_ns"]), int(backup["mtime_ns"])),
            follow_symlinks=False,
        )
        rollback_owned_artifact(receipt)
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass
    final = capture_one(capture, runtime_file, key)
    if final != before:
        if head_restored:
            raise CommitError(
                "compensating rollback restored HEAD but not the exact Vault state"
            )
        raise CommitError("compensating rollback did not restore the Vault")
    if git(repo, git_dir, "ls-tree", "-z", "HEAD", "--", artifact).stdout:
        raise CommitError("rolled-back artifact still exists in HEAD")
    return final


def publish_one_vault(
    *,
    key: str,
    prefix: str,
    role: str,
    artifact_plan_key: str,
    collection_sha_key: str,
    runtime: dict[str, object],
    pre: dict[str, object],
    collection: dict[str, object],
    plan: dict[str, object],
    manifest: dict[str, object],
    installer: str,
    capture: str,
    runtime_file: str,
    collection_file: str,
    plan_file: str,
    output_directory: Path,
    publisher_identity: tuple[str, str],
    resume_state: dict[str, object] | None = None,
) -> tuple[
    dict[str, object], bool, bool, str | None, dict[str, object] | None
]:
    """Install/commit one Vault independently and report owned progress."""
    repo = str(runtime[f"{prefix}_vault_root"])
    git_dir = str(runtime[f"{prefix}_git_dir"])
    before = pre[key]
    mode = str(manifest["publication_mode"])
    deferred = list(manifest.get("deferred_cleanup", []))
    artifact_absolute = str(plan[artifact_plan_key])
    artifact = str(Path(artifact_absolute).relative_to(repo))
    if mode == "blocked":
        current = capture_one(capture, runtime_file, key)
        return (
            vault_result_from_snapshot(
                current,
                before,
                commit_status="not_started",
                commit_hashes=[],
                publication_mode="blocked",
                deferred_cleanup=deferred,
            ),
            False,
            False,
            "review blocked this Vault",
            None,
        )
    receipt: dict[str, object] | None = None
    tracker: dict[str, object] = {"head_updated": False}
    index_backup: dict[str, object] | None = None
    try:
        if resume_state is not None:
            capture_one_exact(capture, runtime_file, key, resume_state)
            installed_state = resume_state
            validate_installed_vault(before, installed_state, artifact)
            # A prior process's installer receipt is not persisted.  Exact bytes
            # may be committed after the reviewed resume-state check, but path
            # metadata must never be rebound into rollback ownership here.
            receipt = None
        else:
            capture_one_exact(capture, runtime_file, key, before)
            index_backup = snapshot_shared_index(git_dir, output_directory, key)
            installed = json.loads(
                run_local_command(
                    [installer, runtime_file, collection_file, plan_file, role],
                    cwd="/",
                    check=True,
                    capture_output=True,
                    text=True,
                    env=clean_environment(),
                    timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
                ).stdout
            )
            if (
                not isinstance(installed, dict)
                or installed.get("summary_target") != plan["summary_target"]
                or installed.get("advisory_target") != plan["advisory_target"]
                or set(installed)
                != {"summary_target", "advisory_target", "installed_receipt"}
            ):
                raise CommitError("installed artifact differs from the approved plan")
            receipt = validated_installer_receipt(
                installed["installed_receipt"],
                Path(artifact_absolute),
                str(collection[collection_sha_key]),
            )
            installed_state = capture_one(capture, runtime_file, key)
            validate_installed_vault(before, installed_state, artifact)
        validate_final_worktree(
            repo,
            git_dir,
            manifest,
            artifact,
            str(collection[collection_sha_key]),
        )
        result = commit_groups(
            repo,
            git_dir,
            str(runtime["gitleaks_bin"]),
            before,
            manifest,
            artifact,
            str(collection[collection_sha_key]),
            output_directory,
            publisher_identity,
            before_update=lambda: capture_one_exact(
                capture, runtime_file, key, installed_state
            ),
            publication_mode=mode,
            mutation_tracker=tracker,
        )
        completed_receipt = receipt
        receipt = None
        after = capture_one(capture, runtime_file, key)
        if after["local_head"] != result["local_head"]:
            raise CommitError("Vault HEAD differs from created commit")
        validate_post_commit_state(before, after, artifact, mode)
        rollback_handle = None
        if index_backup is not None:
            rollback_handle = {
                "repo": repo,
                "git_dir": git_dir,
                "artifact": artifact,
                "artifact_absolute": artifact_absolute,
                "artifact_sha256": str(collection[collection_sha_key]),
                "artifact_receipt": completed_receipt,
                "before": before,
                "after": after,
                "index_backup": index_backup,
            }
        return (
            result,
            True,
            False,
            None,
            rollback_handle,
        )
    except (
        CommitError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        rollback_ok = receipt is None
        if receipt is not None and not tracker.get("head_updated"):
            try:
                rollback_uncommitted_artifact(repo, git_dir, artifact, receipt)
                rollback_ok = True
            except Exception as rollback_exc:
                exc = CommitError(f"{exc}; artifact rollback failed: {rollback_exc}")
                rollback_ok = False
        current = capture_one(capture, runtime_file, key)
        owned_commits = [str(value) for value in tracker.get("candidate_commits", [])]
        progressed = bool(tracker.get("head_updated"))
        result_mode = mode if progressed else "blocked"
        result = vault_result_from_snapshot(
            current,
            before,
            commit_status="failed" if progressed else "not_started",
            commit_hashes=owned_commits if progressed else [],
            publication_mode=result_mode,
            deferred_cleanup=deferred,
        )
        retry_safe = rollback_ok and not progressed
        return result, False, retry_safe, str(exc), None


def legacy_main(argv: list[str]) -> int:
    """Validate inputs, install artifacts, commit approved groups, and emit JSON."""
    if len(argv) != 11:
        print(
            "usage: commit-reviewed-publication.py RUNTIME PRE COLLECTION PLAN "
            "CONTEXT REVIEW INSTALLER CAPTURE REVIEW_SHA OUTPUT",
            file=sys.stderr,
        )
        return 64
    output = Path(argv[10])
    runtime: dict[str, object] = {}
    pre: dict[str, object] = {}
    collection: dict[str, object] = {}
    plan: dict[str, object] = {}
    context_digest: Optional[str] = None
    bound_runtime: Optional[Path] = None
    modes: Optional[dict[str, str]] = None
    deferred: Optional[dict[str, list[dict[str, str]]]] = None
    rollback_receipts: dict[str, dict[str, object]] = {}
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        collection = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        context_path = Path(argv[5])
        context_bytes = stable_regular_bytes(context_path)
        context_digest = hashlib.sha256(context_bytes).hexdigest()
        context = json.loads(context_bytes)
        runtime, pre, collection, plan = reviewed_inputs(
            context, runtime, pre, collection, plan
        )
        publisher_identity = validated_publisher_identity(runtime)
        bound_runtime, bound_runtime_bytes = write_bound_json(
            output.parent, "bound-runtime.json", runtime
        )
        bound_collection, bound_collection_bytes = write_bound_json(
            output.parent, "bound-collection.json", collection
        )
        bound_plan, bound_plan_bytes = write_bound_json(
            output.parent, "bound-artifact-plan.json", plan
        )
        review_path = Path(argv[6])
        review_bytes = stable_regular_bytes(review_path)
        if hashlib.sha256(review_bytes).hexdigest() != argv[9]:
            raise CommitError("publication review changed after validation")
        review = json.loads(review_bytes)
        if review.get("outcome") != "approved" or review.get(
            "publication_context_sha256"
        ) != context_digest:
            raise CommitError("publication review is not approved and context-bound")
        modes = {
            "agents_vault": review["agents_vault"]["publication_mode"],
            "user_vault": review["user_vault"]["publication_mode"],
        }
        deferred = {
            "agents_vault": review["agents_vault"]["deferred_cleanup"],
            "user_vault": review["user_vault"]["deferred_cleanup"],
        }
        if modes["agents_vault"] == "blocked" and modes["user_vault"] == "blocked":
            raise CommitError("review blocked both Vault artifacts")
        previous = None
        current_resume_state = None
        if output.exists():
            candidate = json.loads(stable_regular_bytes(output))
            if (
                candidate.get("outcome") == "partial_publication"
                and candidate.get("phase") == "local_commit"
                and candidate.get("publication_context_sha256") == context_digest
                and isinstance(candidate.get("resumable_state"), dict)
                and candidate.get("agents_vault", {}).get("commit_status")
                in {"complete", "failed"}
                and candidate.get("user_vault", {}).get("commit_status")
                == "not_started"
                and candidate.get("publication_mode") == modes
                and candidate.get("evidence_finalization_commit") is None
                and modes["agents_vault"] != "blocked"
                and modes["user_vault"] != "blocked"
            ):
                previous = candidate
            else:
                raise CommitError("existing commit result is not a resumable partial publication")
        if previous is None:
            capture_exact(argv[8], str(bound_runtime), pre)
        else:
            current_resume_state = capture_state(argv[8], str(bound_runtime))
            if current_resume_state != previous["resumable_state"]:
                raise CommitError("Vaults no longer match the resumable result")
        installed = {
            "summary_target": plan["summary_target"],
            "advisory_target": plan["advisory_target"],
        }
        require_bound_bytes(bound_runtime, bound_runtime_bytes)
        require_bound_bytes(bound_collection, bound_collection_bytes)
        require_bound_bytes(bound_plan, bound_plan_bytes)
        if installed != {
            "summary_target": plan["summary_target"],
            "advisory_target": plan["advisory_target"],
        }:
            raise CommitError("installed artifacts differ from the approved plan")
        agents_artifact = str(
            Path(plan["advisory_target"]).relative_to(runtime["agents_vault_root"])
        )
        user_artifact = str(
            Path(plan["summary_target"]).relative_to(runtime["user_vault_root"])
        )
        if modes["agents_vault"] != "blocked":
            if previous is None:
                installed_agents = json.loads(
                    run_local_command(
                        [
                            argv[7], str(bound_runtime), str(bound_collection),
                            str(bound_plan), "agents_security_advisory",
                        ],
                        cwd="/",
                        check=True,
                        capture_output=True,
                        text=True,
                        env=clean_environment(),
                        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
                    ).stdout
                )
                if (
                    not isinstance(installed_agents, dict)
                    or installed_agents.get("summary_target")
                    != installed["summary_target"]
                    or installed_agents.get("advisory_target")
                    != installed["advisory_target"]
                ):
                    raise CommitError("installed Agents artifact differs from the approved plan")
                rollback_receipts["agents_vault"] = validated_installer_receipt(
                    installed_agents.get("installed_receipt"),
                    Path(plan["advisory_target"]),
                    str(collection["advisory_sha256"]),
                )
                agents_installed_state = capture_installed_scope(
                    argv[8], str(bound_runtime), pre,
                    {"agents_vault": agents_artifact},
                )
            else:
                agents_installed_state = current_resume_state
            validate_final_worktree(
                str(runtime["agents_vault_root"]),
                str(runtime["agents_git_dir"]),
                review["agents_vault"],
                agents_artifact,
                str(collection["advisory_sha256"]),
            )
            if previous is None:
                agents = commit_groups(
                    str(runtime["agents_vault_root"]),
                    str(runtime["agents_git_dir"]),
                    str(runtime["gitleaks_bin"]),
                    pre["agents_vault"],
                    review["agents_vault"],
                    agents_artifact,
                    str(collection["advisory_sha256"]),
                    output.parent,
                    publisher_identity,
                    before_update=lambda: capture_exact(
                        argv[8], str(bound_runtime), agents_installed_state
                    ),
                    publication_mode=modes["agents_vault"],
                )
                rollback_receipts.pop("agents_vault", None)
            else:
                agents = current_state(
                    str(runtime["agents_vault_root"]),
                    str(runtime["agents_git_dir"]),
                    pre["agents_vault"],
                )
                claimed = previous["agents_vault"]
                if any(
                    agents[field] != claimed.get(field)
                    for field in ("commit_hashes", "local_head", "clean", "post_dirty_digest")
                ):
                    raise CommitError("Agents Vault no longer matches the resumable result")
                agents["commit_status"] = "complete"
                agents["publication_mode"] = modes["agents_vault"]
                agents["deferred_cleanup"] = review["agents_vault"]["deferred_cleanup"]
        else:
            agents_installed_state = pre
            agents = unchanged_vault_result(
                str(runtime["agents_vault_root"]),
                str(runtime["agents_git_dir"]),
                pre["agents_vault"],
                review["agents_vault"],
            )
        after_agents = capture_state(argv[8], str(bound_runtime))
        expected_user_before_install = (
            current_resume_state["user_vault"]
            if previous is not None
            else pre["user_vault"]
        )
        if after_agents["user_vault"] != expected_user_before_install:
            raise CommitError("User Vault changed during Agents publication")
        if modes["agents_vault"] == "blocked":
            if after_agents["agents_vault"] != pre["agents_vault"]:
                raise CommitError("blocked Agents Vault changed")
        else:
            if after_agents["agents_vault"]["local_head"] != agents["local_head"]:
                raise CommitError("Agents Vault HEAD differs from created commit")
            validate_post_commit_state(
                pre["agents_vault"],
                after_agents["agents_vault"],
                agents_artifact,
                modes["agents_vault"],
            )
        if modes["user_vault"] != "blocked":
            if previous is None or user_artifact not in after_agents["user_vault"]["dirty_paths"]:
                installed_user = json.loads(
                    run_local_command(
                        [
                            argv[7], str(bound_runtime), str(bound_collection),
                            str(bound_plan), "user_it_news_summary",
                        ],
                        cwd="/",
                        check=True,
                        capture_output=True,
                        text=True,
                        env=clean_environment(),
                        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
                    ).stdout
                )
                if (
                    not isinstance(installed_user, dict)
                    or installed_user.get("summary_target")
                    != installed["summary_target"]
                    or installed_user.get("advisory_target")
                    != installed["advisory_target"]
                ):
                    raise CommitError("installed User artifact differs from the approved plan")
                rollback_receipts["user_vault"] = validated_installer_receipt(
                    installed_user.get("installed_receipt"),
                    Path(plan["summary_target"]),
                    str(collection["summary_sha256"]),
                )
                user_installed_state = capture_installed_scope(
                    argv[8], str(bound_runtime), after_agents,
                    {"user_vault": user_artifact},
                )
            else:
                resume_baseline = {
                    "agents_vault": after_agents["agents_vault"],
                    "user_vault": pre["user_vault"],
                }
                validate_installed_scope(
                    resume_baseline,
                    after_agents,
                    {"user_vault": user_artifact},
                )
                user_installed_state = after_agents
            validate_final_worktree(
                str(runtime["user_vault_root"]),
                str(runtime["user_git_dir"]),
                review["user_vault"],
                user_artifact,
                str(collection["summary_sha256"]),
            )
            user = commit_groups(
                str(runtime["user_vault_root"]),
                str(runtime["user_git_dir"]),
                str(runtime["gitleaks_bin"]),
                pre["user_vault"],
                review["user_vault"],
                user_artifact,
                str(collection["summary_sha256"]),
                output.parent,
                publisher_identity,
                before_update=lambda: capture_exact(
                    argv[8], str(bound_runtime), user_installed_state
                ),
                publication_mode=modes["user_vault"],
            )
            rollback_receipts.pop("user_vault", None)
        else:
            user = unchanged_vault_result(
                str(runtime["user_vault_root"]),
                str(runtime["user_git_dir"]),
                pre["user_vault"],
                review["user_vault"],
            )
        after_all = capture_state(argv[8], str(bound_runtime))
        expected_agents_after_user = (
            user_installed_state["agents_vault"]
            if modes["user_vault"] != "blocked"
            else after_agents["agents_vault"]
        )
        if after_all["agents_vault"] != expected_agents_after_user:
            raise CommitError("Agents Vault changed during User publication")
        if modes["user_vault"] == "blocked":
            if after_all["user_vault"] != pre["user_vault"]:
                raise CommitError("blocked User Vault changed")
        else:
            if after_all["user_vault"]["local_head"] != user["local_head"]:
                raise CommitError("User Vault HEAD differs from created commit")
            validate_post_commit_state(
                pre["user_vault"],
                after_all["user_vault"],
                user_artifact,
                modes["user_vault"],
            )
        result = {
            "outcome": "ready_to_push",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": (
                installed["summary_target"]
                if modes["user_vault"] != "blocked"
                else None
            ),
            "advisory_path": (
                installed["advisory_target"]
                if modes["agents_vault"] != "blocked"
                else None
            ),
            "notification_result": collection.get("notification_result"),
            "agents_vault": agents,
            "user_vault": user,
            "publication_mode": modes,
            "deferred_cleanup": {
                "agents_vault": review["agents_vault"]["deferred_cleanup"],
                "user_vault": review["user_vault"]["deferred_cleanup"],
            },
            "evidence_finalization_commit": None,
            "next_action": review.get("next_action"),
        }
    except (
        CommitError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        rollback_failures = []
        for key, receipt in list(rollback_receipts.items()):
            try:
                prefix = "agents" if key == "agents_vault" else "user"
                if runtime and pre and git(
                    str(runtime[f"{prefix}_vault_root"]),
                    str(runtime[f"{prefix}_git_dir"]),
                    "rev-parse", "HEAD",
                ).stdout.strip() == pre[key]["local_head"]:
                    rollback_owned_artifact(receipt)
            except Exception as rollback_exc:
                rollback_failures.append(f"{key}:{rollback_exc}")
        if rollback_failures:
            exc = CommitError(
                f"{exc}; owned artifact rollback failed: {'; '.join(rollback_failures)}"
            )
        result = result_after_failure(
            runtime,
            pre,
            collection,
            plan,
            f"Local publication failed closed: {exc}",
            context_digest,
            argv[8] if bound_runtime is not None else None,
            str(bound_runtime) if bound_runtime is not None else None,
            modes,
            deferred,
        )
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 75
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    """Publish User then Agents independently from one reviewed context."""
    if len(argv) != 11:
        print(
            "usage: commit-reviewed-publication.py RUNTIME PRE COLLECTION PLAN "
            "CONTEXT REVIEW INSTALLER CAPTURE REVIEW_SHA OUTPUT",
            file=sys.stderr,
        )
        return 64
    output = Path(argv[10])
    runtime: dict[str, object] = {}
    pre: dict[str, object] = {}
    collection: dict[str, object] = {}
    plan: dict[str, object] = {}
    context_digest: str | None = None
    bound_runtime: Path | None = None
    modes: dict[str, str] | None = None
    deferred: dict[str, list[dict[str, str]]] | None = None
    try:
        runtime_input = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre_input = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        collection_input = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan_input = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        context_bytes = stable_regular_bytes(Path(argv[5]))
        context_digest = hashlib.sha256(context_bytes).hexdigest()
        context = json.loads(context_bytes)
        runtime, pre, collection, plan = reviewed_inputs(
            context, runtime_input, pre_input, collection_input, plan_input
        )
        publisher_identity = validated_publisher_identity(runtime)
        review_bytes = stable_regular_bytes(Path(argv[6]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[9]:
            raise CommitError("publication review changed after validation")
        review = json.loads(review_bytes)
        if (
            review.get("outcome") != "approved"
            or review.get("publication_context_sha256") != context_digest
        ):
            raise CommitError("publication review is not approved and context-bound")
        modes = {
            "agents_vault": str(review["agents_vault"]["publication_mode"]),
            "user_vault": str(review["user_vault"]["publication_mode"]),
        }
        deferred = {
            "agents_vault": list(review["agents_vault"].get("deferred_cleanup", [])),
            "user_vault": list(review["user_vault"].get("deferred_cleanup", [])),
        }
        bound_runtime, _ = write_bound_json(output.parent, "bound-runtime.json", runtime)
        bound_collection, _ = write_bound_json(
            output.parent, "bound-collection.json", collection
        )
        bound_plan, _ = write_bound_json(
            output.parent, "bound-artifact-plan.json", plan
        )
        previous: dict[str, object] | None = None
        resume_pair: dict[str, object] | None = None
        if output.exists():
            candidate = json.loads(stable_regular_bytes(output))
            if (
                candidate.get("outcome") != "partial_publication"
                or candidate.get("phase") != "local_commit"
                or candidate.get("publication_context_sha256") != context_digest
                or not isinstance(candidate.get("resumable_state"), dict)
            ):
                raise CommitError("existing commit result is not a resumable partial publication")
            resume_pair = capture_state(argv[8], str(bound_runtime))
            for resume_key, label in (
                ("agents_vault", "Agents"), ("user_vault", "User")
            ):
                if resume_pair.get(resume_key) != candidate["resumable_state"].get(
                    resume_key
                ):
                    raise CommitError(
                        "Vaults no longer match the resumable result: "
                        f"{label} Vault no longer matches"
                    )
            previous = candidate

        if previous is not None:
            for completed_key, completed_prefix, completed_label in (
                ("agents_vault", "agents", "Agents"),
                ("user_vault", "user", "User"),
            ):
                claimed = previous[completed_key]
                if not claimed.get("commit_hashes"):
                    continue
                actual = current_state(
                    str(runtime[f"{completed_prefix}_vault_root"]),
                    str(runtime[f"{completed_prefix}_git_dir"]),
                    pre[completed_key],
                )
                if any(
                    actual[field] != claimed.get(field)
                    for field in (
                        "commit_hashes",
                        "local_head",
                        "clean",
                        "post_dirty_digest",
                    )
                ):
                    raise CommitError(
                        f"{completed_label} Vault no longer matches the resumable result"
                    )

        def run_vault(
            key: str,
            prefix: str,
            role: str,
            plan_key: str,
            sha_key: str,
        ) -> tuple[
            dict[str, object],
            bool,
            bool,
            str | None,
            dict[str, object] | None,
        ]:
            if previous is not None and previous[key].get("commit_hashes"):
                actual = current_state(
                    str(runtime[f"{prefix}_vault_root"]),
                    str(runtime[f"{prefix}_git_dir"]),
                    pre[key],
                )
                claimed = previous[key]
                if any(
                    actual[field] != claimed.get(field)
                    for field in (
                        "commit_hashes",
                        "local_head",
                        "clean",
                        "post_dirty_digest",
                    )
                ):
                    label = "Agents" if key == "agents_vault" else "User"
                    raise CommitError(
                        f"{label} Vault no longer matches the resumable result"
                    )
                actual["commit_status"] = "complete"
                actual["publication_mode"] = claimed["publication_mode"]
                actual["deferred_cleanup"] = list(claimed["deferred_cleanup"])
                return actual, True, False, None, None
            last: tuple[
                dict[str, object],
                bool,
                bool,
                str | None,
                dict[str, object] | None,
            ] | None = None
            attempts = 1 if review[key]["publication_mode"] == "blocked" else 3
            for _ in range(attempts):
                last = publish_one_vault(
                    key=key,
                    prefix=prefix,
                    role=role,
                    artifact_plan_key=plan_key,
                    collection_sha_key=sha_key,
                    runtime=runtime,
                    pre=pre,
                    collection=collection,
                    plan=plan,
                    manifest=review[key],
                    installer=argv[7],
                    capture=argv[8],
                    runtime_file=str(bound_runtime),
                    collection_file=str(bound_collection),
                    plan_file=str(bound_plan),
                    output_directory=output.parent,
                    publisher_identity=publisher_identity,
                    resume_state=(resume_pair[key] if resume_pair is not None else None),
                )
                result, succeeded, retry_safe, _reason, _rollback_handle = last
                if succeeded or result["commit_hashes"] or not retry_safe:
                    break
                if capture_one(argv[8], str(bound_runtime), key) != pre[key]:
                    break
            assert last is not None
            return last

        # The daily summary is the primary availability objective. A failure in
        # Agents publication must not suppress the independently safe User commit.
        user, user_ok, user_retry_safe, user_reason, user_rollback = run_vault(
            "user_vault",
            "user",
            "user_it_news_summary",
            "summary_target",
            "summary_sha256",
        )
        agents, agents_ok, agents_retry_safe, agents_reason, agents_rollback = run_vault(
            "agents_vault",
            "agents",
            "agents_security_advisory",
            "advisory_target",
            "advisory_sha256",
        )
        user_compensated_for_peer = False
        agents_compensated_for_peer = False
        # Cross-repository CAS cannot be atomic. If exactly one candidate was
        # committed and its peer failed without mutation, compensate the owned,
        # unpushed commit back to the reviewed state. The next bounded outer
        # attempt can then take a fresh snapshot and review without duplicating
        # the already-created daily artifact.
        if user_ok and not agents_ok and agents_retry_safe and user_rollback is not None:
            restored = restore_completed_publication(
                handle=user_rollback,
                capture=argv[8],
                runtime_file=str(bound_runtime),
                key="user_vault",
            )
            user = vault_result_from_snapshot(
                restored,
                pre["user_vault"],
                commit_status="not_started",
                commit_hashes=[],
                publication_mode="blocked",
                deferred_cleanup=list(review["user_vault"].get("deferred_cleanup", [])),
            )
            user_ok = False
            user_retry_safe = True
            user_compensated_for_peer = True
            user_reason = "owned unpushed commit was compensated for peer re-plan"
        elif (
            agents_ok
            and not user_ok
            and user_retry_safe
            and agents_rollback is not None
        ):
            restored = restore_completed_publication(
                handle=agents_rollback,
                capture=argv[8],
                runtime_file=str(bound_runtime),
                key="agents_vault",
            )
            agents = vault_result_from_snapshot(
                restored,
                pre["agents_vault"],
                commit_status="not_started",
                commit_hashes=[],
                publication_mode="blocked",
                deferred_cleanup=list(
                    review["agents_vault"].get("deferred_cleanup", [])
                ),
            )
            agents_ok = False
            agents_retry_safe = True
            agents_compensated_for_peer = True
            agents_reason = "owned unpushed commit was compensated for peer re-plan"
        progressed = bool(user["commit_hashes"] or agents["commit_hashes"])
        publishable = user_ok or agents_ok or progressed
        attempted_retry_safety = []
        attempted_retry_vaults = []
        if review["user_vault"]["publication_mode"] != "blocked" and not user_ok:
            attempted_retry_safety.append(user_retry_safe)
            if user_retry_safe and not user_compensated_for_peer:
                attempted_retry_vaults.append("user_vault")
        if review["agents_vault"]["publication_mode"] != "blocked" and not agents_ok:
            attempted_retry_safety.append(agents_retry_safe)
            if agents_retry_safe and not agents_compensated_for_peer:
                attempted_retry_vaults.append("agents_vault")
        retry_disposition = (
            "replan"
            if not publishable
            and attempted_retry_safety
            and all(attempted_retry_safety)
            else "none"
        )
        reasons = [
            value
            for value in (
                f"User Vault: {user_reason}" if user_reason and not user_ok else None,
                f"Agents Vault: {agents_reason}" if agents_reason and not agents_ok else None,
            )
            if value
        ]
        outcome = (
            "partial_publication"
            if progressed and not (user_ok and agents_ok)
            else ("ready_to_push" if publishable else "blocked")
        )
        result = {
            "outcome": outcome,
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": plan["summary_target"] if user_ok else None,
            "advisory_path": plan["advisory_target"] if agents_ok else None,
            "notification_result": collection.get("notification_result"),
            "agents_vault": agents,
            "user_vault": user,
            "publication_mode": {
                "agents_vault": agents["publication_mode"],
                "user_vault": user["publication_mode"],
            },
            "deferred_cleanup": {
                "agents_vault": agents["deferred_cleanup"],
                "user_vault": user["deferred_cleanup"],
            },
            "evidence_finalization_commit": None,
            "retry_disposition": retry_disposition,
            "replan_vaults": (
                attempted_retry_vaults if retry_disposition == "replan" else []
            ),
            "next_action": "; ".join(reasons) if reasons else None,
        }
        if not publishable and result["next_action"] is None:
            result["next_action"] = "Both Vault publications are blocked by review"
        if outcome == "partial_publication":
            result["publication_context_sha256"] = context_digest
            result["resumable_state"] = capture_state(argv[8], str(bound_runtime))
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0 if publishable else 75
    except (
        CommitError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Local publication failed closed: {exc}", file=sys.stderr)
        result = result_after_failure(
            runtime,
            pre,
            collection,
            plan,
            f"Local publication failed closed: {exc}",
            context_digest,
            argv[8] if bound_runtime is not None else None,
            str(bound_runtime) if bound_runtime is not None else None,
            modes,
            deferred,
        )
        try:
            output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
