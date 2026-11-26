"""Knowledge Collector CLI.

Commands:
  sources                          List configured sources
  source add <name> <kind> <url>   Add a source (disabled by default)
  source disable <name>            Disable a source
  source enable <name>             Enable a source (after terms review)
  collect [--source NAME] [--max N]  Collect from enabled sources
  resume [--source NAME]            Resume interrupted collection
  pause [--source NAME]             Pause a running collection
  status                           Show pages discovered/collected/rejected/etc.
  search <query>                   Offline full-text search
  documents [--source NAME]        List collected documents
  export <format> [--out DIR]      Export: jsonl|markdown|sqlite|text|knowledge
  verify                           Verify the DB integrity + manifest
  stats                            Show collection statistics
  help                             This message
  exit                             Quit (REPL only)

Network access is only used by ``collect`` and ``resume``. All other
commands work fully offline.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from . import (config, database, export as X, manifest, collector, fetcher,
               schemas)


def main(argv: Optional[List[str]] = None) -> int:
    """Console-script entry point."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print("Knowledge Collector. Type 'help' for commands.")
        _repl()
        return 0
    out = dispatch(" ".join(argv))
    print(out)
    return 0


def dispatch(line: str) -> str:
    """Dispatch one command line; return the output string. Used by
    tests and the REPL."""
    args = line.split()
    if not args:
        return ""
    cmd = args[0]
    handler = COMMANDS.get(cmd)
    if handler is None:
        return (f"Unknown command: '{cmd}'. Type 'help' for the list.")
    try:
        return handler(args[1:])
    except Exception as e:
        return f"Error in '{cmd}': {e}"


def _repl() -> None:
    while True:
        try:
            line = input("kc> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("Bye.")
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            print("Bye.")
            return
        print(dispatch(line))


# ---- Commands -------------------------------------------------------

def cmd_help(args: List[str]) -> str:
    return _HELP_TEXT


def cmd_sources(args: List[str]) -> str:
    rows = database.list_sources()
    if not rows:
        return ("No sources configured. Use 'source add <name> <kind> <url>'.\n"
                "Kinds: stackexchange_api | forum_html")
    lines = ["Sources:"]
    for r in rows:
        en = "enabled" if r["enabled"] else "disabled"
        lines.append(f"  {r['name']:30s} [{en}]  {r['kind']:18s}  {r['base_url']}")
    return "\n".join(lines)


def cmd_source(args: List[str]) -> str:
    """source add|disable|enable <args>"""
    if not args:
        return ("Usage: source add|disable|enable <name> [kind url]\n"
                "  add <name> <kind> <url> [--license L --terms T --attribution A]\n"
                "  enable <name>\n"
                "  disable <name>")
    sub = args[0]
    rest = args[1:]
    if sub == "add":
        if len(rest) < 3:
            return "Need: <name> <kind> <url>"
        name, kind, base_url = rest[0], rest[1], rest[2]
        # Parse optional --key value flags.
        kv = {}
        i = 3
        while i < len(rest):
            if rest[i].startswith("--") and i + 1 < len(rest):
                kv[rest[i][2:]] = rest[i + 1]
                i += 2
            else:
                i += 1
        database.upsert_source(name, kind, base_url, enabled=False,
                              license=kv.get("license", ""),
                              terms_url=kv.get("terms", ""),
                              attribution=kv.get("attribution", ""),
                              access_method=kv.get("access", "manual review"))
        # Write a default manifest stub so the user can review.
        database.upsert_manifest(name,
                                 license=kv.get("license", "review required"),
                                 terms_url=kv.get("terms", ""),
                                 access_method=kv.get("access", "manual review"),
                                 permitted_storage="metadata + short extracts with link",
                                 attribution=kv.get("attribution", f"{name}; link to original"),
                                 notes="user-added source; review terms before enabling")
        return (f"Added source '{name}' (disabled by default). "
                f"Review its terms, then 'source enable {name}'.")
    if sub == "enable":
        if not rest:
            return "Need: <name>"
        database.enable_source(rest[0], True)
        return f"Enabled '{rest[0]}'."
    if sub == "disable":
        if not rest:
            return "Need: <name>"
        database.enable_source(rest[0], False)
        return f"Disabled '{rest[0]}'."
    return f"Unknown source sub-command: {sub}"


def cmd_collect(args: List[str]) -> str:
    source = None
    max_n = None
    i = 0
    while i < len(args):
        if args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2
        elif args[i] == "--max" and i + 1 < len(args):
            max_n = int(args[i + 1]); i += 2
        else:
            i += 1
    sources = [source] if source else [s["name"] for s in database.list_sources(enabled_only=True)]
    if not sources:
        return ("No enabled sources. Use 'source add' then 'source enable'.")
    f = fetcher.Fetcher()
    summary = []
    for src in sources:
        if src not in collector.CONNECTORS:
            summary.append(f"{src}: no connector registered (skipped)")
            continue
        r = collector.collect(src, f, max_records=max_n)
        if r.get("ok"):
            summary.append(f"{src}: collected {r['collected']}, "
                          f"rejected {r['rejected']}"
                          f"{', paused' if r.get('paused') else ''}")
        else:
            summary.append(f"{src}: FAILED - {r.get('error')}")
    return "\n".join(summary)


def cmd_resume(args: List[str]) -> str:
    source = None
    i = 0
    while i < len(args):
        if args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2
        else:
            i += 1
    # Reset the crawl state to allow resume (status -> idle).
    if source:
        database.update_crawl_state(source, status="running")
        f = fetcher.Fetcher()
        r = collector.collect(source, f)
        return f"{source}: collected {r.get('collected',0)}, rejected {r.get('rejected',0)}"
    sources = [s["name"] for s in database.list_sources(enabled_only=True)
               if database.get_crawl_state(s["name"])["status"] in ("paused", "failed", "done")]
    if not sources:
        return "No sources to resume."
    f = fetcher.Fetcher()
    out = []
    for src in sources:
        database.update_crawl_state(src, status="running")
        r = collector.collect(src, f)
        out.append(f"{src}: collected {r.get('collected',0)}, rejected {r.get('rejected',0)}")
    return "\n".join(out)


def cmd_pause(args: List[str]) -> str:
    source = None
    i = 0
    while i < len(args):
        if args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2
        else:
            i += 1
    if source:
        database.update_crawl_state(source, status="paused")
        return f"Paused '{source}'. Next 'resume' will continue from last URL."
    sources = [s["name"] for s in database.list_sources(enabled_only=True)]
    for src in sources:
        database.update_crawl_state(src, status="paused")
    return f"Paused {len(sources)} sources."


def cmd_status(args: List[str]) -> str:
    counts = database.record_counts()
    rows = database.list_sources(enabled_only=False)
    lines = [
        "Status:",
        f"  Sources configured: {len(rows)}",
        f"  Records total:      {counts['records_total']}",
        f"    new:              {counts['records_new']}",
        f"    duplicate:        {counts['records_duplicate']}",
        f"    near_duplicate:   {counts['records_near_duplicate']}",
        f"    rejected:         {counts['records_rejected']}",
        f"  Documents total:    {counts['documents_total']}",
        f"    extracted:        {counts['documents_extracted']}",
        f"    stored_only:      {counts['documents_stored_only']}",
        f"    failed:           {counts['documents_failed']}",
        f"    rejected (BIOS):  {counts['documents_rejected']}",
        f"  Rejections logged:  {counts['rejections_total']}",
        "",
        "Per-source crawl state:",
    ]
    for r in rows:
        st = database.get_crawl_state(r["name"])
        en = "on" if r["enabled"] else "off"
        lines.append(f"  {r['name']:30s} [{en}] state={st['status']:8s} "
                     f"collected={st['items_collected']} "
                     f"rejected={st['items_rejected']}")
    return "\n".join(lines)


def cmd_search(args: List[str]) -> str:
    if not args:
        return "Usage: search <query>"
    query = " ".join(args)
    hits = database.search_records(query, limit=20)
    if not hits:
        return f"No records match '{query}'."
    lines = [f"Search '{query}' — {len(hits)} hit(s):"]
    for h in hits:
        lines.append(f"\n  [{h['source_name']}] {h.get('title','')}")
        lines.append(f"    URL: {h['url']}")
        lines.append(f"    {h['text'][:200]}{'...' if len(h['text'])>200 else ''}")
    return "\n".join(lines)


def cmd_documents(args: List[str]) -> str:
    source = None
    i = 0
    while i < len(args):
        if args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2
        else:
            i += 1
    if source:
        rows = database.query_all(
            "SELECT * FROM documents WHERE source_name=? ORDER BY id", (source,))
    else:
        rows = database.query_all("SELECT * FROM documents ORDER BY id")
    if not rows:
        return "No documents collected."
    lines = [f"Documents ({len(rows)}):"]
    for r in rows:
        lines.append(f"  [{r['source_name']}] {r['filename']} "
                     f"({r['file_type']}, {r['status']}, {r['size_bytes']}B)")
    return "\n".join(lines)


def cmd_export(args: List[str]) -> str:
    if not args:
        return ("Usage: export <format> [--out DIR] [--source NAME]\n"
                "Formats: jsonl | markdown | sqlite | text | knowledge")
    fmt = args[0]
    out_dir = None
    source = None
    i = 1
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_dir = __import__("pathlib").Path(args[i + 1]); i += 2
        elif args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2
        else:
            i += 1
    r = X.export(fmt, out_dir, source)
    if not r.get("ok"):
        return f"Export failed: {r.get('error')}"
    if fmt == "knowledge":
        return (f"Exported {r['records']} records to {r['files']} Knowledge JSON "
                f"file(s) in {config.export_dir() / 'knowledge'}.")
    return (f"Exported {r.get('records','?')} record(s) to "
            f"{r.get('file', r.get('files','?'))}.")


def cmd_verify(args: List[str]) -> str:
    rep = database.integrity_check()
    if not rep["ok"]:
        return f"Integrity check FAILED: {rep}"
    lines = [f"Integrity: {rep['integrity_check']}",
             f"Schema version: {rep['schema_version']}",
             "",
             manifest.render_manifest_text()]
    return "\n".join(lines)


def cmd_stats(args: List[str]) -> str:
    counts = database.record_counts()
    # Storage used.
    import os
    data = config.data_dir()
    total_bytes = 0
    for root, _, files in os.walk(data):
        for fn in files:
            try:
                total_bytes += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    lines = [
        "Stats:",
        f"  Storage used:       {total_bytes / 1024:.1f} KB",
        f"  Records total:      {counts['records_total']}",
        f"  Documents total:    {counts['documents_total']}",
        f"  Rejections:         {counts['rejections_total']}",
        "",
        "By source:",
    ]
    for r in database.list_sources():
        n = database.query_one(
            "SELECT COUNT(*) AS n FROM records WHERE source_name=?",
            (r["name"],))
        lines.append(f"  {r['name']:30s} {n['n']} records")
    return "\n".join(lines)


COMMANDS = {
    "help": cmd_help,
    "sources": cmd_sources,
    "source": cmd_source,
    "collect": cmd_collect,
    "resume": cmd_resume,
    "pause": cmd_pause,
    "status": cmd_status,
    "search": cmd_search,
    "documents": cmd_documents,
    "export": cmd_export,
    "verify": cmd_verify,
    "stats": cmd_stats,
    "exit": lambda a: "exit",
    "quit": lambda a: "exit",
}


_HELP_TEXT = """\
Knowledge Collector — commands

Sources:
  sources                          List configured sources
  source add <name> <kind> <url>   Add a source (disabled by default)
  source enable <name>             Enable a source (after terms review)
  source disable <name>            Disable a source

Collection (network access):
  collect [--source NAME] [--max N]
  resume [--source NAME]
  pause [--source NAME]

Offline commands (no network):
  status                           Show pages discovered/collected/rejected
  search <query>                   Full-text search of collected records
  documents [--source NAME]        List collected documents
  export <format> [--out DIR] [--source NAME]
                                   Formats: jsonl|markdown|sqlite|text|knowledge
  verify                           DB integrity + source manifest
  stats                            Collection statistics

Other:
  help                             This message
  exit                             Quit (REPL only)

Kinds: stackexchange_api | forum_html
Safety: BIOS dump files are excluded. The Collector never scans personal drives.
"""
