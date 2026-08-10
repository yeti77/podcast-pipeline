#!/usr/bin/env python3
"""Hermetic tests for portable pipeline path resolution."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


class TestPipelinePaths(unittest.TestCase):
    def test_default_root_is_repository_root(self):
        from pipeline_paths import get_pipeline_paths

        paths = get_pipeline_paths(env={})

        self.assertEqual(paths.pipeline_dir, REPO_ROOT)
        self.assertEqual(paths.config_dir, REPO_ROOT / "config")
        self.assertEqual(paths.outputs_dir, REPO_ROOT / "outputs")
        self.assertEqual(paths.state_dir, REPO_ROOT / "state")
        self.assertEqual(paths.logs_dir, REPO_ROOT / "logs")

    def test_explicit_environment_overrides_derived_directories(self):
        from pipeline_paths import get_pipeline_paths

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = {
                "PODCAST_PIPELINE_HOME": str(base / "home"),
                "PODCAST_PIPELINE_CONFIG_DIR": str(base / "cfg"),
                "PODCAST_PIPELINE_OUTPUT_DIR": str(base / "out"),
                "PODCAST_PIPELINE_STATE_DIR": str(base / "state-data"),
                "PODCAST_PIPELINE_LOG_DIR": str(base / "runtime-logs"),
            }

            paths = get_pipeline_paths(env=env)

            self.assertEqual(paths.pipeline_dir, (base / "home").resolve())
            self.assertEqual(paths.config_dir, (base / "cfg").resolve())
            self.assertEqual(paths.outputs_dir, (base / "out").resolve())
            self.assertEqual(paths.state_dir, (base / "state-data").resolve())
            self.assertEqual(paths.logs_dir, (base / "runtime-logs").resolve())

    def test_legacy_pipeline_dir_override_remains_supported(self):
        from pipeline_paths import get_pipeline_paths

        with tempfile.TemporaryDirectory() as tmp:
            paths = get_pipeline_paths(env={"PIPELINE_DIR": tmp})

            self.assertEqual(paths.pipeline_dir, Path(tmp).resolve())

    def test_path_resolution_is_read_only_until_explicit_setup(self):
        from pipeline_paths import ensure_runtime_directories, get_pipeline_paths

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "new-home"
            paths = get_pipeline_paths(env={"PODCAST_PIPELINE_HOME": str(home)})

            self.assertFalse(home.exists())
            ensure_runtime_directories(paths)
            self.assertTrue(paths.outputs_dir.is_dir())
            self.assertTrue(paths.runs_dir.is_dir())
            self.assertTrue(paths.state_dir.is_dir())
            self.assertTrue(paths.logs_dir.is_dir())

    def test_importing_core_modules_does_not_create_runtime_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "import-probe"
            env = dict(os.environ)
            env["PODCAST_PIPELINE_HOME"] = str(home)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            command = [
                sys.executable,
                "-c",
                "import podcast_screener, guest_background_fetcher, podcast_transcriber",
            ]

            result = subprocess.run(
                command,
                cwd=SCRIPT_DIR,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(home.exists(), f"import created runtime path: {home}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
