#!/usr/bin/env python3
"""Fetch the configured public IT-news feeds/pages into run-local staging."""

from __future__ import annotations

import concurrent.futures
import gzip
import html
import hashlib
import io
import json
import os
import re
import socket
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 20
USER_AGENT = "CodexITNewsCollector/1.0 (+public-news-research)"
SOURCE_KEYS = {"name", "tier", "feed_url", "page_url"}


class CollectionError(RuntimeError):
    """Represent a source collection error that should fail closed."""


class RobotsDisallowed(CollectionError):
    """Carry sealed robots.txt evidence for one disallowed direct URL."""

    def __init__(self, requested_url: str, robots_url: str, robots_sha256: str) -> None:
        super().__init__("robots_disallowed")
        self.requested_url = requested_url
        self.robots_url = robots_url
        self.robots_sha256 = robots_sha256


def load_catalog(path: Path) -> list[dict[str, Any]]:
    """Load and strictly validate the tracked source catalog."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("sources"), list):
        raise CollectionError("unsupported source catalog")
    sources = payload["sources"]
    names: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise CollectionError("invalid source catalog entry")
        if (
            not isinstance(source["name"], str)
            or not source["name"].strip()
            or source["name"] in names
            or source["tier"] not in (1, 2)
            or not isinstance(source["page_url"], str)
            or source["feed_url"] is not None
            and not isinstance(source["feed_url"], str)
        ):
            raise CollectionError("invalid source catalog value")
        names.add(source["name"])
    if not sources:
        raise CollectionError("source catalog is empty")
    return sources


def allowed_hosts(sources: list[dict[str, Any]]) -> set[str]:
    """Derive an exact hostname allowlist from reviewed catalog URLs."""
    hosts: set[str] = set()
    for source in sources:
        for value in (source["feed_url"], source["page_url"]):
            if value:
                host = urllib.parse.urlsplit(value).hostname
                if not host:
                    raise CollectionError("catalog URL has no hostname")
                hosts.add(host.lower())
                if host.startswith("www."):
                    hosts.add(host[4:].lower())
                else:
                    hosts.add(f"www.{host.lower()}")
    return hosts


def validate_url(value: str, hosts: set[str]) -> str:
    """Allow only HTTPS catalog hosts without credentials or fragments."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise CollectionError("URL is outside the public source allowlist")
    return value


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects outside the reviewed public source hosts."""

    def __init__(self, hosts: set[str]) -> None:
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validate_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_308(self, req, fp, code, msg, headers):  # type: ignore[no-untyped-def]
        """Follow permanent redirects on Python versions that omit HTTP 308."""
        return self.http_error_302(req, fp, code, msg, headers)


def robots_policy(url: str, hosts: set[str]) -> dict[str, object]:
    """Return a reachable robots.txt decision and its exact evidence digest."""
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
    )
    validate_url(robots_url, hosts)
    request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(SafeRedirectHandler(hosts))
    try:
        with opener.open(request, timeout=8) as response:
            content = response.read(512 * 1024 + 1)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, socket.timeout):
        return {"allowed": True, "robots_url": robots_url, "robots_sha256": None}
    if len(content) > 512 * 1024:
        raise CollectionError("robots.txt exceeds size limit")
    data = content.decode("utf-8", errors="replace")
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(data.splitlines())
    return {
        "allowed": parser.can_fetch(USER_AGENT, url),
        "robots_url": robots_url,
        "robots_sha256": hashlib.sha256(content).hexdigest(),
    }


def read_bounded(response) -> bytes:  # type: ignore[no-untyped-def]
    """Read a response with a hard size cap and optional gzip decoding."""
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            declared_size = int(declared)
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > MAX_BYTES:
            raise CollectionError("response exceeds size limit")
    content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise CollectionError("response exceeds size limit")
    if response.headers.get("Content-Encoding", "").lower() == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as decompressor:
            content = decompressor.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise CollectionError("decompressed response exceeds size limit")
    if not content:
        raise CollectionError("response is empty")
    return content


def fetch_url(url: str, hosts: set[str]) -> dict[str, Any]:
    """Fetch one reviewed URL, accepting public XML/HTML regardless of MIME quirks."""
    validate_url(url, hosts)
    policy = robots_policy(url, hosts)
    if not policy["allowed"]:
        raise RobotsDisallowed(
            url, str(policy["robots_url"]), str(policy["robots_sha256"])
        )
    opener = urllib.request.build_opener(SafeRedirectHandler(hosts))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, text/plain;q=0.9, */*;q=0.1",
            "Accept-Encoding": "gzip",
        },
    )
    last_error = "unknown fetch failure"
    for attempt in range(1, 3):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                content = read_bounded(response)
                content_type = response.headers.get_content_type().lower()
                prefix = content.lstrip()[:32].lower()
                if not (
                    content_type.startswith("text/")
                    or content_type
                    in {
                        "application/rss+xml",
                        "application/atom+xml",
                        "application/xml",
                        "application/xhtml+xml",
                        "application/octet-stream",
                    }
                    and prefix.startswith((b"<", b"<?xml"))
                ):
                    raise CollectionError(f"unsupported response type: {content_type}")
                return {
                    "content": content,
                    "content_type": content_type,
                    "final_url": validate_url(response.geturl(), hosts),
                    "http_status": response.status,
                    "attempt": attempt,
                }
        except urllib.error.HTTPError as exc:
            last_error = f"http_{exc.code}"
            if exc.code in (307, 308) and exc.headers.get("Location"):
                redirected = urllib.parse.urljoin(exc.geturl(), exc.headers["Location"])
                validate_url(redirected, hosts)
                request = urllib.request.Request(redirected, headers=dict(request.header_items()))
                continue
            if exc.code in (401, 402, 403, 407):
                content = read_bounded(exc)
                if detect_access_constraint(content):
                    return {
                        "content": content,
                        "content_type": exc.headers.get_content_type().lower(),
                        "final_url": validate_url(exc.geturl(), hosts),
                        "http_status": exc.code,
                        "attempt": attempt,
                    }
                break
            if exc.code == 429:
                break
        except (CollectionError, OSError, urllib.error.URLError, socket.timeout) as exc:
            last_error = str(exc)
        if attempt == 1:
            time.sleep(0.5)
    raise CollectionError(last_error)


def safe_filename(index: int, name: str, method: str) -> str:
    """Create a stable non-sensitive filename for one fetched source."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "source"
    return f"{index:02d}-{slug}-{method}.data"


def clean_text(value: Optional[str], limit: int = 800) -> Optional[str]:
    """Normalize bounded untrusted markup text for compact model input."""
    if not value:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", value)
    normalized = re.sub(r"\s+", " ", html.unescape(without_tags)).strip()
    return normalized[:limit] or None


def local_name(tag: str) -> str:
    """Drop an XML namespace from one element tag."""
    return tag.rsplit("}", 1)[-1].lower()


def extract_xml(content: bytes) -> list[dict[str, Optional[str]]]:
    """Extract bounded RSS/Atom entry metadata without executing markup."""
    root = ET.fromstring(content)
    entries: list[dict[str, Optional[str]]] = []
    for item in root.iter():
        if local_name(item.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, Optional[str]] = {
            "title": None,
            "url": None,
            "published": None,
            "summary": None,
            "candidate_provenance": "feed_entry",
        }
        for child in list(item):
            name = local_name(child.tag)
            if name == "title" and not fields["title"]:
                fields["title"] = clean_text("".join(child.itertext()), 300)
            elif name == "link" and not fields["url"]:
                fields["url"] = child.attrib.get("href") or clean_text(child.text, 1000)
            elif name in {"pubdate", "published", "updated", "date"} and not fields["published"]:
                fields["published"] = clean_text(child.text, 200)
            elif name in {"description", "summary", "content"} and not fields["summary"]:
                fields["summary"] = clean_text("".join(child.itertext()), 800)
        if fields["title"] or fields["url"]:
            entries.append(fields)
        if len(entries) >= 200:
            break
    return entries


class LinkExtractor(HTMLParser):
    """Collect bounded public links and visible anchor text from HTML."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.current_href: Optional[str] = None
        self.current_text: list[str] = []
        self.entries: list[dict[str, Optional[str]]] = []
        self.seen: set[str] = set()
        self.article_depth = 0
        self.article_entry_start = 0
        self.article_published: Optional[str] = None
        self.heading_depth = 0
        self.current_article_headline = False
        self.in_time = False
        self.time_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        lowered = tag.lower()
        attributes = dict(attrs)
        if lowered == "article":
            if self.article_depth == 0:
                self.article_entry_start = len(self.entries)
                self.article_published = None
            self.article_depth += 1
        if lowered == "time" and self.article_depth:
            self.in_time = True
            self.time_text = []
            self.article_published = clean_text(attributes.get("datetime"), 200)
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.article_depth:
            self.heading_depth += 1
        if lowered != "a" or len(self.entries) >= 400:
            return
        href = attributes.get("href")
        if href:
            self.current_href = urllib.parse.urljoin(self.base_url, href)
            self.current_text = []
            self.current_article_headline = self.article_depth > 0 and self.heading_depth > 0

    def handle_data(self, data: str) -> None:
        if self.current_href:
            self.current_text.append(data)
        if self.in_time:
            self.time_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "time" and self.in_time:
            self.article_published = self.article_published or clean_text(" ".join(self.time_text), 200)
            self.in_time = False
            self.time_text = []
        if lowered == "article" and self.article_depth:
            self.article_depth -= 1
            if self.article_depth == 0:
                article_entries = self.entries[self.article_entry_start:]
                candidate = next(
                    (
                        entry for entry in article_entries
                        if entry.get("candidate_provenance") == "article_headline"
                    ),
                    article_entries[0] if len(article_entries) == 1 else None,
                )
                for entry in article_entries:
                    entry["candidate_provenance"] = "article" if entry is candidate else None
                    if entry is candidate:
                        entry["published"] = entry["published"] or self.article_published
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.heading_depth:
            self.heading_depth -= 1
        if lowered != "a" or not self.current_href:
            return
        parsed = urllib.parse.urlsplit(self.current_href)
        title = clean_text(" ".join(self.current_text), 300)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and self.current_href not in self.seen
            and title
        ):
            self.entries.append(
                {
                    "title": title,
                    "url": self.current_href,
                    "published": None,
                    "summary": None,
                    "candidate_provenance": (
                        "article_headline" if self.current_article_headline else None
                    ),
                }
            )
            self.seen.add(self.current_href)
        self.current_href = None
        self.current_text = []
        self.current_article_headline = False


def extract_json_ld(content: bytes, base_url: str) -> list[dict[str, Optional[str]]]:
    """Extract article metadata exposed by public JSON-LD blocks."""
    text = content.decode("utf-8", errors="replace")
    blocks = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        text, re.IGNORECASE | re.DOTALL,
    )
    entries: list[dict[str, Optional[str]]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        title = clean_text(value.get("headline") or value.get("name"), 300)
        raw_url = value.get("url") or value.get("mainEntityOfPage")
        if isinstance(raw_url, dict):
            raw_url = raw_url.get("@id") or raw_url.get("url")
        published = clean_text(value.get("datePublished") or value.get("dateModified"), 200)
        raw_type = value.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        article_like = any(
            isinstance(item, str)
            and (item.lower().endswith("article") or item.lower().endswith("posting"))
            for item in types
        )
        if title and isinstance(raw_url, str) and (published or article_like):
            entries.append({
                "title": title,
                "url": urllib.parse.urljoin(base_url, raw_url),
                "published": published,
                "summary": clean_text(value.get("description"), 800),
                "candidate_provenance": "json_ld" if article_like else None,
            })
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                visit(nested)

    for block in blocks[:50]:
        try:
            visit(json.loads(html.unescape(block)))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries[:200]


def canonical_url(value: str) -> str:
    """Normalize an HTTP URL for same-article evidence comparison."""
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    host = (parsed.hostname or "").lower()
    host = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port
    except ValueError:
        port = None
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), netloc, path, parsed.query, "")
    )


def extract_primary_publication_date(content: bytes, final_url: str) -> Optional[str]:
    """Return only the publication date belonging to the fetched article itself."""
    target = canonical_url(final_url)
    for entry in extract_json_ld(content, final_url):
        if entry.get("url") and canonical_url(str(entry["url"])) == target:
            return entry.get("published")
    text = content.decode("utf-8", errors="replace")
    meta = re.search(
        r"<meta[^>]+(?:property|name)=[\"'](?:article:published_time|datePublished|date)[\"'][^>]+content=[\"']([^\"']+)",
        text, re.IGNORECASE,
    )
    if not meta:
        meta = re.search(
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"'](?:article:published_time|datePublished|date)[\"']",
            text, re.IGNORECASE,
        )
    return clean_text(meta.group(1), 200) if meta else None


def extract_content(content: bytes, content_type: str, final_url: str) -> dict[str, Any]:
    """Create a compact, inert index while retaining raw evidence separately."""
    prefix = content.lstrip()[:32].lower()
    if "xml" in content_type or prefix.startswith((b"<rss", b"<feed", b"<?xml")):
        try:
            entries = extract_xml(content)
            return {"format": "feed", "entry_count": len(entries), "entries": entries}
        except ET.ParseError:
            pass
    parser = LinkExtractor(final_url)
    parser.feed(content.decode("utf-8", errors="replace"))
    combined: list[dict[str, Optional[str]]] = []
    by_url: dict[str, dict[str, Optional[str]]] = {}
    for entry in [*extract_json_ld(content, final_url), *parser.entries]:
        url = entry.get("url")
        if not isinstance(url, str):
            combined.append(entry)
            continue
        key = canonical_url(url)
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = entry
            combined.append(entry)
            continue
        existing["published"] = existing.get("published") or entry.get("published")
        existing["candidate_provenance"] = (
            existing.get("candidate_provenance") or entry.get("candidate_provenance")
        )
    combined = combined[:400]
    return {
        "format": "html_links",
        "entry_count": len(combined),
        "date_evidence_count": sum(bool(entry["published"]) for entry in combined),
        "entries": combined,
    }


def detect_access_constraint(content: bytes) -> Optional[str]:
    """Recognize explicit gate markup without treating generic HTTP errors as proof."""
    text = content[: 1024 * 1024].decode("utf-8", errors="replace").lower()
    if re.search(r"g-recaptcha|hcaptcha|cf-turnstile|captcha challenge", text):
        return "captcha"
    if re.search(r"<input[^>]+type=[\"']password[\"']", text) and re.search(
        r"sign[ -]?in|log[ -]?in|ログイン", text
    ):
        return "login"
    if re.search(
        r"subscribe (?:to|in order to) (?:continue|read)|\bsubscription required\b|"
        r"購読.{0,30}(?:続きを読む|必要)|会員限定",
        text,
    ):
        return "paywall"
    return None


def collect_source(
    index: int,
    source: dict[str, Any],
    hosts: set[str],
    output_root: Path,
) -> dict[str, Any]:
    """Try feed first, then the public page, retaining an audit of both."""
    attempts: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    candidates = (("rss", source["feed_url"]), ("public_page", source["page_url"]))
    for method, url in candidates:
        if not url:
            continue
        try:
            fetched = fetch_url(url, hosts)
            extract = extract_content(
                fetched["content"], fetched["content_type"], fetched["final_url"]
            )
            constraint = detect_access_constraint(fetched["content"])
            if constraint and extract["entry_count"] < 10:
                constraint_record = {
                    "method": method, "requested_url": url,
                    "final_url": fetched["final_url"], "constraint": constraint,
                    "http_status": fetched["http_status"],
                }
                attempts.append({
                    "method": method, "url": url, "status": "access_constraint",
                    **constraint_record,
                })
                constraints.append(constraint_record)
                continue
            if extract["entry_count"] == 0:
                raise CollectionError("empty_extract")
            filename = safe_filename(index, source["name"], method)
            destination = output_root / filename
            content = fetched.pop("content")
            with destination.open("xb") as handle:
                handle.write(content)
            digest = hashlib.sha256(content).hexdigest()
            extract_filename = filename.replace(".data", ".extract.json")
            (output_root / extract_filename).write_text(
                json.dumps(extract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            attempts.append({"method": method, "url": url, "status": "fetched"})
            return {
                "name": source["name"],
                "tier": source["tier"],
                "status": "fetched",
                "method": method,
                "requested_url": url,
                "final_url": fetched["final_url"],
                "content_type": fetched["content_type"],
                "http_status": fetched["http_status"],
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "content_file": filename,
                "extract_file": extract_filename,
                "extracted_entry_count": extract["entry_count"],
                "attempts": attempts,
            }
        except RobotsDisallowed as exc:
            constraint_record = {
                "method": method,
                "requested_url": exc.requested_url,
                "final_url": exc.requested_url,
                "constraint": "robots",
                "http_status": None,
                "robots_url": exc.robots_url,
                "robots_sha256": exc.robots_sha256,
            }
            attempts.append({
                "method": method, "url": url, "status": "access_constraint",
                "reason": str(exc), **constraint_record,
            })
            constraints.append(constraint_record)
        except (CollectionError, OSError, ValueError) as exc:
            attempts.append(
                {"method": method, "url": url, "status": "failed", "reason": str(exc)}
            )
    if attempts and all(item.get("status") == "access_constraint" for item in attempts):
        resolved = constraints[-1]
        return {
            "name": source["name"], "tier": source["tier"],
            "status": "access_constraint", **resolved,
            "content_file": None, "extract_file": None,
            "extracted_entry_count": 0, "attempts": attempts,
        }
    return {
        "name": source["name"],
        "tier": source["tier"],
        "status": "needs_search_fallback",
        "method": None,
        "requested_url": None,
        "final_url": None,
        "content_type": None,
        "http_status": None,
        "bytes": 0,
        "sha256": None,
        "content_file": None,
        "extract_file": None,
        "extracted_entry_count": 0,
        "attempts": attempts,
    }


def resolve_output_root(value: str) -> Path:
    """Keep fetched untrusted bytes inside the caller-bound collection root."""
    trusted_value = os.environ.get("COLLECTION_OUTPUT_ROOT")
    if not trusted_value:
        raise CollectionError("COLLECTION_OUTPUT_ROOT is required")
    trusted = Path(trusted_value).resolve(strict=True)
    output = Path(value)
    if output.is_symlink():
        raise CollectionError("output root escapes the collection staging root")
    parent = output.parent.resolve(strict=True)
    if trusted != parent and trusted not in parent.parents:
        raise CollectionError("output root escapes the collection staging root")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    resolved = output.resolve(strict=True)
    if trusted == resolved or trusted not in resolved.parents:
        raise CollectionError("output root escapes the collection staging root")
    return resolved


def source_hosts(source: dict[str, Any]) -> set[str]:
    """Return only the reviewed hosts belonging to one catalog source."""
    return allowed_hosts([source])


def load_bounded_run_json(path: Path, trusted: Path) -> dict[str, Any]:
    """Read one regular non-symlink JSON file already contained by the run root."""
    parent = path.parent.resolve(strict=True)
    if parent != trusted and trusted not in parent.parents:
        raise CollectionError("JSON input escapes the run root")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 1024 * 1024:
            raise CollectionError("JSON input is not a bounded regular file")
        content = os.read(descriptor, 1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise CollectionError("JSON input exceeds size limit")
    finally:
        os.close(descriptor)
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise CollectionError("JSON input root is invalid")
    return payload


def verify_resolutions(
    catalog_path: Path,
    source_manifest_path: Path,
    request_path: Path,
    output_path: Path,
) -> None:
    """Independently fetch model-discovered fallback URLs on reviewed hosts."""
    trusted_value = os.environ.get("COLLECTION_OUTPUT_ROOT")
    if not trusted_value:
        raise CollectionError("COLLECTION_OUTPUT_ROOT is required")
    trusted = Path(trusted_value).resolve(strict=True)
    sources = load_catalog(catalog_path)
    by_name = {source["name"]: source for source in sources}
    base = load_bounded_run_json(source_manifest_path, trusted)
    unresolved = {
        item["name"] for item in base.get("sources", [])
        if isinstance(item, dict) and item.get("status") == "needs_search_fallback"
    }
    request = load_bounded_run_json(request_path, trusted)
    resolutions = request.get("resolutions")
    date_requests = request.get("date_evidence")
    if (
        request.get("version") != 1
        or not isinstance(resolutions, list)
        or not isinstance(date_requests, list)
        or len(date_requests) > 500
    ):
        raise CollectionError("invalid fallback resolution request")
    verified = []
    seen: set[str] = set()
    for item in resolutions:
        if not isinstance(item, dict):
            raise CollectionError("invalid fallback resolution entry")
        name, method, url = item["name"], item["method"], item["url"]
        expected_keys = {"name", "method", "url", "constraint"} if method == "access_constraint" else {"name", "method", "url"}
        if set(item) != expected_keys:
            raise CollectionError("invalid fallback resolution entry")
        if (
            name not in unresolved
            or name in seen
            or method not in {"site_search", "official_alternate", "access_constraint"}
            or not isinstance(url, str)
        ):
            raise CollectionError("fallback resolution is outside unresolved scope")
        hosts = source_hosts(by_name[name])
        fetched = fetch_url(validate_url(url, hosts), hosts)
        constraint = detect_access_constraint(fetched["content"])
        extract = extract_content(fetched["content"], fetched["content_type"], fetched["final_url"])
        if extract["entry_count"] >= 10:
            constraint = None
        if method == "access_constraint":
            if item["constraint"] not in {"login", "paywall", "captcha"} or constraint != item["constraint"]:
                raise CollectionError("access constraint lacks verified gate evidence")
            status = "verified_access_constraint"
        elif constraint:
            raise CollectionError("fallback URL is access constrained")
        else:
            status = "verified_fallback"
            if extract["entry_count"] == 0:
                raise CollectionError("verified fallback extract is empty")
        candidates = (
            extract["entries"]
            if extract["format"] == "feed"
            else [
                entry for entry in extract["entries"]
                if entry.get("candidate_provenance") in {"article", "json_ld"}
            ]
        )
        published_dates = [entry.get("published") for entry in candidates]
        if status == "verified_fallback" and (
            not published_dates or any(not value for value in published_dates)
        ):
            raise CollectionError("verified fallback lacks complete publication-date evidence")
        verified.append({
            "name": name,
            "status": status,
            "method": method,
            "requested_url": url,
            "final_url": fetched["final_url"],
            "http_status": fetched["http_status"],
            "constraint": constraint,
            "extracted_entry_count": extract["entry_count"],
            "candidate_entry_count": len(candidates),
            "date_evidence_count": sum(bool(value) for value in published_dates),
            "published_dates": published_dates,
            "candidate_evidence": [
                {
                    "url": entry.get("url"),
                    "provenance": entry.get("candidate_provenance"),
                    "published": entry.get("published"),
                }
                for entry in candidates
            ],
        })
        seen.add(name)
    verified_dates = []
    seen_date_urls: set[tuple[str, str]] = set()
    for item in date_requests:
        if not isinstance(item, dict) or set(item) != {"name", "url"}:
            raise CollectionError("invalid publication-date evidence request")
        name, url = item["name"], item["url"]
        if name not in by_name or not isinstance(url, str) or (name, url) in seen_date_urls:
            raise CollectionError("publication-date evidence is outside catalog scope")
        hosts = source_hosts(by_name[name])
        fetched = fetch_url(validate_url(url, hosts), hosts)
        if canonical_url(url) != canonical_url(fetched["final_url"]):
            raise CollectionError("publication-date evidence redirected to another article")
        extract = extract_content(fetched["content"], fetched["content_type"], fetched["final_url"])
        published_date = extract_primary_publication_date(
            fetched["content"], fetched["final_url"]
        )
        if not published_date:
            raise CollectionError("article URL lacks publication-date evidence")
        verified_dates.append({
            "name": name, "requested_url": url, "final_url": fetched["final_url"],
            "published_date": published_date,
        })
        seen_date_urls.add((name, url))
    parent = output_path.parent.resolve(strict=True)
    if parent != trusted or output_path.is_symlink() or output_path.exists():
        raise CollectionError("verified fallback output escapes the run root")
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(
            {"version": 1, "resolutions": verified, "date_evidence": verified_dates},
            handle, ensure_ascii=False, indent=2,
        )
        handle.write("\n")


def main(argv: list[str]) -> int:
    """Collect all configured sources and emit a run-local manifest."""
    try:
        if len(argv) == 6 and argv[1] == "--verify-resolutions":
            verify_resolutions(Path(argv[2]), Path(argv[3]), Path(argv[4]), Path(argv[5]))
            return 0
        if len(argv) != 3:
            print("usage: collect_public_sources.py CATALOG OUTPUT_ROOT", file=sys.stderr)
            return 64
        catalog_path = Path(argv[1])
        sources = load_catalog(catalog_path)
        hosts = allowed_hosts(sources)
        for source in sources:
            validate_url(source["page_url"], hosts)
            if source["feed_url"]:
                validate_url(source["feed_url"], hosts)
        output_root = resolve_output_root(argv[2])
        results: list[Optional[dict[str, Any]]] = [None] * len(sources)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    collect_source, i, source, source_hosts(source), output_root
                ): i
                for i, source in enumerate(sources, start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future] - 1] = future.result()
        manifest = {
            "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
            "source_count": len(sources),
            "fetched_count": sum(item["status"] == "fetched" for item in results if item),
            "needs_search_fallback_count": sum(
                item["status"] == "needs_search_fallback" for item in results if item
            ),
            "access_constraint_count": sum(
                item["status"] == "access_constraint" for item in results if item
            ),
            "sources": results,
        }
        manifest_path = output_root / "source-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({**manifest, "manifest_path": str(manifest_path)}, ensure_ascii=False))
    except (CollectionError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"source collection failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
