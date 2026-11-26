# Offline Usage

The application is fully offline after the one-time preparation step
(see [MODEL_PREPARATION.md](MODEL_PREPARATION.md)).

## What "offline" means here

- No internet connection is required at runtime.
- No cloud API, no remote server, no subscription, no account.
- No telemetry, no "phone-home", no update check.
- The model file lives on the local disk.
- The reasoning engine and the knowledge base are local.
- All documents you import stay on the local disk in
  `ElectronicsDiagnostic\Documents\`.

## What "offline" does NOT mean

- The one-time model download in `scripts/prepare_model.py` does require
  internet. After that, none.
- If you chose to install `reportlab` for PDF reports, pip needs internet
  during install. The Markdown report is always available without any
  optional dependency.

## Verifying offline operation

```bash
# Disconnect the network, then:
python -m eda settings
python -m eda help
python -m eda new "Test" "Test"
python -m eda symptom "No power"
python -m eda measure "VIN = 19V"
python -m eda analyze
```

All of the above must work with the network cable unplugged. The
`tests/test_cli.py::test_offline_startup` test asserts that the
application boots and reports the LLM as unavailable (or with no model)
without touching the network.

## Moving the application to the target machine

1. Build the EXE on a Windows machine (`scripts\build_windows.bat`).
2. Run `scripts\prepare_model.py` once (on any machine with internet) and
   copy the resulting `Models\*.gguf` and `Config\settings.json`.
3. Copy a `llama.cpp` `llama-cli.exe` into `Engine\`.
4. Copy the entire `ElectronicsDiagnostic\` folder to the target machine
   by any means (USB, network share, etc.).
5. Double-click `ElectronicsDiagnostic.exe`. No install, no admin rights.

## Backup and reset

- Your repair sessions, measurements, hypotheses, reports and imported
  documents live in `Config\eda.sqlite3`, `Documents\`, and `Reports\`.
  Back up those three to back up your work.
- To reset the application to first-run state: delete `Config\eda.sqlite3`
  (the database will be recreated on next launch) and the `Documents\`,
  `Reports\`, `Temp\` folders. `Models\` and `Knowledge\` are left alone.
