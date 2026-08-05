#!/usr/bin/env python3
"""Validate collection output paths and hashes before publication privileges exist."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Tuple

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
SUMMARY_NAME = re.compile(r"^SUMMARY-IT-NEWS-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ADVISORY_NAME = re.compile(r"^Personal-Vulnerability-Advisory-(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\.md$")
ALLOWED_COVERAGE_STATUS = {"取得済み", "対象期間記事なし", "アクセス制約"}
ACCESS_CONTROL_REASON = re.compile(
    r"login|log-in|paywall|subscription|robots|captcha|ログイン|購読|有料|ロボット",
    re.IGNORECASE,
)
CONSTRAINT_REASON = {
    "login": re.compile(r"login|log-in|sign-in|ログイン", re.IGNORECASE),
    "paywall": re.compile(r"paywall|subscription|subscribe|購読|有料|会員限定", re.IGNORECASE),
    "captcha": re.compile(r"captcha|キャプチャ", re.IGNORECASE),
}

class ValidationError(RuntimeError):
    """Represent a collection result that must block publication."""


def digest_fd(descriptor: int) -> tuple[str, bytes]:
    """Hash and retain bounded bytes from one already-open descriptor."""
    hasher = hashlib.sha256()
    content = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        hasher.update(chunk)
        content.extend(chunk)
    return hasher.hexdigest(), bytes(content)


def validate_artifact(
    path_value: str,
    expected_hash: str,
    staging_root: Path,
    earliest_mtime: int,
    expected_date: str,
    role: str,
) -> str:
    """Require a same-run regular non-symlink file below staging root."""
    path = Path(path_value)
    if not path.is_absolute():
        raise ValidationError("artifact is not an absolute regular non-symlink file")
    root = Path(os.path.abspath(staging_root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValidationError("artifact escapes run staging root") from exc
    if not relative.parts or ".." in relative.parts:
        raise ValidationError("artifact escapes run staging root")
    expected_pattern = SUMMARY_NAME if role == "summary" else ADVISORY_NAME
    match = expected_pattern.fullmatch(path.name)
    if not match or "-".join(match.groups()) != expected_date:
        raise ValidationError("artifact filename does not match current JST run date")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    opened = []
    directory_fd = os.open(root, directory_flags)
    opened.append(directory_fd)
    try:
        for component in relative.parts[:-1]:
            directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened.append(directory_fd)
        descriptor = os.open(relative.name, flags, dir_fd=directory_fd)
        opened.append(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidationError("artifact is not a regular file")
        if metadata.st_size <= 0 or metadata.st_size > MAX_ARTIFACT_BYTES:
            raise ValidationError("artifact size is outside the allowed range")
        if metadata.st_mtime < earliest_mtime:
            raise ValidationError("artifact predates this collection run")
        actual_hash, content = digest_fd(descriptor)
        if actual_hash != expected_hash:
            raise ValidationError("artifact SHA-256 mismatch")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("artifact is not UTF-8 text") from exc
    finally:
        for opened_descriptor in reversed(opened):
            os.close(opened_descriptor)
    return decoded


def validate_advisory_reference(
    advisory: str,
    summary_name: str,
    summary_sha256: str,
    staging_root: Path,
) -> None:
    """Require one exact path-free reference and reject private runtime paths."""
    expected = (
        f"- 入力ニュース: {summary_name} "
        f"(same-run SHA-256: {summary_sha256})"
    )
    references = [
        line for line in advisory.splitlines() if line.startswith("- 入力ニュース:")
    ]
    if references != [expected]:
        raise ValidationError("advisory summary reference is missing or inconsistent")
    private_paths = {str(Path(os.path.abspath(staging_root))), str(Path.home())}
    if any(private_path in advisory for private_path in private_paths):
        raise ValidationError("advisory contains a machine-specific path")


def load_source_catalog(path: Path) -> dict[str, dict[str, object]]:
    """Load the reviewed source names and tiers used by this runtime."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources")
    if payload.get("version") != 1 or not isinstance(sources, list) or not sources:
        raise ValidationError("source catalog is invalid")
    expected: dict[str, dict[str, object]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValidationError("source catalog entry is invalid")
        name = source.get("name")
        tier = source.get("tier")
        if not isinstance(name, str) or not name or name in expected or tier not in (1, 2):
            raise ValidationError("source catalog entry is invalid")
        expected[name] = source
    return expected


def load_source_manifest(path: Path, catalog_path: Path) -> dict[str, dict[str, object]]:
    """Bind source evidence to the exact reviewed catalog used by the collector."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > MAX_ARTIFACT_BYTES:
        raise ValidationError("source manifest is not a bounded regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("catalog_sha256") != hashlib.sha256(catalog_path.read_bytes()).hexdigest():
        raise ValidationError("source manifest catalog digest mismatch")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValidationError("source manifest is invalid")
    evidence: dict[str, dict[str, object]] = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("name"), str):
            raise ValidationError("source manifest entry is invalid")
        name = source["name"]
        if name in evidence:
            raise ValidationError("source manifest contains a duplicate source")
        evidence[name] = source
    return evidence


def load_verified_resolutions(path: Path) -> dict[str, dict[str, object]]:
    """Load fallback URLs that the trusted helper independently fetched."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > MAX_ARTIFACT_BYTES:
        raise ValidationError("verified fallback evidence is not a bounded regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    resolutions = payload.get("resolutions")
    if payload.get("version") != 1 or not isinstance(resolutions, list):
        raise ValidationError("verified fallback evidence is invalid")
    result: dict[str, dict[str, object]] = {}
    for item in resolutions:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValidationError("verified fallback entry is invalid")
        if item["name"] in result:
            raise ValidationError("verified fallback evidence contains a duplicate source")
        result[item["name"]] = item
    return result


def load_verified_date_evidence(path: Path) -> dict[str, list[dict[str, object]]]:
    """Group trusted article-date evidence by catalog source."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("date_evidence")
    if not isinstance(items, list):
        raise ValidationError("verified publication-date evidence is invalid")
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValidationError("verified publication-date entry is invalid")
        published = item.get("published_date")
        if not isinstance(published, str) or not published:
            raise ValidationError("verified publication-date entry lacks dates")
        grouped.setdefault(str(item["name"]), []).append(item)
    return grouped


def parse_publication_date(value: object) -> Optional[date]:
    """Parse common RSS/Atom/HTML publication timestamps into a JST date."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            match = re.search(r"\d{4}-\d{2}-\d{2}", value)
            return date.fromisoformat(match.group(0)) if match else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=9))).date()


def canonical_url(value: str) -> str:
    """Normalize an article URL for evidence deduplication."""
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def sealed_extract_dated_entries(
    manifest_path: Path, source_evidence: dict[str, object], run_date: date
) -> dict[str, date]:
    """Load canonical article URLs and dates from one sealed compact extract."""
    filename = source_evidence.get("extract_file")
    if not isinstance(filename, str):
        return {}
    if Path(filename).name != filename:
        raise ValidationError("source extract filename is invalid")
    path = manifest_path.parent / filename
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > MAX_ARTIFACT_BYTES:
        raise ValidationError("source extract is not a bounded regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValidationError("source extract entries are invalid")
    dated: dict[str, date] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        published = parse_publication_date(item.get("published"))
        url = item.get("url")
        if published:
            key = canonical_url(url) if isinstance(url, str) else f"undirected:{index}"
            dated[key] = published
    return dated


def validate_source_coverage(
    summary: str, catalog_path: Path, manifest_path: Path, resolutions_path: Path,
    run_date: date,
) -> None:
    """Require every audit row to match deterministic collector evidence."""
    expected = load_source_catalog(catalog_path)
    evidence = load_source_manifest(manifest_path, catalog_path)
    resolutions = load_verified_resolutions(resolutions_path)
    supplemental_dates = load_verified_date_evidence(resolutions_path)
    if set(evidence) != set(expected):
        raise ValidationError("source manifest does not match the catalog")
    unresolved = {
        name for name, item in evidence.items()
        if item.get("status") == "needs_search_fallback"
    }
    if not set(resolutions).issubset(unresolved):
        raise ValidationError("verified fallback evidence is outside unresolved scope")
    if not set(supplemental_dates).issubset(expected):
        raise ValidationError("verified publication-date evidence is outside catalog scope")
    heading = "## 確認済みサイト一覧"
    if summary.count(heading) != 1:
        raise ValidationError("summary source coverage section is missing or duplicated")
    section = summary.split(heading, 1)[1]
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 7 or cells[0] in {"サイト", "---"} or set(cells[0]) == {"-"}:
            continue
        name = cells[0]
        if name in rows:
            raise ValidationError("summary contains a duplicate source coverage row")
        rows[name] = cells
    if set(rows) != set(expected):
        raise ValidationError("summary source coverage does not match the catalog")
    for name, cells in rows.items():
        _, tier, status, method, confirmed_url, recent_items, reason = cells
        source = expected[name]
        source_evidence = evidence[name]
        if tier != str(source["tier"]) or source_evidence.get("tier") != source["tier"] or status not in ALLOWED_COVERAGE_STATUS:
            raise ValidationError("summary source coverage row is invalid")
        if method not in {"RSS", "公開ページ", "サイト限定検索", "公式代替URL"}:
            raise ValidationError("summary source acquisition method is invalid")
        parsed = urllib.parse.urlsplit(confirmed_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValidationError("summary source confirmation URL is invalid")
        try:
            item_count = int(recent_items)
        except ValueError as exc:
            raise ValidationError("summary source item count is invalid") from exc
        if source_evidence.get("status") == "fetched":
            expected_method = {"rss": "RSS", "public_page": "公開ページ"}.get(
                source_evidence.get("method")
            )
            if method != expected_method or confirmed_url != source_evidence.get("final_url"):
                raise ValidationError("summary source row does not match fetch evidence")
            if status == "アクセス制約":
                raise ValidationError("fetched source cannot be an access constraint")
            dated_entries = sealed_extract_dated_entries(
                manifest_path, source_evidence, run_date
            )
            for item in supplemental_dates.get(name, []):
                final_url = item.get("final_url")
                published = parse_publication_date(item.get("published_date"))
                if not isinstance(final_url, str) or not published:
                    raise ValidationError("verified publication-date entry is invalid")
                key = canonical_url(final_url)
                if key in dated_entries:
                    raise ValidationError("publication-date evidence duplicates an article URL")
                dated_entries[key] = published
            start = run_date - timedelta(days=6)
            evidence_count = sum(
                start <= value <= run_date for value in dated_entries.values()
            )
            if (
                isinstance(source_evidence.get("extract_file"), str)
                and source_evidence.get("extracted_entry_count", 0)
                and not dated_entries
            ):
                raise ValidationError("source lacks publication-date evidence")
            if dated_entries and item_count != evidence_count:
                raise ValidationError("summary source item count does not match dated extract evidence")
        elif source_evidence.get("status") == "access_constraint":
            expected_method = {"rss": "RSS", "public_page": "公開ページ"}.get(
                source_evidence.get("method")
            )
            constraint = source_evidence.get("constraint")
            if (
                method != expected_method
                or confirmed_url != source_evidence.get("final_url")
                or status != "アクセス制約"
                or constraint not in CONSTRAINT_REASON
                or not CONSTRAINT_REASON[str(constraint)].search(reason)
            ):
                raise ValidationError("summary access constraint does not match fetch evidence")
        elif source_evidence.get("status") == "needs_search_fallback":
            fallback = resolutions.get(name)
            if fallback:
                if fallback.get("status") == "verified_access_constraint":
                    constraint = fallback.get("constraint")
                    if (
                        method != "公開ページ"
                        or confirmed_url != fallback.get("requested_url")
                        or status != "アクセス制約"
                        or item_count != 0
                        or constraint not in CONSTRAINT_REASON
                        or not CONSTRAINT_REASON[str(constraint)].search(reason)
                    ):
                        raise ValidationError("summary access constraint does not match verified fallback")
                    continue
                expected_method = {
                    "site_search": "サイト限定検索",
                    "official_alternate": "公式代替URL",
                }.get(fallback.get("method"))
                if (
                    fallback.get("status") != "verified_fallback"
                    or method != expected_method
                    or confirmed_url != fallback.get("requested_url")
                    or status == "アクセス制約"
                ):
                    raise ValidationError("summary source row does not match verified fallback")
                published_dates = fallback.get("published_dates")
                if not isinstance(published_dates, list):
                    raise ValidationError("verified fallback lacks publication-date evidence")
                parsed_dates = [parse_publication_date(value) for value in published_dates]
                dated = [value for value in parsed_dates if value is not None]
                if dated:
                    start = run_date - timedelta(days=6)
                    evidence_count = sum(start <= value <= run_date for value in dated)
                    if item_count != evidence_count:
                        raise ValidationError("fallback item count does not match date evidence")
                if status == "対象期間記事なし" and item_count != 0:
                    raise ValidationError("no-recent-article status must have zero items")
                if status == "取得済み" and item_count <= 0:
                    raise ValidationError("summary source item count contradicts status")
                continue
            attempts = source_evidence.get("attempts")
            if not isinstance(attempts, list):
                raise ValidationError("unresolved source lacks attempt evidence")
            robots_attempts = [
                attempt for attempt in attempts
                if isinstance(attempt, dict) and attempt.get("reason") == "robots_disallowed"
            ]
            expected_methods = {"rss": "RSS", "public_page": "公開ページ"}
            if not any(
                confirmed_url == attempt.get("url")
                and method == expected_methods.get(attempt.get("method"))
                for attempt in robots_attempts
            ):
                raise ValidationError("unresolved source lacks matching robots evidence")
            if status != "アクセス制約":
                raise ValidationError("unresolved source is not a verified access constraint")
        else:
            raise ValidationError("source manifest status is invalid")
        if status == "アクセス制約":
            if item_count != 0 or not ACCESS_CONTROL_REASON.search(reason):
                raise ValidationError("access-control status lacks a permitted reason")
        elif status == "対象期間記事なし":
            if item_count != 0:
                raise ValidationError("no-recent-article status must have zero items")
        elif item_count <= 0:
            raise ValidationError("summary source item count contradicts status")


def main(argv: list[str]) -> int:
    """Validate one collection result and return a fail-closed status."""
    if len(argv) != 8:
        print(
            "usage: validate-collection-result.py RESULT STAGING_ROOT RUN_ID START_EPOCH SOURCE_CATALOG SOURCE_MANIFEST VERIFIED_RESOLUTIONS",
            file=sys.stderr,
        )
        return 64
    try:
        result = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        if result.get("daily_pipeline_status") != "complete":
            raise ValidationError("daily pipeline is not complete")
        if result.get("vault_artifacts_complete") is not True:
            raise ValidationError("artifact set is incomplete")
        if result.get("run_id") != argv[3]:
            raise ValidationError("run ID mismatch")
        run_match = re.match(r"^(\d{4})(\d{2})(\d{2})T", argv[3])
        if not run_match:
            raise ValidationError("run ID does not contain a JST date")
        expected_date = "-".join(run_match.groups())
        earliest_mtime = int(argv[4])
        summary_content = validate_artifact(
            result["summary_path"],
            result["summary_sha256"],
            Path(argv[2]),
            earliest_mtime,
            expected_date,
            "summary",
        )
        advisory_content = validate_artifact(
            result["advisory_path"],
            result["advisory_sha256"],
            Path(argv[2]),
            earliest_mtime,
            expected_date,
            "advisory",
        )
        private_paths = {str(Path(os.path.abspath(argv[2]))), str(Path.home())}
        if any(private_path in summary_content for private_path in private_paths):
            raise ValidationError("summary contains a machine-specific path")
        validate_source_coverage(
            summary_content, Path(argv[5]), Path(argv[6]), Path(argv[7]),
            date.fromisoformat(expected_date),
        )
        validate_advisory_reference(
            advisory_content,
            Path(result["summary_path"]).name,
            result["summary_sha256"],
            Path(argv[2]),
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        print(f"collection validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
