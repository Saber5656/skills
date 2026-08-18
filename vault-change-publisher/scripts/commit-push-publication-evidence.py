#!/usr/bin/env python3
"""Validate, commit, and fixed-push the reviewed publication evidence hunk."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from evidence_hunk import canonical_patch
from git_diff_digest import git_diff_digest
from isolated_git_transport import TransportError, run_transport


class FinalizationError(RuntimeError):
    """Represent a failed evidence finalization."""


MAX_TASK_BYTES = 10 * 1024 * 1024
SCAN_TIMEOUT_SECONDS = 120


def validated_publisher_identity(runtime: dict[str, str]) -> tuple[str, str]:
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
        raise FinalizationError("publisher Git identity is invalid")
    return name, email


def context_bound_inputs(
    runtime: dict[str, str],
    pre: dict[str, object],
    context_bytes: bytes,
    expected_digest: str,
) -> tuple[dict[str, str], dict[str, object]]:
    """Reject valid-looking runtime substitutions after publication review."""
    if hashlib.sha256(context_bytes).hexdigest() != expected_digest:
        raise FinalizationError("publication context digest mismatch")
    context = json.loads(context_bytes)
    bound_runtime = context.get("runtime")
    bound_pre = context.get("pre_collection_state")
    if runtime != bound_runtime or pre != bound_pre:
        raise FinalizationError("finalization inputs differ from reviewed context")
    return bound_runtime, bound_pre


def clean_environment(
    publisher_identity: tuple[str, str] | None = None,
) -> dict[str, str]:
    """Remove Git/Gitleaks override variables while preserving credentials."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("GITLEAKS_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_CONFIG_COUNT"] = "1"
    environment["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    environment["GIT_CONFIG_VALUE_0"] = "false"
    if publisher_identity is not None:
        name, email = publisher_identity
        environment["GIT_AUTHOR_NAME"] = name
        environment["GIT_AUTHOR_EMAIL"] = email
        environment["GIT_COMMITTER_NAME"] = name
        environment["GIT_COMMITTER_EMAIL"] = email
    return environment


def git(
    repo: str,
    *arguments: str,
    check: bool = True,
    git_dir: str | None = None,
    publisher_identity: tuple[str, str] | None = None,
    index_file: str | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git with hooks and ambient configuration disabled."""
    repository_arguments = (
        [f"--git-dir={git_dir}", f"--work-tree={repo}"]
        if git_dir is not None
        else ["-C", repo]
    )
    environment = clean_environment(publisher_identity)
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    if arguments and arguments[0] in {"ls-remote", "push", "fetch"}:
        if git_dir is None:
            raise FinalizationError(
                "network Git operation requires an explicit Git directory"
            )
        return run_transport(git_dir, *arguments, check=check, text=True)
    return subprocess.run(
        [
            "git", *repository_arguments,
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            "-c", "commit.gpgSign=false",
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
        env=environment,
        input=input_text,
    )


def control_digest(repo: str) -> str:
    """Hash local config and hooks before the network-enabled push."""
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    common_dir = Path(
        git(
            repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
    )
    digest = hashlib.sha256()
    git_marker = Path(repo) / ".git"
    digest.update(b"worktree-git-entry\0")
    digest.update(f"{git_marker.lstat().st_mode:o}".encode("ascii"))
    digest.update(b"\0")
    if git_marker.is_symlink():
        digest.update(b"symlink\0")
        digest.update(os.fsencode(os.readlink(git_marker)))
    elif git_marker.is_file():
        digest.update(b"file\0")
        digest.update(git_marker.read_bytes())
    else:
        digest.update(b"directory\0")
    seen_control_paths: set[Path] = set()
    for config_path in (common_dir / "config", git_dir / "config.worktree"):
        if config_path in seen_control_paths or not os.path.lexists(config_path):
            continue
        seen_control_paths.add(config_path)
        digest.update(b"config\0")
        digest.update(str(config_path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{config_path.lstat().st_mode:o}".encode("ascii"))
        digest.update(b"\0")
        if config_path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.fsencode(os.readlink(config_path)))
        else:
            digest.update(config_path.read_bytes())
        digest.update(b"\0")
    hooks = common_dir / "hooks"
    if hooks.exists():
        for root, directories, files in os.walk(hooks, followlinks=False):
            directories.sort()
            files.sort()
            for filename in files:
                path = Path(root) / filename
                digest.update(str(path.relative_to(common_dir)).encode("utf-8"))
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


def remote_head(repo: str, remote_url: str, git_dir: str | None = None) -> str:
    """Resolve the literal remote main URL without using a mutable remote name."""
    result = git(
        repo,
        "ls-remote",
        "--exit-code",
        remote_url,
        "refs/heads/main",
        git_dir=git_dir,
    ).stdout.split()
    if len(result) != 2:
        raise FinalizationError("could not resolve remote main")
    return result[0]


def scan_staged(gitleaks_bin: str, repo: str, index_file: str | None = None) -> None:
    """Run pinned gitleaks against the exact staged evidence hunk."""
    environment = clean_environment()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    try:
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
            cwd="/",
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=SCAN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise FinalizationError("gitleaks evidence scan exceeded its deadline") from exc
    if result.returncode != 0:
        raise FinalizationError("gitleaks rejected staged evidence")


def capture_complete(runtime_file: str) -> dict[str, object]:
    """Capture both Vaults through the canonical publication state helper."""
    helper = Path(__file__).with_name("capture-vault-state.py")
    completed = subprocess.run(
        [str(helper), "--include-local-history", runtime_file],
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
    )
    return json.loads(completed.stdout)


def validate_residual_after_commit(
    baseline: dict[str, object],
    current: dict[str, object],
    target: str,
    index_blob: str,
    worktree_candidate: bytes,
    repo: str,
) -> None:
    """Prove hunk-level transformation preserved every pre-existing residual."""
    for field in (
        "dirty_lines", "dirty_paths", "staged_paths", "git_control_sha256",
        "branch", "upstream", "operation_in_progress", "remote_head",
    ):
        if current.get(field) != baseline.get(field):
            raise FinalizationError(f"evidence commit changed residual state: {field}")
    before_entries = {
        entry["path"]: entry
        for entry in baseline["dirty_entries"]
        if entry["path"] != target
    }
    after_entries = {
        entry["path"]: entry
        for entry in current["dirty_entries"]
        if entry["path"] != target
    }
    if after_entries != before_entries:
        raise FinalizationError("evidence commit changed non-target dirty bytes")
    before_metadata = {
        entry["path"]: entry
        for entry in baseline["dirty_metadata"]
        if entry["path"] != target
    }
    after_metadata = {
        entry["path"]: entry
        for entry in current["dirty_metadata"]
        if entry["path"] != target
    }
    if after_metadata != before_metadata:
        raise FinalizationError("evidence commit changed non-target metadata")
    before_index = [
        entry for entry in baseline["index_entries"] if entry["path"] != target
    ]
    after_index = [
        entry for entry in current["index_entries"] if entry["path"] != target
    ]
    if after_index != before_index:
        raise FinalizationError("evidence commit changed a non-owned index entry")
    target_index = [
        entry for entry in current["index_entries"] if entry["path"] == target
    ]
    if target_index != [
        {"path": target, "mode": "100644", "git_blob_oid": index_blob, "stage": 0}
    ]:
        raise FinalizationError("evidence index hunk differs from the planned variant")
    if stable_regular_bytes(Path(repo) / target) != worktree_candidate:
        raise FinalizationError("evidence worktree hunk differs from the planned variant")


def stable_regular_bytes(path: Path) -> bytes:
    """Read one stable regular file without following the final component."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FinalizationError("evidence target is not a regular file")
        if before.st_size > MAX_TASK_BYTES:
            raise FinalizationError("evidence input exceeds the allowed size")
        chunks = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.extend(chunk)
            if len(chunks) > MAX_TASK_BYTES:
                raise FinalizationError("evidence input grew beyond the allowed size")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise FinalizationError("evidence target changed while being read")
        return bytes(chunks)
    finally:
        os.close(descriptor)


def git_object_bytes(repo: str, git_dir: str, object_spec: str) -> bytes:
    """Read one Git object through an explicit size gate before allocation."""
    size = git(
        repo, "cat-file", "-s", object_spec, git_dir=git_dir
    ).stdout.strip()
    try:
        object_size = int(size)
    except ValueError as exc:
        raise FinalizationError("evidence Git object size is invalid") from exc
    if object_size < 0 or object_size > MAX_TASK_BYTES:
        raise FinalizationError("evidence Git object exceeds the allowed size")
    process = subprocess.Popen(
        [
            "git", f"--git-dir={git_dir}", f"--work-tree={repo}",
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            "cat-file", "blob", object_spec,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=clean_environment(),
    )
    assert process.stdout is not None
    content = bytearray()
    try:
        while chunk := process.stdout.read(
            min(65536, MAX_TASK_BYTES + 1 - len(content))
        ):
            content.extend(chunk)
            if len(content) > MAX_TASK_BYTES:
                process.kill()
                process.wait()
                raise FinalizationError("evidence Git object exceeds the allowed size")
        return_code = process.wait()
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    if return_code != 0 or len(content) != object_size:
        raise FinalizationError("evidence Git object is unavailable or unstable")
    return bytes(content)


def private_candidate(plan: dict[str, object], prefix: str) -> bytes:
    """Read one sealed run candidate and bind it to its planned digest."""
    content = stable_regular_bytes(Path(str(plan[f"{prefix}_candidate_path"])))
    if hashlib.sha256(content).hexdigest() != plan[f"{prefix}_candidate_sha256"]:
        raise FinalizationError(f"sealed {prefix} evidence candidate changed")
    return content


def write_all(descriptor: int, content: bytes) -> None:
    """Write a complete candidate or fail on zero progress."""
    offset = 0
    while offset < len(content):
        count = os.write(descriptor, content[offset:])
        if count <= 0:
            raise FinalizationError("evidence candidate write made no progress")
        offset += count


def replace_worktree_candidate(
    repo: str, target: str, expected_sha256: str, candidate: bytes
) -> dict[str, object]:
    """Atomically install one reviewed worktree variant with a rollback receipt."""
    path = Path(repo) / target
    original = stable_regular_bytes(path)
    if hashlib.sha256(original).hexdigest() != expected_sha256:
        raise FinalizationError("evidence worktree source changed after review")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise FinalizationError("evidence worktree target is not regular")
    parent = path.parent
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{path.name}.publication-{os.getpid()}.tmp"
    temporary_fd = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IMODE(before.st_mode),
            dir_fd=parent_fd,
        )
        write_all(temporary_fd, candidate)
        os.fsync(temporary_fd)
        current = path.lstat()
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_mode,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) or stable_regular_bytes(path) != original:
            raise FinalizationError("evidence worktree target raced before replacement")
        os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary_name = ""
        os.fsync(parent_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)
    return {
        "repo": repo,
        "target": target,
        "original": original,
        "original_mode": stat.S_IMODE(before.st_mode),
        "original_mtime_ns": before.st_mtime_ns,
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
    }


def rollback_worktree_candidate(receipt: dict[str, object]) -> None:
    """Restore only this run's exact uncommitted evidence candidate."""
    repo = str(receipt["repo"])
    target = str(receipt["target"])
    path = Path(repo) / target
    candidate = stable_regular_bytes(path)
    if hashlib.sha256(candidate).hexdigest() != receipt["candidate_sha256"]:
        raise FinalizationError("evidence rollback refused after target change")
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{path.name}.rollback-{os.getpid()}.tmp"
    temporary_fd = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            int(receipt["original_mode"]),
            dir_fd=parent_fd,
        )
        write_all(temporary_fd, bytes(receipt["original"]))
        os.fsync(temporary_fd)
        os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary_name = ""
        os.utime(
            path.name,
            ns=(int(receipt["original_mtime_ns"]), int(receipt["original_mtime_ns"])),
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        os.fsync(parent_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def isolated_evidence_commit(
    runtime: dict[str, str],
    plan: dict[str, object],
    publisher_identity: tuple[str, str],
    directory: Path,
) -> tuple[str, str, str, bytes]:
    """Build, but do not publish, a HEAD-only evidence commit and index variant."""
    repo = runtime["agents_vault_root"]
    git_dir = runtime["agents_git_dir"]
    target = str(plan["target_path"])
    expected_head = str(plan["base_head"])
    if git(repo, "rev-parse", "HEAD", git_dir=git_dir).stdout.strip() != expected_head:
        raise FinalizationError("Agents HEAD changed after evidence planning")
    head_candidate = private_candidate(plan, "head")
    index_candidate = private_candidate(plan, "index")
    review_patch = stable_regular_bytes(Path(str(plan["review_patch_path"])))
    if hashlib.sha256(review_patch).hexdigest() != plan["evidence_diff_sha256"]:
        raise FinalizationError("sealed evidence review patch changed")
    current_head_blob = git_object_bytes(
        repo, git_dir, f"{expected_head}:{target}"
    )
    if hashlib.sha256(current_head_blob).hexdigest() != plan["head_source_sha256"]:
        raise FinalizationError("evidence HEAD source differs from review")
    if canonical_patch(target, current_head_blob, head_candidate) != review_patch:
        raise FinalizationError("evidence candidate differs from reviewed hunk")
    head_blob = subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={repo}", "hash-object", "-w", "--stdin"],
        input=head_candidate,
        check=True,
        capture_output=True,
        env=clean_environment(),
    ).stdout.decode("ascii").strip()
    index_blob = subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={repo}", "hash-object", "-w", "--stdin"],
        input=index_candidate,
        check=True,
        capture_output=True,
        env=clean_environment(),
    ).stdout.decode("ascii").strip()
    descriptor, index_path = tempfile.mkstemp(prefix="evidence-index-", dir=str(directory))
    os.close(descriptor)
    os.unlink(index_path)
    try:
        git(repo, "read-tree", expected_head, git_dir=git_dir, index_file=index_path)
        git(
            repo, "update-index", "--add", "--cacheinfo", f"100644,{head_blob},{target}",
            git_dir=git_dir, index_file=index_path,
        )
        if git(
            repo, "diff", "--cached", "--check", git_dir=git_dir,
            index_file=index_path, check=False,
        ).returncode != 0:
            raise FinalizationError("evidence-only index failed diff check")
        scan_staged(runtime["gitleaks_bin"], repo, index_path)
        tree = git(repo, "write-tree", git_dir=git_dir, index_file=index_path).stdout.strip()
        message = "docs(task): record daily publication evidence"
        commit = git(
            repo, "commit-tree", tree, "-p", expected_head, git_dir=git_dir,
            publisher_identity=publisher_identity, input_text=message + "\n",
        ).stdout.strip()
    finally:
        try:
            os.unlink(index_path)
        except FileNotFoundError:
            pass
    if git(
        repo, "show", "-s", "--format=%P", commit, git_dir=git_dir
    ).stdout.strip() != expected_head:
        raise FinalizationError("evidence commit parent differs from expected HEAD")
    paths = [
        value for value in git(
            repo, "diff", "--name-only", "--no-renames", "-z",
            expected_head, commit, git_dir=git_dir,
        ).stdout.split("\0") if value
    ]
    if paths != [target]:
        raise FinalizationError("evidence commit contains an unexpected path")
    return commit, head_blob, index_blob, index_candidate


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
    finalization_commits = git(
        repo,
        "rev-list",
        "--reverse",
        f"{initial['agents_vault']['local_head']}..{head}",
    ).stdout.splitlines()
    try:
        remote = remote_head(
            repo, runtime["agents_remote_url"], runtime["agents_git_dir"]
        )
    except (FinalizationError, subprocess.SubprocessError):
        remote = initial["agents_vault"]["remote_head"]
    agents = dict(initial["agents_vault"])
    agents.update(
        {
            "commit_status": (
                "complete"
                if initial["agents_vault"]["commit_hashes"] or finalization_commits
                else "not_started"
            ),
            "commit_hashes": [
                *initial["agents_vault"]["commit_hashes"],
                *finalization_commits,
            ],
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
                finalization_commits[-1]
                if finalization_commits
                else None
            ),
            "next_action": reason,
        }
    )
    return result


def main(argv: list[str]) -> int:
    """Finalize reviewed evidence and emit the final automation result."""
    if len(argv) != 9:
        print(
            "usage: commit-push-publication-evidence.py RUNTIME PRE INITIAL "
            "EVIDENCE_PLAN EVIDENCE_REVIEW FINAL REVIEW_STATUS CONTEXT",
            file=sys.stderr,
        )
        return 64
    output = Path(argv[6])
    runtime: dict[str, str] = {}
    pre: dict[str, object] = {}
    initial: dict[str, object] = {}
    worktree_receipt: dict[str, object] | None = None
    head_updated = False
    try:
        runtime = json.loads(stable_regular_bytes(Path(argv[1])))
        pre = json.loads(stable_regular_bytes(Path(argv[2])))
        initial = json.loads(stable_regular_bytes(Path(argv[3])))
        plan = json.loads(stable_regular_bytes(Path(argv[4])))
        runtime, pre = context_bound_inputs(
            runtime,
            pre,
            stable_regular_bytes(Path(argv[8])),
            plan["publication_context_sha256"],
        )
        publisher_identity = validated_publisher_identity(runtime)
        review = json.loads(stable_regular_bytes(Path(argv[5])))
        if int(argv[7]) != 0 or review != {
            "outcome": "approved",
            "target_path": plan["target_path"],
            "evidence_diff_sha256": plan["evidence_diff_sha256"],
            "publication_context_sha256": plan["publication_context_sha256"],
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
        baseline = plan["pre_evidence_state"]
        prepared_state = capture_complete(argv[1])
        if prepared_state != baseline:
            raise FinalizationError("Vault state changed after evidence planning")
        evidence_commit, _head_blob, index_blob, index_candidate = (
            isolated_evidence_commit(runtime, plan, publisher_identity, output.parent)
        )
        # Candidate construction mutates only the object database. Rebind both
        # Vaults immediately before the first worktree/ref/index mutation.
        if capture_complete(argv[1]) != baseline:
            raise FinalizationError("Vault state changed before evidence commit")
        worktree_candidate = private_candidate(plan, "worktree")
        worktree_receipt = replace_worktree_candidate(
            repo,
            target,
            str(plan["worktree_source_sha256"]),
            worktree_candidate,
        )
        git(
            repo,
            "update-ref",
            "HEAD",
            evidence_commit,
            str(plan["base_head"]),
            git_dir=runtime["agents_git_dir"],
        )
        head_updated = True
        git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{index_blob},{target}",
            git_dir=runtime["agents_git_dir"],
        )
        worktree_receipt = None
        committed_state = capture_complete(argv[1])
        if committed_state["user_vault"] != baseline["user_vault"]:
            raise FinalizationError("User Vault changed during evidence commit")
        validate_residual_after_commit(
            baseline["agents_vault"],
            committed_state["agents_vault"],
            target,
            index_blob,
            worktree_candidate,
            repo,
        )
        if committed_state["agents_vault"]["local_head"] != evidence_commit:
            raise FinalizationError("evidence commit HEAD mismatch")
        if control_digest(repo) != pre["agents_vault"]["git_control_sha256"]:
            raise FinalizationError("Git config or hooks changed during finalization")
        before_remote = remote_head(
            repo, runtime["agents_remote_url"], runtime["agents_git_dir"]
        )
        if before_remote != initial["agents_vault"]["remote_head"]:
            raise FinalizationError("remote main raced before evidence push")
        pushed = False
        for _ in range(3):
            result = git(
                repo, "push", runtime["agents_remote_url"],
                f"{evidence_commit}:refs/heads/main", check=False,
                git_dir=runtime["agents_git_dir"],
            )
            remote = remote_head(
                repo, runtime["agents_remote_url"], runtime["agents_git_dir"]
            )
            if remote == evidence_commit:
                pushed = True
                break
            if remote != before_remote:
                break
        if not pushed:
            raise FinalizationError("final evidence push failed")
        remote = remote_head(
            repo, runtime["agents_remote_url"], runtime["agents_git_dir"]
        )
        clean, _ = dirty_status(repo)
        if remote != evidence_commit:
            raise FinalizationError("final evidence state is not published")
        user_repo = runtime["user_vault_root"]
        user_head = git(user_repo, "rev-parse", "HEAD").stdout.strip()
        user_remote = remote_head(
            user_repo, runtime["user_remote_url"], runtime["user_git_dir"]
        )
        user_clean, _ = dirty_status(user_repo)
        if (
            git(user_repo, "branch", "--show-current").stdout.strip() != "main"
            or user_head != initial["user_vault"]["local_head"]
            or user_remote != initial["user_vault"]["remote_head"]
            or user_remote != user_head
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
                "clean": clean,
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
        TransportError,
    ) as exc:
        if worktree_receipt is not None and not head_updated:
            try:
                rollback_worktree_candidate(worktree_receipt)
            except Exception as rollback_exc:
                exc = FinalizationError(
                    f"{exc}; evidence worktree rollback failed: {rollback_exc}"
                )
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
