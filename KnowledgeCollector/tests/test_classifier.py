"""Tests for the A–F classifier."""
from kc import classifier as C
from kc import extraction as X
from kc.schemas import RecordClass


def test_class_A_confirmed_repair_with_confirming_test():
    text = ("I replaced C45 and the board powered on. I measured the "
            "resistance to ground after the fix and it was 480 ohm. "
            "It's fixed and working now.")
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.A_CONFIRMED_REPAIR
    assert "confirmed_outcome" in cls.matched_rules


def test_class_A_downgraded_to_B_without_confirming_test():
    text = "I replaced C45 and the board is fixed and working now."
    cls = C.classify(text, X.extract(text))
    # No "measured"/"checked"/"verified" phrase -> downgraded to B.
    assert cls.cls == RecordClass.B_REPORTED_REPAIR
    assert any("downgraded" in r for r in cls.matched_rules)


def test_class_B_reported_repair_no_success_claim():
    text = "I replaced C45 but haven't tested the board yet."
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.B_REPORTED_REPAIR


def test_class_C_engineering_explanation():
    text = ("A buck converter steps voltage down because the inductor "
            "stores energy during the on-time and releases it during "
            "the off-time. According to the datasheet, the duty cycle "
            "sets the output voltage.")
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.C_ENGINEERING


def test_class_D_diagnostic_hypothesis():
    text = "It could be the EC holding the rail off. Might be a firmware issue."
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.D_HYPOTHESIS
    assert "diagnostic_hypothesis" in cls.matched_rules


def test_class_E_suggested_test():
    text = "Try measuring the resistance on +5VALW with a multimeter."
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.E_SUGGESTED_TEST


def test_class_F_incomplete():
    text = "Interesting question, I wonder too."
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.F_INCOMPLETE


def test_authority_order_A_beats_B():
    # A success phrase + confirming test + replacement -> A wins over B.
    text = "I replaced C45, measured 480 ohm after, board is fixed and working now."
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.A_CONFIRMED_REPAIR


def test_authority_order_C_beats_B():
    # Engineering explanation + replacement mention -> C wins.
    text = "The buck converter regulates because of the inductor. I replaced the inductor."
    cls = C.classify(text, X.extract(text))
    assert cls.cls == RecordClass.C_ENGINEERING


def test_explanation_lists_evidence():
    text = "Replaced C45, measured 480 ohm, board fixed and working now."
    cls = C.classify(text, X.extract(text))
    assert cls.explanation
    # The explanation names the rule(s).
    assert "confirmed_outcome" in cls.explanation


def test_explanation_never_fabricates_probability():
    text = "Maybe the MOSFET is bad, could be a short."
    cls = C.classify(text, X.extract(text))
    # No percentage or probability in the explanation.
    assert "%" not in cls.explanation
    assert "probability" not in cls.explanation.lower()


def test_structured_fields_enrich_explanation():
    sf = X.extract("Replaced C45 and measured 480 ohm. Board fixed.")
    cls = C.classify("Replaced C45 and measured 480 ohm. Board fixed.", sf)
    assert "repair_actions=1" in cls.explanation or "repair_actions" in cls.explanation
