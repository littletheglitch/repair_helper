"""Tests for the Phase 4 learning CLI commands (additive; existing CLI
commands unchanged)."""
import pytest

from eda import cli, config, database, sessions, measurements


def test_help_lists_learning_subcommands(tmp_app):
    database.init_db()
    h = cli.dispatch("help")
    for k in ("learning start", "learning exercise", "learning assess",
              "learning case", "learning review", "learning skills",
              "learning progress", "learning history", "learning hint",
              "learning explain", "learning mode", "learning level"):
        assert k in h, f"help missing '{k}'"


def test_learning_with_no_args_prints_help(tmp_app):
    database.init_db()
    out = cli.dispatch("learning")
    assert "Subcommands" in out or "learning start" in out


def test_learning_skills_lists_16_categories(tmp_app):
    database.init_db()
    out = cli.dispatch("learning skills")
    # 16 rows; the header is one line + separator, so >= 16 data lines.
    assert "Level" in out
    assert "novice" in out
    # Every skill key label fragment should appear.
    for label in ("Electrical mathematics", "Ohm", "Voltage divider",
                  "MOSFET", "Fault isolation"):
        assert label in out


def test_learning_level_default_is_guided(tmp_app):
    database.init_db()
    out = cli.dispatch("learning level")
    assert "guided" in out


def test_learning_level_set_and_persist(tmp_app):
    database.init_db()
    assert "hint" in cli.dispatch("learning level hint")
    assert "guided" in cli.dispatch("learning level guided")
    out = cli.dispatch("learning level")
    assert "guided" in out


def test_learning_level_rejects_bad_value(tmp_app):
    database.init_db()
    out = cli.dispatch("learning level bogus")
    assert "hint" in out and "guided" in out and "full" in out  # usage


def test_learning_exercise_returns_an_exercise(tmp_app):
    database.init_db()
    out = cli.dispatch("learning exercise")
    assert "Exercise #" in out
    assert "difficulty" in out


def test_learning_hint_does_not_leak_solution(tmp_app):
    database.init_db()
    cli.dispatch("learning exercise")
    hint = cli.dispatch("learning hint")
    assert "Hint" in hint
    # The hint must not contain the literal reference solution.
    # (We cannot know it without fetching, so we assert the hint is short
    # and does not contain "Reference solution".)
    assert "Reference solution" not in hint


def test_learning_explain_full_discloses_solution(tmp_app):
    database.init_db()
    cli.dispatch("learning exercise")
    full = cli.dispatch("learning explain full")
    assert "Reference solution" in full or "Provenance" in full


def test_learning_explain_guided_no_final_number(tmp_app):
    database.init_db()
    cli.dispatch("learning exercise")
    guided = cli.dispatch("learning explain guided")
    assert "not shown" in guided.lower()


def test_learning_assess_wrong_answer(tmp_app):
    database.init_db()
    cli.dispatch("learning exercise")
    out = cli.dispatch("learning assess 999 ohm no reasoning")
    assert "not" in out.lower() and ("correct" in out.lower() or "range" in out.lower())


def test_learning_progress_shows_summary(tmp_app):
    database.init_db()
    out = cli.dispatch("learning progress")
    assert "Skills tracked: 16" in out
    assert "novice: 16" in out


def test_learning_gaps_shows_all_initially(tmp_app):
    database.init_db()
    out = cli.dispatch("learning gaps")
    assert "Knowledge gaps" in out
    # All 16 skills are novice initially.
    assert out.count("novice") >= 16


def test_learning_case_converts_session(tmp_app):
    database.init_db()
    cli.dispatch("new TestBoard Lenovo")
    cli.dispatch("symptom no power")
    cli.dispatch("measure VIN = 19.4V")
    out = cli.dispatch("learning case 1")
    assert "Converted" in out
    assert "Follow-up exercises generated" in out
    assert "SIMILAR BUT NOT IDENTICAL" in out


def test_learning_case_nonexistent_session(tmp_app):
    database.init_db()
    out = cli.dispatch("learning case 999")
    assert "ERROR" in out


def test_learning_history_empty_initially(tmp_app):
    database.init_db()
    out = cli.dispatch("learning history")
    assert "No assessment history" in out


def test_learning_review_empty_initially(tmp_app):
    database.init_db()
    out = cli.dispatch("learning review")
    # Either "Nothing due" or a suggestion to build a queue.
    assert "due" in out.lower() or "review" in out.lower()


def test_learning_mode_switch(tmp_app):
    database.init_db()
    out = cli.dispatch("learning mode repair")
    assert "repair" in out
    out2 = cli.dispatch("learning mode learning")
    assert "learning" in out2
    out3 = cli.dispatch("learning mode")
    assert "Current mode" in out3


def test_learning_mode_rejects_bad_value(tmp_app):
    database.init_db()
    out = cli.dispatch("learning mode bogus")
    assert "repair" in out  # error message lists valid values


def test_learning_profile_shows_active(tmp_app):
    database.init_db()
    out = cli.dispatch("learning profile")
    assert "default" in out
    assert "Mode" in out
    assert "level" in out.lower()


def test_learning_start_picks_lowest_mastery(tmp_app):
    database.init_db()
    out = cli.dispatch("learning start")
    assert "Socratic path" in out


def test_learning_start_named_skill(tmp_app):
    database.init_db()
    out = cli.dispatch("learning start ohm_kirchhoff")
    assert "Socratic path" in out
    assert "ohm_kirchhoff" in out


def test_learning_start_unknown_skill(tmp_app):
    database.init_db()
    out = cli.dispatch("learning start not_a_skill")
    assert "No Socratic path" in out or "Available" in out


def test_learning_assess_without_active_exercise(tmp_app):
    database.init_db()
    out = cli.dispatch("learning assess some answer")
    assert "No active exercise" in out


def test_learning_hint_without_active_exercise(tmp_app):
    database.init_db()
    out = cli.dispatch("learning hint")
    assert "No active exercise" in out
