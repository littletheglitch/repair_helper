"""Tests for the measurement validation engine."""
import pytest

from eda.engines import base as E
from eda.engines import measurement_validation as MV
from eda import schemas as S
from eda import sessions, measurements as M
from eda import database


def q(num, unit):
    return S.Quantity.parse(num, unit)


def test_validate_normal_within_range():
    r = E.call("measurement_validation", "validate",
               {"name": "+3VALW", "value": q(3.3, "V"),
                "range": {"lo": 3.2, "hi": 3.4, "source": "verified range"},
                "operating_condition": "on adapter"})
    assert r.ok
    v = r.value
    assert v.verdict == MV.VERDICT_NORMAL
    assert any(f.code == MV.VERDICT_NORMAL for f in v.findings)


def test_validate_low_below_range():
    r = E.call("measurement_validation", "validate",
               {"name": "+5VALW", "value": q(0, "V"),
                "range": {"lo": 4.8, "hi": 5.2, "source": "verified range"},
                "operating_condition": "on adapter"})
    assert r.ok
    assert r.value.verdict == MV.VERDICT_LOW


def test_validate_high_above_range():
    r = E.call("measurement_validation", "validate",
               {"name": "+3VALW", "value": q(3.8, "V"),
                "range": {"lo": 3.2, "hi": 3.4},
                "operating_condition": "on adapter"})
    assert r.ok
    assert r.value.verdict == MV.VERDICT_HIGH


def test_validate_no_range_is_insufficient_context():
    r = E.call("measurement_validation", "validate",
               {"name": "VIN", "value": q(19, "V"),
                "operating_condition": "on adapter"})
    assert r.ok
    assert r.value.verdict == MV.VERDICT_INSUFFICIENT_CONTEXT
    assert any(f.code == MV.VERDICT_INSUFFICIENT_CONTEXT for f in r.value.findings)


def test_validate_missing_operating_condition_is_flagged():
    r = E.call("measurement_validation", "validate",
               {"name": "+3VALW", "value": q(3.3, "V"),
                "range": {"lo": 3.2, "hi": 3.4}})
    # Within range, but operating condition missing -> finding present.
    assert r.ok
    assert any(f.code == MV.VERDICT_INSUFFICIENT_CONTEXT for f in r.value.findings)


def test_validate_missing_value_is_error():
    r = E.call("measurement_validation", "validate",
               {"name": "+3VALW", "range": {"lo": 3.2, "hi": 3.4}})
    assert not r.ok
    assert r.error.code == S.ErrorCode.MISSING_INPUT
    assert r.error.field == "value"


def test_validate_invalid_range_is_error():
    r = E.call("measurement_validation", "validate",
               {"name": "+3VALW", "value": q(3.3, "V"),
                "range": {"lo": "abc", "hi": 3.4}})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT
    assert r.error.field == "range"


# ---- compare ---------------------------------------------------------

def test_compare_same_condition_agree():
    r = E.call("measurement_validation", "compare", {
        "a": {"value": q(3.3, "V"), "operating_condition": "on adapter"},
        "b": {"value": q(3.31, "V"), "operating_condition": "on adapter"},
        "tolerance": 0.01,
    })
    assert r.ok
    assert r.value.verdict == MV.VERDICT_NORMAL


def test_compare_same_condition_contradict():
    r = E.call("measurement_validation", "compare", {
        "a": {"value": q(3.3, "V"), "operating_condition": "on adapter"},
        "b": {"value": q(0, "V"), "operating_condition": "on adapter"},
        "tolerance": 0.05,
    })
    assert r.ok
    assert r.value.verdict == MV.VERDICT_CONTRADICTED


def test_compare_different_conditions_not_comparable():
    r = E.call("measurement_validation", "compare", {
        "a": {"value": q(19, "V"), "operating_condition": "on adapter"},
        "b": {"value": q(0, "V"), "operating_condition": "on battery"},
    })
    assert r.ok
    assert r.value.verdict == MV.VERDICT_INSUFFICIENT_CONTEXT
    assert any("different conditions" in f.message for f in r.value.findings)


def test_compare_dimensional_mismatch():
    r = E.call("measurement_validation", "compare", {
        "a": {"value": q(3.3, "V")},
        "b": {"value": q(2, "A")},
    })
    assert not r.ok
    assert r.error.code == S.ErrorCode.DIMENSIONAL


# ---- session_review (needs a session) -------------------------------

def test_session_review_no_measurements(tmp_app):
    s = sessions.create("S", "")
    r = E.call("measurement_validation", "session_review",
               {"session_id": s.id})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE


def test_session_review_flags_missing_context(tmp_app):
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19.4V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    r = E.call("measurement_validation", "session_review",
               {"session_id": s.id})
    assert r.ok
    # adapter_state and reference missing -> at least one finding.
    assert r.value.findings


def test_session_review_detects_contradiction(tmp_app):
    s = sessions.create("S", "")
    m1 = M.parse_line("measure +3VALW = 3.3V")
    m1["adapter_state"] = "on adapter"
    M.add(s.id, m1)
    # Add a second measurement of +3VALW under the SAME conditions but a
    # very different value -> a real contradiction.
    m2 = M.parse_line("measure +3VALW = 0V")
    m2["adapter_state"] = "on adapter"
    M.add(s.id, m2)
    r = E.call("measurement_validation", "session_review",
               {"session_id": s.id})
    assert r.ok
    assert any(f.code == MV.VERDICT_CONTRADICTED for f in r.value.findings)


def test_session_review_result_serializable(tmp_app):
    s = sessions.create("S", "")
    M.add(s.id, M.parse_line("measure VIN = 19.4V"))
    r = E.call("measurement_validation", "session_review",
               {"session_id": s.id})
    import json
    d = r.to_dict()
    json.dumps(d)
