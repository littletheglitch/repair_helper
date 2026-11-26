# run.sh — convenience launcher for the Python entry point on
# Linux/macOS. The final Windows build is an EXE; this script is for
# development and quick local testing.
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHONPATH=src python3 -m eda "$@"
