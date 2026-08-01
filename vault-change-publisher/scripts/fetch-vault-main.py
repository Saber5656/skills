#!/usr/bin/env python3
"""Fetch both Vault main refs through fixed, sanitized Git transports."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


class FetchError(RuntimeError):
    """Represent an invalid runtime context or rejected fixed fetch."""


def clean_environment() -> dict[str, str]:
    """Remove ambient Git overrides while preserving transport credentials."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def fetch_main(repo: str, git_dir: str, remote_url: str) -> None:
    """Fetch one literal remote main into the exact tracking ref without force."""
    result = subprocess.run(
        [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={repo}",
            "-c",
            f"core.hooksPath={os.devnull}",
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            remote_url,
            "refs/heads/main:refs/remotes/origin/main",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=clean_environment(),
    )
    if result.returncode != 0:
        raise FetchError("fixed main fetch failed")


def main(argv: list[str]) -> int:
    """Load the resolved context and fetch both exact Vault main refs."""
    if len(argv) != 2:
        print("usage: fetch-vault-main.py RUNTIME_CONTEXT", file=sys.stderr)
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        for prefix in ("agents", "user"):
            fetch_main(
                runtime[f"{prefix}_vault_root"],
                runtime[f"{prefix}_git_dir"],
                runtime[f"{prefix}_remote_url"],
            )
        return 0
    except (
        FetchError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"fixed fetch blocked:{exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
