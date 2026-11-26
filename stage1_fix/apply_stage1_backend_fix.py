#!/usr/bin/env python3
"""Apply Stage 1 backend safety fixes to ElectronicsDiagnostic.

This patch is intentionally limited to backend selection and CLI orchestration.
It creates timestamped backups before changing any source file and refuses to
patch if the checked-out source does not match the expected version.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Cannot safely patch {label}: expected exactly one matching block, found {count}. "
            "No files have been changed."
        )
    return text.replace(old, new, 1)


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    eda = root / "ElectronicsDiagnostic" / "src" / "eda"
    paths = {
        "backend": eda / "llm_backend.py",
        "config": eda / "config.py",
        "cli": eda / "cli.py",
    }
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        print("Project files not found. Run this from the ElectronicsDiagnosticSuite root.")
        for item in missing:
            print(f"  - {item}")
        return 2

    # Read all files and preserve each file's existing newline convention.
    originals: dict[str, str] = {}
    newlines: dict[str, str] = {}
    had_bom: dict[str, bool] = {}
    updated: dict[str, str] = {}
    for key, path in paths.items():
        raw_bytes = path.read_bytes()
        had_bom[key] = raw_bytes.startswith(b"\xef\xbb\xbf")
        raw = raw_bytes.decode("utf-8-sig")
        newlines[key] = "\r\n" if "\r\n" in raw else "\n"
        originals[key] = raw.replace("\r\n", "\n")
        updated[key] = originals[key]

    # ------------------------------------------------------------------
    # llm_backend.py
    # ------------------------------------------------------------------
    b = updated["backend"]
    b = replace_once(
        b,
        "* :class:`StubBackend` — deterministic, no model. Used by tests and as\n"
        "  the fallback when no model is configured. It emits *correct* tool\n"
        "  calls for the diagnostic cases it knows and an honest \"I don't have\n"
        "  enough information\" refusal otherwise. This is the upper-bound\n"
        "  reference for the benchmark: a perfect tool-caller.\n",
        "* :class:`StubBackend` — deterministic, no model. Reserved for tests,\n"
        "  explicit development use, and benchmarks. It is never an automatic\n"
        "  fallback for a real repair diagnosis.\n",
        "llm_backend.py module documentation",
    )
    b = replace_once(
        b,
        "    \"\"\"Deterministic backend used by tests and as the no-model fallback.\n\n"
        "    It does not call any model. For a small set of well-known diagnostic\n"
        "    shapes it emits the *correct* sequence of tool calls and a final\n"
        "    answer grounded in the tool results; for anything else it emits an\n"
        "    honest refusal. This makes the orchestrator + tool interface testable\n"
        "    without a real model, and gives the benchmark a \"perfect tool-caller\"\n"
        "    reference to compare real models against.\n"
        "    \"\"\"",
        "    \"\"\"Deterministic synthetic backend for tests, development, and benchmarks.\n\n"
        "    It does not call a model and must not be used for real repair diagnosis.\n"
        "    For a small set of known shapes it emits canned tool calls, which makes\n"
        "    it useful as a deterministic benchmark fixture only.\n"
        "    \"\"\"",
        "StubBackend documentation",
    )

    # Isolate the ServerRuntime section so the same model_path method in the
    # legacy llama-cli class is not accidentally modified.
    server_marker = "# OpenAI-compatible persistent server backend (the real target)"
    cloud_marker = "# Cloud backend (reference only — NOT the target deployment)"
    if b.count(server_marker) != 1 or b.count(cloud_marker) != 1:
        raise RuntimeError("Cannot safely locate ServerRuntime section; no files have been changed.")
    before_server, server_and_after = b.split(server_marker, 1)
    server_section, after_server = server_and_after.split(cloud_marker, 1)
    server_section = replace_once(
        server_section,
        "        cfg_path = self.cfg.get(\"server_path\") or self.cfg.get(\"cli_path\")\n",
        "        # cli_path is reserved for llama-cli; never launch it as llama-server.\n"
        "        cfg_path = self.cfg.get(\"server_path\")\n",
        "ServerRuntime.server_path",
    )
    server_section = replace_once(
        server_section,
        "        return p if p.exists() else None\n\n"
        "    # -- lifecycle -------------------------------------------------------\n",
        "        return p if p.exists() else None\n\n"
        "    def is_available(self) -> bool:\n"
        "        \"\"\"Return whether the server executable and GGUF model exist.\n\n"
        "        This is a non-starting availability check. The server is launched\n"
        "        lazily by chat(), not while displaying backend status.\n"
        "        \"\"\"\n"
        "        return self.server_path() is not None and self.model_path() is not None\n\n"
        "    # -- lifecycle -------------------------------------------------------\n",
        "ServerRuntime availability method",
    )
    server_section = replace_once(
        server_section,
        "                f\"Endpoint: http://{self._host}:{self._port}/v1/chat/completions\\n\"\n"
        "                f\"Available: {cli is not None and model is not None}\")\n",
        "                f\"Endpoint: http://{self._host}:{self._port}/v1/chat/completions\\n\"\n"
        "                f\"Available: {self.is_available()}\")\n",
        "ServerRuntime availability report",
    )
    b = before_server + server_marker + server_section + cloud_marker + after_server

    disabled_marker = "# Disabled backend (rule-engine-only)"
    factory_marker = "# Factory + singleton"
    before_disabled, disabled_and_after = b.split(disabled_marker, 1)
    disabled_section, after_disabled = disabled_and_after.split(factory_marker, 1)
    old_disabled = (
        "\n# ---------------------------------------------------------------------------\n\n"
        "class DisabledBackend:\n"
        "    name = \"disabled\"\n"
        "    model = \"\"\n\n"
        "    def is_available(self) -> bool:\n"
        "        return False\n\n"
        "    def availability_report(self) -> str:\n"
        "        return (\"Backend: disabled\\n\"\n"
        "                \"The deterministic rule engine does all reasoning. \"\n"
        "                \"No LLM is loaded.\")\n\n"
        "    def chat(self, messages: List[Message],\n"
        "             max_tokens: Optional[int] = None,\n"
        "             temperature: Optional[float] = None) -> Optional[BackendResponse]:\n"
        "        return None\n\n"
        "    def shutdown(self) -> None:\n"
        "        pass\n"
    )
    new_disabled = (
        "\n# ---------------------------------------------------------------------------\n\n"
        "class DisabledBackend:\n"
        "    name = \"disabled\"\n"
        "    model = \"\"\n\n"
        "    def __init__(self, reason: str = \"\"):\n"
        "        self.reason = reason\n\n"
        "    def is_available(self) -> bool:\n"
        "        return False\n\n"
        "    def availability_report(self) -> str:\n"
        "        report = (\"Backend: disabled\\n\"\n"
        "                  \"The deterministic rule engine does all reasoning. \"\n"
        "                  \"No LLM is loaded.\")\n"
        "        if self.reason:\n"
        "            report += f\"\\nReason: {self.reason}\"\n"
        "        return report\n\n"
        "    def chat(self, messages: List[Message],\n"
        "             max_tokens: Optional[int] = None,\n"
        "             temperature: Optional[float] = None) -> Optional[BackendResponse]:\n"
        "        return None\n\n"
        "    def shutdown(self) -> None:\n"
        "        pass\n"
    )
    disabled_section = replace_once(disabled_section, old_disabled, new_disabled, "DisabledBackend")
    b = before_disabled + disabled_marker + disabled_section + factory_marker + after_disabled

    factory_marker = "# Factory + singleton"
    small_helpers_marker = "# Small helpers"
    before_factory, factory_and_after = b.split(factory_marker, 1)
    factory_section, after_factory = factory_and_after.split(small_helpers_marker, 1)
    old_factory = (
        "\ndef get_backend(settings: Optional[Dict[str, Any]] = None) -> Backend:\n"
        "    \"\"\"Return the configured backend, constructing it on first use.\n\n"
        "    Backends, in priority order if ``backend == 'auto'``:\n"
        "    1. ``llama_server`` — if a model AND server binary are present.\n"
        "    2. ``llama_cli`` — if a model AND CLI binary are present (legacy).\n"
        "    3. ``stub`` — always available (deterministic reference / fallback).\n"
        "    \"\"\"\n"
        "    global _backend, _runtime\n"
        "    if _backend is not None:\n"
        "        return _backend\n"
        "    s = settings or config.load_settings()\n"
        "    cfg = s.get(\"engine\", {})\n"
        "    choice = cfg.get(\"backend\", \"auto\")\n"
        "    if choice == \"disabled\":\n"
        "        _backend = DisabledBackend()\n"
        "    elif choice == \"stub\":\n"
        "        _backend = StubBackend(s)\n"
        "    elif choice == \"cloud\":\n"
        "        _backend = CloudBackend(s)\n"
        "    elif choice == \"llama_server\":\n"
        "        _runtime = ServerRuntime(s)\n"
        "        _backend = _runtime\n"
        "    elif choice == \"llama_cli\":\n"
        "        _backend = LlamaCliBackend(s)\n"
        "    else:  # auto\n"
        "        rt = ServerRuntime(s)\n"
        "        if rt.server_path() and rt.model_path():\n"
        "            _runtime = rt\n"
        "            _backend = rt\n"
        "        else:\n"
        "            cli = LlamaCliBackend(s)\n"
        "            if cli.is_available():\n"
        "                _backend = cli\n"
        "            else:\n"
        "                _backend = StubBackend(s)\n"
        "    return _backend\n"
    )
    new_factory = (
        "\ndef get_backend(settings: Optional[Dict[str, Any]] = None) -> Backend:\n"
        "    \"\"\"Return the configured backend without silently selecting a Stub.\n\n"
        "    In auto mode, prefer a local llama-server, then legacy llama-cli. If\n"
        "    neither is present, use DisabledBackend so the CLI falls back to the\n"
        "    deterministic rule engine. StubBackend is opt-in for development and\n"
        "    benchmarks only; it is never an automatic fallback.\n"
        "    \"\"\"\n"
        "    global _backend, _runtime\n"
        "    if _backend is not None:\n"
        "        return _backend\n"
        "    s = settings or config.load_settings()\n"
        "    cfg = s.get(\"engine\", {})\n"
        "    requested = str(cfg.get(\"backend\", \"auto\")).strip().lower()\n"
        "    # Compatibility with settings written by older releases.\n"
        "    choice = {\"llama_cpp\": \"auto\", \"llama-cpp\": \"auto\",\n"
        "              \"openai_api\": \"auto\", \"openai-api\": \"auto\"}.get(requested, requested)\n"
        "    if choice == \"disabled\":\n"
        "        _backend = DisabledBackend()\n"
        "    elif choice == \"stub\":\n"
        "        _backend = StubBackend(s)\n"
        "    elif choice == \"cloud\":\n"
        "        _backend = CloudBackend(s)\n"
        "    elif choice == \"llama_server\":\n"
        "        _runtime = ServerRuntime(s)\n"
        "        _backend = _runtime\n"
        "    elif choice == \"llama_cli\":\n"
        "        _backend = LlamaCliBackend(s)\n"
        "    elif choice == \"auto\":\n"
        "        rt = ServerRuntime(s)\n"
        "        if rt.is_available():\n"
        "            _runtime = rt\n"
        "            _backend = rt\n"
        "        else:\n"
        "            cli = LlamaCliBackend(s)\n"
        "            if cli.is_available():\n"
        "                _backend = cli\n"
        "            else:\n"
        "                _backend = DisabledBackend(\n"
        "                    \"Auto mode found neither a usable llama-server + GGUF model "
        "nor a llama-cli + GGUF model. The deterministic rule engine will be used; "
        "StubBackend is not an automatic fallback.\")\n"
        "    else:\n"
        "        _backend = DisabledBackend(\n"
        "            f\"Unsupported backend setting {requested!r}; no LLM will be loaded. \"\n"
        "            \"Choose auto, llama_server, llama_cli, cloud, disabled, or stub.\")\n"
        "    return _backend\n"
    )
    factory_section = replace_once(factory_section, old_factory, new_factory, "get_backend factory")
    b = before_factory + factory_marker + factory_section + small_helpers_marker + after_factory
    updated["backend"] = b

    # ------------------------------------------------------------------
    # config.py
    # ------------------------------------------------------------------
    c = updated["config"]
    c = replace_once(
        c,
        "        \"backend\": \"llama_cpp\",          # llama_cpp | openai_api | disabled\n"
        "        \"model_file\": None,              # e.g. \"qwen2.5-1.5b-instruct-q4_k_m.gguf\"\n"
        "        \"model_dir\": \"Models\",\n"
        "        \"cli_path\": None,                # llama-cli / main executable\n",
        "        \"backend\": \"auto\",               # auto | llama_server | llama_cli | cloud | disabled\n"
        "        \"model_file\": None,              # e.g. \"qwen2.5-1.5b-instruct-q4_k_m.gguf\"\n"
        "        \"model_dir\": \"Models\",\n"
        "        \"server_path\": None,             # llama-server executable (optional explicit path)\n"
        "        \"cli_path\": None,                # llama-cli executable (legacy fallback)\n",
        "config.py default backend settings",
    )
    updated["config"] = c

    # ------------------------------------------------------------------
    # cli.py
    # ------------------------------------------------------------------
    cli = updated["cli"]
    cli = replace_once(
        cli,
        "    backend = LB.get_backend(settings)\n"
        "    if not backend.is_available():\n"
        "        # Rule engine fallback.\n"
        "        analysis = reasoning.analyze(s.id, syms, ms)\n"
        "        return (analysis.to_text() + \"\\n\\n[backend unavailable: \"\n"
        "                + backend.availability_report() + \"]\")\n"
        "    orch = ORCH.Orchestrator(settings, backend=backend)\n",
        "    backend = LB.get_backend(settings)\n"
        "    # StubBackend generates canned/example tool calls. Never execute it\n"
        "    # as part of a real repair diagnosis, even when explicitly selected\n"
        "    # in settings or supplied through --backend.\n"
        "    if isinstance(backend, LB.StubBackend):\n"
        "        analysis = reasoning.analyze(s.id, syms, ms)\n"
        "        return (analysis.to_text() +\n"
        "                \"\\n\\n[LLM skipped: StubBackend is reserved for development/benchmarks. \"\n"
        "                \"The deterministic rule engine was used instead.]\")\n"
        "    if not backend.is_available():\n"
        "        # Rule engine fallback.\n"
        "        analysis = reasoning.analyze(s.id, syms, ms)\n"
        "        return (analysis.to_text() + \"\\n\\n[LLM unavailable: \"\n"
        "                + backend.availability_report() + \"]\")\n"
        "    orch = ORCH.Orchestrator(settings, backend=backend)\n",
        "cli.py orchestrate backend guard",
    )
    cli = replace_once(
        cli,
        "    \"\"\"backend [show|stub|llama_server|llama_cli|cloud|disabled|stop]\"\"\"\n"
        "    if not args or args[0] == \"show\":\n"
        "        b = LB.get_backend()\n"
        "        return b.availability_report()\n"
        "    cmd = args[0]\n"
        "    if cmd == \"stop\":\n"
        "        LB.shutdown()\n"
        "        return \"Backend shut down (next call will rebuild it).\"\n"
        "    valid = {\"stub\", \"llama_server\", \"llama_cli\", \"cloud\", \"disabled\"}\n"
        "    if cmd not in valid:\n"
        "        return f\"Usage: backend [{'|'.join(sorted(valid))}]|show|stop\"\n"
        "    s = config.load_settings()\n"
        "    s.setdefault(\"engine\", {})[\"backend\"] = cmd\n"
        "    config.save_settings(s)\n"
        "    LB.shutdown()\n"
        "    b = LB.get_backend(s)\n"
        "    return f\"Backend set to '{cmd}'.\\n\" + b.availability_report()\n",
        "    \"\"\"backend [show|auto|stub|llama_server|llama_cli|cloud|disabled|stop]\"\"\"\n"
        "    if not args or args[0] == \"show\":\n"
        "        b = LB.get_backend()\n"
        "        return b.availability_report()\n"
        "    cmd = args[0].strip().lower()\n"
        "    if cmd == \"stop\":\n"
        "        LB.shutdown()\n"
        "        return \"Backend shut down (next call will rebuild it).\"\n"
        "    # Older setting names are accepted as aliases for automatic local discovery.\n"
        "    cmd = {\"llama_cpp\": \"auto\", \"llama-cpp\": \"auto\",\n"
        "           \"openai_api\": \"auto\", \"openai-api\": \"auto\"}.get(cmd, cmd)\n"
        "    valid = {\"auto\", \"stub\", \"llama_server\", \"llama_cli\", \"cloud\", \"disabled\"}\n"
        "    if cmd not in valid:\n"
        "        return f\"Usage: backend [{'|'.join(sorted(valid))}]|show|stop\"\n"
        "    s = config.load_settings()\n"
        "    s.setdefault(\"engine\", {})[\"backend\"] = cmd\n"
        "    config.save_settings(s)\n"
        "    LB.shutdown()\n"
        "    b = LB.get_backend(s)\n"
        "    note = (\"\\nNote: Stub is development/benchmark only; 'orchestrate' will bypass it.\"\n"
        "            if cmd == \"stub\" else \"\")\n"
        "    return f\"Backend set to '{cmd}'.\\n\" + b.availability_report() + note\n",
        "cli.py backend selector",
    )
    cli = replace_once(
        cli,
        "backend [show|stub|llama_server|llama_cli|cloud|disabled|stop]",
        "backend [show|auto|stub|llama_server|llama_cli|cloud|disabled|stop]",
        "cli.py help text for backend command",
    )
    updated["cli"] = cli

    # Validate changes before making any on-disk modification.
    for key, text in updated.items():
        if text == originals[key]:
            raise RuntimeError(f"Internal patch error: {key} was not changed.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: list[tuple[Path, Path]] = []
    for key, path in paths.items():
        backup = path.with_name(path.name + f".bak.stage1_{stamp}")
        shutil.copy2(path, backup)
        backups.append((path, backup))

    try:
        for key, path in paths.items():
            restored = updated[key].replace("\n", newlines[key]).encode("utf-8")
            if had_bom[key]:
                restored = b"\xef\xbb\xbf" + restored
            path.write_bytes(restored)
    except Exception:
        # Roll back from backups if any write fails part-way through.
        for path, backup in backups:
            shutil.copy2(backup, path)
        raise

    print("Stage 1 backend safety fix applied.")
    print("Updated files:")
    for path, backup in backups:
        print(f"  {path.relative_to(root)}")
        print(f"    backup: {backup.name}")
    print("\nNo tests, benchmarks, downloads, commits, or pushes were run.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PATCH FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
