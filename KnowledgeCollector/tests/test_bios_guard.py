"""Tests for the BIOS guard (hard BIOS-dump exclusion)."""
import pytest
from pathlib import Path

from kc import bios_guard


def test_extension_rejects_bin():
    is_bios, reason = bios_guard.looks_like_bios("firmware.bin")
    assert is_bios
    assert ".bin" in reason


def test_extension_rejects_rom():
    is_bios, _ = bios_guard.looks_like_bios("bios.rom")
    assert is_bios


def test_extension_rejects_spi():
    is_bios, _ = bios_guard.looks_like_bios("flash.spi")
    assert is_bios


def test_filename_pattern_rejects_bios_dump():
    is_bios, reason = bios_guard.looks_like_bios("lenovo_g50_bios_dump.zip")
    assert is_bios
    assert "pattern" in reason


def test_filename_pattern_rejects_uefi_dump():
    is_bios, _ = bios_guard.looks_like_bios("x230_uefi_dump.tar")
    assert is_bios


def test_magic_bytes_rejects_ami():
    is_bios, reason = bios_guard.looks_like_bios("mystery.dat", head=b"AMIBIOSC" + b"\x00" * 56)
    assert is_bios
    assert "AMIBIOSC" in reason


def test_magic_bytes_rejects_efi_capsule():
    is_bios, _ = bios_guard.looks_like_bios(
        "capsule.img", head=b"\xBD\x86\x66\x3B\x76\x0D\x30\x40" + b"\x00" * 56)
    assert is_bios


def test_magic_bytes_rejects_award():
    is_bios, _ = bios_guard.looks_like_bios("image.bin", head=b"Award BootBlock" + b"\x00" * 50)
    assert is_bios


def test_normal_pdf_not_rejected():
    is_bios, reason = bios_guard.looks_like_bios(
        "datasheet.pdf", head=b"%PDF-1.4\n" + b"\x00" * 55)
    assert not is_bios
    assert reason == ""


def test_normal_text_not_rejected():
    is_bios, _ = bios_guard.looks_like_bios("notes.txt", head=b"laptop no power issue...")
    assert not is_bios


def test_reject_bios_builds_rejection_record():
    from kc.schemas import RejectionReason
    rej = bios_guard.reject_bios("eevblog", "https://example.com/file.bin",
                                  "file.bin", head=b"\xAA\x55" + b"\x00" * 62)
    assert rej.reason == RejectionReason.BIOS
    assert "BIOS dump" in rej.detail


def test_assert_not_bios_raises_for_rom(tmp_path):
    p = tmp_path / "x.rom"
    p.write_bytes(b"\xAA\x55" + b"\x00" * 100)
    with pytest.raises(ValueError, match="BIOS dump"):
        bios_guard.assert_not_bios(p)


def test_assert_not_bios_passes_for_pdf(tmp_path):
    p = tmp_path / "datasheet.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"\x00" * 100)
    # Should not raise.
    bios_guard.assert_not_bios(p)


def test_ec_rom_pattern_rejected():
    is_bios, _ = bios_guard.looks_like_bios("lenovo_ec_rom_v1.07")
    assert is_bios


def test_me_bin_pattern_rejected():
    is_bios, _ = bios_guard.looks_like_bios("intel_me.bin")
    assert is_bios
