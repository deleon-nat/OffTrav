"""Load config.yaml and apply OFFTRAV_<SECTION>_<KEY> environment overrides.

Example: setting OFFTRAV_OLLAMA_CHAT_MODEL=llama3.1:8b changes ollama.chat_model without editing
the file. This is how the evaluation and the demo switch settings from the command line.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent      # the project folder (one level above offtrav/)
DEFAULT_CONFIG = ROOT / "config.yaml"


def _coerce(value: str, like):
    """Convert an environment variable (always text) to the type of the value it replaces."""
    if isinstance(like, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    return value


def load_config(path: str | Path | None = None) -> dict:
    """Read config.yaml (or another file) and return it as a dict, with environment overrides applied."""
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = copy.deepcopy(cfg)
    for section, values in cfg.items():                # e.g. section "ollama", key "chat_model"
        if not isinstance(values, dict):
            continue
        for key, current in values.items():
            env = os.environ.get(f"OFFTRAV_{section}_{key}".upper())
            if env is not None:
                values[key] = _coerce(env, current)
    return cfg
