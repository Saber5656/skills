#!/usr/bin/env python3
"""Compute evidence diff digests under one isolated Git contract."""

from __future__ import annotations

import hashlib
import os
import subprocess


def clean_git_environment() -> dict[str, str]:
    """Remove Git overrides and disable system/global configuration."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def git_diff_digest(repo: str, relative: str, *, cached: bool = False) -> str:
    """Hash the exact raw binary diff for one repo-relative target."""
    command = [
        "git",
        "-C",
        repo,
        "-c",
        f"core.hooksPath={os.devnull}",
        "diff",
    ]
    if cached:
        command.append("--cached")
    command.extend(("--binary", "--no-ext-diff", "--", relative))
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        env=clean_git_environment(),
    )
    return hashlib.sha256(result.stdout).hexdigest()
