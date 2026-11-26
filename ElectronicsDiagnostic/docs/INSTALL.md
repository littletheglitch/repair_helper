# Installation and First Run

The application is portable. There are two ways to run it.

## Option A — Run from Python source (works on any OS with Python 3.10+)

```bash
cd ElectronicsDiagnostic
python3 -m venv build/venv
# Windows: build\venv\Scripts\activate
source build/venv/bin/activate
pip install -e ".[pdf,test]"
python -m eda
```

You are now in the `repair> ` REPL. Type `help`.

## Option B — Run the standalone Windows EXE

1. Build the EXE once on a Windows machine with Python 3.10+:

   ```bat
   scripts\build_windows.bat
   ```

   The output is in `dist\ElectronicsDiagnostic\`.

2. Copy the contents of `dist\ElectronicsDiagnostic\` into the application
   root (next to `Models\`, `Knowledge\`, etc.). The folder then contains
   `ElectronicsDiagnostic.exe`, `Engine\`, `Models\`, `Knowledge\`, etc.

3. Double-click `ElectronicsDiagnostic.exe` or run it from CMD.

## First run

On first launch the application:

1. Creates its directory layout (`Config/`, `Documents/`, `Logs/`, etc.).
2. Initializes `Config/eda.sqlite3` with the full schema (tables, FTS5
   virtual tables, triggers).
3. Loads the curated knowledge base from `Knowledge/*.json` into the
   database.
4. Prints the LLM engine availability report. If no model is found, the
   report says so and the application still works (rule engine only).
5. Prints the safety banner.

Nothing reaches the network. The application runs fully offline after the
knowledge base is loaded (which only reads local files).

## Verifying it works

```bash
python -m pytest tests/ -q
```

All 74 tests should pass. Then:

```bash
python -m eda help
python -m eda new "Test board" "Test model"
python -m eda symptom "No power"
python -m eda measure "VIN = 19V"
python -m eda analyze
```

## Removing the application

Delete the folder. The application writes only inside its own root; no
registry keys, no home-directory dotfiles, no system services.
