@echo off
REM Build ElectronicsDiagnostic.exe on Windows.
REM Prerequisites: Python 3.10+ and pip.
REM
REM This script:
REM   1. Creates a virtual env (build/venv) if missing.
REM   2. Installs runtime + build dependencies.
REM   3. Runs the test suite.
REM   4. Runs PyInstaller to produce dist/ElectronicsDiagnostic/.

setlocal
cd /d "%~dp0\.."

if not exist build\venv (
    echo Creating virtual environment...
    py -3 -m venv build\venv
)
call build\venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -e ".[build,test,pdf]"

echo Running tests...
python -m pytest tests\ -q
if errorlevel 1 (
    echo Tests failed; aborting build.
    exit /b 1
)

echo Building EXE with PyInstaller...
pyinstaller scripts\eda.spec --noconfirm --clean
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete. Output is in dist\ElectronicsDiagnostic\
echo Copy the contents of dist\ElectronicsDiagnostic\ into the application
echo root folder (next to Models\, Knowledge\, etc.) to ship a portable app.
endlocal
