#!/usr/bin/env python3
"""Regression tests for deterministic public-source acquisition."""

from __future__ import annotations

import importlib.util
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
CATALOG = SKILL_ROOT / "references" / "it-news-sources.json"
SCRIPT = SKILL_ROOT / "scripts" / "collect-public-sources.py"
SPEC = importlib.util.spec_from_file_location("collect_public_sources", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeHeaders:
    """Provide the response header subset used by the collector."""

    def __init__(self, content_type: str, content_encoding: str = "") -> None:
        self.content_type = content_type
        self.content_encoding = content_encoding

    def get(self, name: str, default=None):  # type: ignore[no-untyped-def]
        if name == "Content-Encoding":
            return self.content_encoding
        return default

    def get_content_type(self) -> str:
        return self.content_type


class FakeResponse:
    """Act as a bounded urllib response context manager."""

    def __init__(self, content: bytes, content_type: str, url: str, content_encoding: str = "") -> None:
        self.content = content
        self.headers = FakeHeaders(content_type, content_encoding)
        self.url = url
        self.status = 200

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_args):  # type: ignore[no-untyped-def]
        return False

    def read(self, _limit: int) -> bytes:
        return self.content

    def geturl(self) -> str:
        return self.url


class PublicSourceCollectorTests(unittest.TestCase):
    """Exercise catalog, URL, MIME, fallback, and staging boundaries."""

    def test_catalog_has_unique_reviewed_sources(self) -> None:
        """Keep all configured Tier 1 and Tier 2 sources machine-readable."""
        sources = MODULE.load_catalog(CATALOG)
        self.assertEqual(len(sources), 26)
        self.assertEqual(len({source["name"] for source in sources}), 26)
        self.assertEqual(sum(source["tier"] == 1 for source in sources), 11)
        self.assertEqual(sum(source["tier"] == 2 for source in sources), 15)

    def test_url_allowlist_rejects_unsafe_targets(self) -> None:
        """Do not turn the collector into an arbitrary URL or credential client."""
        hosts = MODULE.allowed_hosts(MODULE.load_catalog(CATALOG))
        for value in (
            "http://techcrunch.com/feed/",
            "https://user:secret@techcrunch.com/feed/",
            "https://127.0.0.1/feed/",
            "https://example.com/feed/",
        ):
            with self.subTest(value=value), self.assertRaises(MODULE.CollectionError):
                MODULE.validate_url(value, hosts)

    def test_source_host_boundary_rejects_cross_publisher_redirect(self) -> None:
        """Do not attribute another catalog publisher's response to this source."""
        sources = MODULE.load_catalog(CATALOG)
        first_hosts = MODULE.source_hosts(sources[0])
        with self.assertRaises(MODULE.CollectionError):
            MODULE.validate_url(sources[1]["page_url"], first_hosts)

    def test_fetch_accepts_public_rss_mime(self) -> None:
        """Accept RSS/XML content instead of reproducing the browser MIME failure."""
        sources = MODULE.load_catalog(CATALOG)
        hosts = MODULE.allowed_hosts(sources)
        url = sources[0]["feed_url"]
        response = FakeResponse(b"<?xml version='1.0'?><rss/>", "application/rss+xml", url)
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(MODULE, "robots_allowed", return_value=True), mock.patch.object(
            MODULE.urllib.request, "build_opener", return_value=opener
        ):
            result = MODULE.fetch_url(url, hosts)
        self.assertEqual(result["content_type"], "application/rss+xml")
        self.assertEqual(result["content"], b"<?xml version='1.0'?><rss/>")

    def test_feed_failure_falls_back_to_public_page(self) -> None:
        """Retain the failed feed audit and save the successful page bytes."""
        source = MODULE.load_catalog(CATALOG)[0]
        hosts = MODULE.allowed_hosts([source])
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                MODULE.CollectionError("content-type fixture"),
                {
                    "content": b"<html>public page</html>",
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                    "attempt": 1,
                },
            ],
        ):
            result = MODULE.collect_source(1, source, hosts, Path(temporary))
            extract = json.loads((Path(temporary) / result["extract_file"]).read_text())
            self.assertEqual(extract["format"], "html_links")
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "public_page")
        self.assertEqual([item["status"] for item in result["attempts"]], ["failed", "fetched"])

    def test_unresolved_direct_attempts_require_search_fallback(self) -> None:
        """Never present two direct failures as successful coverage."""
        source = MODULE.load_catalog(CATALOG)[0]
        hosts = MODULE.allowed_hosts([source])
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=MODULE.CollectionError("fixture failure")
        ):
            result = MODULE.collect_source(1, source, hosts, Path(temporary))
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertEqual(len(result["attempts"]), 2)

    def test_extracts_compact_feed_entries(self) -> None:
        """Give the model bounded entry metadata instead of raw untrusted XML."""
        content = b"<rss><channel><item><title>Release</title><link>https://example.test/a</link><pubDate>2026-08-05</pubDate></item></channel></rss>"
        extracted = MODULE.extract_content(content, "application/rss+xml", "https://example.test/feed")
        self.assertEqual(extracted["format"], "feed")
        self.assertEqual(extracted["entry_count"], 1)
        self.assertEqual(extracted["entries"][0]["title"], "Release")

    def test_html_extract_preserves_article_date_evidence(self) -> None:
        """Associate article-card and JSON-LD publication dates with official URLs."""
        content = b'''<html><article><a href="/card">Card story</a><time datetime="2026-08-05">Today</time></article><script type="application/ld+json">{"@type":"NewsArticle","headline":"JSON story","url":"/json","datePublished":"2026-08-04"}</script></html>'''
        extracted = MODULE.extract_content(content, "text/html", "https://example.test/news")
        by_url = {entry["url"]: entry for entry in extracted["entries"]}
        self.assertEqual(by_url["https://example.test/card"]["published"], "2026-08-05")
        self.assertEqual(by_url["https://example.test/json"]["published"], "2026-08-04")
        self.assertEqual(extracted["date_evidence_count"], 2)

    def test_rejects_gzip_expansion_over_limit(self) -> None:
        """Bound decompressed bytes as well as compressed transport bytes."""
        compressed = gzip.compress(b"x" * (MODULE.MAX_BYTES + 1))
        response = FakeResponse(compressed, "text/plain", "https://example.test", "gzip")
        with self.assertRaises(MODULE.CollectionError):
            MODULE.read_bounded(response)

    def test_rejects_escape_before_creating_output(self) -> None:
        """Do not leave directories outside the caller-bound staging root."""
        with tempfile.TemporaryDirectory() as temporary:
            trusted = Path(temporary) / "trusted"
            trusted.mkdir()
            escaped = Path(temporary) / "escaped"
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(trusted)}):
                with self.assertRaises(MODULE.CollectionError):
                    MODULE.resolve_output_root(str(escaped))
            self.assertFalse(escaped.exists())

    def test_independently_verifies_search_fallback_on_source_host(self) -> None:
        """Convert a model-discovered URL into trusted evidence only after fetch."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            candidate = source["page_url"]
            manifest.write_text(json.dumps({"sources": [{
                "name": source["name"], "status": "needs_search_fallback"
            }]}), encoding="utf-8")
            request.write_text(json.dumps({"version": 1, "resolutions": [{
                "name": source["name"], "method": "site_search", "url": candidate
            }], "date_evidence": []}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}), mock.patch.object(
                MODULE, "fetch_url", return_value={"final_url": candidate, "http_status": 200, "content_type": "text/html", "content": b"public article"}
            ) as fetch:
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            fetch.assert_called_once()
            verified = json.loads(output.read_text(encoding="utf-8"))["resolutions"][0]
            self.assertEqual(verified["status"], "verified_fallback")
            self.assertEqual(verified["final_url"], candidate)

    def test_detects_explicit_login_gate_but_not_generic_forbidden_text(self) -> None:
        """Resolve login exceptions from gate markup, not a bare status/error word."""
        login = b'<form><input type="password"><button>Sign in</button></form>'
        self.assertEqual(MODULE.detect_access_constraint(login), "login")
        self.assertIsNone(MODULE.detect_access_constraint(b"HTTP 403 forbidden"))

    def test_rss_gate_falls_back_to_public_page(self) -> None:
        """Do not classify a whole publisher from a constrained feed endpoint."""
        source = MODULE.load_catalog(CATALOG)[0]
        gate = b'<form><input type="password"><button>Sign in</button></form>'
        page = b'<html><a href="/article">Public article</a></html>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=[
                {"content": gate, "content_type": "text/html", "final_url": source["feed_url"], "http_status": 200},
                {"content": page, "content_type": "text/html", "final_url": source["page_url"], "http_status": 200},
            ]
        ):
            result = MODULE.collect_source(1, source, MODULE.source_hosts(source), Path(temporary))
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "public_page")

    def test_resolution_request_rejects_symlink(self) -> None:
        """Do not follow a model-created symlink in the trusted verification phase."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            target = root / "target.json"
            request = root / "request.json"
            output = root / "verified.json"
            manifest.write_text(json.dumps({"sources": [{"name": source["name"], "status": "needs_search_fallback"}]}))
            target.write_text(json.dumps({"version": 1, "resolutions": [], "date_evidence": []}))
            request.symlink_to(target)
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}), self.assertRaises((MODULE.CollectionError, OSError)):
                MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_verifies_date_evidence_for_directly_fetched_source(self) -> None:
        """Allow a fetched HTML source to supplement missing dates through trusted fetch."""
        source = MODULE.load_catalog(CATALOG)[0]
        article = source["page_url"]
        html = (
            '<script type="application/ld+json">'
            f'{{"headline":"Story","url":"{article}","datePublished":"2026-08-05"}}'
            '</script>'
        ).encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            manifest.write_text(json.dumps({"sources": [{"name": source["name"], "status": "fetched"}]}))
            request.write_text(json.dumps({
                "version": 1, "resolutions": [],
                "date_evidence": [{"name": source["name"], "url": article}],
            }))
            with mock.patch.dict(os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}), mock.patch.object(
                MODULE, "fetch_url", return_value={
                    "final_url": article, "http_status": 200,
                    "content_type": "text/html", "content": html,
                },
            ):
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            verified = json.loads(output.read_text())["date_evidence"][0]
            self.assertEqual(verified["published_date"], "2026-08-05")


if __name__ == "__main__":
    unittest.main()
