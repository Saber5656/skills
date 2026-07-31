#!/usr/bin/env python3
"""Capture deterministic Git state for the two catalog-derived Vaults."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import os
from pathlib import Path


def git(repo: str, *arguments: str) -> str:
    """Run one read-only Git command and return trimmed stdout."""
    result = subprocess.run(
        ["git", "-C", repo, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def capture(repo: str) -> dict[str, object]:
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
            ).stdout.decode("ascii").strip()
        else:
            object_id = git(repo, "hash-object", "--no-filters", "--", relative)
        snapshot.extend(b"\0untracked\0")
        snapshot.extend(relative.encode("utf-8"))
        snapshot.extend(b"\0")
        snapshot.extend(object_id.encode("ascii"))
    return {
        "repo_root": str(Path(repo).resolve()),
        "branch": git(repo, "branch", "--show-current"),
        "upstream": git(repo, "rev-parse", "--abbrev-ref", "@{u}"),
        "local_head": git(repo, "rev-parse", "HEAD"),
        "remote_head": git(repo, "rev-parse", "origin/main"),
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
    if len(argv) != 2:
        print("usage: capture-vault-state.py CONTEXT_JSON", file=sys.stderr)
        return 64
    try:
        context = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        result = {
            "agents_vault": capture(context["agents_vault_root"]),
            "user_vault": capture(context["user_vault_root"]),
        }
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Vault state capture failed:{exc}", file=sys.stderr)
        return 75
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
