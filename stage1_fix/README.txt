Stage 1 — Backend / Stub safety patch

What it changes:
- auto backend no longer falls back to StubBackend.
- no detected real local model => DisabledBackend and rule-engine fallback.
- StubBackend is bypassed by the real CLI orchestrate command.
- adds a proper ServerRuntime.is_available() method.
- stops treating cli_path (llama-cli) as a llama-server executable path.
- supports auto and legacy setting aliases in the backend command/factory.
- changes the default backend setting from the unsupported llama_cpp label to auto.

How to apply (Windows PowerShell):
1. Extract this folder somewhere.
2. Open PowerShell in the ElectronicsDiagnosticSuite project root.
3. Run:
   powershell -ExecutionPolicy Bypass -File "PATH_TO_EXTRACTED_FOLDER\Apply_Stage1_Backend_Fix.ps1"

The script creates timestamped backups beside each changed source file.
It does not run tests, benchmarks, downloads, commits, or pushes.

Expected project-root layout:
ElectronicsDiagnosticSuite\
  ElectronicsDiagnostic\src\eda\llm_backend.py
  ElectronicsDiagnostic\src\eda\config.py
  ElectronicsDiagnostic\src\eda\cli.py

After applying, review the three source changes and run your normal workflow when ready.
