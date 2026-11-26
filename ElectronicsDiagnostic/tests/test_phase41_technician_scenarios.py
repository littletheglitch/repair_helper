"""Phase 4.1 — validate the 24 real technician scenarios.

The spec's section 5:
  * >= 20 real technician scenarios (we have 24).
  * Include measurement, datasheet, component behavior, troubleshooting.
  * The reference answer has reasoning AND a valid source.
  * Synthetic scenarios are kept SEPARATE from confirmed repairs.
"""
import pytest

from tests.phase41_technician_scenarios import SCENARIOS


def test_at_least_20_scenarios():
    assert len(SCENARIOS) >= 20, f"need >= 20 scenarios, have {len(SCENARIOS)}"
    assert len(SCENARIOS) == 24


def test_all_categories_present():
    cats = {s["category"] for s in SCENARIOS}
    assert "measurement" in cats
    assert "datasheet" in cats
    assert "component" in cats
    assert "troubleshooting" in cats


@pytest.mark.parametrize("s", SCENARIOS)
def test_every_scenario_has_reference_and_source(s):
    """Every scenario must have a reference answer AND a valid source."""
    assert s["reference_answer"], f"scenario {s['scenario_id']} has no reference answer"
    assert s["source"], f"scenario {s['scenario_id']} has no source"
    assert s["source"] in ("measurement", "datasheet",
                            "confirmed_case", "textbook"), \
        f"scenario {s['scenario_id']} has invalid source {s['source']!r}"
    assert s["source_ref"], f"scenario {s['scenario_id']} has no source_ref"
    # The reference answer must contain REASONING (not just a number).
    assert len(s["reference_answer"]) > 80, (
        f"scenario {s['scenario_id']} reference answer too short to "
        f"contain reasoning")


@pytest.mark.parametrize("s", SCENARIOS)
def test_synthetic_separate_from_confirmed(s):
    """Synthetic scenarios must be marked synthetic=True; confirmed-repair
    scenarios must be marked synthetic=False and cite a confirmed_case.
    They must not be mixed."""
    if s["source"] == "confirmed_case":
        assert s["synthetic"] is False, (
            f"scenario {s['scenario_id']} cites a confirmed case but is "
            f"marked synthetic — confirmed and synthetic must not mix")
    if s["synthetic"] is True:
        assert s["source"] != "confirmed_case", (
            f"scenario {s['scenario_id']} is synthetic but cites a "
            f"confirmed case")


def test_scenario_count_by_category():
    """Distribution across categories (real coverage)."""
    from collections import Counter
    c = Counter(s["category"] for s in SCENARIOS)
    assert c["measurement"] >= 8
    assert c["datasheet"] >= 6
    assert c["component"] >= 6
    assert c["troubleshooting"] >= 4


def test_synthetic_and_confirmed_both_present():
    """Both synthetic and confirmed-repair scenarios are present, kept
    separate."""
    synth = [s for s in SCENARIOS if s["synthetic"]]
    confirmed = [s for s in SCENARIOS if s["source"] == "confirmed_case"]
    assert len(synth) >= 5, "need at least 5 synthetic scenarios"
    assert len(confirmed) >= 4, "need at least 4 confirmed-repair scenarios"
    # No overlap: a scenario is either synthetic OR confirmed, never both.
    assert not (set(s["scenario_id"] for s in synth) &
                set(s["scenario_id"] for s in confirmed))


@pytest.mark.parametrize("s", SCENARIOS)
def test_scenario_reference_has_reasoning_keywords(s):
    """The reference answer should contain reasoning connectives or
    diagnostic verbs (so/because/therefore/if/measure/suggests/shows)
    — not just a bare number or assertion."""
    low = s["reference_answer"].lower()
    reasoning_words = ("so", "because", "therefore", "if", "measure",
                       "indicates", "means", "confirms", "thus",
                       "suggest", "would", "shows", "reads", "is a",
                       "is the", "use", "select", "the fix")
    assert any(w in low for w in reasoning_words), (
        f"scenario {s['scenario_id']} reference lacks reasoning connectives")
