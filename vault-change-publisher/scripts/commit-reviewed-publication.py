#!/usr/bin/env python3
"""Install reviewed artifacts and create only the approved local commits."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


CLEAN_DIGEST = hashlib.sha256(b"").hexdigest()


class CommitError(RuntimeError):
    """Represent a publication mutation that must fail closed."""


def clean_environment() -> dict[str, str]:
    """Remove ambient Git overrides and prohibit lazy object retrieval."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
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
) -> subprocess.CompletedProcess[str]:
    """Run local-only Git with explicit metadata and work-tree paths."""
    environment = clean_environment()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    return subprocess.run(
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
    completed = subprocess.run(
        [capture, runtime_file],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
    )
    if json.loads(completed.stdout) != expected:
        raise CommitError("Vault state changed after the approved review")


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
    completed = subprocess.run(
        [
            gitleaks_bin,
            "--no-banner",
            "--redact",
            "--ignore-gitleaks-allow",
            "--gitleaks-ignore-path",
            os.devnull,
            "git",
            "--staged",
            repo,
        ],
        cwd="/",
        check=False,
        capture_output=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise CommitError("gitleaks rejected the staged publication")
    patch = git(
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
    home = str(Path.home())
    if home and any(
        line.startswith("+") and not line.startswith("+++") and home in line
        for line in patch.splitlines()
    ):
        raise CommitError("staged publication adds a machine-specific home path")
    home_bytes = os.fsencode(home)
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
            candidate = subprocess.run(
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
            ).stdout
            if home_bytes in candidate:
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


def write_blob(
    repo: str,
    git_dir: str,
    content: bytes,
    expected_oid: str | None = None,
) -> str:
    """Write one already-verified blob without filters or a shell."""
    completed = subprocess.run(
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
) -> dict[str, object]:
    """Create the ordered, minimal commits declared by the approved manifest."""
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
        git(repo, git_dir, "update-ref", "HEAD", commit, current_head)
        for path in paths:
            update_index_entry(repo, git_dir, path, entries[path])
        current_head = commit
    result = current_state(repo, git_dir, pre)
    if not result["clean"] or len(result["commit_hashes"]) != len(groups):
        raise CommitError("approved local commits did not leave a clean Vault")
    result["commit_status"] = "complete"
    return result


def result_after_failure(
    runtime: dict[str, object],
    pre: dict[str, object],
    collection: dict[str, object],
    plan: dict[str, object],
    reason: str,
) -> dict[str, object]:
    """Report actual local progress without hiding partially applied mutation."""
    results: dict[str, dict[str, object]] = {}
    changed = False
    zero_head = "0" * 40
    zero_digest = "0" * 64
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
                "commit_status": "not_started",
                "commit_hashes": [],
                "pre_local_head": before.get("local_head", zero_head),
                "local_head": before.get("local_head", zero_head),
                "pre_dirty_digest": before.get("dirty_digest", zero_digest),
                "post_dirty_digest": before.get("dirty_digest", zero_digest),
                "clean": not before.get("dirty_lines", []),
            }
        if state["commit_hashes"]:
            state["commit_status"] = "failed"
        changed = changed or state["local_head"] != state["pre_local_head"] or (
            state["post_dirty_digest"] != state["pre_dirty_digest"]
        )
        results[key] = state
    return {
        "outcome": "partial_publication" if changed else "blocked",
        "phase": "local_commit",
        "daily_pipeline_status": collection.get("daily_pipeline_status", "blocked"),
        "summary_path": plan.get("summary_target"),
        "advisory_path": plan.get("advisory_target"),
        "notification_result": collection.get("notification_result"),
        "agents_vault": results["agents_vault"],
        "user_vault": results["user_vault"],
        "evidence_finalization_commit": None,
        "next_action": reason,
    }


def main(argv: list[str]) -> int:
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
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        collection = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        context_path = Path(argv[5])
        review_path = Path(argv[6])
        review_bytes = stable_regular_bytes(review_path)
        if hashlib.sha256(review_bytes).hexdigest() != argv[9]:
            raise CommitError("publication review changed after validation")
        review = json.loads(review_bytes)
        if review.get("outcome") != "approved" or review.get(
            "publication_context_sha256"
        ) != hashlib.sha256(context_path.read_bytes()).hexdigest():
            raise CommitError("publication review is not approved and context-bound")
        capture_exact(argv[8], argv[1], pre)
        installed = json.loads(
            subprocess.run(
                [argv[7], argv[1], argv[3], argv[4]],
                cwd="/",
                check=True,
                capture_output=True,
                text=True,
                env=clean_environment(),
            ).stdout
        )
        if installed != {
            "summary_target": plan["summary_target"],
            "advisory_target": plan["advisory_target"],
        }:
            raise CommitError("installed artifacts differ from the approved plan")
        validate_final_worktree(
            str(runtime["agents_vault_root"]),
            str(runtime["agents_git_dir"]),
            review["agents_vault"],
            str(
                Path(plan["advisory_target"]).relative_to(
                    runtime["agents_vault_root"]
                )
            ),
            str(collection["advisory_sha256"]),
        )
        validate_final_worktree(
            str(runtime["user_vault_root"]),
            str(runtime["user_git_dir"]),
            review["user_vault"],
            str(
                Path(plan["summary_target"]).relative_to(
                    runtime["user_vault_root"]
                )
            ),
            str(collection["summary_sha256"]),
        )
        agents = commit_groups(
            str(runtime["agents_vault_root"]),
            str(runtime["agents_git_dir"]),
            str(runtime["gitleaks_bin"]),
            pre["agents_vault"],
            review["agents_vault"],
            str(
                Path(plan["advisory_target"]).relative_to(
                    runtime["agents_vault_root"]
                )
            ),
            str(collection["advisory_sha256"]),
            output.parent,
        )
        user = commit_groups(
            str(runtime["user_vault_root"]),
            str(runtime["user_git_dir"]),
            str(runtime["gitleaks_bin"]),
            pre["user_vault"],
            review["user_vault"],
            str(
                Path(plan["summary_target"]).relative_to(
                    runtime["user_vault_root"]
                )
            ),
            str(collection["summary_sha256"]),
            output.parent,
        )
        if agents["post_dirty_digest"] != CLEAN_DIGEST or user[
            "post_dirty_digest"
        ] != CLEAN_DIGEST:
            raise CommitError("local publication did not finish cleanly")
        result = {
            "outcome": "ready_to_push",
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": installed["summary_target"],
            "advisory_path": installed["advisory_target"],
            "notification_result": collection.get("notification_result"),
            "agents_vault": agents,
            "user_vault": user,
            "evidence_finalization_commit": None,
            "next_action": None,
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
        result = result_after_failure(
            runtime, pre, collection, plan, f"Local publication failed closed: {exc}"
        )
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 75
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
