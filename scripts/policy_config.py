#!/usr/bin/env python3
"""Load tracked policy defaults with optional local operator overrides."""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import yaml


def deep_merge_policy(base: Mapping, override: Mapping) -> dict:
    """Return a recursive merge without mutating either input mapping."""
    merged = deepcopy(dict(base))
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, Mapping) and isinstance(override_value, Mapping):
            merged[key] = deep_merge_policy(base_value, override_value)
        else:
            merged[key] = deepcopy(override_value)
    return merged


def _load_yaml_mapping(path: Path) -> dict:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def load_policy_config(base_path: object, *, local_path: object = None) -> dict:
    """Load a public policy and recursively apply its optional local sibling."""
    base = Path(base_path)
    local = (
        Path(local_path)
        if local_path is not None
        else base.with_name(f"{base.stem}.local{base.suffix}")
    )
    return deep_merge_policy(
        _load_yaml_mapping(base),
        _load_yaml_mapping(local),
    )
