"""BIOS guard — hard exclusion of BIOS dump files.

The spec is explicit: "BIOS dump files are explicitly excluded from this
project." This module enforces that at three stages:

1. At fetch time — before a downloaded file is stored.
2. At document-processing time — before extraction.
3. At import time — before the importer accepts a file.

The check is a filename + magic-byte + extension check. A match is
rejected with a logged reason; the file is NEVER stored, NEVER
extracted, NEVER imported. There is no override; the guard is the
final word.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

from .schemas import Rejection, RejectionReason
from . import config


# Extensions that are BIOS dump signatures.
BIOS_EXTENSIONS = {
    ".bin", ".rom", ".fd", ".ima", ".fw", ".spi", ".hex",  # classic flash images
    ".uefibin", ".mebin",                                   # ME/UEFI splits
}

# Filename patterns that strongly suggest a BIOS dump even if the
# extension is ambiguous (e.g. ".zip" containing "bios_dumps/").
# These do NOT use \b word boundaries because the surrounding '_' is a
# word character and \b would not match between two word characters.
BIOS_FILENAME_PATTERNS = [
    re.compile(r"bios[\s_-]*dump", re.IGNORECASE),
    re.compile(r"bios[\s_-]*bin", re.IGNORECASE),
    re.compile(r"bios[\s_-]*rom", re.IGNORECASE),
    re.compile(r"bios[\s_-]*image", re.IGNORECASE),
    re.compile(r"uefi[\s_-]*dump", re.IGNORECASE),
    re.compile(r"flash[\s_-]*dump", re.IGNORECASE),
    re.compile(r"ec[\s_-]*rom", re.IGNORECASE),       # EC firmware image
    re.compile(r"me[\s_-]*bin", re.IGNORECASE),        # Management Engine image
    re.compile(r"spi[\s_-]*dump", re.IGNORECASE),
]

# Known BIOS magic-byte signatures (first 8 bytes).
# - AMI: "\x55\xAA" near start; "AMIBIOSC" string at offset 0
# - Award: "Award" string in first 64 bytes
# - EFI capsule: "\xBD\x86\x66\x3B\x76\x0D\x30\x40" (EFI_CAPSULE_GUID first 8 bytes LE)
# - Intel flash descriptor: "\x5A\xA5\xF0\x0C" signature at offset 0x10 (but the
#   first bytes are arbitrary; we only check the descriptor signature if present)
# - 5AA5 / AA55 bootloader signatures
BIOS_MAGIC = [
    b"AMIBIOSC",            # AMI BIOS
    b"Award",               # Award BIOS
    b"\x55\xAA",            # boot signature
    b"\xAA\x55",            # mirror boot signature
    b"\xBD\x86\x66\x3B\x76\x0D\x30\x40",  # EFI capsule GUID prefix
    b"\x5A\xA5\xF0\x0C",   # Intel flash descriptor signature
    b"FLASHEFS",            # some SPI flash images
]


def looks_like_bios(name: str, head: bytes = b"") -> Tuple[bool, str]:
    """Return (is_bios, reason). A non-empty reason means the file IS a
    BIOS dump and must be rejected. An empty reason means it passed."""
    # Stage 1: extension.
    ext = Path(name).suffix.lower()
    if ext in BIOS_EXTENSIONS:
        return True, f"extension '{ext}' is a BIOS dump signature"
    # Stage 2: filename pattern.
    for pat in BIOS_FILENAME_PATTERNS:
        if pat.search(name):
            return True, f"filename matches BIOS-dump pattern '{pat.pattern}'"
    # Stage 3: magic bytes.
    if head:
        for sig in BIOS_MAGIC:
            if head.startswith(sig) or sig in head[:64]:
                return True, (f"magic bytes match BIOS signature "
                              f"{sig!r}")
    return False, ""


def reject_bios(source_name: str, url: str, name: str, head: bytes = b"") -> Rejection:
    """Build a Rejection record for a BIOS dump."""
    _is, reason = looks_like_bios(name, head)
    return Rejection(
        source_name=source_name, url=url,
        reason=RejectionReason.BIOS,
        detail=f"BIOS dump excluded: {reason}",
        collected_at=config.now_iso(),
    )


def assert_not_bios(path: Path) -> None:
    """Raise ValueError if the file at ``path`` is a BIOS dump.

    Used at the import stage as a final guard before the importer
    accepts a file."""
    name = path.name
    head = b""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        pass
    is_bios, reason = looks_like_bios(name, head)
    if is_bios:
        raise ValueError(f"BIOS dump rejected: {reason}")
