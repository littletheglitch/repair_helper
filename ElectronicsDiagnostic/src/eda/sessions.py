"""Repair session manager.

A session = one board repair job. Sessions persist across application
restarts in SQLite. Each session owns symptoms, measurements, documents,
hypotheses, missing-evidence, next-tests, and a timeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import database
from . import config


@dataclass
class Session:
    id: int
    title: str
    board_model: str
    created_at: str
    updated_at: str
    status: str
    notes: str

    @classmethod
    def from_row(cls, row) -> "Session":
        return cls(
            id=row["id"],
            title=row["title"],
            board_model=row["board_model"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=row["status"],
            notes=row["notes"] or "",
        )


def create(title: str, board_model: str = "") -> Session:
    now = database.now_iso()
    cur = database.execute(
        """INSERT INTO sessions(title, board_model, created_at, updated_at, status, notes)
           VALUES(?,?,?,?,?,?)""",
        (title, board_model, now, now, "open", ""),
    )
    sid = int(cur.lastrowid)
    database.add_timeline(sid, "session", f"Session created: {title} ({board_model})")
    return get(sid)  # type: ignore[return-value]


def get(sid: int) -> Optional[Session]:
    row = database.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
    return Session.from_row(row) if row else None


def list_sessions(open_only: bool = False) -> List[Session]:
    sql = "SELECT * FROM sessions ORDER BY updated_at DESC"
    if open_only:
        sql = "SELECT * FROM sessions WHERE status='open' ORDER BY updated_at DESC"
    return [Session.from_row(r) for r in database.query_all(sql)]


def set_notes(sid: int, note: str) -> None:
    existing = get(sid)
    combined = note
    if existing and existing.notes:
        combined = existing.notes + "\n" + note
    database.execute(
        "UPDATE sessions SET notes=?, updated_at=? WHERE id=?",
        (combined, database.now_iso(), sid),
    )
    database.add_timeline(sid, "note", note)


def close(sid: int) -> None:
    database.execute(
        "UPDATE sessions SET status='closed', updated_at=? WHERE id=?",
        (database.now_iso(), sid),
    )
    database.add_timeline(sid, "session", "Session closed")


def reopen(sid: int) -> None:
    database.execute(
        "UPDATE sessions SET status='open', updated_at=? WHERE id=?",
        (database.now_iso(), sid),
    )


def touch(sid: int) -> None:
    database.execute("UPDATE sessions SET updated_at=? WHERE id=?",
                     (database.now_iso(), sid))


def add_symptom(sid: int, text: str) -> None:
    database.execute(
        "INSERT INTO symptoms(session_id, text, created_at) VALUES(?,?,?)",
        (sid, text, database.now_iso()),
    )
    database.add_timeline(sid, "symptom", text)
    touch(sid)


def symptoms(sid: int) -> List[str]:
    rows = database.query_all(
        "SELECT text FROM symptoms WHERE session_id=? ORDER BY id", (sid,)
    )
    return [r["text"] for r in rows]


def timeline(sid: int) -> List[dict]:
    return database.query_all(
        "SELECT event_at, kind, text FROM timeline WHERE session_id=? ORDER BY id",
        (sid,),
    )
