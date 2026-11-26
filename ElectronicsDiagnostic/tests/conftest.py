"""Shared pytest fixtures.

Each test runs against an isolated temp ``app_root`` so the real
project database and documents are never touched. We monkeypatch
``eda.config.app_root`` and reset the module-level DB connection pool
so connections point at the temp dir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from eda import config, database, knowledge_base, sessions, measurements
from eda import documents, retrieval, reasoning, reporting, llm_engine


@pytest.fixture()
def tmp_app(tmp_path, monkeypatch):
    """Point all eda paths at a temp dir and reinit the DB."""
    root = tmp_path / "EDA"
    (root / "Knowledge").mkdir(parents=True)
    # Copy the real knowledge JSON into the temp Knowledge dir so the
    # knowledge loader has content to load.
    import shutil
    real_kb = ROOT / "Knowledge"
    for f in real_kb.glob("*.json"):
        shutil.copy2(f, root / "Knowledge" / f.name)
    monkeypatch.setattr(config, "app_root", lambda: root)
    # Reset the connection pool so new connections use the temp DB path.
    database._connections.clear()
    database.init_db()
    # Reset any module-level session holders in the CLI so tests don't
    # carry a session id from a previous test into the temp DB.
    import eda.cli as cli_mod
    cli_mod.Session.current = None
    # Phase 3/4: reset the module-level LLM backend and the learning
    # subsystem CLI state so a profile_id/exercise_id from a previous
    # test's temp DB does not leak into this test's temp DB (which
    # would trip FK constraints or "not found" errors).
    try:
        import eda.llm_backend as _lb
        _lb.shutdown()
    except Exception:
        pass
    cli_mod._learning_session = None
    cli_mod._current_exercise_id = None
    yield root
    database._connections.clear()
