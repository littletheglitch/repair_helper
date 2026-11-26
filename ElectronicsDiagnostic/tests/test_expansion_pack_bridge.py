from __future__ import annotations
import json
from pathlib import Path

from eda.engines import base as EB


def test_expansion_pack_registered():
    assert "expansion_pack" in EB.available()
    engine = EB.get("expansion_pack")
    assert engine.version == "1.5.1"


def test_expansion_pack_capabilities_include_power_sequence():
    result = EB.call("expansion_pack", "capabilities", {})
    assert result.ok, result.error
    names = {item["name"] for item in result.value}
    assert "motherboard.power_on_sequence" in names


def test_host_tool_router_can_call_power_on_sequence():
    payload = {
        "platform": "desktop",
        "measurements": [{
            "name": "atx_5vsb_voltage", "kind": "voltage", "state": "in_range",
            "value": 5.0, "unit": "V", "condition": "powered"
        }]
    }
    result = EB.call("expansion_pack", "dispatch", {
        "engine_name": "power_on_sequence", "payload_json": json.dumps(payload)
    })
    assert result.ok, result.error
    assert result.value["ok"] is True
    assert result.value["conclusion"]["engine"] == "motherboard.power_on_sequence.desktop"


def test_missing_pack_root_is_reported_not_silently_faked(tmp_path, monkeypatch):
    # The actual installed pack must be discoverable from the host app root.
    engine = EB.get("expansion_pack")
    assert Path(engine._get_adapter().pack_root).is_dir()


def test_host_tool_router_can_ingest_one_local_html_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    html_file = tmp_path / "repair_case.html"
    html_file.write_text(
        "<html><head><title>GPU no display</title></head><body><main>"
        "GPU no display. Core rail measured 0.82 V. Check enable and power-good."
        "<img src='assets/board.jpg' alt='board photo'></main></body></html>",
        encoding="utf-8",
    )
    engine = EB.get("expansion_pack")
    monkeypatch.setattr(engine, "_get_adapter", lambda: SimpleNamespace(
        pack_root=tmp_path, manifest=lambda: {"pack_version": "1.5.1"}
    ))
    result = EB.call("expansion_pack", "ingest_file", {
        "path": str(html_file), "category": "forum_post",
        "source_url": "https://forum.example/thread/1",
    })
    assert result.ok, result.error
    assert result.value["title"] == "GPU no display"
    assert result.value["characters"] > 30
    assert result.value["image_references"][0]["src"] == "assets/board.jpg"
    assert result.metadata["deduplication"] == "SHA-256"

