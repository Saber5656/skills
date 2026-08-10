#!/usr/bin/env python3
"""Static contract tests for privacy-safe scheduled collection prompts."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class CollectionPromptPrivacyTests(unittest.TestCase):
    """Ensure prompt and PVA instructions agree on artifact privacy."""

    @staticmethod
    def _flat(content: str) -> str:
        return " ".join(content.split())

    def test_collection_prompt_requires_formatter_and_forbids_staging_path(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn("format-summary-reference.py", prompt)
        self.assertIn("must not contain", prompt)
        self.assertIn("absolute staging path", prompt)

    def test_pva_report_contract_uses_basename_and_digest(self) -> None:
        skill = (ROOT / "personal-vulnerability-advisor/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "入力ニュース: {summary basename} (same-run SHA-256: {sha256})",
            skill,
        )
        self.assertIn("format-summary-reference.py", skill)

    def test_collection_instructions_share_exact_jst_calendar_window(self) -> None:
        files = [
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md",
            ROOT / "personal-vulnerability-advisor/SKILL.md",
            ROOT / "summarize-it-news/SKILL.md",
        ]
        for path in files:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn("run_date - 6 days", content)
                self.assertIn("run_date - 7 days", content)
                self.assertIn("JST", content)

    def test_complete_collection_state_mapping_is_unambiguous(self) -> None:
        files = [
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md",
            ROOT / "personal-vulnerability-advisor/SKILL.md",
        ]
        for path in files:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn("daily_pipeline_status", content)
                self.assertIn("vault_artifacts_complete", content)
                self.assertIn("next_action", content)
                self.assertIn("publisher", content)
                self.assertIn("handoff", content)

    def test_undated_html_candidates_require_fallback(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        skill = (ROOT / "summarize-it-news/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("zero publication-date evidence is unresolved", prompt)
        self.assertIn("must not be reported as", prompt)
        self.assertIn("date_evidence_count=0", skill)
        self.assertIn("対象期間記事なし", skill)
        for forbidden_resolution in ("home", "category", "archive", "listing"):
            self.assertIn(forbidden_resolution, prompt)
            self.assertIn(forbidden_resolution, skill)
        self.assertIn("specific official article page", prompt)
        self.assertIn("具体的な公式記事ページ", skill)

    def test_direct_counts_are_copied_from_trusted_manifest(self) -> None:
        """Keep routine audit counts deterministic instead of model-recounted."""
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        skill = (ROOT / "summarize-it-news/SKILL.md").read_text(encoding="utf-8")
        for content in (prompt, skill):
            self.assertIn("jst_window_item_count", content)
        self.assertIn("copy the trusted", prompt)
        self.assertIn("再集計・上書きしない", skill)

    def test_approved_publication_has_no_excluded_path_placeholders(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.review.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("both `excluded_paths` and", prompt)
        self.assertIn("must be empty arrays", prompt)
        self.assertIn("`.obsidian/`", prompt)
        self.assertIn("return `blocked`", flat)

    def test_resolution_request_is_limited_to_unresolved_sources(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        flat = self._flat(prompt)
        self.assertIn("status is exactly `needs_search_fallback`", prompt)
        self.assertIn("Never add a redundant resolution", flat)
        self.assertIn("direct manifest `access_constraint`", prompt)

    def test_audit_method_is_one_exact_manifest_mapping(self) -> None:
        prompt = (
            ROOT / "vault-change-publisher/assets/daily-it-news.collect.prompt.md"
        ).read_text(encoding="utf-8")
        for mapping in (
            "`rss` -> `RSS`",
            "`public_page` ->",
            "`site_search` -> `サイト限定検索`",
            "`official_alternate` -> `公式代替URL`",
        ):
            self.assertIn(mapping, prompt)
        self.assertIn("do not write `RSS / 公開ページ`", prompt)
        self.assertIn("exact sealed constraint in `理由`", prompt)
        self.assertIn("Never describe a sealed `robots` constraint as `購読`", prompt)


if __name__ == "__main__":
    unittest.main()
