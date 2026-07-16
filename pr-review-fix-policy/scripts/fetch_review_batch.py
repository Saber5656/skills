#!/usr/bin/env python3
"""Fetch deterministic, thread-aware snapshots for explicitly listed GitHub PRs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from typing import Any

PR_REF = re.compile(r"^(?P<owner>[^/\s]+)/(?P<repo>[^#\s]+)#(?P<number>[1-9][0-9]*)$")

QUERY = r"""
query($owner:String!, $repo:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      number url state baseRefName headRefName headRefOid
      reviewThreads(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first:1) { nodes { author { login } body } }
        }
      }
    }
  }
}
"""


def parse_ref(value: str) -> tuple[str, str, int]:
    match = PR_REF.fullmatch(value)
    if not match:
        raise ValueError(f"invalid PR reference: {value!r}; expected owner/repo#number")
    return match["owner"], match["repo"], int(match["number"])


def run_graphql(owner: str, repo: str, number: int, cursor: str | None) -> dict[str, Any]:
    command = [
        "gh", "api", "graphql", "-f", f"query={QUERY}",
        "-F", f"owner={owner}", "-F", f"repo={repo}", "-F", f"number={number}",
    ]
    if cursor is not None:
        command.extend(["-F", f"cursor={cursor}"])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip() or "gh api graphql failed"
        raise RuntimeError(message)
    return json.loads(completed.stdout)


def summarize(body: str, limit: int = 240) -> str:
    compact = " ".join(body.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def fetch_one(owner: str, repo: str, number: int) -> dict[str, Any]:
    record: dict[str, Any] = {
        "repository": f"{owner}/{repo}", "pr_number": number, "pagination_complete": False,
        "actionable_threads": [], "ignored": {"resolved": 0, "outdated": 0}, "blocker": None,
    }
    cursor: str | None = None
    fixed_head: str | None = None
    try:
        while True:
            payload = run_graphql(owner, repo, number, cursor)
            pr = payload.get("data", {}).get("repository", {}).get("pullRequest")
            if pr is None:
                record["blocker"] = "not_found_or_inaccessible"
                return record
            current_head = str(pr.get("headRefOid") or "").lower()
            if fixed_head is None:
                fixed_head = current_head
            elif current_head != fixed_head:
                record["blocker"] = "head_changed_during_fetch"
                record["actionable_threads"] = []
                return record
            if "url" not in record:
                record.update({key: pr.get(key) for key in ("url", "state", "baseRefName", "headRefName", "headRefOid")})
            connection = pr["reviewThreads"]
            for thread in connection["nodes"]:
                if thread["isResolved"]:
                    record["ignored"]["resolved"] += 1
                    continue
                if thread["isOutdated"]:
                    record["ignored"]["outdated"] += 1
                    continue
                first = (thread.get("comments") or {}).get("nodes") or [{}]
                comment = first[0]
                record["actionable_threads"].append({
                    "thread_node_id": thread["id"], "path": thread.get("path"), "line": thread.get("line"),
                    "original_line": thread.get("originalLine"), "author": (comment.get("author") or {}).get("login"),
                    "summary": summarize(comment.get("body") or ""), "content_trust": "untrusted_review_content",
                    "isResolved": False, "isOutdated": False,
                })
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                record["pagination_complete"] = True
                break
            cursor = page["endCursor"]
            if not cursor:
                record["blocker"] = "pagination_cursor_missing"
                break
        final_payload = run_graphql(owner, repo, number, None)
        final_pr = final_payload.get("data", {}).get("repository", {}).get("pullRequest")
        final_head = str((final_pr or {}).get("headRefOid") or "").lower()
        if not final_pr or final_head != fixed_head:
            record["blocker"] = "head_changed_during_fetch"
            record["actionable_threads"] = []
            record["pagination_complete"] = False
            return record
        if record.get("state") != "OPEN":
            record["blocker"] = f"pr_state_{str(record.get('state')).lower()}"
        record["actionable_threads"].sort(key=lambda item: (item["path"] or "", item["original_line"] or -1, item["thread_node_id"]))
    except (RuntimeError, json.JSONDecodeError) as error:
        record["blocker"] = f"fetch_failed: {error}"
    return record


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pr", nargs="+", help="explicit owner/repo#number references (maximum 20)")
    args = parser.parse_args()
    if len(args.pr) > 20:
        parser.error("at most 20 PRs may be fetched in one batch")
    try:
        refs = sorted({parse_ref(item) for item in args.pr}, key=lambda item: (item[0], item[1], item[2]))
    except ValueError as error:
        parser.error(str(error))
    snapshot = {
        "schema_version": "1", "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "selection": [fetch_one(*item) for item in refs],
    }
    snapshot["snapshot_digest"] = canonical_digest(snapshot["selection"])
    json.dump(snapshot, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if all(item["blocker"] is None for item in snapshot["selection"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
