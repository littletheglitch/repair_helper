"""Tests for the model registry (Phase 3).

Verifies: candidate classes present, listing, SHA-256 of a temp file,
runtime settings get/set, portability bundle round-trip.
"""
import json
import pytest
from pathlib import Path

from eda import config, model_registry as MR


def test_model_classes_present():
    assert "1b-1.5b" in MR.MODEL_CLASSES
    assert "3b-4b" in MR.MODEL_CLASSES
    assert "7b-8b" in MR.MODEL_CLASSES
    # Each class has at least one candidate model with a real URL.
    for klass, info in MR.MODEL_CLASSES.items():
        assert info["models"], f"class {klass} has no models"
        for m in info["models"]:
            assert m["url"].startswith("https://")


def test_list_models_returns_candidates(tmp_app):
    models = MR.list_models()
    assert len(models) >= 6  # 2 per class x 3 classes
    # None exist in the test env (fresh Models dir).
    assert all(not m.exists for m in models)
    # Each has a class label.
    assert {m.klass for m in models} == {"1b-1.5b", "3b-4b", "7b-8b"}


def test_find_model(tmp_app):
    m = MR.find_model("qwen2.5-1.5b-instruct-q4_k_m")
    assert m is not None
    assert m.klass == "1b-1.5b"
    assert MR.find_model("does_not_exist") is None


def test_sha256_file(tmp_app, tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    sha = MR.sha256_file(p)
    # Known SHA-256 of "hello world"
    assert sha == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_verify_sha256_matches(tmp_app, tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    ok, actual = MR.verify_sha256(
        p, "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")
    assert ok
    assert actual == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_download_model_refuses_without_yes(tmp_app):
    r = MR.download_model("qwen2.5-1.5b-instruct-q4_k_m", yes=False)
    assert not r["ok"]
    assert "yes=True" in r["note"]


def test_download_model_unknown_key(tmp_app):
    r = MR.download_model("not_a_real_model", yes=True)
    assert not r["ok"]
    assert "unknown model key" in r["note"]


def test_runtime_settings_returns_defaults(tmp_app):
    s = MR.runtime_settings()
    assert "n_ctx" in s
    assert "n_threads" in s
    assert "n_batch" in s
    assert "n_gpu_layers" in s
    assert s["n_gpu_layers"] == 0  # CPU-only default


def test_set_runtime_settings_persists(tmp_app):
    MR.set_runtime_settings(n_ctx=4096, n_threads=6)
    s = MR.runtime_settings()
    assert s["n_ctx"] == 4096
    assert s["n_threads"] == 6


def test_ram_usage_mb_no_process(tmp_app):
    r = MR.ram_usage_mb()
    # No llama-server running in the test env.
    assert "running" in r
    assert isinstance(r["rss_mb"], int)


def test_export_and_import_bundle_round_trip(tmp_app, tmp_path):
    # Create a fake model file + a fake engine binary, then bundle.
    models_dir = config.app_dir("Models")
    engine_dir = config.app_dir("Engine")
    (models_dir / "fake.gguf").write_bytes(b"\x00GGUF\x00" * 100)
    (engine_dir / "llama-server.exe").write_bytes(b"\x00MZ" + b"\x00" * 50)
    out = tmp_path / "bundle"
    r = MR.export_bundle(out, model_keys=[])  # model_keys empty -> all present
    assert r["ok"]
    assert (out / "Models" / "fake.gguf").exists()
    assert (out / "Engine" / "llama-server.exe").exists()
    manifest = json.loads((out / "bundle_manifest.json").read_text())
    assert len(manifest["models"]) == 1
    assert manifest["models"][0]["filename"] == "fake.gguf"
    # Wipe local Models/Engine and re-import.
    (models_dir / "fake.gguf").unlink()
    (engine_dir / "llama-server.exe").unlink()
    assert not (models_dir / "fake.gguf").exists()
    r2 = MR.import_bundle(out)
    assert r2["ok"]
    assert (models_dir / "fake.gguf").exists()
    assert (engine_dir / "llama-server.exe").exists()
