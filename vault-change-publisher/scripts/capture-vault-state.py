#!/usr/bin/env python3
"""Capture deterministic Git state for the two catalog-derived Vaults."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import os
from pathlib import Path


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
    return environment


def git(repo: str, *arguments: str) -> str:
    """Run one read-only Git command and return trimmed stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
    )
    return result.stdout.rstrip("\n")


def is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    """Return whether one exact commit is an ancestor of another."""
    result = subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
    )
    if result.returncode not in {0, 1}:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    return result.returncode == 0


def commit_patch(repo: str, commit: str, parents: list[str]) -> bytes:
    """Return a deterministic binary patch against the first parent."""
    if parents:
        arguments = [
            "git", "-C", repo, "diff", "--binary", "--full-index",
            "--no-ext-diff", parents[0], commit,
        ]
    else:
        arguments = [
            "git", "-C", repo, "diff-tree", "--root", "-p", "--binary",
            "--full-index", "--no-ext-diff", commit,
        ]
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        env=clean_git_environment(),
    ).stdout


def local_commit_metadata(
    repo: str, remote_head: str, local_head: str
) -> list[dict[str, object]]:
    """Describe every local-only commit that a later fixed push would publish."""
    commits = [
        value
        for value in git(
            repo, "rev-list", "--reverse", "--topo-order",
            f"{remote_head}..{local_head}",
        ).splitlines()
        if value
    ]
    result: list[dict[str, object]] = []
    for commit in commits:
        parents = git(repo, "show", "-s", "--format=%P", commit).split()
        tree = git(repo, "show", "-s", "--format=%T", commit)
        message = git(repo, "show", "-s", "--format=%B", commit)
        if parents:
            changed = git(
                repo, "diff", "--name-only", "--no-renames", "-z",
                parents[0], commit,
            )
        else:
            changed = git(
                repo, "diff-tree", "--root", "--no-commit-id", "--name-only",
                "--no-renames", "-r", "-z", commit,
            )
        patch = commit_patch(repo, commit, parents)
        result.append(
            {
                "commit": commit,
                "parents": parents,
                "tree": tree,
                "message": message,
                "changed_paths": sorted(value for value in changed.split("\0") if value),
                "patch_sha256": hashlib.sha256(patch).hexdigest(),
            }
        )
    return result


def capture(repo: str, include_local_history: bool = False) -> dict[str, object]:
    """Capture branch, heads, operation markers, and all dirty paths."""
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
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
    config_path = git_dir / "config"
    control.update(b"config\0")
    control.update(config_path.read_bytes())
    hooks_path = git_dir / "hooks"
    if hooks_path.exists():
        for root, directories, files in os.walk(hooks_path, followlinks=False):
            directories.sort()
            files.sort()
            for filename in files:
                path = Path(root) / filename
                relative = path.relative_to(git_dir)
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
    repo_path = Path(repo)
    for relative in dirty_paths:
        path = repo_path / relative
        if not os.path.lexists(path):
            dirty_entries.append(
                {"path": relative, "git_blob_oid": None, "mode": None}
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
                blob_oid = subprocess.run(
                    ["git", "-C", repo, "hash-object", "--stdin"],
                    input=os.fsencode(os.readlink(path)),
                    check=True,
                    capture_output=True,
                    env=clean_git_environment(),
                ).stdout.decode("ascii").strip()
            elif path.is_file():
                mode = "100755" if metadata.st_mode & 0o111 else "100644"
                blob_oid = git(
                    repo, "hash-object", f"--path={relative}", "--", relative
                )
            else:
                mode = "unsupported"
                blob_oid = git(
                    repo, "hash-object", f"--path={relative}", "--", relative
                )
        dirty_entries.append(
            {"path": relative, "git_blob_oid": blob_oid, "mode": mode}
        )
    snapshot = bytearray(b"cached\0")
    snapshot.extend(
        git(repo, "diff", "--cached", "--binary", "--no-ext-diff").encode("utf-8")
    )
    snapshot.extend(b"\0worktree\0")
    snapshot.extend(
        git(repo, "diff", "--binary", "--no-ext-diff").encode("utf-8")
    )
    for relative in untracked_paths:
        path = repo_path / relative
        if path.is_symlink():
            object_id = subprocess.run(
                ["git", "-C", repo, "hash-object", "--stdin"],
                input=os.fsencode(os.readlink(path)),
                check=True,
                capture_output=True,
                env=clean_git_environment(),
            ).stdout.decode("ascii").strip()
        else:
            object_id = git(repo, "hash-object", "--no-filters", "--", relative)
        snapshot.extend(b"\0untracked\0")
        snapshot.extend(relative.encode("utf-8"))
        snapshot.extend(b"\0")
        snapshot.extend(object_id.encode("ascii"))
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
    local_commits = (
        local_commit_metadata(repo, remote_head, local_head)
        if include_local_history and history_relation == "local_ahead"
        else []
    )
    history_snapshot = json.dumps(
        local_commits, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "repo_root": str(Path(repo).resolve()),
        "branch": git(repo, "branch", "--show-current"),
        "upstream": git(repo, "rev-parse", "--abbrev-ref", "@{u}"),
        "local_head": local_head,
        "remote_head": remote_head,
        "history_relation": history_relation,
        "local_commits": local_commits,
        "history_snapshot_sha256": hashlib.sha256(history_snapshot).hexdigest(),
        "operation_in_progress": operation,
        "git_control_sha256": control.hexdigest(),
        "dirty_lines": status.splitlines() if status else [],
        "dirty_paths": dirty_paths,
        "dirty_entries": dirty_entries,
        "dirty_digest": hashlib.sha256(
            (status + ("\n" if status else "")).encode("utf-8")
        ).hexdigest(),
        "diff_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
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
        result = {
            "agents_vault": capture(
                context["agents_vault_root"], include_local_history
            ),
            "user_vault": capture(
                context["user_vault_root"], include_local_history
            ),
        }
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Vault state capture failed:{exc}", file=sys.stderr)
        return 75
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
