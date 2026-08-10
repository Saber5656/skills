#!/usr/bin/env python3
"""Regression tests for deterministic public-source acquisition."""

from __future__ import annotations

import importlib.util
import gzip
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parent
CATALOG = SKILL_ROOT / "references" / "it-news-sources.json"
SCRIPT = SKILL_ROOT / "scripts" / "collect-public-sources.py"
SPEC = importlib.util.spec_from_file_location("collect_public_sources", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
VALIDATOR_SCRIPT = (
    REPO_ROOT / "vault-change-publisher/scripts/validate-collection-result.py"
)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_collection_result_for_collector", VALIDATOR_SCRIPT
)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)


class FakeHeaders:
    """Provide the response header subset used by the collector."""

    def __init__(
        self,
        content_type: str,
        content_encoding: str = "",
        content_length=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.content_type = content_type
        self.content_encoding = content_encoding
        self.content_length = content_length

    def get(self, name: str, default=None):  # type: ignore[no-untyped-def]
        if name == "Content-Encoding":
            return self.content_encoding
        if name == "Content-Length":
            return self.content_length
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
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(
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
                    "content": b'<html><article><a href="/article">Public article</a><time datetime="2026-08-10">Today</time></article></html>',
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                    "attempt": 1,
                },
            ],
        ):
            result = MODULE.collect_source(
                1, source, hosts, Path(temporary), MODULE.date(2026, 8, 10)
            )
            extract = json.loads((Path(temporary) / result["extract_file"]).read_text())
            self.assertEqual(extract["format"], "html_links")
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "public_page")
        self.assertEqual(result["jst_window_start"], "2026-08-04")
        self.assertEqual(result["jst_window_end"], "2026-08-10")
        self.assertEqual(result["jst_window_item_count"], 1)
        self.assertEqual([item["status"] for item in result["attempts"]], ["failed", "fetched"])

    def test_undated_html_requires_search_fallback(self) -> None:
        """Do not seal an HTML candidate set that cannot support a date audit."""
        source = MODULE.load_catalog(CATALOG)[0]
        undated = b'<html><article><a href="/article">Public article</a></article></html>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                MODULE.CollectionError("feed unavailable"),
                {
                    "content": undated,
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                },
            ],
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertEqual(
            result["attempts"][-1]["reason"],
            "html_extract_lacks_publication_date_evidence",
        )

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

    def test_html_extract_binds_visible_card_date_to_article(self) -> None:
        """Normalize a visible YYYY/M/D date inside one article card."""
        content = (
            b'<article><h2><a href="/card">Card story</a></h2>'
            b'<span class="date">2026/8/4</span></article>'
        )
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["entries"][0]["published"], "2026-08-04")
        self.assertEqual(extracted["date_evidence_count"], 1)

    def test_html_extract_rejects_arbitrary_visible_and_malformed_dates(self) -> None:
        """Do not promote body dates or malformed metadata to publication evidence."""
        content = b'''<article><h2><a href="/card">Support ends 2026-08-04</a></h2></article><script type="application/ld+json">{"@type":"NewsArticle","headline":"Broken","url":"/broken","datePublished":"not-a-date"}</script>'''
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        candidates = [
            entry for entry in extracted["entries"]
            if entry.get("candidate_provenance") in {"article", "json_ld"}
        ]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(entry["published"] is None for entry in candidates))
        self.assertEqual(extracted["date_evidence_count"], 0)

    def test_non_article_json_ld_does_not_supply_date_evidence(self) -> None:
        """Ignore dated WebPage metadata when sealing article candidates."""
        content = b'''<script type="application/ld+json">{"@type":"WebPage","name":"Archive","url":"/news","datePublished":"2026-08-04","dateModified":"2026-08-05"}</script>'''
        extracted = MODULE.extract_content(
            content, "text/html", "https://example.test/news"
        )
        self.assertEqual(extracted["candidate_entry_count"], 0)
        self.assertEqual(extracted["date_evidence_count"], 0)

    def test_collector_and_validator_share_jst_timestamp_semantics(self) -> None:
        """Keep RFC, offset, Z, and naive timestamps identical at window edges."""
        fixtures = {
            "Mon, 03 Aug 2026 15:00:00 GMT": MODULE.date(2026, 8, 4),
            "2026-08-03T15:00:00Z": MODULE.date(2026, 8, 4),
            "2026-08-03T23:59:59+09:00": MODULE.date(2026, 8, 3),
            "2026-08-03T16:00:00": MODULE.date(2026, 8, 4),
            "2026-08-10T14:59:59Z": MODULE.date(2026, 8, 10),
            "2026-08-10T15:00:00Z": MODULE.date(2026, 8, 11),
        }
        for value, expected in fixtures.items():
            with self.subTest(value=value):
                self.assertEqual(MODULE.publication_date_in_jst(value), expected)
                self.assertEqual(VALIDATOR.parse_publication_date(value), expected)
        extract = {
            "format": "feed",
            "entries": [
                {"published": "2026-08-03T14:59:59Z"},
                {"published": "2026-08-03T15:00:00Z"},
                {"published": "2026-08-10T14:59:59Z"},
                {"published": "2026-08-10T15:00:00Z"},
            ],
        }
        self.assertEqual(
            MODULE.jst_window_item_count(extract, MODULE.date(2026, 8, 10)), 2
        )

    def test_extracts_dated_publisher_list_metadata(self) -> None:
        """Bind supported public list metadata to its official article URL."""
        fixtures = [
            (
                b'<p class="title"><a href="/docs/1.html">Forest story</a></p>'
                b'<p class="date">(2026/8/7)</p>',
                "https://forest.watch.impress.co.jp/category/genai/",
                "https://forest.watch.impress.co.jp/docs/1.html",
            ),
            (
                b"{'url':'/ait/articles/2608/07/news008.html','title':'AIT story',"
                b"'subtitle':'x','date':'2026/08/07',}",
                "https://atmarkit.itmedia.co.jp/",
                "https://atmarkit.itmedia.co.jp/ait/articles/2608/07/news008.html",
            ),
            (
                b'{\\"datePublished\\":\\"2026-08-07T16:00:00.000Z\\",'
                b'\\"articleMetadata\\":{\\"title\\":\\"Cyber story\\"},'
                b'\\"issueId\\":\\"daily-briefing\\",\\"issueVolume\\":15,'
                b'\\"issueNumber\\":150}',
                "https://thecyberwire.com/newsletters/daily-briefing",
                "https://thecyberwire.com/newsletters/daily-briefing/15/150",
            ),
        ]
        for content, base, expected_url in fixtures:
            with self.subTest(base=base):
                entries = MODULE.extract_embedded_article_metadata(content, base)
                self.assertEqual(entries[0]["url"], expected_url)
                self.assertTrue(entries[0]["published"].startswith("2026-08-07"))

    def test_embedded_list_date_cannot_cross_into_the_next_record(self) -> None:
        """Keep an undated title from consuming a later record's date."""
        content = (
            b'<p class="title"><a href="/first">First</a></p>'
            b'<p class="summary">No publication date</p>'
            b'<p class="title"><a href="/second">Second</a></p>'
            b'<p class="date">(2026/8/7)</p>'
        )
        entries = MODULE.extract_embedded_article_metadata(
            content, "https://example.test/news"
        )
        self.assertEqual([entry["url"] for entry in entries], ["https://example.test/second"])

    def test_rejects_gzip_expansion_over_limit(self) -> None:
        """Bound decompressed bytes as well as compressed transport bytes."""
        compressed = gzip.compress(b"x" * (MODULE.MAX_BYTES + 1))
        response = FakeResponse(compressed, "text/plain", "https://example.test", "gzip")
        with self.assertRaises(MODULE.CollectionError):
            MODULE.read_bounded(response)

    def test_ignores_malformed_content_length_and_reads_bounded_body(self) -> None:
        """Do not let a malformed upstream header abort fallback verification."""
        response = FakeResponse(b"bounded", "text/plain", "https://example.test")
        response.headers = FakeHeaders("text/plain", content_length="not-a-number")
        self.assertEqual(MODULE.read_bounded(response), b"bounded")

    def test_reads_explicit_gate_body_from_http_error(self) -> None:
        """Recognize bounded login markup returned with an HTTP gate status."""
        source = MODULE.load_catalog(CATALOG)[0]
        body = b'<form><input type="password"><button>Sign in</button></form>'
        error = MODULE.urllib.error.HTTPError(
            source["page_url"],
            403,
            "Forbidden",
            FakeHeaders("text/html", content_length=str(len(body))),
            io.BytesIO(body),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(
            MODULE,
            "robots_policy",
            return_value={"allowed": True, "robots_url": "https://example/robots.txt", "robots_sha256": None},
        ), mock.patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
            fetched = MODULE.fetch_url(source["page_url"], MODULE.source_hosts(source))
        self.assertEqual(fetched["http_status"], 403)
        self.assertEqual(MODULE.detect_access_constraint(fetched["content"]), "login")

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
                MODULE, "fetch_url", return_value={
                    "final_url": candidate,
                    "http_status": 200,
                    "content_type": "text/html",
                    "content": (
                        b'<article><time datetime="2026-08-08"></time>'
                        b'<h2><a href="/article">Public article</a></h2>'
                        b'<a href="/author">Alice</a><a href="/tag">AI</a></article>'
                        b'<nav><a href="/about">About</a></nav>'
                    ),
                }
            ) as fetch:
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            fetch.assert_called_once()
            verified = json.loads(output.read_text(encoding="utf-8"))["resolutions"][0]
            self.assertEqual(verified["status"], "verified_fallback")
            self.assertEqual(verified["final_url"], candidate)
            self.assertEqual(verified["extracted_entry_count"], 4)
            self.assertEqual(verified["candidate_entry_count"], 1)
            self.assertEqual(verified["date_evidence_count"], 1)
            self.assertEqual(verified["published_dates"], ["2026-08-08"])
            self.assertEqual(
                verified["candidate_evidence"][0]["url"],
                MODULE.urllib.parse.urljoin(candidate, "/article"),
            )

    def test_fallback_rejects_partially_dated_article_candidates(self) -> None:
        """Require dates for every sealed article candidate while ignoring navigation."""
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
            content = (
                b'<article><time datetime="2026-08-08"></time>'
                b'<h2><a href="/article">Public article</a></h2></article>'
                b'<article><h2><a href="/article-2">Undated article</a></h2></article>'
                b'<nav><a href="/about">About</a></nav>'
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": content,
            }):
                with self.assertRaisesRegex(MODULE.CollectionError, "complete"):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_specific_article_fallback_seals_only_its_primary_date(self) -> None:
        """Ignore undated related cards when the requested page is itself an article."""
        source = MODULE.load_catalog(CATALOG)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            candidate = MODULE.urllib.parse.urljoin(source["page_url"], "/article")
            manifest.write_text(json.dumps({"sources": [{
                "name": source["name"], "status": "needs_search_fallback"
            }]}), encoding="utf-8")
            request.write_text(json.dumps({"version": 1, "resolutions": [{
                "name": source["name"], "method": "site_search", "url": candidate
            }], "date_evidence": []}), encoding="utf-8")
            content = (
                b'<meta property="article:published_time" content="2026-08-08">'
                b'<article><h2><a href="/related">Undated related story</a></h2></article>'
            )
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": candidate,
                "http_status": 200,
                "content_type": "text/html",
                "content": content,
            }):
                MODULE.verify_resolutions(CATALOG, manifest, request, output)
            verified = json.loads(output.read_text())["resolutions"][0]
            self.assertEqual(verified["candidate_entry_count"], 1)
            self.assertEqual(verified["candidate_evidence"][0]["url"], candidate)
            self.assertEqual(verified["published_dates"], ["2026-08-08"])

    def test_generic_page_date_does_not_collapse_listing_candidates(self) -> None:
        """Do not treat generic category metadata as the listing's publication date."""
        content = (
            b'<meta name="date" content="2026-08-08">'
            b'<article><h2><a href="/undated">Undated story</a></h2></article>'
        )
        self.assertIsNone(
            MODULE.extract_primary_publication_date(
                content, "https://example.test/news"
            )
        )

    def test_json_ld_seals_undated_article_and_merges_same_url_date(self) -> None:
        """Keep undated article-like JSON-LD and merge matching card evidence."""
        base = "https://example.test/"
        undated = b'''<script type="application/ld+json">{"@type":"NewsArticle","headline":"A","url":"/a"}</script>'''
        extracted = MODULE.extract_content(undated, "text/html", base)
        self.assertEqual(extracted["entries"][0]["candidate_provenance"], "json_ld")
        self.assertIsNone(extracted["entries"][0]["published"])

        merged = b'''<script type="application/ld+json">{"@type":"NewsArticle","headline":"A","url":"https://example.test:443/a/"}</script><article><time datetime="2026-08-08"></time><h2><a href="/a">A</a></h2></article>'''
        extracted = MODULE.extract_content(merged, "text/html", base)
        self.assertEqual(len(extracted["entries"]), 1)
        self.assertEqual(extracted["entries"][0]["published"], "2026-08-08")

        distinct_queries = b'''<script type="application/ld+json">[{"@type":"NewsArticle","headline":"A1","url":"/a?id=1","datePublished":"2026-08-08"},{"@type":"NewsArticle","headline":"A2","url":"/a?id=2","datePublished":"2026-08-08"}]</script>'''
        extracted = MODULE.extract_content(distinct_queries, "text/html", base)
        self.assertEqual(len(extracted["entries"]), 2)

    def test_article_without_headline_is_candidate_only_when_unambiguous(self) -> None:
        """Do not guess that an author/tag link is the article permalink."""
        base = "https://example.test/"
        ambiguous = (
            b'<article><time datetime="2026-08-08"></time>'
            b'<a href="/author">Alice</a><a href="/story">Story</a></article>'
        )
        extracted = MODULE.extract_content(ambiguous, "text/html", base)
        self.assertFalse(any(entry.get("candidate_provenance") for entry in extracted["entries"]))

        single = (
            b'<article><time datetime="2026-08-08"></time>'
            b'<a href="/story">Story</a></article>'
        )
        extracted = MODULE.extract_content(single, "text/html", base)
        self.assertEqual(extracted["entries"][0]["candidate_provenance"], "article")

    def test_detects_explicit_login_gate_but_not_generic_forbidden_text(self) -> None:
        """Resolve login exceptions from gate markup, not a bare status/error word."""
        login = b'<form><input type="password"><button>Sign in</button></form>'
        self.assertEqual(MODULE.detect_access_constraint(login), "login")
        self.assertIsNone(MODULE.detect_access_constraint(b"HTTP 403 forbidden"))

    def test_rss_gate_falls_back_to_public_page(self) -> None:
        """Do not classify a whole publisher from a constrained feed endpoint."""
        source = MODULE.load_catalog(CATALOG)[0]
        gate = b'<form><input type="password"><button>Sign in</button></form>'
        page = b'<html><article><a href="/article">Public article</a><time datetime="2026-08-10">Today</time></article></html>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=[
                {"content": gate, "content_type": "text/html", "final_url": source["feed_url"], "http_status": 200},
                {"content": page, "content_type": "text/html", "final_url": source["page_url"], "http_status": 200},
            ]
        ):
            result = MODULE.collect_source(1, source, MODULE.source_hosts(source), Path(temporary))
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["method"], "public_page")

    def test_direct_html_requires_dates_for_every_article_candidate(self) -> None:
        """Send partially dated listings to fallback instead of sealing a subset."""
        source = MODULE.load_catalog(CATALOG)[0]
        page = (
            b'<article><h2><a href="/dated">Dated</a></h2>'
            b'<time datetime="2026-08-10"></time></article>'
            b'<article><h2><a href="/undated">Undated</a></h2></article>'
        )
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                MODULE.CollectionError("feed unavailable"),
                {
                    "content": page,
                    "content_type": "text/html",
                    "final_url": source["page_url"],
                    "http_status": 200,
                },
            ],
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertIn("lacks_publication", result["attempts"][-1]["reason"])

    def test_mixed_constraint_and_failure_requires_fallback(self) -> None:
        """Do not close a publisher when another direct endpoint failed generically."""
        source = MODULE.load_catalog(CATALOG)[0]
        gate = b'<form><input type="password"><button>Sign in</button></form>'
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE,
            "fetch_url",
            side_effect=[
                {"content": gate, "content_type": "text/html", "final_url": source["feed_url"], "http_status": 403},
                MODULE.CollectionError("transient failure"),
            ],
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")

    def test_verified_robots_constraint_requires_all_direct_endpoints(self) -> None:
        """Seal robots evidence only when every direct endpoint is disallowed."""
        source = MODULE.load_catalog(CATALOG)[0]
        digest = "a" * 64
        errors = [
            MODULE.RobotsDisallowed(source["feed_url"], "https://techcrunch.com/robots.txt", digest),
            MODULE.RobotsDisallowed(source["page_url"], "https://techcrunch.com/robots.txt", digest),
        ]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=errors
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "access_constraint")
        self.assertEqual(result["constraint"], "robots")
        self.assertEqual(result["robots_sha256"], digest)
        self.assertTrue(all(item["constraint"] == "robots" for item in result["attempts"]))
        self.assertTrue(all(item["robots_sha256"] == digest for item in result["attempts"]))

    def test_date_evidence_rejects_redirect_to_another_article(self) -> None:
        """Do not assign a redirect target's date to the requested sealed article."""
        source = MODULE.load_catalog(CATALOG)[0]
        requested = source["page_url"] + "article-a"
        redirected = source["page_url"] + "article-b"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            request = root / "request.json"
            output = root / "verified.json"
            manifest.write_text(json.dumps({"sources": []}), encoding="utf-8")
            request.write_text(json.dumps({
                "version": 1,
                "resolutions": [],
                "date_evidence": [{"name": source["name"], "url": requested}],
            }), encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"COLLECTION_OUTPUT_ROOT": str(root)}
            ), mock.patch.object(MODULE, "fetch_url", return_value={
                "final_url": redirected,
                "http_status": 200,
                "content_type": "text/html",
                "content": b'<meta property="article:published_time" content="2026-08-08">',
            }):
                with self.assertRaisesRegex(MODULE.CollectionError, "redirected"):
                    MODULE.verify_resolutions(CATALOG, manifest, request, output)

    def test_empty_direct_extract_requires_fallback(self) -> None:
        """Treat unparseable direct pages as unresolved instead of article-free."""
        source = MODULE.load_catalog(CATALOG)[0]
        empty = {
            "content": b"<html></html>",
            "content_type": "text/html",
            "final_url": source["page_url"],
            "http_status": 200,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            MODULE, "fetch_url", side_effect=[empty, empty]
        ):
            result = MODULE.collect_source(
                1, source, MODULE.source_hosts(source), Path(temporary)
            )
        self.assertEqual(result["status"], "needs_search_fallback")
        self.assertTrue(all(item["reason"] == "empty_extract" for item in result["attempts"]))

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
            f'{{"@type":"NewsArticle","headline":"Story","url":"{article}","datePublished":"2026-08-05"}}'
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
