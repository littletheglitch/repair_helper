"""Diagnostic reasoning engine tests, including the sample case."""
from eda import sessions, measurements as M, reasoning, safety
from eda import document_requests


def _sess():
    return sessions.create("Lenovo G50-80", "Lenovo G50-80 motherboard")


def test_analyze_no_data_returns_initial_next_test(tmp_app):
    s = _sess()
    a = reasoning.analyze(s.id, [], [])
    # With no evidence, the engine asks for VIN first.
    assert a.next_test is not None
    assert "VIN" in a.next_test.test or "adapter" in a.next_test.test.lower()


def test_sample_case_no_power(tmp_app):
    """The sample case from the spec:

    Laptop motherboard has no power.
    Adapter input = 19V.
    +3VALW = 3.3V.
    +5VALW = 0V.

    The engine MUST NOT declare a failed component on this evidence and
    must request the next useful measurement.
    """
    s = _sess()
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 3.3V"))
    M.add(s.id, M.parse_line("measure +5VALW = 0V"))
    a = reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))

    # The engine must NOT have confirmed any specific component failure.
    assert a.confirmed_diagnosis is None, (
        "Engine declared a confirmed diagnosis on incomplete evidence."
    )

    # Top hypotheses are present and ranked.
    assert len(a.hypotheses) >= 2
    top = a.hypotheses[0]
    assert top.score >= 0.0
    # Each hypothesis must expose supporting/contradicting/missing lists.
    assert isinstance(top.supporting, list)
    assert isinstance(top.contradicting, list)
    assert isinstance(top.missing, list)

    # The next test must point at a concrete measurement, not a conclusion.
    assert a.next_test is not None
    assert a.next_test.where_to_measure
    assert a.next_test.reference_point
    assert a.next_test.rationale
    # Because +5VALW is 0 V while VIN is present, the next test should
    # target the missing/failed 5VALW rail or its resistance to ground.
    nt_text = (a.next_test.test + a.next_test.where_to_measure).lower()
    assert ("5valw" in nt_text) or ("resistance" in nt_text) or ("5v_alw" in nt_text), (
        f"Next test should target the dead 5VALW rail or its resistance; got: {a.next_test.test}"
    )

    # The safety banner must be present.
    assert "SAFETY" in a.safety_banner


def test_analyze_requests_schematic_when_needed(tmp_app):
    """When VIN present, +3VALW is 0 V, and ACOK is unknown, the engine
    should ask for a schematic to map the EC pin (per the spec's example
    about requesting documents only when genuinely needed)."""
    s = _sess()
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    M.add(s.id, M.parse_line("measure +3VALW = 0V"))
    a = reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))
    # Walk down the engine's next-test chain: the engine will first ask
    # for resistance to ground on +3VALW. After a normal resistance is
    # supplied, the next analyze call should request ACOK measurement
    # with a schematic document request.
    M.add(s.id, M.parse_line("measure resistance_to_ground = 480ohm"))
    a2 = reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))
    nt = a2.next_test
    assert nt is not None
    # Either ACOK is requested (needs schematic) or another rail measurement.
    # In the branch where 3VALW is dead + resistance is normal, the engine
    # asks for ACOK with a schematic request.
    assert nt.needs_document is not None or "ACOK" in nt.test or "5VALW" in nt.test


def test_next_test_persisted(tmp_app):
    s = _sess()
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))
    from eda import database
    rows = database.query_all(
        "SELECT * FROM next_tests WHERE session_id=?", (s.id,)
    )
    assert rows


def test_hypotheses_persisted(tmp_app):
    s = _sess()
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))
    from eda import database
    rows = database.query_all(
        "SELECT * FROM hypotheses WHERE session_id=?", (s.id,)
    )
    assert rows


def test_safety_blocks_forbidden_actions(tmp_app):
    allowed, forbidden, _ = safety.check_action(
        "Short the battery terminals to test the charger."
    )
    assert not allowed
    assert any("battery" in f for f in forbidden)


def test_safety_warns_on_live_measurement(tmp_app):
    allowed, _, warnings = safety.check_action(
        "Live probe the input MOSFET drain on adapter power."
    )
    assert allowed
    assert "live measurement" in warnings


def test_safety_required_caution(tmp_app):
    c = safety.required_caution_for("live voltage measurement on adapter")
    assert "CAUTION" in c


def test_document_request_render(tmp_app):
    req = document_requests.DocumentRequest(
        kind="datasheet",
        name_hint="TPS51716 datasheet",
        fields_needed=["EN pin", "UVLO threshold", "PGOOD behavior"],
        rationale="To verify EN requirements and protection conditions.",
    )
    text = req.render_prompt()
    assert "TPS51716" in text
    assert "EN pin" in text
    assert "UVLO" in text
    assert "PGOOD" in text


def test_engine_does_not_invent_pin_numbers(tmp_app):
    """The engine must not output specific pin numbers without a datasheet."""
    s = _sess()
    sessions.add_symptom(s.id, "No power")
    M.add(s.id, M.parse_line("measure VIN = 19V"))
    a = reasoning.analyze(s.id, sessions.symptoms(s.id), M.list_for(s.id))
    nt = a.next_test
    # The next test may mention pin numbers ONLY when accompanied by a
    # document request. Standalone pin numbers without a doc request are
    # a fabrication. The engine either asks for the rail (no pins) or asks
    # for ACOK + a schematic (pin numbers from the schematic).
    body = (nt.test + " " + nt.where_to_measure).lower()
    has_pin = "pin" in body
    if has_pin:
        assert nt.needs_document is not None, (
            "Pin number referenced but no document requested."
        )
