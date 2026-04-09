"""Shared config loader for sherpa-voice (XDG-compliant, JSON format)."""
import json
import os
from pathlib import Path

CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "sherpa-voice"
    / "config.json"
)

DEFAULTS = {
    "voice":   "piper-lessac",
    "speaker": 0,
    "speed":   1.0,
    "port":    45678,
}


def load() -> dict:
    """Return merged config (file values override defaults)."""
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(CONFIG_PATH.read_text())}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
