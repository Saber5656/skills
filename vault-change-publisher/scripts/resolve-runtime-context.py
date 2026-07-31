#!/usr/bin/env python3
"""Resolve machine-local automation paths through the Saihai directory catalog."""

from __future__ import annotations

import json
import hashlib
import os
import shlex
import subprocess
import sys
import re
import tempfile
from urllib.parse import urlsplit
from pathlib import Path, PurePosixPath

ALLOWED_KEYS = {
    "SAIHAI_CHECKOUT_ROOT",
    "CODEX_BIN",
    "GITLEAKS_BIN",
    "IT_NEWS_ARCHIVE_RELATIVE",
    "ADVISORY_ARCHIVE_RELATIVE",
    "STANDING_TASK_ID",
    "STANDING_TASK_RELATIVE",
    "AUTHORIZATION_TASK_ID",
    "AUTHORIZATION_TASK_RELATIVE",
    "AUTHORIZATION_TASK_SHA256",
}
REQUIRED_KEYS = ALLOWED_KEYS


class ContextError(RuntimeError):
    """Represent invalid or unavailable runtime configuration."""


def parse_local_config(path: Path) -> dict[str, str]:
    """Parse a restricted KEY=VALUE file without evaluating shell code."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ContextError(f"invalid local config line:{line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_KEYS or key in values:
            raise ContextError(f"unsupported or duplicate local config key:{key}")
        parts = shlex.split(raw_value, posix=True)
        if len(parts) != 1:
            raise ContextError(f"invalid local config value:{key}")
        values[key] = parts[0]
    missing = sorted(REQUIRED_KEYS - values.keys())
    if missing:
        raise ContextError(f"missing local config keys:{','.join(missing)}")
    return values


def validated_relative(value: str, key: str) -> PurePosixPath:
    """Accept a normalized relative path without traversal."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ContextError(f"invalid relative path:{key}")
    return path


def git_directory(repo_root: Path) -> str:
    """Return repo/.git only for a direct, non-symlink working tree."""
    top_level = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if Path(top_level).resolve() != repo_root.resolve():
        raise ContextError("catalog Vault root is not the repository top level")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--absolute-git-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    git_dir = Path(result.stdout.strip())
    expected = repo_root / ".git"
    if (
        expected.is_symlink()
        or not expected.is_dir()
        or git_dir.resolve() != expected.resolve()
    ):
        raise ContextError("Vault Git directory must be a real repo-root/.git directory")
    return str(git_dir.resolve())


def remote_url(repo_root: Path) -> str:
    """Return a credential-free HTTPS or SSH origin URL."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    candidate = Path(value)
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if candidate.is_absolute():
        resolved_candidate = candidate.resolve()
        resolved_repo = repo_root.resolve()
        if (
            temporary_root in resolved_candidate.parents
            and temporary_root in resolved_repo.parents
        ):
            return str(resolved_candidate)
    if re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+(?:\.git)?", value):
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.username not in {None, "git"}
        or parsed.password is not None
        or not parsed.path.strip("/")
    ):
        raise ContextError("origin URL must be credential-free HTTPS or SSH")
    return value


def resolve_context(local_config: Path, workdir: Path) -> dict[str, str]:
    """Load the canonical catalog with env={} and derive runtime paths."""
    values = parse_local_config(local_config)
    saihai_root = Path(values["SAIHAI_CHECKOUT_ROOT"]).expanduser().resolve()
    if not saihai_root.is_dir():
        raise ContextError("Saihai checkout is not a directory")
    sys.path.insert(0, str(saihai_root))
    import directory_paths  # type: ignore[import-not-found]

    catalog: dict[str, str] = {}
    diagnostics = directory_paths.load_environment(
        checkout_root=saihai_root,
        environ=catalog,
        require_catalog=True,
    )
    if diagnostics.get("status") != "loaded":
        raise ContextError("directory catalog status is not loaded")

    agents_root = Path(catalog["AGENTS_VAULT_ROOT"]).resolve()
    user_root = Path(catalog["USER_VAULT_ROOT"]).resolve()
    skills_root = Path(catalog["SKILLS_REPO_ROOT"]).resolve()
    for vault_root in (agents_root, user_root):
        if not vault_root.is_dir() or not os.access(vault_root, os.R_OK | os.W_OK):
            raise ContextError("catalog Vault root is not readable and writable")
    archive_relative = validated_relative(
        values["IT_NEWS_ARCHIVE_RELATIVE"], "IT_NEWS_ARCHIVE_RELATIVE"
    )
    advisory_relative = validated_relative(
        values["ADVISORY_ARCHIVE_RELATIVE"], "ADVISORY_ARCHIVE_RELATIVE"
    )
    standing_relative = validated_relative(
        values["STANDING_TASK_RELATIVE"], "STANDING_TASK_RELATIVE"
    )
    authorization_relative = validated_relative(
        values["AUTHORIZATION_TASK_RELATIVE"], "AUTHORIZATION_TASK_RELATIVE"
    )
    authorization_path = agents_root.joinpath(*authorization_relative.parts)
    if authorization_path.is_symlink() or not authorization_path.is_file():
        raise ContextError("authorization task is not a regular file")
    standing_path = agents_root.joinpath(*standing_relative.parts)
    if (
        standing_path.is_symlink()
        or not standing_path.is_file()
        or not os.access(standing_path, os.R_OK | os.W_OK)
    ):
        raise ContextError("standing task is not a readable and writable regular file")
    authorization_digest = hashlib.sha256(authorization_path.read_bytes()).hexdigest()
    if authorization_digest != values["AUTHORIZATION_TASK_SHA256"]:
        raise ContextError("authorization task digest does not match pinned evidence")

    context = {
        "workdir": str(workdir.resolve()),
        "saihai_root": str(saihai_root),
        "codex_bin": str(Path(values["CODEX_BIN"]).expanduser().resolve()),
        "gitleaks_bin": str(
            Path(values["GITLEAKS_BIN"]).expanduser().resolve()
        ),
        "gitleaks_version": subprocess.run(
            [str(Path(values["GITLEAKS_BIN"]).expanduser().resolve()), "version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "skills_root": str(skills_root),
        "agents_vault_root": str(agents_root),
        "user_vault_root": str(user_root),
        "agents_git_dir": git_directory(agents_root),
        "user_git_dir": git_directory(user_root),
        "agents_remote_url": remote_url(agents_root),
        "user_remote_url": remote_url(user_root),
        "it_news_archive_root": str(user_root.joinpath(*archive_relative.parts)),
        "it_news_archive_relative": str(archive_relative),
        "advisory_archive_root": str(
            agents_root.joinpath(*advisory_relative.parts)
        ),
        "advisory_archive_relative": str(advisory_relative),
        "standing_task_id": values["STANDING_TASK_ID"],
        "standing_task_path": str(standing_path),
        "authorization_task_id": values["AUTHORIZATION_TASK_ID"],
        "authorization_task_path": str(
            authorization_path
        ),
        "authorization_task_sha256": authorization_digest,
    }
    for key, value in context.items():
        if not value:
            raise ContextError(f"empty resolved context:{key}")
    if not os.access(context["codex_bin"], os.X_OK):
        raise ContextError("CODEX_BIN is not executable")
    if not os.access(context["gitleaks_bin"], os.X_OK):
        raise ContextError("GITLEAKS_BIN is not executable")
    return context


def main(argv: list[str]) -> int:
    """Resolve context and print JSON without exporting path variables."""
    if len(argv) != 3:
        print("usage: resolve-runtime-context.py LOCAL_CONFIG WORKDIR", file=sys.stderr)
        return 64
    try:
        context = resolve_context(Path(argv[1]), Path(argv[2]))
    except (ContextError, OSError, subprocess.SubprocessError, KeyError) as exc:
        print(f"runtime context resolution failed:{exc}", file=sys.stderr)
        return 78
    print(json.dumps(context, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
