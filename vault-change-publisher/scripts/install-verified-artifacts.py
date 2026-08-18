#!/usr/bin/env python3
"""Install verified staged artifacts below catalog-derived Vault roots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath

SUMMARY_PATTERN = re.compile(r"^SUMMARY-IT-NEWS-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ADVISORY_PATTERN = re.compile(r"^Personal-Vulnerability-Advisory-\d{4}-\d{2}-\d{2}(?:-\d+)?\.md$")


class InstallError(RuntimeError):
    """Represent a fail-closed artifact installation error."""


def sha256_fd(descriptor: int) -> str:
    """Calculate SHA-256 from one already-open regular-file descriptor."""
    hasher = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        hasher.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return hasher.hexdigest()


def validate_source(source: Path, expected_hash: str) -> None:
    """Validate one source through a single no-follow file descriptor."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise InstallError("source artifact validation failed") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or sha256_fd(descriptor) != expected_hash:
            raise InstallError("source artifact validation failed")
    finally:
        os.close(descriptor)


def open_directory(parent_fd: int, component: str) -> int:
    """Open or create one descriptor-relative directory without symlinks."""
    try:
        os.mkdir(component, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(component, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise InstallError("destination contains a symlink or non-directory") from exc


def install(
    source: Path,
    expected_hash: str,
    vault_root: Path,
    relative_directory: PurePosixPath,
    filename: str,
    expected_target: Path,
) -> Path:
    """Copy one verified artifact with O_NOFOLLOW and O_EXCL."""
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise InstallError("source artifact validation failed") from exc
    source_stat = os.fstat(source_fd)
    if not stat.S_ISREG(source_stat.st_mode) or sha256_fd(source_fd) != expected_hash:
        os.close(source_fd)
        raise InstallError("source artifact validation failed")
    root_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        root_flags |= os.O_NOFOLLOW
    root_fd = os.open(vault_root, root_flags)
    opened = [root_fd]
    directory_fd = root_fd
    try:
        for component in relative_directory.parts:
            directory_fd = open_directory(directory_fd, component)
            opened.append(directory_fd)
        target_directory = vault_root.joinpath(*relative_directory.parts)
        if expected_target.parent != target_directory:
            raise InstallError("planned destination directory mismatch")
        candidate = expected_target.name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            target_fd = os.open(candidate, flags, 0o644, dir_fd=directory_fd)
        except OSError as exc:
            raise InstallError("planned destination is no longer available") from exc
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    count = os.write(target_fd, view)
                    if count <= 0:
                        raise InstallError("could not write destination artifact")
                    view = view[count:]
            os.fsync(target_fd)
        except Exception:
            os.close(target_fd)
            os.unlink(candidate, dir_fd=directory_fd)
            raise
        else:
            os.close(target_fd)
        return expected_target
    finally:
        os.close(source_fd)
        for descriptor in reversed(opened):
            os.close(descriptor)


def safe_relative(value: str) -> PurePosixPath:
    """Validate a catalog-derived relative destination."""
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise InstallError("invalid relative destination")
    return path


def planned_target(
    vault_root: Path, relative_directory: PurePosixPath, filename: str
) -> Path:
    """Choose the first currently unused target without mutating the Vault."""
    if vault_root.is_symlink() or not vault_root.is_dir():
        raise InstallError("Vault root must be a real directory")
    current = vault_root
    missing_parent = False
    for component in relative_directory.parts:
        current = current / component
        if missing_parent:
            continue
        if current.is_symlink():
            raise InstallError("destination contains a symlink")
        if current.exists():
            if not current.is_dir():
                raise InstallError("destination contains a non-directory")
        else:
            missing_parent = True
    stem, suffix = os.path.splitext(filename)
    for index in range(1, 10000):
        candidate = filename if index == 1 else f"{stem}-{index}{suffix}"
        target = current / candidate
        if missing_parent or not os.path.lexists(target):
            return target
    raise InstallError("artifact collision limit exceeded")


def target_from_bound_plan(
    value: object,
    vault_root: Path,
    relative_directory: PurePosixPath,
    filename: str,
) -> Path:
    """Validate a runner-generated target without probing a blocked Vault."""
    if not isinstance(value, str):
        raise InstallError("artifact plan target is not a string")
    target = Path(value)
    expected_parent = vault_root.joinpath(*relative_directory.parts)
    stem, suffix = os.path.splitext(filename)
    candidate_stem, candidate_suffix = os.path.splitext(target.name)
    suffix_number = candidate_stem.removeprefix(f"{stem}-")
    if (
        not target.is_absolute()
        or target.parent != expected_parent
        or candidate_suffix != suffix
        or (
            target.name != filename
            and (not suffix_number.isdigit() or int(suffix_number) < 2)
        )
    ):
        raise InstallError("artifact plan target is invalid")
    return target


def main(argv: list[str]) -> int:
    """Install summary and advisory artifacts and emit their final paths."""
    plan_only = len(argv) == 4 and argv[1] == "--plan"
    install_from_plan = len(argv) == 5 and argv[1] != "--plan"
    if not plan_only and not install_from_plan:
        print(
            "usage: install-verified-artifacts.py --plan CONTEXT COLLECTION | "
            "install-verified-artifacts.py CONTEXT COLLECTION PLAN ROLE",
            file=sys.stderr,
        )
        return 64
    try:
        offset = 1 if plan_only else 0
        context = json.loads(Path(argv[1 + offset]).read_text(encoding="utf-8"))
        collection = json.loads(Path(argv[2 + offset]).read_text(encoding="utf-8"))
        summary_source = Path(collection["summary_path"])
        advisory_source = Path(collection["advisory_path"])
        summary_match = SUMMARY_PATTERN.fullmatch(summary_source.name)
        if not summary_match or not ADVISORY_PATTERN.fullmatch(advisory_source.name):
            raise InstallError("artifact filename does not match its declared role")
        validate_source(summary_source, collection["summary_sha256"])
        validate_source(advisory_source, collection["advisory_sha256"])
        year, month, day = summary_match.groups()
        summary_relative = safe_relative(context["it_news_archive_relative"]).joinpath(
            year, month, day
        )
        advisory_relative = safe_relative(context["advisory_archive_relative"])
        user_root = Path(context["user_vault_root"])
        agents_root = Path(context["agents_vault_root"])
        if plan_only:
            summary_target = planned_target(
                user_root, summary_relative, summary_source.name
            )
            advisory_target = planned_target(
                agents_root, advisory_relative, advisory_source.name
            )
        else:
            selected = {value for value in argv[4].split(",") if value}
            if len(selected) != 1 or not selected <= {
                "user_it_news_summary",
                "agents_security_advisory",
            }:
                raise InstallError("exactly one artifact role must be selected")
            plan = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
            summary_target = target_from_bound_plan(
                plan.get("summary_target"),
                user_root,
                summary_relative,
                summary_source.name,
            )
            advisory_target = target_from_bound_plan(
                plan.get("advisory_target"),
                agents_root,
                advisory_relative,
                advisory_source.name,
            )
            if "user_it_news_summary" in selected:
                if planned_target(
                    user_root, summary_relative, summary_source.name
                ) != summary_target:
                    raise InstallError(
                        "selected summary target no longer matches the artifact plan"
                    )
                summary_target = install(
                    summary_source,
                    collection["summary_sha256"],
                    user_root,
                    summary_relative,
                    summary_source.name,
                    summary_target,
                )
            if "agents_security_advisory" in selected:
                if planned_target(
                    agents_root, advisory_relative, advisory_source.name
                ) != advisory_target:
                    raise InstallError(
                        "selected advisory target no longer matches the artifact plan"
                    )
                advisory_target = install(
                    advisory_source,
                    collection["advisory_sha256"],
                    agents_root,
                    advisory_relative,
                    advisory_source.name,
                    advisory_target,
                )
        result = {
            "summary_target": str(summary_target),
            "advisory_target": str(advisory_target),
        }
    except (InstallError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"artifact installation failed:{exc}", file=sys.stderr)
        return 75
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
