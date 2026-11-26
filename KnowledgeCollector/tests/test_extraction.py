"""Tests for the structured-field extractor."""
from kc import extraction as X


def test_extract_component_model():
    sf = X.extract("The TPS51716 charger IC was bad; I also replaced the ISL6248.")
    assert "TPS51716" in sf.component_model
    assert "ISL6248" in sf.component_model


def test_extract_device_model():
    sf = X.extract("My Lenovo G50-80 has no power. Also fixed a ThinkPad T420.")
    assert any("Lenovo G50-80" in d for d in sf.device_model)
    assert any("ThinkPad T420" in d for d in sf.device_model)


def test_extract_motherboard_model():
    sf = X.extract("This board is NM-A111. The schematic also references DAOZ9AMB6E0.")
    assert "NM-A111" in sf.motherboard_model


def test_extract_symptoms():
    sf = X.extract("The laptop has no power and no POST. The board is dead.")
    assert "no power" in sf.reported_symptoms
    assert "no post" in sf.reported_symptoms


def test_extract_measurements():
    sf = X.extract("I measured +5VALW = 0V. Resistance to ground was 8 ohm. VIN is 19V.")
    # At least one measurement captured.
    assert any("5VALW" in m for m in sf.measurements)
    assert any("8" in m for m in sf.measurements)


def test_extract_repair_actions():
    sf = X.extract("Replaced C45 with a new 22uF cap. Also desoldered Q12.")
    assert any("C45" in a for a in sf.repair_actions)


def test_extract_outcome_fixed():
    sf = X.extract("Replaced C45 and the board powered on. It boots now.")
    assert sf.reported_outcome == "fixed"


def test_extract_outcome_failed():
    sf = X.extract("Replaced C45 but the board is still dead, still no power.")
    assert sf.reported_outcome == "not fixed"


def test_extract_proposed_tests():
    sf = X.extract("Try measuring the resistance on +5VALW. Use a multimeter in diode mode.")
    assert any("measuring" in t for t in sf.proposed_tests)


def test_extract_no_fabrication():
    sf = X.extract("This is a generic post with no technical content at all.")
    assert sf.component_model == []
    assert sf.device_model == []
    assert sf.measurements == []
    assert sf.reported_outcome == ""


def test_extract_image_ref():
    sf = X.extract("Here is a photo: https://example.com/img/board.png of the damage.")
    assert any("board.png" in r for r in sf.image_refs)


def test_extract_combined_title_and_text():
    sf = X.extract(title="Lenovo G50-80 no power",
                   text="Measured +5VALW = 0V. Replaced C45.")
    assert "no power" in sf.reported_symptoms
    assert any("C45" in a for a in sf.repair_actions)
