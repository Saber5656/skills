#!/usr/bin/env python3
"""Static contract tests for privacy-safe scheduled collection prompts."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class CollectionPromptPrivacyTests(unittest.TestCase):
    """Ensure prompt and PVA instructions agree on artifact privacy."""

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


if __name__ == "__main__":
    unittest.main()
