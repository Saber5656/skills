#!/usr/bin/env python3
"""Validate, commit, and fixed-push the reviewed publication evidence hunk."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from git_diff_digest import git_diff_digest


class FinalizationError(RuntimeError):
    """Represent a failed evidence finalization."""


def clean_environment() -> dict[str, str]:
    """Remove Git/Gitleaks override variables while preserving credentials."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("GITLEAKS_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def git(
    repo: str, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run Git with hooks and ambient configuration disabled."""
    return subprocess.run(
        ["git", "-C", repo, "-c", f"core.hooksPath={os.devnull}", *arguments],
        check=check,
        capture_output=True,
        text=True,
        env=clean_environment(),
    )


def control_digest(repo: str) -> str:
    """Hash local config and hooks before the network-enabled push."""
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    digest = hashlib.sha256()
    digest.update(b"config\0")
    digest.update((git_dir / "config").read_bytes())
    hooks = git_dir / "hooks"
    if hooks.exists():
        for root, directories, files in os.walk(hooks, followlinks=False):
            directories.sort()
            files.sort()
            for filename in files:
                path = Path(root) / filename
                digest.update(str(path.relative_to(git_dir)).encode("utf-8"))
                digest.update(b"\0")
                digest.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                digest.update(b"\0")
                if path.is_symlink():
                    digest.update(b"symlink\0")
                    digest.update(os.readlink(path).encode("utf-8"))
                else:
                    digest.update(path.read_bytes())
                digest.update(b"\0")
    return digest.hexdigest()


def diff_digest(repo: str, relative: str) -> str:
    """Hash the exact unstaged binary evidence diff."""
    return git_diff_digest(repo, relative)


def cached_diff_digest(repo: str, relative: str) -> str:
    """Hash the exact staged binary evidence diff."""
    return git_diff_digest(repo, relative, cached=True)


def dirty_status(repo: str) -> tuple[bool, str]:
    """Return clean state and the contract's porcelain digest."""
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    return (not status, hashlib.sha256(status.encode("utf-8")).hexdigest())


def remote_head(repo: str, remote_url: str) -> str:
    """Resolve the literal remote main URL without using a mutable remote name."""
    result = git(
        repo, "ls-remote", "--exit-code", remote_url, "refs/heads/main"
    ).stdout.split()
    if len(result) != 2:
        raise FinalizationError("could not resolve remote main")
    return result[0]


def scan_staged(gitleaks_bin: str, repo: str) -> None:
    """Run pinned gitleaks against the exact staged evidence hunk."""
    result = subprocess.run(
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
        check=False,
        capture_output=True,
        text=True,
        env=clean_environment(),
    )
    if result.returncode != 0:
        raise FinalizationError("gitleaks rejected staged evidence")


def partial_result(
    runtime: dict[str, str],
    pre: dict[str, object],
    initial: dict[str, object],
    reason: str,
) -> dict[str, object]:
    """Capture actual local/remote evidence state after any failure."""
    repo = runtime["agents_vault_root"]
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    clean, _ = dirty_status(repo)
    commits = git(
        repo,
        "rev-list",
        "--reverse",
        f"{pre['agents_vault']['local_head']}..{head}",
    ).stdout.splitlines()
    try:
        remote = remote_head(repo, runtime["agents_remote_url"])
    except (FinalizationError, subprocess.SubprocessError):
        remote = initial["agents_vault"]["remote_head"]
    agents = dict(initial["agents_vault"])
    agents.update(
        {
            "commit_status": "complete" if commits else "not_started",
            "commit_hashes": commits,
            "push_status": "complete" if remote == head else "failed",
            "local_head": head,
            "remote_head": remote,
            "clean": clean,
        }
    )
    result = dict(initial)
    result.update(
        {
            "outcome": "partial_publication",
            "phase": "evidence_finalization",
            "agents_vault": agents,
            "evidence_finalization_commit": (
                commits[-1]
                if len(commits) > len(initial["agents_vault"]["commit_hashes"])
                else None
            ),
            "next_action": reason,
        }
    )
    return result


def main(argv: list[str]) -> int:
    """Finalize reviewed evidence and emit the final automation result."""
    if len(argv) != 8:
        print(
            "usage: commit-push-publication-evidence.py RUNTIME PRE INITIAL "
            "EVIDENCE_PLAN EVIDENCE_REVIEW FINAL REVIEW_STATUS",
            file=sys.stderr,
        )
        return 64
    output = Path(argv[6])
    runtime: dict[str, str] = {}
    pre: dict[str, object] = {}
    initial: dict[str, object] = {}
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        initial = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        review = json.loads(Path(argv[5]).read_text(encoding="utf-8"))
        if int(argv[7]) != 0 or review != {
            "outcome": "approved",
            "target_path": plan["target_path"],
            "evidence_diff_sha256": plan["evidence_diff_sha256"],
            "review_status": "quality_ok",
            "next_action": None,
        }:
            raise FinalizationError("evidence review is not approved and digest-bound")
        repo = runtime["agents_vault_root"]
        target = plan["target_path"]
        if target == ".obsidian" or target.startswith(".obsidian/"):
            raise FinalizationError("evidence target is forbidden")
        if git(repo, "branch", "--show-current").stdout.strip() != "main":
            raise FinalizationError("Agents Vault is not on main")
        if git(repo, "rev-parse", "HEAD").stdout.strip() != initial[
            "agents_vault"
        ]["local_head"]:
            raise FinalizationError("Agents Vault moved after the initial push")
        if control_digest(repo) != pre["agents_vault"]["git_control_sha256"]:
            raise FinalizationError("Git config or hooks changed")
        changed = [
            value
            for value in git(
                repo, "diff", "--name-only", "--no-renames", "-z", "HEAD"
            ).stdout.split("\0")
            if value
        ]
        if changed != [target] or diff_digest(repo, target) != plan[
            "evidence_diff_sha256"
        ]:
            raise FinalizationError("evidence worktree diff differs from review")
        git(repo, "add", "--", target)
        if cached_diff_digest(repo, target) != plan["evidence_diff_sha256"]:
            raise FinalizationError("staged evidence differs from reviewed diff")
        if git(repo, "diff", "--cached", "--check", check=False).returncode != 0:
            raise FinalizationError("staged evidence failed diff check")
        scan_staged(runtime["gitleaks_bin"], repo)
        message = "docs(task): record daily publication evidence"
        git(repo, "commit", "-m", message)
        evidence_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
        committed_paths = [
            value
            for value in git(
                repo,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "--no-renames",
                "-r",
                "-z",
                evidence_commit,
            ).stdout.split("\0")
            if value
        ]
        if committed_paths != [target]:
            raise FinalizationError("evidence commit contains an unexpected path")
        if control_digest(repo) != pre["agents_vault"]["git_control_sha256"]:
            raise FinalizationError("Git config or hooks changed during finalization")
        result = git(
            repo,
            "push",
            runtime["agents_remote_url"],
            f"{evidence_commit}:refs/heads/main",
            check=False,
        )
        if result.returncode != 0:
            raise FinalizationError("final evidence push failed")
        remote = remote_head(repo, runtime["agents_remote_url"])
        clean, _ = dirty_status(repo)
        if remote != evidence_commit or not clean:
            raise FinalizationError("final evidence state is not clean and published")
        user_repo = runtime["user_vault_root"]
        user_head = git(user_repo, "rev-parse", "HEAD").stdout.strip()
        user_remote = remote_head(user_repo, runtime["user_remote_url"])
        user_clean, _ = dirty_status(user_repo)
        if (
            git(user_repo, "branch", "--show-current").stdout.strip() != "main"
            or user_head != initial["user_vault"]["local_head"]
            or user_remote != initial["user_vault"]["remote_head"]
            or user_remote != user_head
            or not user_clean
            or control_digest(user_repo)
            != pre["user_vault"]["git_control_sha256"]
            or control_digest(repo)
            != pre["agents_vault"]["git_control_sha256"]
        ):
            raise FinalizationError("final two-Vault state changed during evidence work")
        agents = dict(initial["agents_vault"])
        agents.update(
            {
                "commit_hashes": [
                    *initial["agents_vault"]["commit_hashes"],
                    evidence_commit,
                ],
                "push_status": "complete",
                "local_head": evidence_commit,
                "remote_head": remote,
                "clean": True,
            }
        )
        final = dict(initial)
        final.update(
            {
                "outcome": "success",
                "phase": "evidence_finalization",
                "agents_vault": agents,
                "evidence_finalization_commit": evidence_commit,
                "next_action": None,
            }
        )
        output.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
        return 0
    except (
        FinalizationError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"evidence finalization failed:{exc}", file=sys.stderr)
        if runtime and pre and initial:
            try:
                output.write_text(
                    json.dumps(
                        partial_result(
                            runtime,
                            pre,
                            initial,
                            f"Repair evidence finalization without force: {exc}",
                        ),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception as capture_exc:
                print(f"could not capture partial state:{capture_exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
