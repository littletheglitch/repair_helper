"""Application configuration and path resolution.

All paths are resolved relative to the application root directory so the
application is fully portable. Nothing here touches the network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


APP_NAME = "ElectronicsDiagnostic"
APP_VERSION = "1.0.0"


def app_root() -> Path:
    """Return the directory that contains the running executable or source.

    When frozen with PyInstaller, ``sys.executable`` lives inside a temporary
    ``_MEIPASS`` for bundled resources; the *distribution* folder is the parent
    of the EXE. When running from source, the parent of ``src/eda`` is used.
    """
    if getattr(os.sys, "frozen", False):  # PyInstaller EXE
        return Path(os.sys.executable).resolve().parent
    # Source checkout: src/eda/config.py -> .../ElectronicsDiagnostic
    return Path(__file__).resolve().parents[2]


def app_dir(name: str) -> Path:
    """Return ``<root>/<name>``, creating it on demand."""
    p = app_root() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_dir("Config") / "eda.sqlite3"


def config_path() -> Path:
    return app_dir("Config") / "settings.json"


DEFAULT_SETTINGS: Dict[str, Any] = {
    "app_name": APP_NAME,
    "app_version": APP_VERSION,
    # Local model runtime configuration. The engine tries these in order.
    "engine": {
        # llama.cpp server or main executable. None means "try to locate".
        "backend": "auto",               # auto | llama_server | llama_cli | cloud | disabled
        "model_file": None,              # e.g. "qwen2.5-1.5b-instruct-q4_k_m.gguf"
        "model_dir": "Models",
        "server_path": None,             # llama-server executable (optional explicit path)
        "cli_path": None,                # llama-cli executable (legacy fallback)
        "n_ctx": 2048,
        "n_threads": 4,
        "n_gpu_layers": 0,               # CPU-only target
        "temperature": 0.2,
        "max_tokens": 512,
        "timeout_seconds": 120,
        "fallback_to_rules": True,       # if model unavailable, use rule engine only
    },
    "documents": {
        "library_dir": "Documents",
        "temp_dir": "Temp",
        "max_extract_chars": 200_000,
        "ocr_enabled": False,            # local OCR is optional and off by default
    },
    "knowledge": {
        "builtin_dir": "Knowledge",
    },
    "ui": {
        "prompt": "repair> ",
        "show_safety_banner": True,
        "page_pause_lines": 24,
    },
    "logging": {
        "log_dir": "Logs",
        "level": "INFO",
    },
}


def load_settings() -> Dict[str, Any]:
    """Load settings from ``Config/settings.json``, filling defaults."""
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    path = config_path()
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            _deep_update(merged, user)
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def save_settings(settings: Dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def _deep_update(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def ensure_layout() -> None:
    """Create every directory the application expects."""
    for name in (
        "Config", "Engine", "Models", "Knowledge", "Documents",
        "Sessions", "Temp", "Reports", "Logs",
    ):
        app_dir(name)
