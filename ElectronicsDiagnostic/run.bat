@echo off
REM Launch ElectronicsDiagnostic on Windows.
REM
REM - If the bundled ElectronicsDiagnostic.exe exists, run it.
REM - Otherwise run via the Python module if a venv is present.
REM - Otherwise print a friendly message pointing at the install docs.

setlocal
cd /d "%~dp0"

if exist "ElectronicsDiagnostic.exe" (
    "ElectronicsDiagnostic.exe" %*
    goto :eof
)
if exist "build\venv\Scripts\python.exe" (
    call build\venv\Scripts\activate.bat
    python -m eda %*
    goto :eof
)
if exist "python.exe" (
    python.exe -m eda %*
    goto :eof
)
echo ElectronicsDiagnostic.exe not found.
echo Run scripts\build_windows.bat to build it, or see README.md for the
echo Python fallback.
endlocal
