"""Tests for the circuit topology engine."""
from eda.engines import base as E
from eda.engines import topology as T
from eda import schemas as S, sessions


def test_add_node(tmp_app):
    s = sessions.create("S", "")
    r = E.call("topology", "add_node",
               {"session_id": s.id, "component": "Q12", "pin": "1",
                "net": "VIN", "role": "drain"})
    assert r.ok
    assert "Q12.1 -> VIN" in r.value["added"]


def test_add_node_invalid_component(tmp_app):
    s = sessions.create("S", "")
    r = E.call("topology", "add_node",
               {"session_id": s.id, "component": "12", "pin": "1", "net": "VIN"})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


def test_add_node_missing_inputs(tmp_app):
    s = sessions.create("S", "")
    r = E.call("topology", "add_node",
               {"session_id": s.id, "component": "Q12", "pin": "1"})
    assert not r.ok
    assert r.error.code == S.ErrorCode.MISSING_INPUT


def test_add_connection(tmp_app):
    s = sessions.create("S", "")
    r = E.call("topology", "add_connection",
               {"session_id": s.id, "from_ref": "Q12.1", "to_ref": "R5.2",
                "net": "VIN", "source": "schematic p.3", "page": 3,
                "verified": True})
    assert r.ok


def test_add_connection_invalid_ref(tmp_app):
    s = sessions.create("S", "")
    r = E.call("topology", "add_connection",
               {"session_id": s.id, "from_ref": "no_dot", "to_ref": "R5.2",
                "net": "VIN"})
    assert not r.ok
    assert r.error.code == S.ErrorCode.INVALID_INPUT


def test_parse_netlist(tmp_app):
    s = sessions.create("S", "")
    text = """
# This is a comment
Q12.1 -> VIN
Q12.2 -> GND
Q12.3 -> +3VALW
R5.1 -> +3VALW
R5.2 -> GND
"""
    r = E.call("topology", "parse_netlist",
               {"session_id": s.id, "netlist": text})
    assert r.ok
    assert r.value["added"] == 5
    assert r.value["skipped"] == []


def test_parse_netlist_skips_garbage(tmp_app):
    s = sessions.create("S", "")
    text = "Q12.1 -> VIN\nthis is not a netlist line\nR5.2 -> GND\n"
    r = E.call("topology", "parse_netlist",
               {"session_id": s.id, "netlist": text})
    assert r.ok
    assert r.value["added"] == 2
    assert len(r.value["skipped"]) == 1


def test_components_on_net(tmp_app):
    s = sessions.create("S", "")
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "1", "net": "VIN"})
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "R5", "pin": "1", "net": "VIN"})
    r = E.call("topology", "components_on_net",
               {"session_id": s.id, "net": "VIN"})
    assert r.ok
    assert set(r.value["components"]) == {"Q12", "R5"}


def test_rail_origin(tmp_app):
    s = sessions.create("S", "")
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "1", "net": "VIN"})
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "R5", "pin": "1", "net": "VIN"})
    r = E.call("topology", "rail_origin",
               {"session_id": s.id, "net": "VIN"})
    assert r.ok
    assert r.value["origin"] == "Q12.1"  # first added


def test_rail_origin_not_found(tmp_app):
    s = sessions.create("S", "")
    r = E.call("topology", "rail_origin",
               {"session_id": s.id, "net": "DOES_NOT_EXIST"})
    assert r.ok
    assert r.value["origin"] is None


def test_find_opens_single_pin_net(tmp_app):
    s = sessions.create("S", "")
    # VIN has only one pin -> possible open.
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "1", "net": "VIN"})
    # GND has two pins -> not an open.
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "R5", "pin": "2", "net": "GND"})
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "2", "net": "GND"})
    r = E.call("topology", "find_opens", {"session_id": s.id})
    assert r.ok
    assert "VIN" in r.value["possible_opens"]
    assert "GND" not in r.value["possible_opens"]


def test_find_shorts_internal(tmp_app):
    s = sessions.create("S", "")
    # Q12 has two pins on +3VALW (non-GND) -> possible internal short.
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "1", "net": "+3VALW"})
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "2", "net": "+3VALW"})
    r = E.call("topology", "find_shorts", {"session_id": s.id})
    assert r.ok
    assert any(item["component"] == "Q12" for item in r.value["possible_internal_shorts"])


def test_request_for_unsupported_format_is_honest(tmp_app):
    r = E.call("topology", "request_for_unsupported_format",
               {"format": "binary BoardView (.fz)"})
    assert r.ok
    msg = r.value["message"]
    assert "not parsed" in msg
    assert "screenshot" in msg or "ASCII netlist" in msg
    assert "do not claim" in msg


def test_graph_returns_full_graph(tmp_app):
    s = sessions.create("S", "")
    E.call("topology", "add_node",
           {"session_id": s.id, "component": "Q12", "pin": "1", "net": "VIN"})
    r = E.call("topology", "graph", {"session_id": s.id})
    assert r.ok
    assert "nets" in r.value and "pins" in r.value


def test_toplogy_does_not_claim_pdf_understanding(tmp_app):
    """The engine must never claim to have understood a PDF schematic."""
    r = E.call("topology", "request_for_unsupported_format",
               {"format": "PDF schematic"})
    assert r.ok
    assert "not parsed" in r.value["message"]
    # The limitations field documents the honesty.
    assert any("no image" in l or "no schematic" in l
               for l in r.limitations)
