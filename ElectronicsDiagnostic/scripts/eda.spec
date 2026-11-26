# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ElectronicsDiagnostic.exe

Build on Windows with Python 3.10+ and pyinstaller installed::

    pip install pyinstaller pypdf PyMuPDF reportlab
    pyinstaller scripts/eda.spec --noconfirm --clean

This bundles the Python runtime and all pure-Python deps into a single
on-disk folder (ElectronicsDiagnostic/) that already contains the
application data directories (Models/, Knowledge/, etc.). The result is
a portable Windows application that does not require Python on the
target machine.

NOTES:
- The model GGUF and the llama.cpp CLI binary are deliberately NOT
  bundled by PyInstaller; they are too large and too platform-specific.
  Use scripts/prepare_model.py once and drop a llama.cpp build into
  Engine/. Both are loaded from disk at runtime.
- onefolder mode (not onefile) keeps startup fast and lets the user
  replace the model or runtime without rebuilding.
"""

import sys
from pathlib import Path

block_cipher = None

ROOT = Path.cwd()

a = Analysis(
    ["src/eda/__main__.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        ("Knowledge/*.json", "Knowledge"),
    ],
    hiddenimports=[
        "pypdf",
        "fitz",          # PyMuPDF
        "reportlab",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib", "numpy", "pandas", "scipy", "PIL.Image",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="ElectronicsDiagnostic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ElectronicsDiagnostic",
)
