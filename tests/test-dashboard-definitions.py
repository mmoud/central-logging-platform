#!/usr/bin/env python3
"""Validate bundled dashboard schema without contacting OpenObserve."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "provision-dashboards.py"
SPEC = importlib.util.spec_from_file_location("dashboard_provisioner", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def validate_dashboard(body: dict, expected_panels: int, period: str,
                       variable: str | None = "device") -> None:
    assert body["version"] == 5
    assert body["defaultDatetimeDuration"] == {"type": "relative", "relativeTimePeriod": period}
    variables = body["variables"]["list"]
    if variable:
        assert len(variables) == 1
        assert variables[0]["name"] == variable
        assert variables[0]["type"] == "query_values"
        assert variables[0]["multiSelect"] is True
    else:
        assert variables == []
    panels = [panel for tab in body["tabs"] for panel in tab["panels"]]
    assert len(panels) == expected_panels
    assert max(len(tab["panels"]) for tab in body["tabs"]) <= 6
    assert len({panel["id"] for panel in panels}) == len(panels)

    for panel in panels:
        query = panel["queries"][0]
        fields = query["fields"]
        axes = fields["x"] + fields["y"]
        assert query["customQuery"] is True
        assert query["query"].strip()
        if variable:
            assert f"${variable}" in query["query"]
        assert axes
        assert len({item["alias"] for item in axes}) == len(axes)
        for item in fields["x"]:
            assert re.fullmatch(r"x_axis_\d+", item["alias"])
            assert f'AS "{item["alias"]}"' in query["query"]
        for item in fields["y"]:
            assert re.fullmatch(r"y_axis_\d+", item["alias"])
            assert f'AS "{item["alias"]}"' in query["query"]
        if panel["type"] == "table":
            assert len(fields["x"]) == 1
            assert fields["y"]
            assert all(item["aggregationFunction"] is None for item in fields["y"])


def validate_bootstrap_exclusion(body: dict) -> None:
    for tab in body["tabs"]:
        for panel in tab["panels"]:
            assert "coalesce(schema_bootstrap,'false') <> 'true'" in panel["queries"][0]["query"]


def validate_pmg_bootstrap() -> None:
    calls: list[tuple[str, list[dict]]] = []
    original = MODULE.api_request

    def capture(_base: str, path: str, _user: str, _password: str,
                _method: str = "GET", data: list[dict] | None = None) -> dict:
        calls.append((path, data or []))
        return {}

    try:
        MODULE.api_request = capture
        MODULE.bootstrap_schema("http://127.0.0.1:5080", "default", "user", "password", "pmg")
    finally:
        MODULE.api_request = original

    assert len(calls) == 1
    path, records = calls[0]
    assert path == "/api/default/proxmox_mail_gateway/_json"
    assert len(records) == 1
    record = records[0]
    assert record["schema_bootstrap"] == "true"
    for field in (
        "mail_header_from", "mail_header_sender", "mail_header_sender_domain", "mail_header_to",
        "mail_envelope_sender", "mail_envelope_sender_domain", "mail_envelope_recipients",
        "mail_spam_threshold", "mail_auth_hits", "mail_spf_result", "mail_dkim_result",
        "mail_dmarc_result", "mail_arc_result",
    ):
        assert record[field]


def main() -> int:
    pmg = MODULE.pmg_dashboard()
    validate_dashboard(pmg, 41, "7d")
    validate_bootstrap_exclusion(pmg)
    validate_pmg_bootstrap()
    validate_dashboard(MODULE.fortigate_dashboard(), 70, "24h")
    validate_dashboard(MODULE.juniper_dashboard(), 30, "24h")
    validate_dashboard(MODULE.proxmox_ve_dashboard(), 32, "24h", "source")
    validate_dashboard(MODULE.ubersmith_dashboard(), 24, "24h")
    validate_dashboard(MODULE.unclassified_dashboard(), 24, "24h", "source")
    validate_dashboard(MODULE.central_overview_dashboard(), 22, "24h", None)
    print("Dashboard definitions passed (243 panels).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
