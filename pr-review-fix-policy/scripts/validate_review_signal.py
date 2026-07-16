#!/usr/bin/env python3
"""Validate a review signal envelope and recompute its deterministic signal id."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")


def signal_id(payload: dict) -> str:
    identity = "\n".join([
        payload["schema_version"], payload["repository"], str(payload["pr_number"]),
        payload["head_sha"].lower(), ",".join(sorted(payload["review_ids"])),
        payload["review_state_digest"], ",".join(sorted(payload["actionable_thread_ids"])), payload["thread_state_digest"],
    ])
    return hashlib.sha256(identity.encode()).hexdigest()


def validate(payload: dict) -> list[str]:
    required = {"schema_version", "signal_id", "repository", "pr_number", "head_sha", "observed_at", "settled_at", "source_event", "review_ids", "review_state_digest", "actionable_thread_ids", "thread_state_digest", "workflow_url", "delivery"}
    errors = [f"missing field: {key}" for key in sorted(required - payload.keys())]
    if errors:
        return errors
    if payload["schema_version"] != "2": errors.append("schema_version must be 2")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", str(payload["repository"])): errors.append("repository must be owner/repo")
    if not isinstance(payload["pr_number"], int) or payload["pr_number"] < 1: errors.append("pr_number must be a positive integer")
    if not SHA40.fullmatch(str(payload["head_sha"])): errors.append("head_sha must be 40 hex characters")
    if not HEX64.fullmatch(str(payload["thread_state_digest"])): errors.append("thread_state_digest must be lowercase sha256")
    if not HEX64.fullmatch(str(payload["review_state_digest"])): errors.append("review_state_digest must be lowercase sha256")
    if not isinstance(payload["review_ids"], list) or len(payload["review_ids"]) != len(set(payload["review_ids"])): errors.append("review_ids must be a unique list")
    if not isinstance(payload["actionable_thread_ids"], list) or len(payload["actionable_thread_ids"]) != len(set(payload["actionable_thread_ids"])): errors.append("actionable_thread_ids must be a unique list")
    if payload["delivery"] not in {"ready", "no_actionable_review", "duplicate_suppressed", "signal_delivery_blocked"}: errors.append("invalid delivery")
    if not str(payload["workflow_url"]).startswith("https://github.com/"): errors.append("workflow_url must be a github.com URL")
    if not errors and payload["signal_id"] != signal_id(payload): errors.append("signal_id mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.envelope.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"invalid envelope: {error}", file=sys.stderr)
        return 2
    errors = validate(payload)
    if errors:
        for error in errors: print(error, file=sys.stderr)
        return 1
    print(payload["signal_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
