"""Session persistence tests."""
from eda import sessions


def test_create_and_get(tmp_app):
    s = sessions.create("Lenovo G50-80", "Lenovo G50-80 mobo")
    assert s.id > 0
    assert s.title == "Lenovo G50-80"
    assert s.status == "open"
    again = sessions.get(s.id)
    assert again is not None
    assert again.title == "Lenovo G50-80"


def test_list_sessions(tmp_app):
    sessions.create("A", "x")
    sessions.create("B", "y")
    rows = sessions.list_sessions()
    assert len(rows) >= 2


def test_close_reopen(tmp_app):
    s = sessions.create("S", "")
    sessions.close(s.id)
    assert sessions.get(s.id).status == "closed"
    sessions.reopen(s.id)
    assert sessions.get(s.id).status == "open"


def test_symptoms_persist(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "No power")
    sessions.add_symptom(s.id, "No lights")
    assert sessions.symptoms(s.id) == ["No power", "No lights"]


def test_notes_append(tmp_app):
    s = sessions.create("S", "")
    sessions.set_notes(s.id, "first")
    sessions.set_notes(s.id, "second")
    got = sessions.get(s.id)
    assert "first" in got.notes and "second" in got.notes


def test_timeline_records_events(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "dead")
    tl = sessions.timeline(s.id)
    kinds = [t["kind"] for t in tl]
    assert "session" in kinds and "symptom" in kinds


def test_cascade_delete(tmp_app):
    s = sessions.create("S", "")
    sessions.add_symptom(s.id, "x")
    from eda import database
    database.execute("DELETE FROM sessions WHERE id=?", (s.id,))
    rows = database.query_all(
        "SELECT * FROM symptoms WHERE session_id=?", (s.id,)
    )
    assert rows == []
