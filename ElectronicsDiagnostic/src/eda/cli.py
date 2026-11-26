"""Command-line interface for the Electronics Diagnostic Assistant.

Usage::

    ElectronicsDiagnostic.exe            # interactive REPL
    ElectronicsDiagnostic.exe <command>  # one-shot command
    ElectronicsDiagnostic.exe --analyze  # analyze current session

Commands are listed in the ``help`` action. Natural-language input is
also accepted: if a line does not match a known command, the CLI
attempts to interpret it as a symptom, a measurement, or a freeform
question routed to the reasoning engine.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import List, Optional

from . import config
from . import database
from . import sessions
from . import measurements as M
from . import documents as D
from . import retrieval
from . import reasoning
from . import reporting
from . import knowledge_base
from . import llm_engine
from . import safety
from . import llm_backend as LB
from . import orchestrator as ORCH
from . import tool_interface as TI
from . import context_manager as CM
from . import model_registry as MR
from .learning_system import LearningSession, ExplanationLevel
from .learning_system import db as LDB
from .learning_system import RepairToLearning


class Session:
    """The currently-open session. Stored as a module-level holder so the
    REPL persists it across commands. ``None`` means no session is open."""
    current: Optional[sessions.Session] = None


# ---- One-shot command handlers ----------------------------------------

def cmd_help(args: List[str]) -> str:
    return _HELP_TEXT


def cmd_new(args: List[str]) -> str:
    if not args:
        return "Usage: new <title> [board model...]"
    title = args[0]
    board_model = " ".join(args[1:]) if len(args) > 1 else ""
    s = sessions.create(title, board_model)
    Session.current = s
    return (f"Created session #{s.id}: {s.title} ({s.board_model or 'no model'})\n"
            f"Use 'symptom', 'measure', 'analyze' to record data.")


def cmd_open(args: List[str]) -> str:
    if not args:
        rows = sessions.list_sessions()
        if not rows:
            return "No sessions yet. Use 'new <title>' to create one."
        lines = ["Sessions:"]
        for r in rows:
            lines.append(f"  #{r.id}  [{r.status}]  {r.title}  ({r.board_model or ''})  created {r.created_at}")
        lines.append("Use 'open <id>' to open one.")
        return "\n".join(lines)
    try:
        sid = int(args[0])
    except ValueError:
        return f"'{args[0]}' is not a session id."
    s = sessions.get(sid)
    if not s:
        return f"No session with id {sid}."
    Session.current = s
    return f"Opened session #{s.id}: {s.title}"


def cmd_sessions(args: List[str]) -> str:
    rows = sessions.list_sessions(open_only=False)
    if not rows:
        return "No sessions yet. Use 'new <title>' to create one."
    lines = ["Sessions:"]
    for r in rows:
        marker = "*" if (Session.current and r.id == Session.current.id) else " "
        lines.append(f"{marker}#{r.id}  [{r.status}]  {r.title}  ({r.board_model or ''})  created {r.created_at}")
    return "\n".join(lines)


def _require_session() -> Optional[sessions.Session]:
    if Session.current is None:
        print("No active session. Use 'new <title>' or 'open <id>' first.")
        return None
    return Session.current


def cmd_symptom(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    if not args:
        return "Usage: symptom <description>"
    text = " ".join(args)
    sessions.add_symptom(s.id, text)
    return f"Recorded symptom: {text}"


def cmd_measure(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    if not args:
        return ("Usage: measure <name> = <value>[unit] [@ point]\n"
                "Example: measure VIN = 19.4V @ board input\n"
                "Example: measure resistance_to_ground = 12.4ohm")
    line = "measure " + " ".join(args)
    parsed = M.parse_line(line)
    if not parsed:
        return (f"Could not parse: '{line}'\n"
                "Expected: measure NAME = VALUE UNIT [@ point]")
    mid = M.add(s.id, parsed)
    return (f"Recorded measurement #{mid}: {parsed['name']} = "
            f"{parsed.get('value')} {parsed.get('unit','')}".strip())


def cmd_analyze(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    syms = sessions.symptoms(s.id)
    ms = M.list_for(s.id)
    if not syms and not ms:
        return ("No symptoms or measurements yet. Add at least a symptom "
                "(symptom <text>) and one measurement (measure ...) first.")
    analysis = reasoning.analyze(s.id, syms, ms)
    text = analysis.to_text()
    # Optionally paraphrase with the LLM if available.
    para = llm_engine.paraphrase_analysis(text, syms)
    if para:
        text = text + "\n\n--- LLM explanation ---\n" + para
    return text


def cmd_next_test(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    syms = sessions.symptoms(s.id)
    ms = M.list_for(s.id)
    analysis = reasoning.analyze(s.id, syms, ms)
    nt = analysis.next_test
    if not nt:
        return "No next test could be derived from the current evidence."
    out = [
        f"Next most informative test: {nt.test}",
        f"  Where: {nt.where_to_measure}",
        f"  Reference: {nt.reference_point}",
        f"  Expected: {nt.expected}",
        f"  Why: {nt.rationale}",
    ]
    if nt.caution:
        out.append(f"  {nt.caution}")
    if nt.needs_document:
        out.append("")
        out.append(nt.needs_document.render_prompt())
    return "\n".join(out)


def cmd_add_file(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    if not args:
        return ("Usage: add-file <path> [path2 ...]\n"
                "Drag-and-drop the file into the CMD window and press Enter.")
    results = []
    for raw in args:
        # Drag-and-drop on Windows quotes paths with spaces; shlex handles that,
        # but a user can also paste a bare path. Strip surrounding quotes.
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            continue
        # Handle Windows-style D:\ paths on Linux gracefully.
        path = Path(raw)
        try:
            res = D.import_file(path, session_id=s.id, requested_by="user")
        except Exception as e:
            results.append(f"  {raw}: FAILED ({e})")
            continue
        results.append(
            f"  {res.file_name}: {res.status} "
            f"({res.file_type}, {res.size_bytes} bytes, sha256={res.sha256[:12]}…) "
            f"— {res.message}"
        )
    return "Import results:\n" + "\n".join(results)


def cmd_documents(args: List[str]) -> str:
    s = _require_session()
    if not s:
        # Show all docs in the library when no session is open.
        docs = D.list_documents()
    else:
        docs = D.list_documents(session_id=s.id)
    if not docs:
        return "No documents imported yet. Use 'add-file <path>'."
    lines = ["Documents:"]
    for d in docs:
        lines.append(
            f"  #{d['id']}  {d['file_name']}  ({d['file_type']}, {d['status']}, "
            f"{d['size_bytes']} bytes, sha256={d['sha256'][:12]}…)"
        )
    return "\n".join(lines)


def cmd_search(args: List[str]) -> str:
    if not args:
        return "Usage: search <query>"
    query = " ".join(args)
    res = retrieval.search_all(query, limit=10)
    lines = []
    if res["knowledge"]:
        lines.append("Knowledge base:")
        for r in res["knowledge"][:6]:
            lines.append(f"  - [{r['confidence']}] {r['topic']}: {r['fact']}")
            if r.get("source_title"):
                lines.append(f"      source: {r['source_title']}")
    if res["documents"]:
        lines.append("")
        lines.append("Imported documents:")
        for r in res["documents"][:6]:
            lines.append(f"  - {r['file_name']} p.{r['page']}: {r['snippet'][:120]}")
    if not lines:
        return f"No results for '{query}'."
    return "\n".join(lines)


def cmd_notes(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    if not args:
        return f"Notes for session #{s.id}:\n{s.notes or '(none)'}"
    text = " ".join(args)
    sessions.set_notes(s.id, text)
    return "Note added."


def cmd_report(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    pdf = "--pdf" in args
    md = reporting.save_markdown(s.id)
    lines = [f"Markdown report saved: {md}"]
    if pdf:
        p = reporting.save_pdf(s.id)
        if p:
            lines.append(f"PDF report saved: {p}")
        else:
            lines.append("PDF generation failed (reportlab not available?).")
    return "\n".join(lines)


def cmd_remove_document(args: List[str]) -> str:
    if not args:
        return "Usage: remove-document <id>"
    try:
        did = int(args[0])
    except ValueError:
        return f"'{args[0]}' is not a document id."
    ok = D.remove_document(did)
    return f"Removed document #{did}." if ok else f"No document with id {did}."


def cmd_settings(args: List[str]) -> str:
    eng = llm_engine.get_engine()
    lines = [
        "Settings:",
        f"  app_version: {config.APP_VERSION}",
        f"  app_root: {config.app_root()}",
        f"  db_path: {config.db_path()}",
        "",
        "LLM engine:",
        eng.availability_report(),
        "",
        f"Knowledge facts loaded: {database.count_facts()}",
    ]
    if args and args[0] == "show":
        import json
        lines.append("")
        lines.append(json.dumps(config.load_settings(), indent=2, ensure_ascii=False))
    return "\n".join(lines)


def cmd_exit(args: List[str]) -> str:
    raise EOFError


# ---- Phase 2 engine commands ------------------------------------------

def _json_arg(args: List[str], key: str, default=None):
    """Pull a --key value pair from a token list."""
    if f"--{key}" in args:
        i = args.index(f"--{key}")
        if i + 1 < len(args):
            return args[i + 1]
    return default


def _kv_args(args: List[str]) -> dict:
    """Parse trailing --key value tokens into a dict (values are strings)."""
    out = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("--") and i + 1 < len(args):
            out[tok[2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return out


def cmd_engines(args: List[str]) -> str:
    from .engines import base as EB
    names = EB.available()
    lines = ["Engines registered:"]
    for n in names:
        try:
            eng = EB.get(n)
            lines.append(f"  {n:24s} v{eng.version}  {eng.summary[:60]}")
        except Exception as e:
            lines.append(f"  {n:24s} (load failed: {e})")
    return "\n".join(lines)


def cmd_engine(args: List[str]) -> str:
    """engine <name> <method> [--key value ...]"""
    if len(args) < 2:
        return ("Usage: engine <name> <method> [--key value ...]\n"
                "Use 'engines' to list available engines.")
    name, method = args[0], args[1]
    inputs = _kv_args(args[2:])
    # Coerce numeric-looking values.
    coerced = {}
    for k, v in inputs.items():
        try:
            if "." in v or "e" in v.lower():
                coerced[k] = float(v)
            else:
                coerced[k] = int(v)
        except (ValueError, TypeError):
            coerced[k] = v
    from .engines import base as EB
    r = EB.call(name, method, coerced)
    if not r.ok:
        return f"[{name}.{method}] ERROR: {r.error}"
    # Render the value nicely.
    v = r.value
    if isinstance(v, dict):
        import json
        return f"[{name}.{method}] OK\n" + json.dumps(v, indent=2, default=str, ensure_ascii=False)
    return f"[{name}.{method}] OK\n{r.render()}"


def cmd_validate(args: List[str]) -> str:
    """validate <name> <value> <unit> [--lo N --hi N --condition S]"""
    if not args:
        return ("Usage: validate <name> <value> <unit> [--lo N --hi N --condition S]")
    name = args[0]
    if len(args) < 3:
        return "Need: <name> <value> <unit>"
    try:
        value = float(args[1])
    except ValueError:
        return f"'{args[1]}' is not a number"
    unit = args[2]
    kv = _kv_args(args[3:])
    rng = None
    if "lo" in kv and "hi" in kv:
        try:
            rng = {"lo": float(kv["lo"]), "hi": float(kv["hi"]),
                   "source": kv.get("source", "explicit")}
        except ValueError:
            return "lo/hi must be numbers"
    from .engines import base as EB
    inputs = {"name": name, "value": value, "unit": unit,
              "operating_condition": kv.get("condition", "")}
    if rng is not None:
        inputs["range"] = rng
    r = EB.call("measurement_validation", "validate", inputs)
    if not r.ok:
        return f"ERROR: {r.error}"
    import json
    return json.dumps(r.value, indent=2, default=str, ensure_ascii=False)


def cmd_hypotheses(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    from .engines import base as EB
    r = EB.call("hypotheses", "analyze", {"session_id": s.id})
    if not r.ok:
        return f"ERROR: {r.error}"
    out = ["Structured hypotheses (v2):"]
    for h in r.value["hypotheses"]:
        out.append(f"  {h['rank']}. [{h['status']}] {h['description']}")
        out.append(f"     basis: {h['confidence_basis']}")
        if h["supporting"]:
            out.append("     supporting: " + "; ".join(h["supporting"]))
        if h["contradicting"]:
            out.append("     contradicting: " + "; ".join(h["contradicting"]))
        if h["required_evidence"]:
            out.append("     required: " + "; ".join(h["required_evidence"]))
    if r.value["confirmed"]:
        out.append("")
        out.append("CONFIRMED: " + r.value["confirmed"])
    return "\n".join(out)


def cmd_next_best_test(args: List[str]) -> str:
    s = _require_session()
    if not s:
        return ""
    from .engines import base as EB
    r = EB.call("next_best_test", "recommend", {"session_id": s.id})
    if not r.ok:
        return f"No next-best test: {r.error}"
    rec = r.value
    out = [
        f"Next best test: {rec['test']}",
        f"  Where: {rec['where']}",
        f"  Reference: {rec['reference']}",
        f"  Board state: {rec['board_state']}",
        f"  Adapter/battery: {rec['adapter_battery']}",
        f"  Instrument: {rec['instrument']}",
        f"  Expected: {rec['expected']}  (source: {rec['expected_supported_by']})",
        f"  Distinguishes: {', '.join(rec['distinguishes'])}  "
        f"(information gain {rec['information_gain']})",
        f"  Rationale: {rec['rationale']}",
    ]
    if rec["safety_precautions"]:
        out.append(f"  {rec['safety_precautions']}")
    out.append("  Outcomes:")
    for o in rec["outcomes"]:
        out.append(f"    - {o['condition']} -> {o['implication']}")
    return "\n".join(out)


def cmd_sequence(args: List[str]) -> str:
    """sequence [default|list|analyze|get <name>]"""
    from .engines import base as EB
    if not args or args[0] == "default":
        r = EB.call("power_sequence", "default_sequence", {})
        out = ["Default power sequence:"]
        for n in r.value:
            out.append(f"  {n['node_key']:18s} [{n['state']:3s}] "
                       f"deps={n.get('depends_on', [])} nom={n.get('nominal_voltage', '')}")
        return "\n".join(out)
    if args[0] == "list":
        r = EB.call("power_sequence", "list_sequences", {})
        return "\n".join(f"  {s['name']}: {s.get('board_model','')}" for s in r.value["sequences"]) or "(none saved)"
    if args[0] == "analyze":
        s = _require_session()
        if not s:
            return ""
        name = args[1] if len(args) > 1 else "default"
        r = EB.call("power_sequence", "analyze",
                    {"session_id": s.id, "sequence_name": name})
        if not r.ok:
            return f"ERROR: {r.error}"
        import json
        return json.dumps(r.value, indent=2, default=str, ensure_ascii=False)
    if args[0] == "get" and len(args) > 1:
        r = EB.call("power_sequence", "get_sequence", {"name": args[1]})
        import json
        return json.dumps(r.value, indent=2, default=str, ensure_ascii=False)
    return ("Usage: sequence [default|list|analyze [name]|get <name>]")


def cmd_topology(args: List[str]) -> str:
    """topology <add|netlist|on-net|origin|opens|shorts|unsupported> ..."""
    if not args:
        return ("Usage: topology <sub> ...\n"
                "  add <component> <pin> <net> [role]\n"
                "  netlist \"<ASCII netlist>\"\n"
                "  on-net <net>\n"
                "  origin <net>\n"
                "  opens\n"
                "  shorts\n"
                "  unsupported <format>")
    s = _require_session()
    if not s and args[0] not in ("unsupported",):
        return ""
    sub = args[0]
    rest = args[1:]
    from .engines import base as EB
    if sub == "add":
        if len(rest) < 3:
            return "Need: <component> <pin> <net> [role]"
        r = EB.call("topology", "add_node", {
            "session_id": s.id, "component": rest[0], "pin": rest[1],
            "net": rest[2], "role": rest[3] if len(rest) > 3 else "",
        })
        return "Added." if r.ok else f"ERROR: {r.error}"
    if sub == "netlist":
        if not rest:
            return "Need: <ASCII netlist string>"
        r = EB.call("topology", "parse_netlist",
                    {"session_id": s.id, "netlist": " ".join(rest)})
        return f"Added {r.value['added']}, skipped {len(r.value['skipped'])}." if r.ok else f"ERROR: {r.error}"
    if sub == "on-net":
        if not rest:
            return "Need: <net>"
        r = EB.call("topology", "components_on_net",
                    {"session_id": s.id, "net": rest[0]})
        return f"Components on {rest[0]}: {r.value['components']}" if r.ok else f"ERROR: {r.error}"
    if sub == "origin":
        if not rest:
            return "Need: <net>"
        r = EB.call("topology", "rail_origin",
                    {"session_id": s.id, "net": rest[0]})
        return f"Origin of {rest[0]}: {r.value['origin']}" if r.ok else f"ERROR: {r.error}"
    if sub == "opens":
        r = EB.call("topology", "find_opens", {"session_id": s.id})
        return f"Possible opens: {r.value['possible_opens']}" if r.ok else f"ERROR: {r.error}"
    if sub == "shorts":
        r = EB.call("topology", "find_shorts", {"session_id": s.id})
        return f"Possible internal shorts: {r.value['possible_internal_shorts']}" if r.ok else f"ERROR: {r.error}"
    if sub == "unsupported":
        fmt = rest[0] if rest else "unknown"
        r = EB.call("topology", "request_for_unsupported_format", {"format": fmt})
        return r.value["message"]
    return f"Unknown topology sub-command: {sub}"


def cmd_benchmark(args: List[str]) -> str:
    """benchmark [run|--held-out]"""
    from . import benchmark as B
    held = "--held-out" in args
    report = B.run_benchmark(held_out_only=held)
    return B.render_report_text(report)


def cmd_learn(args: List[str]) -> str:
    """learn record <actual_fault> [--replaced C1,C2] [--confirming test] [--outcome fixed] [--unverified]"""
    s = _require_session()
    if not s:
        return ""
    if not args or args[0] != "record":
        return ("Usage: learn record <actual_fault> "
                "[--replaced C1,C2] [--confirming test] [--outcome fixed] [--unverified]")
    kv = _kv_args(args[1:])
    fault = " ".join(a for a in args[1:] if not a.startswith("--"))
    # fault is the remaining non-flag tokens before any --key.
    # Recompute: take tokens after 'record' up to the first --flag.
    fault_tokens = []
    for a in args[1:]:
        if a.startswith("--"):
            break
        fault_tokens.append(a)
    fault = " ".join(fault_tokens)
    if not fault:
        return "actual_fault required"
    replaced = [c for c in (kv.get("replaced", "") or "").split(",") if c]
    confirming = kv.get("confirming", "")
    outcome = kv.get("outcome", "fixed")
    confirmed = "--unverified" not in args
    from . import learning as L
    r = L.record_case(s.id, fault, replaced, confirming, outcome,
                      technician_confirmed=confirmed)
    if not r.get("ok"):
        return f"ERROR: {r.get('error')}"
    return (f"Recorded case (version {r['version']}, "
            f"{'confirmed' if confirmed else 'unverified'}).")


def cmd_db(args: List[str]) -> str:
    """db <integrity|backup|recover|version>"""
    if not args:
        return "Usage: db <integrity|backup|recover|version>"
    from . import migrations as M
    if args[0] == "integrity":
        rep = M.integrity_check()
        import json
        return json.dumps(rep, indent=2, default=str, ensure_ascii=False)
    if args[0] == "backup":
        p = M.backup()
        return f"Backup saved: {p}"
    if args[0] == "recover":
        rep = M.recover()
        import json
        return json.dumps(rep, indent=2, default=str, ensure_ascii=False)
    if args[0] == "version":
        return f"Schema version: {M.current_version()}"
    return f"Unknown db sub-command: {args[0]}"


def cmd_exit(args: List[str]) -> str:
    raise EOFError


_HELP_TEXT = """\
Electronics Diagnostic Assistant — commands

Session
  new <title> [board model]      Create and open a new repair session
  open [<id>]                    Open a session by id; lists sessions if no id
  sessions                       List all sessions
  close                          Close the current session
  notes [<text>]                 Add a note, or show notes with no args

Diagnosis
  symptom <description>          Record a reported symptom
  measure <name> = <value>[unit] [@ point]
                                 Record a measurement, e.g.
                                   measure VIN = 19.4V @ board input
                                   measure +3VALW = 3.31V
                                   measure resistance_to_ground = 12.4ohm
  analyze                        Run the evidence-based reasoning engine
  next-test                      Show the recommended next test

Documents (never auto-scanned; only files you submit)
  add-file <path>                Import a datasheet/schematic/PDF/BoardView/BIOS
  documents                      List imported documents for this session
  remove-document <id>           Remove an imported document and its index
  search <query>                 Keyword search over knowledge base + documents

Reporting
  report [--pdf]                 Save a Markdown (and optionally PDF) report

Other
  settings [show]                 Show settings and LLM-engine status
  help                           This message
  exit                           Quit

Phase 2 computational engines (modular, deterministic; LLM only paraphrases)
  engines                        List registered engines
  engine <name> <method> [--key val ...]   Call an engine method
  validate <name> <val> <unit> [--lo N --hi N --condition S]
                                 Validate one measurement against a range
  hypotheses                     Run the structured hypothesis manager (v2)
  next-best-test                 Information-gain next-test recommendation
  sequence [default|list|analyze|get <name>]   Power-sequence analysis
  topology <add|netlist|on-net|origin|opens|shorts|unsupported> ...
                                 Circuit topology graph
  benchmark [run|--held-out]      Blind diagnostic benchmark
  learn record <fault> [--replaced C1,C2 --confirming test --outcome fixed --unverified]
                                 Record a completed repair case
  db <integrity|backup|recover|version>   DB integrity / recovery

Phase 3 LLM orchestration + model registry (LLM drives diagnosis; engines compute)
  orchestrate [--iterations N --backend B]   LLM-driven diagnosis via tool calls
  backend [show|stub|llama_server|llama_cli|cloud|disabled|stop]
                                 Show/switch the LLM backend
  models [list|download <key>|verify <file>|info <key>|bundle <dir>|import <dir>]
                                 Manage GGUF models (explicit download only)
  model-settings [key val]       Get/set n_ctx/n_threads/n_batch/n_gpu_layers/etc.
  model-profile [name]           Show/apply a CPU-only resource profile (low_memory|balanced|diagnostic)
  ram                            Show resident RAM of the running model process
  context                        Show the assembled context for the current session
  tools                          List the tools the orchestrator can call
  llm-benchmark [--backends stub,cloud]  Compare LLM backends on the orchestrator

Phase 4 Engineering Learning & Skill Development (separate subsystem; LLM-driven diagnosis unchanged)
  learning start [skill]        Start a Socratic learning path (one question at a time)
  learning mode <repair|learning>  Switch between repair and learning mode
  learning level <hint|guided|full>  Set the default explanation level (default: guided)
  learning skills                List all 16 skill categories + your mastery level
  learning progress              Summary of evidence-based mastery across skills
  learning gaps                  Skills you have not yet reached apprentice on
  learning review                Show concepts due for spaced review
  learning exercise [skill]      Get the next exercise (adaptive pick, or a skill)
  learning hint                  Get a hint (not the solution) for the current exercise
  learning explain [hint|guided|full]  Show the explanation at a level (default: guided)
  learning assess <answer>       Submit your answer to the current exercise (reasoning graded)
  learning case <session_id>    Convert a completed repair session into a learning case
  learning history [skill]      Show assessment history (append-only)

Safety: resistance measurements require power off and discharged caps.
        Live measurements require a stated ground reference and caution.
"""


# ---- LLM orchestration + model registry commands (Phase 3) -------------

def cmd_orchestrate(args: List[str]) -> str:
    """orchestrate [--iterations N] [--backend stub|llama_server|llama_cli|cloud|disabled]

    Run the LLM-driven diagnostic orchestrator on the current session.
    The orchestrator assembles the session context, calls specialized
    engines via the tool interface, compares hypotheses, and recommends
    the next test. The deterministic rule engine is always run as a
    cross-check; its output is shown if the LLM backend is unavailable.
    """
    s = _require_session()
    if not s:
        return ""
    syms = sessions.symptoms(s.id)
    ms = M.list_for(s.id)
    if not syms and not ms:
        return ("No symptoms or measurements yet. Add a symptom and a "
                "measurement first, then run orchestrate again.")
    iters = 8
    backend_override = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--iterations" and i + 1 < len(args):
            try:
                iters = max(1, min(20, int(args[i + 1])))
            except ValueError:
                pass
            i += 2
            continue
        if a == "--backend" and i + 1 < len(args):
            backend_override = args[i + 1]
            i += 2
            continue
        i += 1
    # Build the backend (override or current settings).
    settings = config.load_settings()
    if backend_override:
        settings.setdefault("engine", {})["backend"] = backend_override
        LB.shutdown()  # reset singleton so the new backend is built
    backend = LB.get_backend(settings)
    if not backend.is_available():
        # Rule engine fallback.
        analysis = reasoning.analyze(s.id, syms, ms)
        return (analysis.to_text() + "\n\n[backend unavailable: "
                + backend.availability_report() + "]")
    orch = ORCH.Orchestrator(settings, backend=backend)
    result = orch.run(s.id, max_iterations=iters)
    return result.to_text()


def cmd_backend(args: List[str]) -> str:
    """backend [show|stub|llama_server|llama_cli|cloud|disabled|stop]"""
    if not args or args[0] == "show":
        b = LB.get_backend()
        return b.availability_report()
    cmd = args[0]
    if cmd == "stop":
        LB.shutdown()
        return "Backend shut down (next call will rebuild it)."
    valid = {"stub", "llama_server", "llama_cli", "cloud", "disabled"}
    if cmd not in valid:
        return f"Usage: backend [{'|'.join(sorted(valid))}]|show|stop"
    s = config.load_settings()
    s.setdefault("engine", {})["backend"] = cmd
    config.save_settings(s)
    LB.shutdown()
    b = LB.get_backend(s)
    return f"Backend set to '{cmd}'.\n" + b.availability_report()


def cmd_models(args: List[str]) -> str:
    """models [list|download <key>|verify <filename>|info <key>]"""
    if not args or args[0] == "list":
        lines = []
        for klass in ("1b-1.5b", "3b-4b", "7b-8b"):
            info = MR.MODEL_CLASSES[klass]
            lines.append(f"== {info['label']} (class '{klass}') ==")
            lines.append(f"  {info['notes']}")
            for m in info["models"]:
                present = (MR.config.app_dir("Models") / m["filename"]).exists()
                mark = "[installed]" if present else "[not downloaded]"
                lines.append(f"  - {m['key']}")
                lines.append(f"      file: {m['filename']}  ~{m['size_gb_approx']} GB  {mark}")
            lines.append("")
        lines.append("Models live in Models/ and are NOT bundled into the EXE.")
        lines.append("Download with: models download <key>  (explicit only)")
        return "\n".join(lines)
    if args[0] == "download" and len(args) > 1:
        # Explicit confirmation prompt; never downloads silently.
        m = MR.find_model(args[1])
        if not m:
            return f"Unknown model key '{args[1]}'. Use 'models list'."
        if m.exists and "--force" not in args:
            return (f"{m.filename} is already present. "
                    "Pass --force to re-download.")
        print(f"Will download: {m.url}")
        print(f"Approx size:   {m.size_gb_approx} GB")
        print(f"Destination:   {m.path}")
        ans = input("Proceed? [y/N] ").strip().lower() if "--yes" not in args else "y"
        if ans not in ("y", "yes"):
            return "Aborted."
        r = MR.download_model(args[1], yes=True)
        if not r["ok"]:
            return f"Download failed: {r['note']}"
        return (f"Downloaded {m.filename}.\n"
                f"  SHA-256: {r['sha256']}\n"
                f"  Size:    {r['size_bytes']} bytes\n"
                f"  Verified: {r['verified']}  ({r['note']})")
    if args[0] == "verify" and len(args) > 1:
        from pathlib import Path
        p = Path(args[1])
        if not p.exists():
            return f"File not found: {p}"
        sha = MR.sha256_file(p)
        return f"SHA-256 of {p.name}:\n  {sha}\n  size: {p.stat().st_size} bytes"
    if args[0] == "info" and len(args) > 1:
        m = MR.find_model(args[1])
        if not m:
            return f"Unknown model key '{args[1]}'."
        import json
        return json.dumps(m.to_dict(), indent=2, ensure_ascii=False)
    if args[0] == "bundle" and len(args) > 1:
        from pathlib import Path
        out = Path(args[1])
        r = MR.export_bundle(out)
        return (f"Bundle written to {r['out_dir']}: "
                f"{r['models']} model(s), {r['engine_files']} engine file(s).")
    if args[0] == "import" and len(args) > 1:
        from pathlib import Path
        r = MR.import_bundle(Path(args[1]))
        if not r["ok"]:
            return f"Import failed: {r.get('note','')}"
        return (f"Imported {r['models_imported']} model(s), "
                f"{r['engine_imported']} engine file(s).")
    return ("Usage: models [list|download <key>|verify <file>|info <key>"
            "|bundle <dir>|import <dir>]")


def cmd_model_settings(args: List[str]) -> str:
    """model-settings [n_ctx N|n_threads N|n_batch N|n_gpu_layers N|
                       temperature F|max_tokens N|server_port N]"""
    if not args:
        eng = MR.runtime_settings()
        lines = ["Current runtime settings:"]
        for k in ("n_ctx", "n_threads", "n_batch", "n_gpu_layers",
                  "temperature", "max_tokens", "server_port", "server_host"):
            lines.append(f"  {k}: {eng.get(k)}")
        lines.append("")
        lines.append("Adjust with: model-settings <key> <value>")
        lines.append("Example: model-settings n_ctx 4096")
        return "\n".join(lines)
    if len(args) < 2:
        return "Usage: model-settings <key> <value>"
    key = args[0]
    valid = {"n_ctx", "n_threads", "n_batch", "n_gpu_layers",
             "temperature", "max_tokens", "timeout_seconds",
             "startup_timeout_seconds", "server_port", "server_host"}
    if key not in valid:
        return f"Unknown setting '{key}'. Valid: {sorted(valid)}"
    try:
        if key in ("temperature",):
            val: object = float(args[1])
        else:
            val = int(args[1])
    except ValueError:
        return f"Invalid value for {key}: {args[1]}"
    MR.set_runtime_settings(**{key: val})
    return f"Set {key} = {val}."


def cmd_model_profile(args: List[str]) -> str:
    """model-profile [name] — show or apply a CPU-only resource profile.

    Profiles (for the 1B–1.5B Q4_K_M class on the 8 GB target):
      low_memory  n_ctx=1024 threads=2 batch=256  (~1.2 GB)
      balanced    n_ctx=2048 threads=4 batch=512  (~1.5–2 GB)  [start here]
      diagnostic  n_ctx=4096 threads=4 batch=512  (~2.2–2.8 GB)

    No profile is claimed optimal; run the hardware validation harness
    on the target to confirm fit. Applying a profile only changes
    settings.engine; it does not reload a running model.
    """
    from . import profiles as PF
    if not args:
        return PF.profile_report()
    name = args[0]
    if name not in PF.list_profiles():
        return (f"Unknown profile '{name}'. Available: "
                + ", ".join(PF.list_profiles()))
    res = PF.apply_profile(name)
    if not res.get("ok"):
        return res.get("error", "could not apply profile")
    lines = [f"Applied profile '{name}'."]
    changed = res.get("changed", {})
    if changed:
        lines.append("Changed:")
        for k, (old, new) in changed.items():
            lines.append(f"  {k}: {old} -> {new}")
    else:
        lines.append("(no values changed; profile was already active)")
    if res.get("warning"):
        lines.append(f"WARNING: {res['warning']}")
    lines.append("")
    lines.append("Restart the model (or relaunch) for the new values to "
                 "take effect on a running server.")
    return "\n".join(lines)



def cmd_ram(args: List[str]) -> str:
    """ram — show resident memory of the running llama-server/CLI."""
    r = MR.ram_usage_mb()
    if not r["running"]:
        return ("No llama-server/llama-cli process is running. "
                "The model is loaded only when the orchestrator is invoked.")
    lines = [f"Running: yes (pid {r['pid']})",
             f"Resident RAM: {r['rss_mb']} MB"]
    if r.get("note"):
        lines.append(f"Note: {r['note']}")
    lines.append("")
    lines.append("Compare against your 8 GB target: leave ~3 GB for the OS.")
    return "\n".join(lines)


def cmd_context(args: List[str]) -> str:
    """context — show the assembled context for the current session."""
    s = _require_session()
    if not s:
        return ""
    ctx = CM.assemble(s.id)
    out = [ctx.text, "",
           f"--- context stats ---",
           f"total tokens: {ctx.total_tokens} / budget {ctx.budget_tokens}",
           f"KB facts included: {len(ctx.kb_facts)}",
           f"measurements omitted (still in DB): {ctx.measurements_omitted}"]
    return "\n".join(out)


def cmd_tools(args: List[str]) -> str:
    """tools — list the tools the orchestrator can call."""
    specs = TI.build_catalog()
    lines = [f"{len(specs)} tools available to the orchestrator:", ""]
    for s in specs:
        arg_strs = []
        for a, meta in s.args.items():
            tag = f"{a}:{meta['type']}" + ("*" if meta["required"] else "")
            arg_strs.append(tag)
        lines.append(f"  {s.name}({', '.join(arg_strs)})")
        lines.append(f"    {s.purpose}")
    return "\n".join(lines)


def cmd_llm_benchmark(args: List[str]) -> str:
    """llm-benchmark [--backends stub,cloud,auto] [--json]

    Run the LLM orchestrator benchmark across the requested backends
    and print a comparison table. The stub backend is the deterministic
    upper-bound reference; cloud is an optional reference model
    (requires the z-ai CLI); auto uses the configured backend.
    """
    import json
    from . import llm_benchmark as LBENCH
    backends = ["stub"]
    want_json = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--backends" and i + 1 < len(args):
            backends = [b.strip() for b in args[i + 1].split(",") if b.strip()]
            i += 2
            continue
        if a == "--json":
            want_json = True
            i += 1
            continue
        i += 1
    reports = []
    for name in backends:
        if name == "stub":
            b = LB.StubBackend()
        elif name == "cloud":
            b = LB.CloudBackend()
        elif name == "auto":
            b = LB.get_backend()
        else:
            # Treat as a settings backend key
            s = config.load_settings()
            s.setdefault("engine", {})["backend"] = name
            b = LB.get_backend(s)
        if not b.is_available():
            reports.append(LBENCH.BackendReport(backend=name,
                                                model=getattr(b, "model", ""),
                                                case_scores=[]))
            continue
        rep = LBENCH.run_backend(b)
        reports.append(rep)
    if want_json:
        return json.dumps([r.to_dict() for r in reports], indent=2,
                          default=str, ensure_ascii=False)
    return LBENCH.render_report(reports)


# ---- Learning subsystem commands (Phase 4) ------------------------------

# Module-level learning session (a single-user REPL). Created lazily on
# first use so importing the CLI has no cost when the user only repairs.
_learning_session: Optional[LearningSession] = None
# A module-level holder for the "current exercise" the user is working
# on, so 'learning exercise' -> 'learning hint'/'learning assess' form a
# short interaction without the user re-typing the exercise id.
_current_exercise_id: Optional[int] = None


def _get_learning_session() -> LearningSession:
    global _learning_session
    if _learning_session is None:
        _learning_session = LearningSession.create()
    return _learning_session


def cmd_learning(args: List[str]) -> str:
    """learning <start|mode|level|profile|skills|progress|review|exercise|
                 hint|explain|assess|case|history> [args...]

    The Engineering Learning & Skill Development subsystem. Distinct from
    the existing 'learn record' command (which records a completed repair
    case). Default explanation level is 'guided' (not full disclosure).
    """
    if not args:
        return _learning_help()
    sub = args[0]
    rest = args[1:]
    handlers = {
        "start": _learning_start, "mode": _learning_mode,
        "level": _learning_level, "profile": _learning_profile,
        "skills": _learning_skills, "progress": _learning_progress,
        "review": _learning_review, "exercise": _learning_exercise,
        "hint": _learning_hint, "explain": _learning_explain,
        "assess": _learning_assess, "case": _learning_case,
        "history": _learning_history, "gaps": _learning_gaps,
        "help": lambda a: _learning_help(),
    }
    if sub in handlers:
        return handlers[sub](rest) or ""
    return f"Unknown learning subcommand '{sub}'.\n" + _learning_help()


def _learning_help() -> str:
    return """learning — Engineering Learning & Skill Development (Phase 4)

Subcommands:
  learning start [ohm_kirchhoff|voltage_divider|mosfet|...]
                                 Start a Socratic learning path on a skill
                                 (asks one focused question at a time).
  learning mode <repair|learning>  Switch between repair and learning mode.
  learning level <hint|guided|full>  Set the default explanation level.
  learning profile               Show the active learning profile.
  learning skills                 List all 16 skill categories + your level.
  learning progress               Summary of mastery across skills.
  learning gaps                   Skills you have not yet reached apprentice on.
  learning review                 Show concepts due for spaced review.
  learning exercise [skill]       Get the next exercise (adaptive pick, or a skill).
  learning hint                   Get a hint for the current exercise.
  learning explain [hint|guided|full]  Show the explanation at a level.
  learning assess <your answer>   Submit your answer to the current exercise.
  learning case <session_id>      Convert a completed repair session into a learning case.
  learning history [skill]        Show assessment history (append-only).

Default level is 'guided' (hint/guided, not full disclosure).
Use 'learning explain full' only when you want the complete solution.
"""


def _learning_start(args: List[str]) -> str:
    sess = _get_learning_session()
    skill = args[0] if args else ""
    if not skill:
        # Pick the skill the user has the lowest mastery on.
        rec = sess.adaptive.recommend_next()
        skill = rec.skill_key
    turn = sess.socratic.start(skill)
    if not turn:
        avail = ", ".join(sess.socratic.available_skills())
        return (f"No Socratic path for '{skill}'. Available: {avail}")
    return (f"[Socratic path: {turn.skill_key}, step {turn.step + 1}/"
            f"{turn.question and '?'} — focus: {turn.focus}]\n{turn.question}")


def _learning_mode(args: List[str]) -> str:
    sess = _get_learning_session()
    if not args:
        return f"Current mode: {sess.mode}\nUse 'learning mode <repair|learning>' to switch."
    mode = args[0]
    try:
        sess.set_mode(mode)
    except ValueError as e:
        return str(e)
    return (f"Mode set to '{mode}'. "
            + ("Diagnostic orchestration is active (the existing flow)."
               if mode == "repair" else
               "Learning mode is active (this subsystem)."))


def _learning_level(args: List[str]) -> str:
    sess = _get_learning_session()
    if not args:
        return (f"Current explanation level: {sess.level}\n"
                "Use 'learning level <hint|guided|full>'. Default is 'guided'.")
    try:
        sess.set_level(args[0])
    except ValueError as e:
        return str(e)
    return f"Explanation level set to '{sess.level}'."


def _learning_profile(args: List[str]) -> str:
    sess = _get_learning_session()
    profs = LDB.list_profiles()
    lines = [f"Active profile: {sess.profile_name} (id {sess.profile_id})",
             f"Mode: {sess.mode}", f"Default level: {sess.level}"]
    lines.append("All profiles:")
    for p in profs:
        lines.append(f"  - id {p['id']}: {p['name']} (level={p['default_level']})")
    return "\n".join(lines)


def _learning_skills(args: List[str]) -> str:
    sess = _get_learning_session()
    skills = sess.skills()
    lines = [f"{'Skill':<28} {'Level':<13} {'Indep':>5} {'Distinct':>8} {'Hints':>5} {'NewCtx':>6}"]
    lines.append("-" * 70)
    for s in skills:
        lines.append(f"{s['label'][:27]:<28} {s['level']:<13} "
                     f"{s['independent']:>5} {s['distinct']:>8} "
                     f"{s['with_hints']:>5} {s['new_context']:>6}")
    lines.append("")
    lines.append("Level: novice<apprentice<practitioner<expert. "
                 "Mastery is evidence-based; viewing does NOT raise it.")
    return "\n".join(lines)


def _learning_progress(args: List[str]) -> str:
    sess = _get_learning_session()
    p = sess.progress()
    lines = ["Learning progress summary:"]
    lines.append(f"  Skills tracked: {p['skills_tracked']}")
    lines.append(f"  novice: {p['novice']}  apprentice: {p['apprentice']}  "
                 f"practitioner: {p['practitioner']}  expert: {p['expert']}")
    lines.append(f"  Total independent solves: {p['total_independent_solves']}")
    lines.append(f"  Total explanation views: {p['total_views']}")
    lines.append("")
    lines.append("Only independent solves (and new-context demonstrations) "
                 "raise mastery. Views and attempts do not.")
    return "\n".join(lines)


def _learning_gaps(args: List[str]) -> str:
    sess = _get_learning_session()
    gaps = sess.adaptive.gaps()
    if not gaps:
        return ("No gaps detected — every skill is at practitioner or "
                "expert. Run 'learning review' for spaced repetition.")
    lines = ["Knowledge gaps (skills below apprentice):"]
    for g in gaps:
        lines.append(f"  - {g['skill_key']}: level={g['level']}, "
                     f"{g['independent_solves']} independent solve(s), "
                     f"{g['distinct_exercises']} distinct. {g['rationale']}")
    return "\n".join(lines)


def _learning_review(args: List[str]) -> str:
    sess = _get_learning_session()
    due = sess.review.due_now()
    if not due:
        upcoming = sess.review.upcoming(within_days=7)
        if not upcoming:
            return ("Nothing due now and nothing scheduled in the next 7 days. "
                    "Solve exercises with 'learning exercise' to build a "
                    "review queue.")
        lines = ["Nothing due now. Upcoming in the next 7 days:"]
        for u in upcoming:
            lines.append(f"  - {u.skill_key} (ex {u.exercise_id}) due {u.due_at}")
        return "\n".join(lines)
    lines = [f"Due now ({len(due)}):"]
    for d in due:
        lines.append(f"  - {d.skill_key} (ex {d.exercise_id}) due {d.due_at} "
                     f"[interval={d.interval_days}d ease={d.ease_factor} "
                     f"reps={d.repetitions}]")
    lines.append("Re-attempt with 'learning exercise <skill>' then 'learning assess'.")
    return "\n".join(lines)


def _learning_exercise(args: List[str]) -> str:
    global _current_exercise_id
    sess = _get_learning_session()
    skill = args[0] if args else None
    rec = sess.adaptive.recommend_next(focus_skill=skill)
    if not rec.exercise_id:
        # Generate one if adaptive had none.
        exs = sess.generator.generate(rec.skill_key, n=1)
        if exs:
            rec.exercise_id = exs[0].id
            rec.exercise_prompt = exs[0].prompt
            rec.exercise_source_kind = exs[0].source_kind
    if not rec.exercise_id:
        return (f"No exercise available for '{rec.skill_key}'. "
                f"Reason: {rec.reason}")
    _current_exercise_id = rec.exercise_id
    ex = sess.generator.get(rec.exercise_id)
    if not ex:
        return f"Exercise {rec.exercise_id} not found."
    out = [f"[Exercise #{ex.id} — skill: {ex.skill_key}, "
           f"difficulty: {ex.difficulty}, source: {ex.source_kind}]",
           ex.prompt, "",
           f"Reason: {rec.reason}", "",
           "Submit your answer with: learning assess <your reasoning + answer>",
           "Get a hint with:   learning hint",
           "See the solution:  learning explain (default level: " + sess.level + ")"]
    return "\n".join(out)


def _learning_hint(args: List[str]) -> str:
    global _current_exercise_id
    if _current_exercise_id is None:
        return ("No active exercise. Use 'learning exercise [skill]' to get one.")
    sess = _get_learning_session()
    ex = sess.generator.get(_current_exercise_id)
    if not ex:
        return f"Exercise {_current_exercise_id} not found."
    return sess.explain(ex.__dict__, level=ExplanationLevel.HINT)


def _learning_explain(args: List[str]) -> str:
    global _current_exercise_id
    if _current_exercise_id is None:
        return ("No active exercise. Use 'learning exercise [skill]' to get one.")
    sess = _get_learning_session()
    ex = sess.generator.get(_current_exercise_id)
    if not ex:
        return f"Exercise {_current_exercise_id} not found."
    level = args[0] if args else None
    if level and level not in ExplanationLevel.ALL:
        return f"Level must be one of {ExplanationLevel.ALL}"
    return sess.explain(ex.__dict__, level=level)


def _learning_assess(args: List[str]) -> str:
    global _current_exercise_id
    if _current_exercise_id is None:
        return ("No active exercise. Use 'learning exercise [skill]' to get one.")
    if not args:
        return ("Usage: learning assess <your reasoning + answer>. "
                "Example: learning assess Using Ohm's law R=V/I, so R=5/2=2.5 ohm")
    sess = _get_learning_session()
    ex = sess.generator.get(_current_exercise_id)
    if not ex:
        return f"Exercise {_current_exercise_id} not found."
    user_text = " ".join(args)
    res = sess.attempt(ex.__dict__, user_text, hints_used=0)
    ev = res["evaluation"]
    out = ["--- Evaluation ---", ev["feedback"], "",
           f"Reasoning score: {ev['reasoning_score']:.2f} / 1.00",
           f"Answer in range: {ev['answer_in_range']}",
           f"Counted as solved independently: {res['solved_independently']}"]
    if ev["issues"]:
        out.append("Issues:")
        for i in ev["issues"]:
            out.append(f"  - {i}")
    # Show updated mastery for this skill.
    m = sess.assessment.mastery(ex.skill_key)
    out.append("")
    out.append(f"Mastery on {ex.skill_key}: {m.level} "
               f"({m.independent_solves} independent, "
               f"{m.distinct_exercises_solved} distinct, "
               f"{m.new_context_demos} new-context). {m.rationale}")
    return "\n".join(out)


def _learning_case(args: List[str]) -> str:
    if not args:
        return ("Usage: learning case <session_id>. Converts a completed "
                "repair session into a learning case (follow-up exercises).")
    try:
        sid = int(args[0])
    except ValueError:
        return f"session_id must be an integer, got '{args[0]}'"
    sess = _get_learning_session()
    r2l = RepairToLearning(profile_id=sess.profile_id)
    res = r2l.convert(sid, seed=sid)
    if not res.get("ok"):
        return f"ERROR: {res.get('error')}"
    case = res["case"]
    out = ["Converted repair session into a learning case:",
           f"  Board: {case['board_model']}",
           f"  Symptoms: {', '.join(case['symptoms'][:3])}",
           f"  Confirmed: {case['confirmed']} (outcome: {case['outcome']})",
           f"  Root cause: {case['confirmed_root_cause'] or '(unconfirmed — not disclosed)'}",
           f"  Relevant principles: {', '.join(case['relevant_principles'])}",
           f"  Knowledge gaps exposed: {len(case['knowledge_gaps'])}",
           f"  Follow-up exercises generated: {case['exercises_generated']}",
           "",
           "The follow-up exercises are SIMILAR BUT NOT IDENTICAL to the "
           "original case (component labels and values perturbed). The "
           "original solution is hidden; solve them with "
           "'learning exercise <skill>' + 'learning assess'."]
    return "\n".join(out)


def _learning_history(args: List[str]) -> str:
    sess = _get_learning_session()
    skill = args[0] if args else None
    rows = sess.history(skill)
    if not rows:
        return ("No assessment history yet. Solve exercises with "
                "'learning exercise' + 'learning assess' to build it.")
    lines = [f"Assessment history ({'skill=' + skill if skill else 'all skills'}):"]
    for r in rows[:30]:
        lines.append(f"  {r['assessed_at']}  {r['skill_key']:<22} "
                     f"{r['level']:<12} ({r['evidence_type']})")
    lines.append(f"(showing {min(30, len(rows))} of {len(rows)}; append-only)")
    return "\n".join(lines)


# ---- Dispatcher ---------------------------------------------------------

COMMANDS = {
    "help": cmd_help,
    "new": cmd_new,
    "open": cmd_open,
    "sessions": cmd_sessions,
    "close": lambda a: _close_current(),
    "symptom": cmd_symptom,
    "measure": cmd_measure,
    "analyze": cmd_analyze,
    "next-test": cmd_next_test,
    "add-file": cmd_add_file,
    "documents": cmd_documents,
    "search": cmd_search,
    "notes": cmd_notes,
    "report": cmd_report,
    "remove-document": cmd_remove_document,
    "settings": cmd_settings,
    "exit": cmd_exit,
    "quit": cmd_exit,
    # Phase 2 engine commands (additive; existing commands unchanged)
    "engines": cmd_engines,
    "engine": cmd_engine,
    "validate": cmd_validate,
    "hypotheses": cmd_hypotheses,
    "next-best-test": cmd_next_best_test,
    "sequence": cmd_sequence,
    "topology": cmd_topology,
    "benchmark": cmd_benchmark,
    "learn": cmd_learn,
    "db": cmd_db,
    # Phase 3 LLM orchestration + model registry (additive)
    "orchestrate": cmd_orchestrate,
    "backend": cmd_backend,
    "models": cmd_models,
    "model-settings": cmd_model_settings,
    "model-profile": cmd_model_profile,
    "ram": cmd_ram,
    "context": cmd_context,
    "tools": cmd_tools,
    "llm-benchmark": cmd_llm_benchmark,
    "learning": cmd_learning,
}


def _close_current() -> str:
    if Session.current is None:
        return "No active session."
    sessions.close(Session.current.id)
    title = Session.current.title
    Session.current = None
    return f"Closed session: {title}"


def dispatch(line: str) -> str:
    """Interpret one input line. Returns the text to print."""
    line = line.strip()
    if not line:
        return ""
    # Try to tokenize with shlex so quoted paths survive. On Windows
    # use non-posix mode so backslash separators in paths like
    # D:\dir\file are NOT treated as escape characters (a real bug on
    # Windows that broke drag-and-drop paths). shlex non-posix still
    # respects single/double quotes for paths containing spaces.
    try:
        tokens = shlex.split(line, posix=(os.name != "nt"))
    except ValueError:
        tokens = line.split()
    if not tokens:
        return ""
    cmd = tokens[0].lower()
    args = tokens[1:]
    if cmd in COMMANDS:
        try:
            return COMMANDS[cmd](args) or ""
        except EOFError:
            raise
        except Exception as e:
            return f"Error in '{cmd}': {e}"
    # Natural-language fallback: try measurement then symptom then search.
    nl = M.parse_line("measure " + line) if False else None  # placeholder
    # Try measurement parse first (the user may type "VIN = 19.4V").
    if "=" in line and any(c.isdigit() for c in line):
        parsed = M.parse_line("measure " + line)
        if parsed:
            s = _require_session_silent()
            if s:
                mid = M.add(s.id, parsed)
                return (f"(interpreted as measurement) Recorded #{mid}: "
                        f"{parsed['name']} = {parsed.get('value')} "
                        f"{parsed.get('unit','')}".strip())
            return "(interpreted as measurement) No active session. Open one first."
    # Otherwise treat as a symptom if a session is open and no other parse matched.
    s = _require_session_silent()
    if s:
        sessions.add_symptom(s.id, line)
        return f"(interpreted as symptom) Recorded: {line}"
    return (
        f"Unknown command '{cmd}'. Type 'help' for the command list."
    )


def _require_session_silent() -> Optional[sessions.Session]:
    return Session.current


# ---- REPL ---------------------------------------------------------------

def repl() -> int:
    """Run the interactive read-eval-print loop."""
    config.ensure_layout()
    database.init_db()
    knowledge_base.load_builtin()
    print(f"Electronics Diagnostic Assistant v{config.APP_VERSION}")
    print(f"Root: {config.app_root()}")
    eng = llm_engine.get_engine()
    print(eng.availability_report())
    print(f"Knowledge facts loaded: {database.count_facts()}")
    print(safety.safety_banner())
    print("Type 'help' for commands.\n")
    prompt = config.load_settings().get("ui", {}).get("prompt", "repair> ")
    while True:
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0
        try:
            out = dispatch(line)
        except EOFError:
            print("Bye.")
            return 0
        if out:
            print(out)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for the console script / PyInstaller EXE."""
    argv = argv if argv is not None else sys.argv[1:]
    config.ensure_layout()
    database.init_db()
    knowledge_base.load_builtin()
    if not argv:
        return repl()
    # One-shot mode: join all args and dispatch as a single line.
    line = " ".join(argv)
    try:
        out = dispatch(line)
        if out:
            print(out)
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
