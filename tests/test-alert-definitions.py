#!/usr/bin/env python3
"""Validate bundled alert definitions without contacting OpenObserve."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "provision-alerts.py"
SPEC = importlib.util.spec_from_file_location("alert_provisioner", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def validate(rules: list[dict], *, enabled: bool, destination: str | None) -> None:
    assert len(rules) == 3
    assert len({rule["name"] for rule in rules}) == 3
    for rule in rules:
        assert rule["stream_type"] == "logs"
        assert rule["stream_name"] == "unclassified"
        assert rule["enabled"] is enabled
        assert rule["destinations"] == ([destination] if destination else [])
        assert rule["query_condition"]["type"] == "sql"
        assert 'FROM "unclassified"' in rule["query_condition"]["sql"]
        assert rule["trigger_condition"]["operator"] == ">="
        assert rule["trigger_condition"]["period"] == 5
        assert rule["trigger_condition"]["frequency"] == 5


def main() -> int:
    validate(MODULE.definitions(), enabled=False, destination=None)
    validate(MODULE.definitions("operations-email"), enabled=True,
             destination="operations-email")
    print("Alert definitions passed (3 rules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
