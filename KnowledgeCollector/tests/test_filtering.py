"""Tests for the relevance filter."""
from kc import filtering as F


def test_relevant_no_power_post():
    r = F.is_relevant("Lenovo G50-80 no power",
                      "My Lenovo G50-80 has no power. I measured +5VALW and it is 0V. The board is dead.")
    assert r.relevant
    assert "no power" in r.matched_keywords


def test_relevant_mosfet_question():
    r = F.is_relevant("Buck converter MOSFET selection",
                      "I am designing a buck converter and need to pick a MOSFET for 19V input, 5V output at 3A.")
    assert r.relevant


def test_irrelevant_recipe():
    r = F.is_relevant("Best chocolate cake",
                      "This is a recipe for chocolate cake with 2 cups of flour and 1 cup of sugar.")
    assert not r.relevant
    # Rejected either by the blacklist ("recipe") or the no-relevant-keyword rule.
    assert "blacklist" in r.reason or "no relevant keyword" in r.reason


def test_blacklist_ad():
    r = F.is_relevant("Buy now! Discount code inside",
                      "Buy now! Discount code XYZ123 for laptop repair tools, free shipping worldwide.")
    assert not r.relevant
    assert "blacklist" in r.reason


def test_bios_dump_reference_rejected():
    r = F.is_relevant("BIOS dump request",
                      "Can anyone share the bios dump for this board? I need the dump file.")
    assert not r.relevant
    assert "bios dump" in r.reason


def test_short_text_rejected():
    r = F.is_relevant("x", "yes")
    assert not r.relevant
    assert "short" in r.reason


def test_long_text_rejected():
    r = F.is_relevant("x", "x" * 60000)
    assert not r.relevant
    assert "long" in r.reason


def test_relevant_with_voltage_measurement():
    r = F.is_relevant("Voltage check",
                      "I measured the +3VALW rail and got 3.3V. The +5VALW is 0V. Resistance to ground is 8 ohm.")
    assert r.relevant


def test_relevant_with_component_repair():
    r = F.is_relevant("Replaced the MOSFET",
                      "Replaced the AO4407 MOSFET on the VIN path and the board powers on again.")
    assert r.relevant
