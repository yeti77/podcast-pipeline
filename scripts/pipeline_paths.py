#!/usr/bin/env python3
"""Side-effect-free runtime path resolution for podcast_pipeline."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PipelinePaths:
    pipeline_dir: Path
    config_dir: Path
    outputs_dir: Path
    state_dir: Path
    logs_dir: Path

    @property
    def runs_dir(self) -> Path:
        return self.outputs_dir / "runs"


def _resolved_path(value: object) -> Path:
    return Path(str(value)).expanduser().resolve()


def get_pipeline_paths(env: Optional[Mapping[str, str]] = None) -> PipelinePaths:
    """Resolve paths without creating files or directories."""
    values = os.environ if env is None else env
    root_value = (
        values.get("PODCAST_PIPELINE_HOME")
        or values.get("PIPELINE_DIR")
        or REPOSITORY_ROOT
    )
    pipeline_dir = _resolved_path(root_value)
    config_dir = _resolved_path(
        values.get("PODCAST_PIPELINE_CONFIG_DIR") or pipeline_dir / "config"
    )
    outputs_dir = _resolved_path(
        values.get("PODCAST_PIPELINE_OUTPUT_DIR") or pipeline_dir / "outputs"
    )
    state_dir = _resolved_path(
        values.get("PODCAST_PIPELINE_STATE_DIR") or pipeline_dir / "state"
    )
    logs_dir = _resolved_path(
        values.get("PODCAST_PIPELINE_LOG_DIR") or pipeline_dir / "logs"
    )
    return PipelinePaths(
        pipeline_dir=pipeline_dir,
        config_dir=config_dir,
        outputs_dir=outputs_dir,
        state_dir=state_dir,
        logs_dir=logs_dir,
    )


def ensure_runtime_directories(paths: PipelinePaths) -> None:
    """Create shared writable directories at an explicit runtime boundary."""
    for directory in (paths.outputs_dir, paths.runs_dir, paths.state_dir, paths.logs_dir):
        directory.mkdir(parents=True, exist_ok=True)
