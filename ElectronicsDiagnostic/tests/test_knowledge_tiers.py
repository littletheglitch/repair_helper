"""Tests for the 6-tier provenance model and DB migrations."""
from eda import database, knowledge_base, migrations, config


def test_migration_adds_tier_column(tmp_app):
    # The fixture runs init_db which now triggers migrations.
    conn = database.connect()
    cols = [c["name"] for c in conn.execute(
        "PRAGMA table_info(knowledge_facts)").fetchall()]
    assert "tier" in cols


def test_schema_version_is_current(tmp_app):
    # Phase 4 bumped the schema to v2 (learning subsystem tables).
    assert migrations.current_version() == migrations.SCHEMA_VERSION
    assert migrations.SCHEMA_VERSION >= 2


def test_migration_is_idempotent(tmp_app):
    # Calling migrate() again must not error.
    report = migrations.migrate()
    assert report["to"] == migrations.SCHEMA_VERSION
    assert not report.get("errors")


def test_new_tables_exist(tmp_app):
    conn = database.connect()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("datasheet_params", "power_sequences", "power_sequence_nodes",
              "hypotheses_v2", "nets", "pins", "connections",
              "reference_ranges", "measurement_validations",
              "benchmark_cases", "repair_cases"):
        assert t in names, f"missing table {t}"


def test_tier_counts_after_load(tmp_app):
    knowledge_base.load_builtin()
    counts = knowledge_base.tier_counts()
    assert counts  # non-empty
    # Phase 1 KB used provenance strings that map to known tiers.
    assert any(t in counts for t in
               ("general-principle", "manufacturer-spec", "community",
                "verified-reference"))


def test_search_facts_returns_tier(tmp_app):
    knowledge_base.load_builtin()
    hits = knowledge_base.search_facts("Ohm", limit=5)
    assert hits
    assert "tier" in hits[0]


def test_search_facts_filtered_by_tier(tmp_app):
    knowledge_base.load_builtin()
    community = knowledge_base.search_facts("MOSFET", tier="community")
    gen = knowledge_base.search_facts("Ohm", tier="general-principle")
    # We expect general-principle for Ohm's law, community for some MOSFET facts.
    assert any(h["tier"] == "general-principle" for h in gen)
    assert all(h["tier"] == "community" for h in community)


def test_import_fact_with_explicit_tier(tmp_app):
    fid = database.import_fact("test", "test", "a fact", "verified-reference",
                                tier="manufacturer-spec")
    rows = database.query_all("SELECT tier FROM knowledge_facts WHERE id=?", (fid,))
    assert rows[0]["tier"] == "manufacturer-spec"


def test_integrity_check_ok(tmp_app):
    rep = migrations.integrity_check()
    assert rep["ok"]
    assert rep["schema_version"] == migrations.SCHEMA_VERSION


def test_backup_and_restore(tmp_app):
    knowledge_base.load_builtin()
    b = migrations.backup()
    assert b.exists()
    # Restore wipes and re-loads; count_facts should match.
    n_before = database.count_facts()
    rep = migrations.restore(b)
    assert rep["ok"]
    assert database.count_facts() == n_before


def test_recover_when_clean(tmp_app):
    rep = migrations.recover()
    assert rep["before"]["ok"] is True
    assert rep["action"] == "none"
