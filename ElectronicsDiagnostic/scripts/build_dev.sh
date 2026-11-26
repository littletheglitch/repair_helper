#!/usr/bin/env bash
# Build / test script for Linux/macOS development of the same project.
# The final EXE is Windows-only; this script runs the tests and the
# Python entry point for local validation.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d build/venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv build/venv
fi
# shellcheck disable=SC1091
source build/venv/bin/activate

echo "Installing dependencies..."
python -m pip install --upgrade pip
python -m pip install -e ".[test,pdf]"

echo "Running tests..."
python -m pytest tests/ -q

echo "Running CLI smoke test..."
python -m eda help | head -40
echo "OK."
