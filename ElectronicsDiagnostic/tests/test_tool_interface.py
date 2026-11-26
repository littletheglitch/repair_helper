"""Tests for the Tool Calling Interface (Phase 3).

Verifies: catalog builds, tool-call parsing, dispatch via the existing
Tool Router, Quantity coercion, provenance passthrough, structured errors.
"""
import json
import pytest

from eda import schemas as S
from eda.engines import base as E
from eda import tool_interface as TI


# ---- catalog -------------------------------------------------------------

def test_catalog_lists_exposed_tools():
    E.reset_for_tests()
    specs = TI.build_catalog()
    names = [s.name for s in specs]
    # Read/compute engines the LLM may call:
    assert "electrical_math.ohm_voltage" in names
    assert "next_best_test.recommend" in names
    assert "measurement_validation.validate" in names
    # Mutating engines are NOT exposed to the LLM:
    assert "hypotheses.confirm" not in names
    assert "power_sequence.save_sequence" not in names
    assert "topology.add_node" not in names


def test_catalog_prompt_text_lists_every_tool():
    E.reset_for_tests()
    text = TI.catalog_as_prompt_text()
    assert "electrical_math.ohm_voltage" in text
    assert "next_best_test.recommend" in text
    assert "->" in text  # return-shape marker


# ---- dispatch + coercion -------------------------------------------------

def test_dispatch_ohm_voltage_with_quantity_dicts():
    E.reset_for_tests()
    call = TI.ToolCall(
        tool="electrical_math.ohm_voltage",
        args={"I": {"value": 2, "unit": "A"}, "R": {"value": 5, "unit": "Ω"}},
        id="t1")
    r = TI.dispatch(call)
    assert r.ok
    assert r.provenance  # non-empty; the engine stamps its own provenance
    # value is the jsonable Quantity
    assert r.value["magnitude"] == 10.0
    assert r.unit == "V"


def test_dispatch_ohm_voltage_with_string_quantity():
    E.reset_for_tests()
    call = TI.ToolCall(
        tool="electrical_math.ohm_voltage",
        args={"I": "2A", "R": "5Ω"}, id="t2")
    r = TI.dispatch(call)
    assert r.ok
    assert r.value["magnitude"] == 10.0


def test_dispatch_missing_required_arg_returns_structured_error():
    E.reset_for_tests()
    call = TI.ToolCall(
        tool="electrical_math.ohm_voltage",
        args={"I": {"value": 2, "unit": "A"}}, id="t3")  # missing R
    r = TI.dispatch(call)
    assert not r.ok
    assert r.error["code"] == "missing_input"
    assert r.error["field"] == "R"


def test_dispatch_bad_tool_name_returns_error():
    call = TI.ToolCall(tool="not_a_real_tool", args={}, id="t4")
    r = TI.dispatch(call)
    assert not r.ok
    assert r.error["code"] == "invalid_input"


def test_dispatch_unknown_method_returns_error():
    E.reset_for_tests()
    call = TI.ToolCall(tool="electrical_math.does_not_exist", args={}, id="t5")
    r = TI.dispatch(call)
    assert not r.ok
    # engine exists, method doesn't -> not_applicable
    assert r.error["code"] in ("not_applicable", "invalid_input")


def test_dispatch_carries_provenance():
    E.reset_for_tests()
    call = TI.ToolCall(
        tool="electrical_math.ohm_voltage",
        args={"I": {"value": 1, "unit": "A"}, "R": {"value": 10, "unit": "Ω"}},
        id="t6")
    r = TI.dispatch(call)
    assert r.ok
    assert r.provenance  # non-empty; the engine stamps its real provenance
    # The provenance is the engine's descriptive string OR the dispatch fallback.
    assert isinstance(r.provenance, str) and len(r.provenance) > 10


# ---- tool-call parsing ---------------------------------------------------

def test_parse_tool_calls_fenced_block():
    text = ('Here is my call:\n```tool_call\n'
            '{"tool": "electrical_math.ohm_voltage", '
            '"args": {"I": {"value": 2, "unit": "A"}, '
            '"R": {"value": 5, "unit": "Ω"}}, "id": "tc1"}\n```')
    calls = TI.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].tool == "electrical_math.ohm_voltage"
    assert calls[0].id == "tc1"
    assert calls[0].args["I"]["value"] == 2


def test_parse_tool_calls_calls_list():
    text = ('```json\n{"calls": [\n'
            '{"tool": "electrical_math.ohm_voltage", "args": {}, "id": "a"},\n'
            '{"tool": "next_best_test.recommend", "args": {}, "id": "b"}\n'
            "]}\n```")
    calls = TI.parse_tool_calls(text)
    assert len(calls) == 2
    assert {c.tool for c in calls} == {"electrical_math.ohm_voltage",
                                       "next_best_test.recommend"}


def test_parse_tool_calls_no_block_returns_empty():
    calls = TI.parse_tool_calls("just plain text, no tool call here")
    assert calls == []


def test_parse_tool_calls_bare_json():
    text = '{"tool": "electrical_math.ohm_voltage", "args": {}, "id": "x"}'
    calls = TI.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].tool == "electrical_math.ohm_voltage"
