"""One-time model preparation script.

Downloads and prepares a quantized GGUF model for fully offline use.

Target hardware: Intel Core i7 6th gen, integrated GPU, 8 GB RAM.
Recommended model class: 1.5B-parameter instruct model, Q4_K_M or
Q4_0 quantization. The script is conservative: it never downloads
without explicit confirmation, never executes anything from the
downloaded archive other than the expected single .gguf file, and
writes the model into Models/ inside the application root.

If the user has no internet on the target machine, they can run this
script once on a machine that does, copy the resulting Models/*.gguf
to the target machine's ElectronicsDiagnostic\Models folder, and the
application will use it offline.

Recommended models (verify URLs are still valid before downloading):
- Qwen2.5-1.5B-Instruct, Q4_K_M GGUF
  Hugging Face repo: bartowski/Qwen2.5-1.5B-Instruct-GGUF
- Llama-3.2-1B-Instruct, Q4_K_M GGUF
  Hugging Face repo: bartowski/Llama-3.2-1B-Instruct-GGUF

A 1.5B Q4_K_M model is ~1.0-1.1 GB on disk and uses ~1.5-2 GB RAM at
n_ctx=2048 on CPU, which fits the 8 GB target with room for the OS.

This script uses urllib from the standard library only; no pip
install is required.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

# Make eda importable when run as a script from the project root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eda import config  # noqa: E402


# Curated candidate models. The user picks one. URLs are Hugging Face
# /resolve/ links; verify they still exist before relying on them.
CANDIDATES = {
    "qwen2.5-1.5b-instruct-q4_k_m": {
        "url": ("https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/"
                "resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"),
        "filename": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "size_gb_approx": 1.0,
        "notes": "Good 1.5B-class instruct model. Q4_K_M is the best quality/size tradeoff.",
    },
    "llama-3.2-1b-instruct-q4_k_m": {
        "url": ("https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/"
                "resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf"),
        "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size_gb_approx": 0.9,
        "notes": "1B-parameter model; lighter but slightly less capable than Qwen2.5-1.5B.",
    },
}


def list_candidates() -> None:
    print("Candidate models:")
    for k, v in CANDIDATES.items():
        print(f"  {k}")
        print(f"    file:   {v['filename']}")
        print(f"    size:   ~{v['size_gb_approx']} GB")
        print(f"    url:    {v['url']}")
        print(f"    notes:  {v['notes']}")
        print()


def download(url: str, dest: Path, timeout: int = 600) -> str:
    """Stream-download url to dest. Returns the SHA-256 of the file."""
    h = hashlib.sha256()
    # Hugging Face may 302-redirect; use a small opener that follows
    # redirects within the same request (urllib follows by default).
    req = urllib.request.Request(url, headers={"User-Agent": "eda-model-prep/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        got = 0
        last_pct = -1
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            if total:
                pct = int(100 * got / total)
                if pct != last_pct and pct % 5 == 0:
                    print(f"  ...{pct}% ({got // 1024 // 1024} MB / {total // 1024 // 1024} MB)")
                    last_pct = pct
    return h.hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Prepare a local GGUF model for offline use."
    )
    p.add_argument("--list", action="store_true", help="list candidate models and exit")
    p.add_argument("--model", help="model key (see --list)")
    p.add_argument("--url", help="custom GGUF URL (overrides --model)")
    p.add_argument("--filename", help="filename to save as (with --url)")
    p.add_argument("--yes", action="store_true", help="do not prompt")
    args = p.parse_args(argv)

    if args.list or (not args.model and not args.url):
        list_candidates()
        print("Run with --model <key> to download a curated model.")
        print("Run with --url <url> --filename <name> to use a custom GGUF.")
        return 0

    if args.url:
        url = args.url
        fname = args.filename or url.rsplit("/", 1)[-1]
    else:
        spec = CANDIDATES.get(args.model)
        if not spec:
            print(f"Unknown model: {args.model}. Use --list.")
            return 1
        url = spec["url"]
        fname = spec["filename"]

    models_dir = config.app_dir("Models")
    dest = models_dir / fname
    print(f"Will download: {url}")
    print(f"Destination:    {dest}")
    print(f"Approx size:     {CANDIDATES.get(args.model, {}).get('size_gb_approx', '?')} GB")
    if not args.yes:
        ans = input("Proceed? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1

    print("Downloading (this may take a while)...")
    sha = download(url, dest)
    print(f"\nSaved: {dest}")
    print(f"SHA-256: {sha}")
    print(f"Size:   {dest.stat().st_size} bytes")

    # Update settings.json so the engine uses this model.
    import json
    settings = config.load_settings()
    settings.setdefault("engine", {})["model_file"] = fname
    config.save_settings(settings)
    print(f"Wrote settings.json with model_file = {fname}")
    print("\nNext: place a llama.cpp build (llama-cli.exe or main.exe) in the")
    print("Engine/ directory, or add its location to settings.engine.cli_path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
