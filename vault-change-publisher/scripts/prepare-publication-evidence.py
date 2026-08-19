#!/usr/bin/env python3
"""Insert deterministic, personal-path-free evidence after initial pushes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from evidence_hunk import canonical_patch, insert_evidence_block
from isolated_git_transport import (
    LOCAL_COMMAND_TIMEOUT_SECONDS,
    kill_process_group,
    run_local_command,
)


class EvidenceError(RuntimeError):
    """Represent an unsafe or incomplete evidence preparation."""


def approved_context_digest(review: dict[str, object], context_bytes: bytes) -> str:
    """Keep evidence bound to the context approved before the initial push."""
    digest = hashlib.sha256(context_bytes).hexdigest()
    if digest != review.get("publication_context_sha256"):
        raise EvidenceError("publication context changed after approval")
    return digest


def read_regular_nofollow(path: Path) -> bytes:
    """Read stable bytes from one regular file without following its final link."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("input is not a regular file")
        if before.st_size > MAX_TASK_BYTES:
            raise EvidenceError("input exceeds the allowed size")
        content = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            content.extend(chunk)
            if len(content) > MAX_TASK_BYTES:
                raise EvidenceError("input grew beyond the allowed size")
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
            raise EvidenceError("input changed while being read")
        return bytes(content)
    finally:
        os.close(descriptor)


def relative_path(root: str, absolute: str) -> str:
    """Convert an installed artifact path to a normalized repo-relative path."""
    try:
        relative = Path(absolute).resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise EvidenceError("published artifact path escapes its Vault") from exc
    if not relative.parts or ".." in relative.parts:
        raise EvidenceError("invalid published artifact path")
    return str(relative)


def capture_complete(runtime_file: str) -> dict[str, object]:
    """Capture the exact two-Vault baseline before evidence mutation."""
    helper = Path(__file__).with_name("capture-vault-state.py")
    completed = run_local_command(
        [str(helper), "--include-local-history", runtime_file],
        check=True,
        capture_output=True,
        text=True,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        },
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return json.loads(completed.stdout)


EVIDENCE_HEADING = "### Vault Publication Evidence"
SECTION_BOUNDARY = re.compile(r"^ {0,3}#{1,3}(?:[ \t]+|$)")
FENCE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")
MAX_TASK_BYTES = 10 * 1024 * 1024


def clean_git_environment() -> dict[str, str]:
    """Disable ambient Git controls and command-capable fsmonitor config."""
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
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
        }
    )
    return environment


def git_bytes(repo: str, git_dir: str, *arguments: str) -> bytes:
    """Read size- and time-bounded local Git data without ambient controls."""
    process = subprocess.Popen(
        [
            "git", f"--git-dir={git_dir}", f"--work-tree={repo}",
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            *arguments,
        ],
        cwd="/",
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
                raise EvidenceError("evidence Git object read exceeded its deadline")
            chunk = os.read(
                process.stdout.fileno(),
                min(65536, MAX_TASK_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_TASK_BYTES:
                raise EvidenceError("evidence Git object exceeds the allowed size")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceError("evidence Git object command exceeded its deadline")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise EvidenceError("evidence Git object command exceeded its deadline") from exc
        finished = True
    finally:
        selector.close()
        process.stdout.close()
        if not finished:
            try:
                kill_process_group(process)
            except subprocess.SubprocessError as exc:
                raise EvidenceError("evidence Git process could not be reaped") from exc
    if return_code != 0:
        raise EvidenceError("evidence Git object is unavailable")
    return bytes(content)


def target_entries(
    runtime: dict[str, object], baseline: dict[str, object], target: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Return exact regular HEAD/index entries for the standing task."""
    repo = str(runtime["agents_vault_root"])
    git_dir = str(runtime["agents_git_dir"])
    raw = git_bytes(repo, git_dir, "ls-tree", "-z", "HEAD", "--", target)
    if not raw.endswith(b"\0") or raw.count(b"\0") != 1:
        raise EvidenceError("evidence target HEAD entry is missing or ambiguous")
    metadata, separator, path = raw[:-1].partition(b"\t")
    fields = metadata.split()
    if separator != b"\t" or len(fields) != 3 or path.decode("utf-8") != target:
        raise EvidenceError("evidence target is missing from HEAD")
    head = {
        "mode": fields[0].decode("ascii"),
        "type": fields[1].decode("ascii"),
        "git_blob_oid": fields[2].decode("ascii"),
    }
    if head["mode"] != "100644" or head["type"] != "blob":
        raise EvidenceError("evidence target HEAD entry is not a regular Markdown blob")
    index_matches = [
        entry
        for entry in baseline["agents_vault"]["index_entries"]
        if entry.get("path") == target
    ]
    if len(index_matches) != 1 or index_matches[0].get("stage") != 0:
        raise EvidenceError("evidence target index entry is missing or unmerged")
    index = {
        "mode": str(index_matches[0]["mode"]),
        "git_blob_oid": str(index_matches[0]["git_blob_oid"]),
    }
    if index["mode"] != "100644":
        raise EvidenceError("evidence target index mode is unsupported")
    return head, index


def read_blob(repo: str, git_dir: str, oid: str) -> bytes:
    """Read one exact bounded Git blob."""
    object_type = git_bytes(repo, git_dir, "cat-file", "-t", oid).strip()
    if object_type != b"blob":
        raise EvidenceError("evidence source object is not a blob")
    return git_bytes(repo, git_dir, "cat-file", "blob", oid)


def write_private_exclusive(path: Path, content: bytes) -> None:
    """Create one immutable run-owned review input."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return identity fields that expose replacement or content mutation."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def markdown_evidence_boundary(lines: list[str]) -> int:
    """Find the canonical section boundary while ignoring fenced content."""
    headings = []
    fence_character = None
    fence_length = 0
    fence_start_index = None
    for index, line in enumerate(lines):
        text = line.rstrip("\r\n")
        if fence_character is not None:
            closing = re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$",
                text,
            )
            if closing:
                fence_character = None
                fence_length = 0
                fence_start_index = None
            continue
        opening = FENCE_START.match(text)
        if opening:
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
            fence_start_index = index
            continue
        if text == EVIDENCE_HEADING:
            headings.append(index)
    if fence_character is not None:
        location = (
            "in evidence section"
            if headings and fence_start_index is not None and fence_start_index > headings[0]
            else "before evidence heading"
        )
        raise EvidenceError(f"unterminated fenced block {location}")
    if len(headings) != 1:
        raise EvidenceError("canonical evidence heading is missing or duplicated")

    fence_character = None
    fence_length = 0
    for index in range(headings[0] + 1, len(lines)):
        text = lines[index].rstrip("\r\n")
        if fence_character is not None:
            closing = re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$",
                text,
            )
            if closing:
                fence_character = None
                fence_length = 0
            continue
        opening = FENCE_START.match(text)
        if opening:
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
            continue
        if SECTION_BOUNDARY.match(text):
            return index
    if fence_character is not None:
        raise EvidenceError("unterminated fenced block in evidence section")
    return len(lines)


def read_bounded_descriptor(descriptor: int) -> bytes:
    """Read at most the standing-task limit from the descriptor's start."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    content = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_TASK_BYTES:
            raise EvidenceError("evidence target grew beyond the allowed size")
    return bytes(content)


def write_all(descriptor: int, content: bytes) -> None:
    """Write all bytes or fail instead of looping on zero progress."""
    written = 0
    while written < len(content):
        count = os.write(descriptor, content[written:])
        if count <= 0:
            raise EvidenceError("evidence temporary write made no progress")
        written += count


def insert_under_evidence_section_no_follow(
    root: Path, relative: PurePosixPath, content: bytes
) -> None:
    """Insert one block inside the canonical evidence section and fsync."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    opened = []
    directory_fd = os.open(root, flags)
    opened.append(directory_fd)
    try:
        for component in relative.parts[:-1]:
            directory_fd = os.open(component, flags, dir_fd=directory_fd)
            opened.append(directory_fd)
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        file_fd = os.open(relative.name, file_flags, dir_fd=directory_fd)
        opened.append(file_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise EvidenceError("evidence target is not a regular file")
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("evidence target is not a regular file")
        if before.st_size <= 0 or before.st_size > MAX_TASK_BYTES:
            raise EvidenceError("evidence target size is outside the allowed range")
        existing = read_bounded_descriptor(file_fd)
        after_read = os.fstat(file_fd)
        if stable_identity(before) != stable_identity(after_read):
            raise EvidenceError("evidence target changed while being read")
        try:
            text = existing.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceError("evidence target is not UTF-8 text") from exc
        marker = next(
            line for line in content.splitlines() if b"vault-change-publisher:" in line
        )
        if marker in existing:
            raise EvidenceError("run evidence marker already exists")
        lines = text.splitlines(keepends=True)
        boundary_index = markdown_evidence_boundary(lines)
        offset = len("".join(lines[:boundary_index]).encode("utf-8"))
        prefix = existing[:offset]
        suffix = existing[offset:]
        separator = b"" if prefix.endswith(b"\n\n") else b"\n"
        updated = prefix + separator + content.lstrip(b"\n") + suffix
        if len(updated) > MAX_TASK_BYTES:
            raise EvidenceError("updated evidence target exceeds the allowed size")

        temporary_name = None
        temporary_fd = None
        try:
            temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                temporary_flags |= os.O_NOFOLLOW
            for _ in range(10):
                candidate = f".{relative.name}.evidence-{secrets.token_hex(8)}.tmp"
                try:
                    temporary_fd = os.open(
                        candidate,
                        temporary_flags,
                        stat.S_IMODE(before.st_mode),
                        dir_fd=directory_fd,
                    )
                    temporary_name = candidate
                    break
                except FileExistsError:
                    continue
            if temporary_fd is None or temporary_name is None:
                raise EvidenceError("could not create evidence temporary file")
            write_all(temporary_fd, updated)
            os.fsync(temporary_fd)

            path_metadata = os.stat(
                relative.name, dir_fd=directory_fd, follow_symlinks=False
            )
            if stable_identity(before) != stable_identity(path_metadata):
                raise EvidenceError("evidence target changed before replacement")
            verification_fd = os.open(relative.name, file_flags, dir_fd=directory_fd)
            try:
                verification_before = os.fstat(verification_fd)
                verification_content = read_bounded_descriptor(verification_fd)
                verification_after = os.fstat(verification_fd)
                if (
                    stable_identity(before) != stable_identity(verification_before)
                    or stable_identity(verification_before)
                    != stable_identity(verification_after)
                    or verification_content != existing
                ):
                    raise EvidenceError("evidence target changed before replacement")
            finally:
                os.close(verification_fd)
            os.replace(
                temporary_name,
                relative.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = None
            os.fsync(directory_fd)
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def main(argv: list[str]) -> int:
    """Prepare one deterministic evidence hunk and emit its review plan."""
    if len(argv) != 9:
        print(
            "usage: prepare-publication-evidence.py RUNTIME CONTEXT REVIEW "
            "INITIAL_RESULT RUN_ID STARTED_AT REVIEW_SHA256 PLAN_OUTPUT",
            file=sys.stderr,
        )
        return 64
    try:
        runtime = json.loads(read_regular_nofollow(Path(argv[1])))
        context_path = Path(argv[2])
        review_bytes = read_regular_nofollow(Path(argv[3]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[7]:
            raise EvidenceError("approved review digest mismatch")
        review = json.loads(review_bytes.decode("utf-8"))
        initial = json.loads(read_regular_nofollow(Path(argv[4])))
        if not all(
            initial[key]["push_status"] in {"complete", "not_required"}
            and initial[key]["local_head"] == initial[key]["remote_head"]
            for key in ("agents_vault", "user_vault")
        ):
            raise EvidenceError("initial pushes are not complete")
        finalization = review["agents_vault"]["evidence_finalization"]
        if finalization["template"] != "daily_publication_v1":
            raise EvidenceError("unsupported evidence template")
        target_relative = PurePosixPath(finalization["target_path"])
        if target_relative.is_absolute() or ".." in target_relative.parts:
            raise EvidenceError("invalid evidence target")
        context_digest = approved_context_digest(
            review, read_regular_nofollow(context_path)
        )
        evidence_baseline = capture_complete(argv[1])
        payload = {
            "run_id": argv[5],
            "publication_context_sha256": review["publication_context_sha256"],
            "agents_vault": {
                "commit_hashes": initial["agents_vault"]["commit_hashes"],
                "push_status": initial["agents_vault"]["push_status"],
                "local_remote_equal": True,
            },
            "user_vault": {
                "commit_hashes": initial["user_vault"]["commit_hashes"],
                "push_status": initial["user_vault"]["push_status"],
                "local_remote_equal": True,
            },
            "summary_repo_path": relative_path(
                runtime["user_vault_root"], initial["summary_path"]
            ),
            "advisory_repo_path": relative_path(
                runtime["agents_vault_root"], initial["advisory_path"]
            ),
            "publication_mode": initial["publication_mode"],
            "deferred_cleanup": initial["deferred_cleanup"],
        }
        marker = f"vault-change-publisher:{argv[5]}"
        block = (
            f"\n#### Daily publication evidence — {argv[5]}\n"
            f"<!-- {marker} -->\n\n"
            "```json\n"
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n"
            "```\n\n"
        ).encode("utf-8")
        target = str(target_relative)
        repo = str(runtime["agents_vault_root"])
        git_dir = str(runtime["agents_git_dir"])
        head_entry, index_entry = target_entries(
            runtime, evidence_baseline, target
        )
        head_before = read_blob(repo, git_dir, head_entry["git_blob_oid"])
        index_before = read_blob(repo, git_dir, index_entry["git_blob_oid"])
        worktree_before = read_regular_nofollow(Path(repo) / target)
        marker_bytes = marker.encode("utf-8")
        head_candidate = insert_evidence_block(head_before, block, marker_bytes)
        index_candidate = insert_evidence_block(index_before, block, marker_bytes)
        worktree_candidate = insert_evidence_block(
            worktree_before, block, marker_bytes
        )
        if max(len(head_candidate), len(index_candidate), len(worktree_candidate)) > MAX_TASK_BYTES:
            raise EvidenceError("evidence candidate exceeds the allowed size")
        review_patch = canonical_patch(target, head_before, head_candidate)
        output_root = Path(argv[8]).parent
        head_candidate_path = output_root / "evidence-head-candidate.blob"
        index_candidate_path = output_root / "evidence-index-candidate.blob"
        worktree_candidate_path = output_root / "evidence-worktree-candidate.blob"
        review_patch_path = output_root / "evidence-review.patch"
        for path, content in (
            (head_candidate_path, head_candidate),
            (index_candidate_path, index_candidate),
            (worktree_candidate_path, worktree_candidate),
            (review_patch_path, review_patch),
        ):
            write_private_exclusive(path, content)
        plan = {
            "template": "daily_publication_v1",
            "target_path": target,
            "base_head": initial["agents_vault"]["local_head"],
            "head_entry": head_entry,
            "index_entry": index_entry,
            "head_source_sha256": hashlib.sha256(head_before).hexdigest(),
            "index_source_sha256": hashlib.sha256(index_before).hexdigest(),
            "worktree_source_sha256": hashlib.sha256(worktree_before).hexdigest(),
            "head_candidate_path": str(head_candidate_path),
            "head_candidate_sha256": hashlib.sha256(head_candidate).hexdigest(),
            "index_candidate_path": str(index_candidate_path),
            "index_candidate_sha256": hashlib.sha256(index_candidate).hexdigest(),
            "worktree_candidate_path": str(worktree_candidate_path),
            "worktree_candidate_sha256": hashlib.sha256(worktree_candidate).hexdigest(),
            "review_patch_path": str(review_patch_path),
            "evidence_diff_sha256": hashlib.sha256(review_patch).hexdigest(),
            "publication_context_sha256": context_digest,
            "marker": marker,
            "pre_evidence_state": evidence_baseline,
        }
        Path(argv[8]).write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )
    except (
        EvidenceError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"evidence preparation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
