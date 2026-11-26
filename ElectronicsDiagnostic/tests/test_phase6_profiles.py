"""Phase 6, Task 2 — resource profile tests.

Verifies:
  * The three profiles exist with documented assumptions.
  * apply_profile writes the engine values and records changes.
  * current_profile_name detects the active profile (and 'custom').
  * Applying a profile does NOT silently increase context/threads beyond
    the profile's own values (it is a set, not an upward merge).
  * The CLI `model-profile` command shows profiles and applies one.
  * No profile claims optimality; each states an expected RAM ASSUMPTION.
"""
from __future__ import annotations

import pytest

from eda import config
from eda import profiles as PF


def test_three_profiles_exist(tmp_app):
    names = PF.list_profiles()
    assert set(names) == {"low_memory", "balanced", "diagnostic"}
    for n in names:
        p = PF.get_profile(n)
        assert p is not None
        # Each profile documents its RAM assumption (not a measurement).
        assert "ram_headroom_gb" in p
        assert isinstance(p["ram_headroom_gb"], (int, float))
        assert p["description"]  # non-empty
        # Each profile sets the core knobs.
        e = p["engine"]
        for k in ("n_ctx", "n_threads", "n_batch", "n_gpu_layers"):
            assert k in e


def test_balanced_is_the_safe_default(tmp_app):
    """balanced is the recommended starting point: n_ctx=2048, 4
    threads, batch=512, CPU-only."""
    p = PF.get_profile("balanced")
    e = p["engine"]
    assert e["n_ctx"] == 2048
    assert e["n_threads"] == 4
    assert e["n_batch"] == 512
    assert e["n_gpu_layers"] == 0  # CPU-only target


def test_low_memory_smaller_than_balanced(tmp_app):
    """low_memory has a smaller context and fewer threads than balanced
    — it is the conservative envelope."""
    lo = PF.get_profile("low_memory")["engine"]
    bal = PF.get_profile("balanced")["engine"]
    assert lo["n_ctx"] < bal["n_ctx"]
    assert lo["n_threads"] <= bal["n_threads"]
    assert lo["n_batch"] <= bal["n_batch"]


def test_diagnostic_has_longest_context(tmp_app):
    """diagnostic has the longest context (for long multi-step sessions)
    at the cost of more RAM."""
    diag = PF.get_profile("diagnostic")["engine"]
    bal = PF.get_profile("balanced")["engine"]
    assert diag["n_ctx"] > bal["n_ctx"]


def test_apply_profile_writes_engine_values(tmp_app):
    """apply_profile writes the profile's values to settings.json."""
    res = PF.apply_profile("low_memory")
    assert res["ok"]
    s = config.load_settings()
    eng = s["engine"]
    assert eng["n_ctx"] == 1024
    assert eng["n_threads"] == 2
    assert eng["profile"] == "low_memory"


def test_apply_profile_records_changes(tmp_app):
    """apply_profile returns a 'changed' dict showing old -> new."""
    PF.apply_profile("balanced")
    res = PF.apply_profile("low_memory")
    assert res["ok"]
    changed = res["changed"]
    # n_ctx changed from 2048 -> 1024.
    assert "n_ctx" in changed
    assert changed["n_ctx"] == (2048, 1024)


def test_apply_profile_no_change_when_already_active(tmp_app):
    """Applying the already-active profile changes nothing."""
    PF.apply_profile("balanced")
    res = PF.apply_profile("balanced")
    assert res["ok"]
    assert res["changed"] == {}


def test_current_profile_name_detection(tmp_app):
    """current_profile_name returns the active profile, or None for custom."""
    PF.apply_profile("diagnostic")
    assert PF.current_profile_name() == "diagnostic"
    # Tweak one value -> no longer matches a profile exactly.
    config.load_settings()
    s = config.load_settings()
    s["engine"]["n_ctx"] = 3072  # not any profile's value
    config.save_settings(s)
    assert PF.current_profile_name() is None


def test_apply_profile_does_not_silently_increase_beyond_profile(tmp_app):
    """Applying low_memory when balanced is active must REDUCE n_ctx,
    not keep the larger balanced value. (It is a set, not an upward merge.)"""
    PF.apply_profile("balanced")
    assert config.load_settings()["engine"]["n_ctx"] == 2048
    PF.apply_profile("low_memory")
    assert config.load_settings()["engine"]["n_ctx"] == 1024


def test_unknown_profile_rejected(tmp_app):
    res = PF.apply_profile("nonexistent")
    assert not res["ok"]
    assert "available" in res


def test_cli_model_profile_shows_report(tmp_app):
    """`model-profile` with no args shows the profile report."""
    import eda.cli as cli
    out = cli.dispatch("model-profile")
    assert "low_memory" in out
    assert "balanced" in out
    assert "diagnostic" in out
    # The report states the assumptions are not measurements.
    assert "optimal" in out.lower() or "assumption" in out.lower() or "confirm" in out.lower()


def test_cli_model_profile_applies(tmp_app):
    """`model-profile balanced` applies the balanced profile."""
    import eda.cli as cli
    out = cli.dispatch("model-profile balanced")
    assert "Applied profile 'balanced'" in out
    s = config.load_settings()
    assert s["engine"]["n_ctx"] == 2048


def test_cli_model_profile_unknown_rejected(tmp_app):
    """`model-profile bogus` rejects with the available list."""
    import eda.cli as cli
    out = cli.dispatch("model-profile bogus")
    assert "Unknown profile" in out
    assert "balanced" in out


def test_profiles_do_not_claim_optimality(tmp_app):
    """No profile description contains the word 'optimal'."""
    for n in PF.list_profiles():
        p = PF.get_profile(n)
        assert "optimal" not in p["description"].lower()
