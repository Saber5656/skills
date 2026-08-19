#!/usr/bin/env python3
"""Capture deterministic Git state for the two catalog-derived Vaults."""

from __future__ import annotations

import json
import hashlib
import selectors
import subprocess
import sys
import os
import time
from pathlib import Path

from isolated_git_transport import (
    LOCAL_COMMAND_TIMEOUT_SECONDS,
    kill_process_group,
    run_local_command,
)


MAX_GIT_METADATA_BYTES = 1024 * 1024
MAX_LOCAL_COMMITS = 256
MAX_TOTAL_HISTORY_METADATA_BYTES = 8 * 1024 * 1024


class HistoryUnavailable(RuntimeError):
    """Represent local-only history that cannot be reviewed within bounds."""


def clean_git_environment() -> dict[str, str]:
    """Ignore ambient Git overrides and replacement/lazy objects."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    # A repository-local fsmonitor command must never execute while a state
    # snapshot is being captured, including during a config-change race after
    # runtime-context validation.
    environment["GIT_CONFIG_COUNT"] = "1"
    environment["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    environment["GIT_CONFIG_VALUE_0"] = "false"
    return environment


def git(repo: str, *arguments: str) -> str:
    """Run one read-only Git command and return trimmed stdout."""
    result = run_local_command(
        ["git", "-C", repo, *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return result.stdout.rstrip("\n")


def git_bounded_bytes(repo: str, arguments: list[str], limit: int) -> bytes:
    """Read one Git metadata stream without unbounded buffering."""
    process = subprocess.Popen(
        ["git", "-C", repo, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=clean_git_environment(),
        start_new_session=True,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + LOCAL_COMMAND_TIMEOUT_SECONDS
    content = bytearray()
    finished = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise HistoryUnavailable("local history metadata exceeded its deadline")
            chunk = os.read(
                process.stdout.fileno(), min(65536, limit + 1 - len(content))
            )
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > limit:
                raise HistoryUnavailable("local history metadata exceeds size limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HistoryUnavailable("local history metadata exceeded its deadline")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise HistoryUnavailable(
                "local history metadata exceeded its deadline"
            ) from exc
        finished = True
    finally:
        selector.close()
        process.stdout.close()
        if not finished:
            kill_process_group(process)
    if return_code != 0:
        raise HistoryUnavailable("local history metadata is unavailable")
    return bytes(content)


def git_bounded_text(repo: str, arguments: list[str], limit: int) -> str:
    """Return bounded UTF-8 Git metadata or block only this Vault history."""
    try:
        return git_bounded_bytes(repo, arguments, limit).decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise HistoryUnavailable("local history metadata is not UTF-8") from exc


def file_sha256(path: Path) -> str:
    """Hash a file with bounded memory and no symlink following."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    """Return whether one exact commit is an ancestor of another."""
    result = run_local_command(
        ["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode not in {0, 1}:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    return result.returncode == 0


def commit_patch(repo: str, commit: str, parents: list[str]) -> bytes:
    """Return a bounded deterministic patch for focused verification callers."""
    if parents:
        arguments = [
            "diff", "--binary", "--full-index", "--no-ext-diff",
            "--no-textconv", parents[0], commit,
        ]
    else:
        arguments = [
            "diff-tree", "--root", "-p", "--binary", "--full-index",
            "--no-ext-diff", "--no-textconv", commit,
        ]
    return git_bounded_bytes(repo, arguments, MAX_GIT_METADATA_BYTES)


def local_commit_metadata(
    repo: str, remote_head: str, local_head: str
) -> list[dict[str, object]]:
    """Describe local-only commits without materializing their patches."""
    commits = [
        value
        for value in git_bounded_text(
            repo,
            ["rev-list", "--reverse", "--topo-order", f"{remote_head}..{local_head}"],
            MAX_GIT_METADATA_BYTES,
        ).splitlines()
        if value
    ]
    if len(commits) > MAX_LOCAL_COMMITS:
        raise HistoryUnavailable("local commit count exceeds review limit")
    result: list[dict[str, object]] = []
    total_metadata = 0
    for commit in commits:
        parents = git_bounded_text(
            repo, ["show", "-s", "--format=%P", commit], 8192
        ).split()
        tree = git_bounded_text(
            repo, ["show", "-s", "--format=%T", commit], 256
        )
        message = git_bounded_text(
            repo, ["show", "-s", "--format=%B", commit], MAX_GIT_METADATA_BYTES
        )
        if parents:
            changed = git_bounded_bytes(
                repo,
                ["diff", "--name-only", "--no-renames", "-z", parents[0], commit],
                MAX_GIT_METADATA_BYTES,
            )
        else:
            changed = git_bounded_bytes(
                repo,
                [
                    "diff-tree", "--root", "--no-commit-id", "--name-only",
                    "--no-renames", "-r", "-z", commit,
                ],
                MAX_GIT_METADATA_BYTES,
            )
        try:
            changed_paths = sorted(
                value.decode("utf-8") for value in changed.split(b"\0") if value
            )
        except UnicodeDecodeError as exc:
            raise HistoryUnavailable("local commit path is not UTF-8") from exc
        total_metadata += len(message.encode("utf-8")) + len(changed)
        if total_metadata > MAX_TOTAL_HISTORY_METADATA_BYTES:
            raise HistoryUnavailable("local history metadata exceeds Vault limit")
        result.append(
            {
                "commit": commit,
                "parents": parents,
                "tree": tree,
                "message": message,
                "changed_paths": changed_paths,
            }
        )
    return result


def capture(repo: str, include_local_history: bool = False) -> dict[str, object]:
    """Capture branch, heads, operation markers, and all dirty paths."""
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    common_dir = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    operation = any(
        git_dir.joinpath(marker).exists()
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
    )
    control = hashlib.sha256()
    git_marker = Path(repo) / ".git"
    control.update(b"worktree-git-entry\0")
    control.update(f"{git_marker.lstat().st_mode:o}".encode("ascii"))
    control.update(b"\0")
    if git_marker.is_symlink():
        control.update(b"symlink\0")
        control.update(os.fsencode(os.readlink(git_marker)))
    elif git_marker.is_file():
        control.update(b"file\0")
        control.update(git_marker.read_bytes())
    else:
        control.update(b"directory\0")
    control_paths = [common_dir / "config", git_dir / "config.worktree"]
    seen_control_paths: set[Path] = set()
    for config_path in control_paths:
        if config_path in seen_control_paths or not os.path.lexists(config_path):
            continue
        seen_control_paths.add(config_path)
        control.update(b"config\0")
        control.update(str(config_path).encode("utf-8"))
        control.update(b"\0")
        control.update(f"{config_path.lstat().st_mode:o}".encode("ascii"))
        control.update(b"\0")
        if config_path.is_symlink():
            control.update(b"symlink\0")
            control.update(os.fsencode(os.readlink(config_path)))
        else:
            control.update(config_path.read_bytes())
        control.update(b"\0")
    hooks_path = common_dir / "hooks"
    if hooks_path.exists():
        for root, directories, files in os.walk(hooks_path, followlinks=False):
            directories.sort()
            files.sort()
            for filename in files:
                path = Path(root) / filename
                relative = path.relative_to(common_dir)
                control.update(str(relative).encode("utf-8"))
                control.update(b"\0")
                control.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                control.update(b"\0")
                if path.is_symlink():
                    control.update(b"symlink\0")
                    control.update(os.readlink(path).encode("utf-8"))
                else:
                    control.update(path.read_bytes())
                control.update(b"\0")
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    staged_paths = [
        value
        for value in git(
            repo, "diff", "--cached", "--name-only", "--no-renames", "-z"
        ).split("\0")
        if value
    ]
    staged_paths = sorted(staged_paths)
    worktree_paths = [
        value
        for value in git(
            repo, "diff", "--name-only", "--no-renames", "-z"
        ).split("\0")
        if value
    ]
    head_worktree_paths = [
        value
        for value in git(
            repo, "diff", "--name-only", "--no-renames", "-z", "HEAD"
        ).split("\0")
        if value
    ]
    untracked_paths = [
        value
        for value in git(
            repo, "ls-files", "--others", "--exclude-standard", "-z"
        ).split("\0")
        if value
    ]
    dirty_paths = sorted(set(staged_paths + worktree_paths + untracked_paths))
    dirty_entries = []
    dirty_metadata = []
    repo_path = Path(repo)
    for relative in dirty_paths:
        path = repo_path / relative
        if not os.path.lexists(path):
            dirty_entries.append(
                {"path": relative, "git_blob_oid": None, "mode": None}
            )
            dirty_metadata.append(
                {
                    "path": relative,
                    "exists": False,
                    "size": None,
                    "mtime_ns": None,
                    "st_mode": None,
                }
            )
            continue
        if relative in staged_paths and relative not in head_worktree_paths:
            index_entry = git(repo, "ls-files", "--stage", "--", relative).split()
            if len(index_entry) < 4:
                dirty_entries.append(
                    {"path": relative, "git_blob_oid": None, "mode": None}
                )
                continue
            mode = index_entry[0]
            blob_oid = index_entry[1]
        else:
            metadata = path.lstat()
            if path.is_symlink():
                mode = "120000"
                blob_oid = run_local_command(
                    ["git", "-C", repo, "hash-object", "--stdin"],
                    input=os.fsencode(os.readlink(path)),
                    check=True,
                    capture_output=True,
                    env=clean_git_environment(),
                    timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
                ).stdout.decode("ascii").strip()
            elif path.is_file():
                mode = "100755" if metadata.st_mode & 0o111 else "100644"
                # Snapshot the literal reviewed bytes.  --path would apply a
                # repository-controlled clean filter from .gitattributes.
                blob_oid = git(repo, "hash-object", "--no-filters", "--", relative)
            else:
                mode = "unsupported"
                blob_oid = None
        dirty_entries.append(
            {"path": relative, "git_blob_oid": blob_oid, "mode": mode}
        )
        metadata = path.lstat()
        dirty_metadata.append(
            {
                "path": relative,
                "exists": True,
                "size": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
                "st_mode": metadata.st_mode,
            }
        )
    local_head = git(repo, "rev-parse", "HEAD")
    remote_head = git(repo, "rev-parse", "origin/main")
    remote_is_ancestor = is_ancestor(repo, remote_head, local_head)
    local_is_ancestor = is_ancestor(repo, local_head, remote_head)
    if local_head == remote_head:
        history_relation = "equal"
    elif remote_is_ancestor:
        history_relation = "local_ahead"
    elif local_is_ancestor:
        history_relation = "remote_ahead"
    else:
        history_relation = "diverged"
    local_commits: list[dict[str, object]] = []
    history_capture_status = "not_required"
    history_capture_reason = None
    if include_local_history and history_relation == "local_ahead":
        try:
            local_commits = local_commit_metadata(repo, remote_head, local_head)
            history_capture_status = "available"
        except HistoryUnavailable as exc:
            history_capture_status = "blocked"
            history_capture_reason = str(exc)
    elif include_local_history:
        history_capture_status = "available"
    history_snapshot = json.dumps(
        {
            "status": history_capture_status,
            "reason": history_capture_reason,
            "commits": local_commits,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    branch = git(repo, "branch", "--show-current")
    upstream = git(
        repo,
        "for-each-ref",
        "--format=%(upstream:short)",
        f"refs/heads/{branch}",
    )
    # `git rev-parse --git-path index` can return `.git/index`, which is not
    # directly resolvable for linked worktrees because `.git` is a gitfile.
    # The absolute git dir above is the canonical location for the active
    # worktree index.
    index_path = git_dir / "index"
    index_sha256 = file_sha256(index_path) if index_path.exists() else hashlib.sha256(b"").hexdigest()
    index_entries = []
    raw_index_entries = git(repo, "ls-files", "--stage", "-z")
    for raw_entry in (value for value in raw_index_entries.split("\0") if value):
        metadata, separator, relative = raw_entry.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise ValueError("could not parse Git index entry")
        index_entries.append(
            {
                "path": relative,
                "mode": fields[0],
                "git_blob_oid": fields[1],
                "stage": int(fields[2]),
            }
        )
    dirty_worktree = json.dumps(
        {"entries": dirty_entries, "metadata": dirty_metadata},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    diff_snapshot = json.dumps(
        {
            "dirty_lines": status.splitlines() if status else [],
            "dirty_entries": dirty_entries,
            "dirty_metadata": dirty_metadata,
            "staged_paths": staged_paths,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "capture_status": "available",
        "capture_reason": None,
        "repo_root": str(Path(repo).resolve()),
        "branch": branch,
        "upstream": upstream or None,
        "local_head": local_head,
        "remote_head": remote_head,
        "history_relation": history_relation,
        "local_commits": local_commits,
        "history_capture_status": history_capture_status,
        "history_capture_reason": history_capture_reason,
        "history_snapshot_sha256": hashlib.sha256(history_snapshot).hexdigest(),
        "operation_in_progress": operation,
        "git_control_sha256": control.hexdigest(),
        "dirty_lines": status.splitlines() if status else [],
        "dirty_paths": dirty_paths,
        "dirty_entries": dirty_entries,
        "dirty_metadata": dirty_metadata,
        "staged_paths": staged_paths,
        "index_entries": index_entries,
        "index_sha256": index_sha256,
        "dirty_worktree_sha256": hashlib.sha256(dirty_worktree).hexdigest(),
        "dirty_digest": hashlib.sha256(
            (status + ("\n" if status else "")).encode("utf-8")
        ).hexdigest(),
        "diff_snapshot_sha256": hashlib.sha256(diff_snapshot).hexdigest(),
    }


def blocked_capture(repo: object, reason: BaseException) -> dict[str, object]:
    """Return a non-publishable per-Vault state without blocking collection."""
    zero_digest = "0" * 64
    return {
        "capture_status": "blocked",
        "capture_reason": f"{type(reason).__name__}:state_unavailable",
        "repo_root": str(Path(str(repo)).resolve()),
        "branch": None,
        "upstream": None,
        "local_head": "0" * 40,
        "remote_head": "0" * 40,
        "history_relation": "unavailable",
        "local_commits": [],
        "history_capture_status": "blocked",
        "history_capture_reason": "vault_state_capture_unavailable",
        "history_snapshot_sha256": zero_digest,
        "operation_in_progress": True,
        "git_control_sha256": zero_digest,
        "dirty_lines": [],
        "dirty_paths": [],
        "dirty_entries": [],
        "dirty_metadata": [],
        "staged_paths": [],
        "index_entries": [],
        "index_sha256": zero_digest,
        "dirty_worktree_sha256": zero_digest,
        "dirty_digest": zero_digest,
        "diff_snapshot_sha256": zero_digest,
    }


def main(argv: list[str]) -> int:
    """Read runtime context and emit both Vault states."""
    if len(argv) not in {2, 3} or (len(argv) == 3 and argv[1] != "--include-local-history"):
        print(
            "usage: capture-vault-state.py [--include-local-history] CONTEXT_JSON",
            file=sys.stderr,
        )
        return 64
    try:
        include_local_history = len(argv) == 3
        context_path = argv[2] if include_local_history else argv[1]
        context = json.loads(Path(context_path).read_text(encoding="utf-8"))
        result = {}
        for key, context_key in (
            ("agents_vault", "agents_vault_root"),
            ("user_vault", "user_vault_root"),
        ):
            repo = context[context_key]
            try:
                result[key] = capture(repo, include_local_history)
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
                result[key] = blocked_capture(repo, exc)
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Vault state capture failed:{exc}", file=sys.stderr)
        return 75
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
