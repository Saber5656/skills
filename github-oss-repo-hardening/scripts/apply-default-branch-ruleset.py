#!/usr/bin/env python3
"""Create or update a GitHub default-branch ruleset for solo OSS repositories.

The default mode is dry-run. Applying changes requires both:

- a temporary GH_TOKEN environment variable, unless explicitly overridden
- the --yes flag

This script never prints token values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_RULESET_NAME = "protect-main-branch-of-OSS"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply a protected default-branch GitHub ruleset.",
    )
    parser.add_argument("--repo", required=True, help="Target repository as OWNER/REPO.")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "apply"),
        default="dry-run",
        help="dry-run prints the planned mutation. apply performs it.",
    )
    parser.add_argument(
        "--operation",
        choices=("upsert", "create", "update"),
        default="upsert",
        help="upsert creates a ruleset or plans an existing-ruleset replacement.",
    )
    parser.add_argument(
        "--ruleset-name",
        default=DEFAULT_RULESET_NAME,
        help=f"Ruleset name. Default: {DEFAULT_RULESET_NAME}",
    )
    parser.add_argument(
        "--ruleset-id",
        help="Existing ruleset id. Required for --operation update.",
    )
    parser.add_argument(
        "--payload-out",
        help="Write the generated JSON payload to this path.",
    )
    parser.add_argument(
        "--payload-in",
        help="Read a previously reviewed JSON payload from this path.",
    )
    parser.add_argument(
        "--required-check",
        action="append",
        default=[],
        help="Future option: add a required status check context. Repeatable.",
    )
    parser.add_argument(
        "--require-signed-commits",
        action="store_true",
        help="Future option: require verified signed commits.",
    )
    parser.add_argument(
        "--code-scanning-tool",
        action="append",
        default=[],
        help="Future option: require code scanning results from this tool. Repeatable.",
    )
    parser.add_argument(
        "--code-scanning-alerts-threshold",
        choices=("none", "errors", "errors_and_warnings", "all"),
        default="errors",
        help="Threshold for non-security code scanning alerts.",
    )
    parser.add_argument(
        "--code-scanning-security-threshold",
        choices=("none", "critical", "high_or_higher", "medium_or_higher", "all"),
        default="high_or_higher",
        help="Threshold for security code scanning alerts.",
    )
    parser.add_argument(
        "--enable-copilot-review",
        action="store_true",
        help="Future option: add the Copilot code review ruleset rule.",
    )
    parser.add_argument(
        "--copilot-review-drafts",
        action="store_true",
        help="When Copilot review is enabled, also review draft pull requests.",
    )
    parser.add_argument(
        "--copilot-review-on-push",
        action="store_true",
        help="When Copilot review is enabled, review each new PR push.",
    )
    parser.add_argument(
        "--allow-stored-gh-auth",
        action="store_true",
        help="Allow apply mode to use the stored gh credential instead of GH_TOKEN.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Allow apply mode to replace an existing ruleset with the reviewed payload.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required with --mode apply. Confirms the mutation manifest.",
    )
    return parser.parse_args()


def fail(message: str, exit_code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def validate_args(args: argparse.Namespace) -> None:
    if not REPO_RE.fullmatch(args.repo):
        fail("--repo must be in OWNER/REPO form and contain only simple GitHub name characters.")
    if args.operation == "update" and not args.ruleset_id:
        fail("--ruleset-id is required when --operation update is used.")
    if args.ruleset_id and not args.ruleset_id.isdigit():
        fail("--ruleset-id must be numeric.")
    if args.payload_in and args.payload_out:
        fail("--payload-in and --payload-out cannot be used together.")
    if args.payload_in:
        if args.required_check:
            fail("--required-check cannot be combined with --payload-in.")
        if args.require_signed_commits:
            fail("--require-signed-commits cannot be combined with --payload-in.")
        if args.code_scanning_tool:
            fail("--code-scanning-tool cannot be combined with --payload-in.")
        if args.enable_copilot_review:
            fail("--enable-copilot-review cannot be combined with --payload-in.")


def build_rules(args: argparse.Namespace) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {
            "type": "pull_request",
            "parameters": {
                "allowed_merge_methods": ["squash", "rebase"],
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_approving_review_count": 0,
                "required_review_thread_resolution": True,
            },
        },
    ]

    if args.require_signed_commits:
        rules.append({"type": "required_signatures"})

    if args.required_check:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": True,
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": context} for context in args.required_check
                    ],
                },
            }
        )

    if args.code_scanning_tool:
        rules.append(
            {
                "type": "code_scanning",
                "parameters": {
                    "code_scanning_tools": [
                        {
                            "tool": tool,
                            "alerts_threshold": args.code_scanning_alerts_threshold,
                            "security_alerts_threshold": args.code_scanning_security_threshold,
                        }
                        for tool in args.code_scanning_tool
                    ],
                },
            }
        )

    if args.enable_copilot_review:
        rules.append(
            {
                "type": "copilot_code_review",
                "parameters": {
                    "review_draft_pull_requests": args.copilot_review_drafts,
                    "review_on_push": args.copilot_review_on_push,
                },
            }
        )

    return rules


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "name": args.ruleset_name,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH"],
                "exclude": [],
            }
        },
        "rules": build_rules(args),
    }


def load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"payload file not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"could not parse payload JSON: {exc}")

    if not isinstance(payload, dict):
        fail("--payload-in must point to a JSON object.")
    if not isinstance(payload.get("name"), str) or not payload["name"]:
        fail("--payload-in JSON must contain a non-empty string name.")
    if payload.get("target") != "branch":
        fail("--payload-in JSON must target branch rulesets.")
    if not isinstance(payload.get("rules"), list):
        fail("--payload-in JSON must contain a rules array.")
    return payload


def default_payload_path(repo: str) -> Path:
    safe_repo = repo.replace("/", "_")
    return Path(tempfile.gettempdir()) / f"github-default-branch-ruleset-{safe_repo}.json"


def write_payload(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def run_gh_api(endpoint: str, *, method: str | None = None, input_path: Path | None = None) -> str:
    command = ["gh", "api"]
    if method:
        command.extend(["--method", method])
    command.append(endpoint)
    if input_path:
        command.extend(["--input", str(input_path)])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown gh api error"
        fail(stderr, exit_code=result.returncode)
    return result.stdout


def list_rulesets(repo: str) -> list[dict[str, Any]]:
    rulesets: list[dict[str, Any]] = []
    page = 1

    while True:
        output = run_gh_api(
            f"repos/{repo}/rulesets?includes_parents=false&per_page=100&page={page}"
        )
        try:
            page_rulesets = json.loads(output)
        except json.JSONDecodeError as exc:
            fail(f"could not parse gh rulesets response as JSON: {exc}")
        if not isinstance(page_rulesets, list):
            fail("gh rulesets response was not a JSON array.")

        rulesets.extend(page_rulesets)
        if len(page_rulesets) < 100:
            break
        page += 1

    return rulesets


def discover_ruleset_id(repo: str, ruleset_name: str) -> str | None:
    rulesets = list_rulesets(repo)

    matches = [ruleset for ruleset in rulesets if ruleset.get("name") == ruleset_name]
    if len(matches) > 1:
        ids = ", ".join(str(match.get("id")) for match in matches)
        fail(f"multiple rulesets named {ruleset_name!r} found: {ids}. Pass --ruleset-id.")
    if not matches:
        return None
    return str(matches[0]["id"])


def choose_mutation(args: argparse.Namespace) -> tuple[str, str]:
    if args.operation == "create":
        return "POST", f"repos/{args.repo}/rulesets"

    if args.operation == "update":
        return "PUT", f"repos/{args.repo}/rulesets/{args.ruleset_id}"

    ruleset_id = args.ruleset_id or discover_ruleset_id(args.repo, args.ruleset_name)
    if ruleset_id:
        return "PUT", f"repos/{args.repo}/rulesets/{ruleset_id}"
    return "POST", f"repos/{args.repo}/rulesets"


def auth_source(args: argparse.Namespace) -> str:
    if os.environ.get("GH_TOKEN"):
        return "GH_TOKEN environment variable"
    if args.allow_stored_gh_auth:
        return "stored gh credential explicitly allowed"
    return "stored gh credential, read-only discovery only"


def apply_command(args: argparse.Namespace, method: str, payload_path: Path) -> list[str]:
    command = [
        "python3",
        sys.argv[0],
        "--repo",
        args.repo,
        "--mode",
        "apply",
        "--yes",
        "--payload-in",
        str(payload_path),
    ]
    if args.operation != "upsert":
        command.extend(["--operation", args.operation])
    if args.ruleset_id:
        command.extend(["--ruleset-id", args.ruleset_id])
    if method == "PUT":
        command.append("--replace-existing")
    return command


def print_manifest(
    args: argparse.Namespace,
    method: str,
    endpoint: str,
    payload_path: Path,
    payload: dict[str, Any],
) -> None:
    print("gh mutation dry-run")
    print()
    print(f"Target repository: {args.repo}")
    print(f"Endpoint: {method} /{endpoint}")
    print(f"Auth source: {auth_source(args)}")
    print("Required permission: repository Administration: write")
    print("Change summary: create or update an active default-branch ruleset")
    print("Reversible: yes")
    print("Rollback: edit or delete the ruleset in GitHub UI, or apply a saved previous payload with --payload-in")
    print("Token hygiene: enter GH_TOKEN without echo, unset it after use, and revoke the temporary token")
    print(f"Payload: {payload_path}")
    if method == "PUT" and not args.replace_existing:
        print("Replacement guard: apply requires --replace-existing before replacing an existing ruleset")
    print()
    print("Payload rules:")
    target_include = payload.get("conditions", {}).get("ref_name", {}).get("include", [])
    print(f"- target: {json.dumps(target_include)}")
    print(f"- bypass actors: {len(payload.get('bypass_actors', []))}")
    for rule in payload.get("rules", []):
        rule_type = rule.get("type", "unknown")
        if rule_type == "pull_request":
            parameters = rule.get("parameters", {})
            merge_methods = ", ".join(parameters.get("allowed_merge_methods", []))
            print(
                "- pull_request: "
                f"approvals {parameters.get('required_approving_review_count')}, "
                "review thread resolution "
                f"{json.dumps(parameters.get('required_review_thread_resolution'))}, "
                f"merge methods {merge_methods}"
            )
        else:
            print(f"- {rule_type}")
    print()
    print("History-safe apply command shape:")
    print("printf 'GitHub fine-grained PAT: '")
    print("IFS= read -r -s GH_TOKEN")
    print("printf '\\n'")
    print("export GH_TOKEN")
    print(shlex.join(apply_command(args, method, payload_path)))
    print("unset GH_TOKEN")


def guard_apply_auth(args: argparse.Namespace) -> None:
    if not args.yes:
        fail("--mode apply requires --yes after reviewing the dry-run manifest.")
    if not os.environ.get("GH_TOKEN") and not args.allow_stored_gh_auth:
        fail(
            "--mode apply requires GH_TOKEN. "
            "Use a short-lived fine-grained PAT with Administration: write, "
            "or pass --allow-stored-gh-auth intentionally."
        )


def guard_replace(args: argparse.Namespace, method: str) -> None:
    if method == "PUT" and not args.replace_existing:
        fail(
            "apply would replace an existing ruleset. Re-run with --replace-existing "
            "after reviewing the payload and confirming that replacement is intended."
        )


def main() -> int:
    args = parse_args()
    validate_args(args)

    if args.mode == "apply":
        guard_apply_auth(args)

    if args.payload_in:
        payload_path = Path(args.payload_in)
        payload = load_payload(payload_path)
        payload_name = payload["name"]
        if args.ruleset_name != DEFAULT_RULESET_NAME and args.ruleset_name != payload_name:
            fail("--ruleset-name conflicts with the name in --payload-in.")
        args.ruleset_name = payload_name
    else:
        payload = build_payload(args)
        payload_path = Path(args.payload_out) if args.payload_out else default_payload_path(args.repo)
        write_payload(payload, payload_path)

    method, endpoint = choose_mutation(args)
    print_manifest(args, method, endpoint, payload_path, payload)

    if args.mode == "dry-run":
        return 0

    guard_replace(args, method)
    output = run_gh_api(endpoint, method=method, input_path=payload_path)
    print()
    print("Applied ruleset:")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
