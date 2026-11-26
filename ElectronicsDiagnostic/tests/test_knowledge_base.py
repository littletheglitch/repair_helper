"""Knowledge-base loader tests."""
from eda import knowledge_base, database


def test_load_builtin_inserts_facts(tmp_app):
    n = knowledge_base.load_builtin()
    assert n > 0
    assert database.count_facts() >= n


def test_load_idempotent(tmp_app):
    a = knowledge_base.load_builtin()
    b = knowledge_base.load_builtin()
    assert b == 0  # already loaded


def test_force_reload(tmp_app):
    knowledge_base.load_builtin()
    n = knowledge_base.load_builtin(force=True)
    assert n > 0


def test_categories_present(tmp_app):
    knowledge_base.load_builtin()
    cats = knowledge_base.categories()
    assert any("basic" in c for c in cats)
    assert any("power" in c for c in cats)
    assert any("laptop" in c for c in cats)


def test_search_returns_provenance(tmp_app):
    knowledge_base.load_builtin()
    hits = knowledge_base.search_facts("MOSFET")
    assert hits
    assert "provenance" in hits[0]


def test_required_topics_present(tmp_app):
    """The spec lists a long set of topics the KB must cover. Assert that
    each appears at least once as a topic or in a fact."""
    required = [
        "ohms-law", "kirchhoffs-laws", "series-parallel-circuits",
        "capacitors-inductors", "diodes-zener", "mosfet-operation",
        "bjt-operation", "operational-amplifiers-comparators",
        "buck-converter", "boost-converter", "buck-boost-converter",
        "pwm-controllers", "feedback-networks", "protections",
        "current-sensing", "battery-charging", "dc-input-protection",
        "always-on-rails", "power-states-s5-s3-s0", "embedded-controller",
        "vrm-cpu-power", "memory-power", "clock-reset",
        "common-fault-patterns", "spi", "i2c-smbus", "uart",
        "pcie-fundamentals", "usb-fundamentals",
    ]
    knowledge_base.load_builtin()
    rows = database.query_all("SELECT topic, fact FROM knowledge_facts")
    all_text = " ".join(r["topic"] + " " + r["fact"] for r in rows).lower()
    missing = [t for t in required if t.replace("-", " ") not in all_text and t not in all_text]
    assert not missing, f"missing topics in KB: {missing}"
