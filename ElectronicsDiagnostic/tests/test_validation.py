"""Tests for the validation framework (machine-readable test reports)."""
from eda import validation as V
from eda.engines import base as E
from eda import schemas as S


def test_report_pass_when_cases_pass():
    E.reset_for_tests()
    cases = [
        V.Case(name="ohm_v_ok", method="ohm_voltage",
               inputs={"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")},
               expected=S.Quantity.parse(10, "V"), tolerance=1e-9, kind="normal"),
    ]
    report = V.run_cases("electrical_math", cases)
    assert report.summary == "PASS"
    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0


def test_report_fail_when_cases_fail():
    E.reset_for_tests()
    cases = [
        V.Case(name="ohm_v_wrong", method="ohm_voltage",
               inputs={"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")},
               expected=S.Quantity.parse(99, "V"),  # engine returns 10 V
               tolerance=1e-9, kind="normal"),
    ]
    report = V.run_cases("electrical_math", cases)
    assert report.summary == "FAIL"
    assert report.failed == 1


def test_report_invalid_case_passes_when_engine_errors():
    # Register an engine that returns an error on a method.
    class Bad(E.Engine):
        name = "bad"
        version = "1.0.0"
        methods = {"neg": E.MethodSpec("neg", "negate",
                                       {"x": ("number", True)}, "number")}
        def run(self, method, inputs):
            if inputs.get("x", 0) < 0:
                return self._err(method, S.ErrorCode.OUT_OF_RANGE, "negative not allowed", "x")
            return self._ok("neg", value=-inputs["x"], inputs=inputs)
    E._REGISTRY["bad"] = ("__test__", "Bad")
    E._LOADED["bad"] = Bad()
    cases = [
        V.Case(name="negative_rejected", method="neg", inputs={"x": -1},
               expected=("out_of_range", "negative"), kind="invalid"),
    ]
    report = V.run_cases("bad", cases)
    assert report.summary == "PASS"
    assert report.passed == 1


def test_report_has_environment_and_version():
    E.reset_for_tests()
    cases = [V.Case(name="ohm_v", method="ohm_voltage",
                    inputs={"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")},
                    expected=S.Quantity.parse(10, "V"), tolerance=1e-9)]
    report = V.run_cases("electrical_math", cases)
    assert report.version == "1.0.0"
    assert "python" in report.environment
    assert "sqlite" in report.environment


def test_report_to_dict_is_json_serializable():
    import json
    E.reset_for_tests()
    cases = [V.Case(name="ohm_v", method="ohm_voltage",
                    inputs={"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")},
                    expected=S.Quantity.parse(10, "V"), tolerance=1e-9)]
    report = V.run_cases("electrical_math", cases)
    d = report.to_dict()
    s = json.dumps(d)  # must not raise
    assert "cases" in d
    assert d["summary"] == "PASS"


def test_render_report_text_includes_summary_and_counts():
    E.reset_for_tests()
    cases = [V.Case(name="ohm_v", method="ohm_voltage",
                    inputs={"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")},
                    expected=S.Quantity.parse(10, "V"), tolerance=1e-9)]
    report = V.run_cases("electrical_math", cases)
    txt = V.render_report_text(report)
    assert "Summary: PASS" in txt
    assert "Passed: 1" in txt


def test_report_includes_max_error():
    E.reset_for_tests()
    # ohm_voltage returns exactly 10 V; tolerance 1e-9 means the case passes with
    # near-zero error; max_error should be ~0.
    cases = [V.Case(name="ohm_v", method="ohm_voltage",
                    inputs={"I": S.Quantity.parse(2, "A"), "R": S.Quantity.parse(5, "Ω")},
                    expected=S.Quantity.parse(10, "V"), tolerance=1e-9)]
    report = V.run_cases("electrical_math", cases)
    assert report.max_error >= 0.0
