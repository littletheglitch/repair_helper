"""Spaced Review Scheduler.

A deterministic, performance-based spaced-repetition scheduler (SM-2
family). Configuration is exposed so the user can tune the base
intervals, but the scheduling logic itself is fixed and testable: the
same inputs always produce the same next interval.

SM-2 recap (with our adjustments for an engineering-learning context):

  * A review has a quality q in 0..5:
      0-2  : failed (wrong, or solved only after many hints).
      3    : barely correct (solved with a hint).
      4-5  : correct, solved independently (5 = also a new-context demo).
  * State: (interval_days, ease_factor, repetitions).
  * On a failed review (q < 3): reset repetitions to 0 and interval to
    the base (1 day) — we re-learn, not just review. Ease drops by 0.2
    (floor 1.3).
  * On a passing review (q >= 3):
      repetitions == 0 -> interval = base_interval
      repetitions == 1 -> interval = base_interval * 2
      repetitions  >= 2 -> interval = round(prev_interval * ease)
      ease = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))  [SM-2]
      repetitions += 1
  * The next due date = now + interval_days.

Determinism: ``schedule(q, state)`` is a pure function of its inputs;
no randomness, no clock (the caller supplies ``now``). This makes the
scheduler fully testable and reproducible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .. import database
from . import db


@dataclass
class ReviewItem:
    skill_key: str
    exercise_id: Optional[int]
    interval_days: float
    ease_factor: float
    repetitions: int
    due_at: str
    last_reviewed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


# Default SM-2 parameters; user-tunable via set_config.
_DEFAULTS = {
    "base_interval_days": 1.0,
    "ease_min": 1.3,
    "ease_start": 2.5,
    "max_interval_days": 365.0,
}


class SpacedReview:
    """SM-2-style scheduler. Pure logic; persistence via db.upsert_review."""

    def __init__(self, profile_id: Optional[int] = None,
                 profile_name: str = "default",
                 config: Optional[Dict[str, float]] = None):
        if profile_id is None:
            prof = db.get_or_create_profile(profile_name)
            profile_id = int(prof["id"])
        self.profile_id = int(profile_id)
        self.cfg = dict(_DEFAULTS)
        if config:
            self.cfg.update(config)

    # -- Pure scheduling (testable) -------------------------------------
    def schedule(self, quality: int, prev_interval: float,
                 prev_ease: float, prev_repetitions: int,
                 now: Optional[str] = None) -> Tuple[float, float, int, str]:
        """Return (new_interval_days, new_ease, new_repetitions, due_at).

        Pure function: same inputs -> same outputs (modulo the due_at
        clock, which the caller controls via ``now``).
        """
        now = now or database.now_iso()
        base = self.cfg["base_interval_days"]
        ease_min = self.cfg["ease_min"]
        ease = max(prev_ease if prev_ease > 0 else self.cfg["ease_start"],
                   ease_min)
        if quality < 3:
            # Failed: reset.
            repetitions = 0
            interval = base
            ease = max(ease_min, ease - 0.2)
        else:
            # Passed: SM-2 progression.
            repetitions = prev_repetitions + 1
            if repetitions == 1:
                interval = base
            elif repetitions == 2:
                interval = base * 2
            else:
                interval = prev_interval * ease
            # SM-2 ease update.
            ease = ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
            ease = max(ease_min, ease)
        # Cap to a sane maximum.
        interval = min(interval, self.cfg["max_interval_days"])
        due_at = _add_days(now, interval)
        return round(interval, 3), round(ease, 3), repetitions, due_at

    # -- Recording a review ---------------------------------------------
    def record_review(self, skill_key: str, quality: int,
                      exercise_id: Optional[int] = None,
                      now: Optional[str] = None) -> Dict[str, Any]:
        """Apply a review outcome to the stored schedule and persist the
        new state (versioned). Returns the new state."""
        prev = db.latest_review(self.profile_id, skill_key, exercise_id)
        prev_interval = float(prev["interval_days"]) if prev else 0.0
        prev_ease = float(prev["ease_factor"]) if prev else self.cfg["ease_start"]
        prev_reps = int(prev["repetitions"]) if prev else 0
        last = prev["due_at"] if prev else ""
        new_interval, new_ease, new_reps, due_at = self.schedule(
            quality, prev_interval, prev_ease, prev_reps, now)
        review_id = db.upsert_review(
            self.profile_id, skill_key, exercise_id,
            new_interval, new_ease, new_reps, due_at,
            last_reviewed_at=now or database.now_iso())
        return {
            "review_id": review_id, "skill_key": skill_key,
            "exercise_id": exercise_id, "interval_days": new_interval,
            "ease_factor": new_ease, "repetitions": new_reps,
            "due_at": due_at,
        }

    # -- What is due ----------------------------------------------------
    def due_now(self, now: Optional[str] = None) -> List[ReviewItem]:
        rows = db.due_reviews(self.profile_id, now_iso=now)
        # Collapse to the latest version per (skill, exercise).
        latest: Dict[Tuple[str, Optional[int]], ReviewItem] = {}
        for r in rows:
            key = (r["skill_key"], r["exercise_id"])
            latest[key] = ReviewItem(
                skill_key=r["skill_key"],
                exercise_id=r["exercise_id"],
                interval_days=float(r["interval_days"]),
                ease_factor=float(r["ease_factor"]),
                repetitions=int(r["repetitions"]),
                due_at=r["due_at"],
                last_reviewed_at=r.get("last_reviewed_at") or "",
            )
        return list(latest.values())

    def upcoming(self, within_days: int = 7,
                 now: Optional[str] = None) -> List[ReviewItem]:
        now_iso = now or database.now_iso()
        horizon = _add_days(now_iso, within_days)
        rows = database.query_all(
            "SELECT * FROM review_schedule WHERE profile_id=? "
            "AND due_at BETWEEN ? AND ? ORDER BY due_at",
            (self.profile_id, now_iso, horizon))
        latest: Dict[Tuple[str, Optional[int]], ReviewItem] = {}
        for r in rows:
            key = (r["skill_key"], r["exercise_id"])
            latest[key] = ReviewItem(
                skill_key=r["skill_key"],
                exercise_id=r["exercise_id"],
                interval_days=float(r["interval_days"]),
                ease_factor=float(r["ease_factor"]),
                repetitions=int(r["repetitions"]),
                due_at=r["due_at"],
                last_reviewed_at=r.get("last_reviewed_at") or "",
            )
        return list(latest.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_days(iso_ts: str, days: float) -> str:
    """Add ``days`` (may be fractional) to an ISO timestamp string."""
    from datetime import datetime, timedelta
    # Accept either a naive ISO string or one with a timezone offset.
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        dt = datetime.utcnow()
    dt = dt + timedelta(days=float(days))
    return dt.isoformat(timespec="seconds")
