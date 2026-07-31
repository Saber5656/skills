#!/usr/bin/env python3
"""Regression tests for descriptor-relative summary persistence."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "save_summary.py"
SPEC = importlib.util.spec_from_file_location("save_summary", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SaveSummaryTests(unittest.TestCase):
    """Exercise success, collision, validation, and cleanup behavior."""

    def setUp(self) -> None:
        """Create an isolated output root and source file."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.content = self.root / "content.md"
        self.content.write_text("# current run\n", encoding="utf-8")
        self.archive = self.root / "archive"
        self.archive.mkdir()
        self.started = "2026-07-31T04:00:00+09:00"

    def tearDown(self) -> None:
        """Remove the isolated fixture."""
        self.temp_dir.cleanup()

    def test_save_and_collision_suffix(self) -> None:
        """Create the first summary and a non-overwriting -2 collision."""
        first = MODULE.save_summary(
            self.archive, "2026-07-31", self.content, self.started
        )
        second = MODULE.save_summary(
            self.archive, "2026-07-31", self.content, self.started
        )
        self.assertTrue(str(first["summary_path"]).endswith("2026-07-31.md"))
        self.assertTrue(str(second["summary_path"]).endswith("2026-07-31-2.md"))
        self.assertRegex(str(first["collection_completed_at"]), r"\+09:00$")

    def test_rejects_invalid_started_at(self) -> None:
        """Reject malformed, impossible, non-JST, and wrong-day timestamps."""
        invalid_values = (
            "2026-07-31 04:00:00",
            "2026-07-31T99:99:99+09:00",
            "2026-07-31T04:00:00+08:00",
            "2026-07-30T04:00:00+09:00",
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), self.assertRaises(MODULE.SaveError):
                MODULE.save_summary(
                    self.archive,
                    "2026-07-31",
                    self.content,
                    invalid,
                )

    def test_rejects_symlink_component(self) -> None:
        """Reject an existing date component that is a symlink."""
        archive = self.root / "escape-archive"
        outside = self.root / "outside"
        (archive / "2026" / "07").mkdir(parents=True)
        outside.mkdir()
        (archive / "2026" / "07" / "31").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(MODULE.SaveError):
            MODULE.save_summary(archive, "2026-07-31", self.content, self.started)
        self.assertEqual(list(outside.iterdir()), [])

    def test_removes_partial_target_after_copy_failure(self) -> None:
        """Delete an incomplete target and stop instead of retrying suffixes."""
        with mock.patch.object(
            MODULE, "copy_stream", side_effect=OSError("fixture read failure")
        ):
            with self.assertRaises(MODULE.SaveError):
                MODULE.save_summary(
                    self.archive, "2026-07-31", self.content, self.started
                )
        output_dir = self.archive / "2026" / "07" / "31"
        self.assertEqual(list(output_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
