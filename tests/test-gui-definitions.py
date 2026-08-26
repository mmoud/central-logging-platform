#!/usr/bin/env python3
"""Static validation for saved views, reports, folders, and stream tuning."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "provision-gui.py"
SPEC = importlib.util.spec_from_file_location("gui_provisioner", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main() -> int:
    names = [item[0] for item in MODULE.SAVED_VIEWS]
    assert len(names) == 12
    assert len(names) == len(set(names))
    for name, stream, period, sql in MODULE.SAVED_VIEWS:
        assert name and stream and period in {"24h", "7d"}
        assert f'FROM "{stream}"' in sql
        assert "raw_message" in sql or name == "Unclassified - New Sources"
        data = MODULE.saved_view_data("default", stream, period, sql)
        assert data["data"]["stream"]["selectedStream"] == [stream]
        assert data["data"]["timezone"]
        assert data["meta"]["sqlMode"] is True
    assert set(MODULE.STREAM_SETTINGS) == {
        "fortigate", "proxmox_mail_gateway", "juniper", "proxmox_ve",
        "ubersmith", "unclassified",
    }
    for settings in MODULE.STREAM_SETTINGS.values():
        assert settings["index_fields"]
        assert settings["bloom_filter_fields"]
    assert len(MODULE.REPORTS) == 4
    print("GUI definitions passed (12 saved views, 4 cached reports, 6 tuned streams).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
