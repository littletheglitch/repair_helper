"""Tests for the Tool Router (engine registry) and Engine base class."""
import pytest

from eda.engines import base as E
from eda import schemas as S


def test_registry_lists_registered_engines():
    names = E.available()
    # The stub plus the future engines are registered.
    assert "electrical_math" in names
    # Future engines registered (their modules don't exist yet, but
    # `available()` only reads the registry table, not the imports).
    assert "spice" in names
    assert "hypotheses" in names


def test_call_known_engine_known_method():
    E.reset_for_tests()
    r = E.call("electrical_math", "ohm_voltage",
               {"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")})
    assert r.ok
    assert r.engine == "electrical_math"
    assert r.value.magnitude == 10.0


def test_call_unknown_engine():
    r = E.call("nonexistent_engine", "m", {})
    assert not r.ok
    assert r.error.code == S.ErrorCode.UNAVAILABLE


def test_call_unknown_method():
    E.reset_for_tests()
    r = E.call("electrical_math", "does_not_exist", {})
    assert not r.ok
    assert r.error.code == S.ErrorCode.NOT_APPLICABLE
    assert "does_not_exist" in r.error.message


def test_call_missing_required_input():
    # Build an engine with a required input and register it for this test.
    class Demo(E.Engine):
        name = "demo"
        version = "1.0.0"
        methods = {"add": E.MethodSpec("add", "add two numbers",
                                       {"a": ("number", True), "b": ("number", True)},
                                       "number")}
        def run(self, method, inputs):
            if method == "add":
                return self._ok("add", value=inputs["a"] + inputs["b"], inputs=inputs)
            return self._err(method, S.ErrorCode.NOT_APPLICABLE, "?")

    E._REGISTRY["demo"] = ("__test__", "Demo")
    E._LOADED["demo"] = Demo()
    r = E.call("demo", "add", {"a": 2})  # missing b
    assert not r.ok
    assert r.error.code == S.ErrorCode.MISSING_INPUT
    assert r.error.field == "b"


def test_call_wrong_input_type():
    class Demo2(E.Engine):
        name = "demo2"
        version = "1.0.0"
        methods = {"echo": E.MethodSpec("echo", "echo a string",
                                        {"x": ("string", True)}, "string")}
        def run(self, method, inputs):
            return self._ok("echo", value=inputs["x"], inputs=inputs)

    E._REGISTRY["demo2"] = ("__test__", "Demo2")
    E._LOADED["demo2"] = Demo2()
    r = E.call("demo2", "echo", {"x": 123})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT
    assert r.error.field == "x"


def test_call_engine_that_raises_returns_internal_error():
    class Crashy(E.Engine):
        name = "crashy"
        version = "1.0.0"
        methods = {"boom": E.MethodSpec("boom", "always crashes", {}, "any")}
        def run(self, method, inputs):
            raise RuntimeError("kaboom")

    E._REGISTRY["crashy"] = ("__test__", "Crashy")
    E._LOADED["crashy"] = Crashy()
    r = E.call("crashy", "boom", {})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INTERNAL
    assert "kaboom" in r.error.message


def test_engine_result_envelope_preserves_value():
    E.reset_for_tests()
    r = E.call("electrical_math", "ohm_voltage",
               {"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")})
    # The LLM orchestrator may paraphrase but must not alter value_si.
    assert r.value_si == 10.0
    assert r.ok
