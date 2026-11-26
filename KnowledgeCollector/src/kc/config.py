"""Configuration for the Knowledge Collector.

All paths resolve relative to the Collector's data directory, which
defaults to a portable ``data/`` folder under the project root. No
network access is needed at runtime except during ``collect``/``resume``.

The Collector's database is separate from the Electronics Diagnostic
Assistant's database; the two never share a SQLite file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


# Defaults that respect the spec's i7-6th/8GB target.
DEFAULTS: Dict[str, Any] = {
    "rate_per_min": 6,           # one request every 10s by default
    "max_workers": 1,             # conservative; the target is CPU+RAM bound
    "max_depth": 5,               # pagination depth
    "max_records_per_source": 5000,
    "max_bytes_per_source": 500 * 1024 * 1024,   # 500 MB
    "retry_max": 3,
    "retry_backoff_base": 1.0,    # seconds
    "retry_backoff_cap": 60.0,
    "user_agent": "KnowledgeCollector/1.0 (+offline electronics repair research; respects robots.txt)",
    "request_timeout": 20.0,
    "enable_ocr": False,          # OCR is opt-in (Tesseract required)
}


def app_dir() -> Path:
    """The Collector's root directory (where this package lives)."""
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Where the Collector stores its DB, originals, extracted text."""
    env = os.environ.get("KC_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return app_dir() / "data"


def db_path() -> Path:
    return data_dir() / "kc.sqlite3"


def originals_dir() -> Path:
    return data_dir() / "originals"


def extracted_dir() -> Path:
    return data_dir() / "extracted"


def export_dir() -> Path:
    return data_dir() / "exports"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def ensure_layout() -> None:
    """Create the data subdirectories if missing."""
    for d in (data_dir(), originals_dir(), extracted_dir(), export_dir()):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Dict[str, Any]:
    """Load settings, falling back to defaults. Missing keys are filled
    from DEFAULTS so a partial settings file still works."""
    s = dict(DEFAULTS)
    p = settings_path()
    if p.exists():
        try:
            s.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return s


def save_settings(s: Dict[str, Any]) -> None:
    ensure_layout()
    settings_path().write_text(json.dumps(s, indent=2, ensure_ascii=False),
                                encoding="utf-8")


def now_iso() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")
