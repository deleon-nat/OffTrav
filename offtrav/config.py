"""Load config.yaml and apply OFFTRAV_<SECTION>_<KEY> environment overrides."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


def _coerce(value: str, like):
    if isinstance(like, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    return value


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = copy.deepcopy(cfg)
    for section, values in cfg.items():
        if not isinstance(values, dict):
            continue
        for key, current in values.items():
            env = os.environ.get(f"OFFTRAV_{section}_{key}".upper())
            if env is not None:
                values[key] = _coerce(env, current)
    return cfg
