from __future__ import annotations

import unittest
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]


class PublicationHygieneTest(unittest.TestCase):
    def assert_ignored(self, path: str) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(0, result.returncode, path)

    def assert_publishable(self, path: str) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(1, result.returncode, path)

    def test_private_and_machine_local_files_are_ignored(self) -> None:
        for path in {
            ".env",
            ".env.production",
            "secrets.json",
            "secrets.local.json",
            "credentials.private.pem",
            ".credentials/signing.key",
            "skill/config.local.json",
            "skill/config.private.yaml",
            ".claude/settings.json",
            ".codex/config.toml",
            ".system/openai-docs/SKILL.md",
            ".workspace/eval/result.json",
            "skill/__pycache__/module.cpython-314.pyc",
        }:
            with self.subTest(path=path):
                self.assert_ignored(path)

    def test_public_examples_and_fixtures_remain_publishable(self) -> None:
        for path in {
            ".env.example",
            "secrets.example.json",
            "example.pem",
            "skill/tests/fixtures/test.key",
            "skill/references/public-key.pem",
            "skill/config.example.yaml",
        }:
            with self.subTest(path=path):
                self.assert_publishable(path)

    def test_readme_uses_the_new_flat_skill_root(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("$HOME/dev/skills", readme)
        self.assertIn("Each skill lives directly under the repository root", readme)
        self.assertNotIn("$HOME/" + "skills-repo", readme)
        self.assertNotIn("skills/<skill-name>", readme)


if __name__ == "__main__":
    unittest.main()
