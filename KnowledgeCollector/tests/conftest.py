"""Pytest configuration: point KC_DATA_DIR at a per-test temp dir."""
import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_app(tmp_path, monkeypatch):
    """Redirect the Collector's data dir to a temp directory and init it."""
    data = tmp_path / "KC"
    data.mkdir()
    monkeypatch.setenv("KC_DATA_DIR", str(data))
    # Force re-import of config so db_path picks up the new env var.
    import importlib
    from kc import config
    importlib.reload(config)
    from kc import database
    importlib.reload(database)
    database.init_db()
    yield data
    # Close any connections so the temp DB isn't locked.
    database._connections.clear()
