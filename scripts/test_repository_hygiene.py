#!/usr/bin/env python3
"""Repository-level checks for a secret-safe, portable GitHub baseline."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()


def tracked_files():
    output = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [line for line in output.splitlines() if line]


class TestRepositoryHygiene(unittest.TestCase):
    def test_public_repository_metadata_exists(self):
        required = [
            "LICENSE",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
            "CHANGELOG.md",
            "docs/transcription.md",
            "requirements.txt",
            "requirements-transcription.txt",
            ".env.example",
            ".github/workflows/ci.yml",
            "config/feishu_config.example.json",
            "config/feishu_folder_mapping.example.json",
            "config/policy.local.example.yaml",
        ]

        missing = [name for name in required if not (ROOT / name).is_file()]
        self.assertEqual(missing, [])

    def test_runtime_and_secret_paths_are_ignored(self):
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        required_patterns = {
            ".env",
            "cache/",
            "chunks/",
            "outputs/",
            "state/",
            "config/feishu_config.json",
            "config/feishu_folder_mapping.json",
            "config/policy.local.yaml",
        }

        self.assertFalse(required_patterns - set(ignore_text.splitlines()))

    def test_sensitive_and_runtime_files_are_not_tracked(self):
        tracked = set(tracked_files())
        forbidden = {
            ".env",
            "config/feishu_config.json",
            "config/feishu_folder_mapping.json",
            "config/policy.local.yaml",
        }
        forbidden_prefixes = ("outputs/", "state/", "cache/", "chunks/", "logs/")

        self.assertFalse(forbidden & tracked)
        self.assertFalse([name for name in tracked if name.startswith(forbidden_prefixes)])

    def test_tracked_text_has_no_private_keys_or_user_absolute_paths(self):
        violations = []
        private_key_marker = re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        )
        user_path = re.compile(r"/(?:Users|home)/[^/\s]+/")
        for name in tracked_files():
            if name == SELF:
                continue
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if private_key_marker.search(text) or user_path.search(text):
                violations.append(name)

        self.assertEqual(violations, [])

    def test_production_scripts_have_no_legacy_fixed_home_or_node_version(self):
        violations = []
        patterns = (
            'expanduser("~/podcast_pipeline")',
            "$HOME/podcast_pipeline",
            ".nvm/versions/node/v24.14.0/bin",
        )
        for name in tracked_files():
            if not name.startswith("scripts/"):
                continue
            path = ROOT / name
            if not path.is_file() or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(pattern in text for pattern in patterns):
                violations.append(path.name)

        self.assertEqual(violations, [])

    def test_secret_examples_contain_no_values(self):
        feishu = json.loads(
            (ROOT / "config" / "feishu_config.example.json").read_text(encoding="utf-8")
        )

        self.assertEqual(feishu, {"app_id": "", "app_secret": "", "webhook_url": ""})

    def test_readme_schema_and_ci_describe_current_safe_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        schema = (ROOT / "data_schema.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("Quick Start", readme)
        self.assertIn("0-100", schema)
        self.assertIn("runtime_metadata", schema)
        self.assertIn("show_notes_display_metadata", schema)
        self.assertIn("show_notes_display_snapshot", schema)
        self.assertIn("show_notes_translation_summary", schema)
        self.assertIn("scripts/run_safe_regression.py", workflow)
        self.assertNotIn("python3 scripts/podcast_screener.py", workflow)
        self.assertNotIn("openclaw agent", workflow)

    def test_transcription_guide_documents_local_only_optional_contract(self):
        guide = (ROOT / "docs" / "transcription.md").read_text(encoding="utf-8")

        required_terms = (
            "python3 scripts/podcast_transcriber.py --check",
            "--audio",
            "--output-dir",
            "requirements-transcription.txt",
            "ffmpeg",
            "OpenClaw",
            "本项目不下载音频",
        )
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, guide)


if __name__ == "__main__":
    unittest.main(verbosity=2)
