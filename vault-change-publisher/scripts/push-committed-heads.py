#!/usr/bin/env python3
"""Validate local publication commits, then push only fixed main refs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


class PushError(RuntimeError):
    """Represent invalid local publication state or a rejected push."""


def git(
    repo: str,
    *arguments: str,
    check: bool = True,
    git_dir: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git without a shell and capture sanitized text output."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    repository_arguments = (
        [f"--git-dir={git_dir}", f"--work-tree={repo}"]
        if git_dir is not None
        else ["-C", repo]
    )
    return subprocess.run(
        ["git", *repository_arguments, "-c", f"core.hooksPath={os.devnull}", *arguments],
        check=check,
        capture_output=True,
        text=True,
        env=environment,
    )


def dirty_digest(repo: str) -> str:
    """Hash the complete porcelain status used by the publication contract."""
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    return hashlib.sha256(status.encode("utf-8")).hexdigest()


def current_local(repo: str, pre_state: dict[str, object]) -> dict[str, object]:
    """Capture local progress relative to the immutable pre-collection state."""
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    digest = dirty_digest(repo)
    commits = git(
        repo,
        "rev-list",
        "--reverse",
        f"{pre_state['local_head']}..{head}",
    ).stdout.splitlines()
    clean = digest == hashlib.sha256(b"").hexdigest()
    return {
        "commit_status": "complete" if commits else "not_started",
        "commit_hashes": commits,
        "pre_local_head": pre_state["local_head"],
        "local_head": head,
        "pre_dirty_digest": pre_state["dirty_digest"],
        "post_dirty_digest": digest,
        "clean": clean,
    }


def git_control_digest(repo: str) -> str:
    """Hash repository config and hooks that could redirect a later push."""
    git_dir = Path(
        git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()
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
    control.update(b"config\0")
    control.update((git_dir / "config").read_bytes())
    hooks = git_dir / "hooks"
    if hooks.exists():
        for root, directories, files in os.walk(hooks, followlinks=False):
            directories.sort()
            files.sort()
            for filename in files:
                path = Path(root) / filename
                control.update(str(path.relative_to(git_dir)).encode("utf-8"))
                control.update(b"\0")
                control.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                control.update(b"\0")
                if path.is_symlink():
                    control.update(b"symlink\0")
                    control.update(os.readlink(path).encode("utf-8"))
                else:
                    control.update(path.read_bytes())
                control.update(b"\0")
    return control.hexdigest()


def changed_paths(repo: str, old: str, new: str) -> list[str]:
    """Return all repo-relative paths changed across one commit range."""
    output = git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        old,
        new,
    ).stdout
    return sorted(value for value in output.split("\0") if value)


def commit_paths(repo: str, commit: str) -> list[str]:
    """Return repo-relative paths changed by one commit."""
    output = git(
        repo,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--name-only",
        "--no-renames",
        "-r",
        "-z",
        commit,
    ).stdout
    return sorted(value for value in output.split("\0") if value)


def blob_sha256(repo: str, head: str, relative: str) -> str:
    """Hash one committed blob without checking out or following a symlink."""
    result = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "-c",
            f"core.hooksPath={os.devnull}",
            "show",
            f"{head}:{relative}",
        ],
        check=True,
        capture_output=True,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("GIT_")
            },
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    )
    return hashlib.sha256(result.stdout).hexdigest()


def validate_blob_mode(repo: str, head: str, relative: str) -> None:
    """Require a normal non-executable Git blob for an installed Markdown artifact."""
    line = git(repo, "ls-tree", head, "--", relative).stdout.strip()
    metadata, separator, listed_path = line.partition("\t")
    fields = metadata.split()
    if (
        not separator
        or listed_path != relative
        or len(fields) != 3
        or fields[0] != "100644"
        or fields[1] != "blob"
    ):
        raise PushError("committed artifact is not a regular 100644 blob")


def validate_dirty_entry(
    repo: str, head: str, entry: dict[str, object]
) -> None:
    """Bind an approved pre-existing dirty path to its reviewed Git blob and mode."""
    line = git(repo, "ls-tree", head, "--", str(entry["path"])).stdout.strip()
    if entry["git_blob_oid"] is None:
        if line:
            raise PushError("approved deletion still exists in the committed tree")
        return
    metadata, separator, listed_path = line.partition("\t")
    fields = metadata.split()
    if (
        not separator
        or listed_path != entry["path"]
        or len(fields) != 3
        or fields[0] != entry["mode"]
        or fields[1] != "blob"
        or fields[2] != entry["git_blob_oid"]
    ):
        raise PushError("committed dirty path differs from approved blob or mode")


def validate_scope(
    repo: str,
    pre_state: dict[str, object],
    reported: dict[str, object],
    manifest: dict[str, object],
) -> None:
    """Bind actual commits and artifact blobs to an approved review manifest."""
    approved_groups = manifest["commit_groups"]
    approved_paths = sorted(
        path for group in approved_groups for path in group["paths"]
    )
    actual_paths = changed_paths(
        repo, str(pre_state["local_head"]), str(reported["local_head"])
    )
    if actual_paths != approved_paths:
        raise PushError("committed paths differ from approved initial scope")
    if any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in actual_paths
    ):
        raise PushError("publication commit contains a forbidden .obsidian path")
    for entry in manifest["approved_dirty_entries"]:
        validate_dirty_entry(
            repo, str(reported["local_head"]), entry
        )
    if len(reported["commit_hashes"]) != len(approved_groups):
        raise PushError("commit count differs from approved commit groups")
    for commit, group in zip(reported["commit_hashes"], approved_groups):
        paths = set(commit_paths(repo, commit))
        if (
            paths != set(group["paths"])
            or git(repo, "show", "-s", "--format=%s", commit).stdout.strip()
            != group["message"]
        ):
            raise PushError("commit does not exactly match its ordered approved group")
    for artifact in manifest["reviewed_artifacts"]:
        validate_blob_mode(
            repo,
            str(reported["local_head"]),
            str(artifact["target_path"]),
        )
        if (
            blob_sha256(
                repo,
                str(reported["local_head"]),
                str(artifact["target_path"]),
            )
            != artifact["source_sha256"]
        ):
            raise PushError("committed artifact blob hash differs from review")


def scan_commits(gitleaks_bin: str, repo: str, old: str, new: str) -> None:
    """Scan the exact candidate commit range with pinned gitleaks."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GITLEAKS_") and not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    result = subprocess.run(
        [
            gitleaks_bin,
            "--no-banner",
            "--redact",
            "--ignore-gitleaks-allow",
            "--gitleaks-ignore-path",
            os.devnull,
            "git",
            "--log-opts",
            f"{old}..{new}",
            repo,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        raise PushError("gitleaks rejected the candidate commit range")


def validate_local(
    repo: str,
    pre_state: dict[str, object],
    reported: dict[str, object],
) -> str:
    """Bind a reported commit sequence to the actual clean main checkout."""
    branch = git(repo, "branch", "--show-current").stdout.strip()
    upstream = git(repo, "rev-parse", "--abbrev-ref", "@{u}").stdout.strip()
    local_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if branch != "main" or upstream != "origin/main":
        raise PushError("publication checkout is not main tracking origin/main")
    if local_head != reported["local_head"]:
        raise PushError("reported local head does not match repository")
    if reported["pre_local_head"] != pre_state["local_head"]:
        raise PushError("reported pre-publication head does not match captured state")
    if reported["pre_dirty_digest"] != pre_state["dirty_digest"]:
        raise PushError("reported pre-publication dirty digest does not match")
    actual_dirty = dirty_digest(repo)
    if actual_dirty != reported["post_dirty_digest"] or actual_dirty != hashlib.sha256(b"").hexdigest():
        raise PushError("publication checkout is not clean")
    commits = git(
        repo,
        "rev-list",
        "--reverse",
        f"{pre_state['local_head']}..{local_head}",
    ).stdout.splitlines()
    if commits != reported["commit_hashes"]:
        raise PushError("reported commit sequence does not match local history")
    status = reported["commit_status"]
    if status == "complete" and not commits:
        raise PushError("complete commit status has no commits")
    if status == "not_required" and commits:
        raise PushError("not_required commit status changed history")
    return local_head


def remote_head(repo: str, remote_url: str, git_dir: str | None = None) -> str:
    """Read the remote main object ID without trusting a stale local ref."""
    output = git(
        repo,
        "ls-remote",
        "--exit-code",
        remote_url,
        "refs/heads/main",
        git_dir=git_dir,
    ).stdout
    fields = output.split()
    if len(fields) != 2:
        raise PushError("could not resolve remote main")
    return fields[0]


def push_one(
    repo: str,
    remote_url: str,
    local_head: str,
    required: bool,
    before: str,
    git_dir: str | None = None,
) -> tuple[str, str]:
    """Push exactly one validated object ID to refs/heads/main."""
    if before == local_head:
        return ("not_required", before)
    if not required:
        raise PushError("not_required publication unexpectedly needs a push")
    result = git(
        repo,
        "push",
        remote_url,
        f"{local_head}:refs/heads/main",
        check=False,
        git_dir=git_dir,
    )
    if result.returncode != 0:
        return ("failed", before)
    try:
        after = remote_head(repo, remote_url, git_dir)
    except (PushError, subprocess.SubprocessError):
        return ("failed", before)
    if after != local_head:
        return ("failed", after)
    return ("complete", after)


def final_vault(
    reported: dict[str, object],
    push_status: str,
    remote: str,
) -> dict[str, object]:
    """Convert one local commit result into the final publication shape."""
    return {
        "commit_status": reported["commit_status"],
        "commit_hashes": reported["commit_hashes"],
        "push_status": push_status,
        "local_head": reported["local_head"],
        "remote_head": remote,
        "clean": reported["clean"],
    }


def main(argv: list[str]) -> int:
    """Validate a ready result, perform fixed pushes, and emit final JSON."""
    if len(argv) != 9:
        print(
            "usage: push-committed-heads.py RUNTIME PRE_STATE COMMIT_RESULT "
            "RESULT PROCESS_STATUS CONTEXT REVIEW PLAN",
            file=sys.stderr,
        )
        return 64
    output_path = Path(argv[4])
    runtime: dict[str, object] = {}
    pre: dict[str, object] = {}
    committed: dict[str, object] = {}
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        context_path = Path(argv[6])
        context = json.loads(context_path.read_text(encoding="utf-8"))
        review = json.loads(Path(argv[7]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[8]).read_text(encoding="utf-8"))
        if review.get("publication_context_sha256") != hashlib.sha256(
            context_path.read_bytes()
        ).hexdigest():
            raise PushError("approved review is not bound to publication context")
        process_status = int(argv[5])
        try:
            committed = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            committed = {}
        actual_agents = current_local(runtime["agents_vault_root"], pre["agents_vault"])
        actual_user = current_local(runtime["user_vault_root"], pre["user_vault"])
        if process_status != 0 or committed.get("outcome") != "ready_to_push":
            unchanged = all(
                actual["local_head"] == before["local_head"]
                and actual["post_dirty_digest"] == before["dirty_digest"]
                for actual, before in (
                    (actual_agents, pre["agents_vault"]),
                    (actual_user, pre["user_vault"]),
                )
            )
            requested_outcome = committed.get("outcome")
            if requested_outcome == "blocked" and not unchanged:
                requested_outcome = "partial_publication"
            outcome = "blocked" if unchanged else "partial_publication"
            if requested_outcome == "partial_publication":
                outcome = "partial_publication"
            agents_remote = remote_head(
                runtime["agents_vault_root"],
                runtime["agents_remote_url"],
                runtime["agents_git_dir"],
            )
            user_remote = remote_head(
                runtime["user_vault_root"],
                runtime["user_remote_url"],
                runtime["user_git_dir"],
            )
            result = {
                "outcome": outcome,
                "phase": "local_commit",
                "daily_pipeline_status": committed.get(
                    "daily_pipeline_status", "blocked"
                ),
                "summary_path": committed.get("summary_path"),
                "advisory_path": committed.get("advisory_path"),
                "notification_result": committed.get("notification_result"),
                "agents_vault": final_vault(
                    actual_agents, "not_started", agents_remote
                ),
                "user_vault": final_vault(actual_user, "not_started", user_remote),
                "evidence_finalization_commit": None,
                "next_action": committed.get("next_action")
                or "Inspect the local publication failure; do not force or rewrite history.",
            }
            output_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
            return 75
        agents_head = validate_local(
            runtime["agents_vault_root"],
            pre["agents_vault"],
            committed["agents_vault"],
        )
        user_head = validate_local(
            runtime["user_vault_root"],
            pre["user_vault"],
            committed["user_vault"],
        )
        if git_control_digest(runtime["agents_vault_root"]) != pre["agents_vault"][
            "git_control_sha256"
        ] or git_control_digest(runtime["user_vault_root"]) != pre["user_vault"][
            "git_control_sha256"
        ]:
            raise PushError("Git config or hooks changed during local publication")
        validate_scope(
            runtime["agents_vault_root"],
            pre["agents_vault"],
            committed["agents_vault"],
            review["agents_vault"],
        )
        validate_scope(
            runtime["user_vault_root"],
            pre["user_vault"],
            committed["user_vault"],
            review["user_vault"],
        )
        if committed.get("evidence_finalization_commit") is not None:
            raise PushError("evidence must be finalized after the first fixed pushes")
        if (
            committed.get("summary_path") != plan["summary_target"]
            or committed.get("advisory_path") != plan["advisory_target"]
        ):
            raise PushError("reported artifact paths differ from the approved plan")
        scan_commits(
            runtime["gitleaks_bin"],
            runtime["agents_vault_root"],
            pre["agents_vault"]["local_head"],
            agents_head,
        )
        scan_commits(
            runtime["gitleaks_bin"],
            runtime["user_vault_root"],
            pre["user_vault"]["local_head"],
            user_head,
        )
        if committed.get("daily_pipeline_status") != "complete":
            raise PushError("ready publication does not mark the pipeline complete")
        agents_before = remote_head(
            runtime["agents_vault_root"],
            runtime["agents_remote_url"],
            runtime["agents_git_dir"],
        )
        user_before = remote_head(
            runtime["user_vault_root"],
            runtime["user_remote_url"],
            runtime["user_git_dir"],
        )
        agents_push, agents_remote = push_one(
            runtime["agents_vault_root"],
            runtime["agents_remote_url"],
            agents_head,
            committed["agents_vault"]["commit_status"] == "complete",
            agents_before,
            runtime["agents_git_dir"],
        )
        user_push, user_remote = push_one(
            runtime["user_vault_root"],
            runtime["user_remote_url"],
            user_head,
            committed["user_vault"]["commit_status"] == "complete",
            user_before,
            runtime["user_git_dir"],
        )
        success = (
            agents_push in {"complete", "not_required"}
            and user_push in {"complete", "not_required"}
            and agents_remote == agents_head
            and user_remote == user_head
        )
        result = {
            "outcome": "partial_publication",
            "phase": "initial_fixed_push",
            "daily_pipeline_status": committed["daily_pipeline_status"],
            "summary_path": committed["summary_path"],
            "advisory_path": committed["advisory_path"],
            "notification_result": committed["notification_result"],
            "agents_vault": final_vault(
                committed["agents_vault"], agents_push, agents_remote
            ),
            "user_vault": final_vault(
                committed["user_vault"], user_push, user_remote
            ),
            "evidence_finalization_commit": committed[
                "evidence_finalization_commit"
            ],
            "next_action": (
                "Finalize and review actual push evidence, then push the Agents evidence commit."
                if success
                else "Repair the failed plain main push without force or history rewrite."
            ),
        }
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        PushError,
    ) as exc:
        print(f"fixed push blocked:{exc}", file=sys.stderr)
        if runtime and pre:
            try:
                actual_agents = current_local(
                    str(runtime["agents_vault_root"]), pre["agents_vault"]
                )
                actual_user = current_local(
                    str(runtime["user_vault_root"]), pre["user_vault"]
                )
                agents_remote = remote_head(
                    str(runtime["agents_vault_root"]),
                    str(runtime["agents_remote_url"]),
                    str(runtime["agents_git_dir"]),
                )
                user_remote = remote_head(
                    str(runtime["user_vault_root"]),
                    str(runtime["user_remote_url"]),
                    str(runtime["user_git_dir"]),
                )
                progressed = any(
                    actual["local_head"] != before["local_head"]
                    or actual["post_dirty_digest"] != before["dirty_digest"]
                    for actual, before in (
                        (actual_agents, pre["agents_vault"]),
                        (actual_user, pre["user_vault"]),
                    )
                )
                fallback = {
                    "outcome": (
                        "partial_publication" if progressed else "blocked"
                    ),
                    "phase": "initial_fixed_push_validation",
                    "daily_pipeline_status": committed.get(
                        "daily_pipeline_status", "blocked"
                    ),
                    "summary_path": committed.get("summary_path"),
                    "advisory_path": committed.get("advisory_path"),
                    "notification_result": committed.get("notification_result"),
                    "agents_vault": final_vault(
                        actual_agents,
                        (
                            "complete"
                            if agents_remote == actual_agents["local_head"]
                            and actual_agents["commit_hashes"]
                            else "not_required"
                            if agents_remote == actual_agents["local_head"]
                            else "not_started"
                        ),
                        agents_remote,
                    ),
                    "user_vault": final_vault(
                        actual_user,
                        (
                            "complete"
                            if user_remote == actual_user["local_head"]
                            and actual_user["commit_hashes"]
                            else "not_required"
                            if user_remote == actual_user["local_head"]
                            else "not_started"
                        ),
                        user_remote,
                    ),
                    "evidence_finalization_commit": None,
                    "next_action": (
                        f"Repair fixed-push validation without force: {exc}"
                    ),
                }
                output_path.write_text(
                    json.dumps(fallback, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as capture_exc:
                print(
                    f"could not capture fixed-push partial state:{capture_exc}",
                    file=sys.stderr,
                )
        return 75
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if success else 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
