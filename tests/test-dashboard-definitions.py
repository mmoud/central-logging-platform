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
                       variable: str | None = "device",
                       extra_variables: tuple[str, ...] = ()) -> None:
    assert body["version"] == 5
    assert body["defaultDatetimeDuration"] == {"type": "relative", "relativeTimePeriod": period}
    variables = body["variables"]["list"]
    if variable:
        assert len(variables) == 1 + len(extra_variables)
        assert variables[0]["name"] == variable
        assert variables[0]["type"] == "query_values"
        assert variables[0]["multiSelect"] is True
        text_variables = variables[1:]
    else:
        assert len(variables) == len(extra_variables)
        text_variables = variables
    assert [item["name"] for item in text_variables] == list(extra_variables)
    assert all(item["type"] == "textbox" for item in text_variables)
    assert all(item["value"] == "_o2_all_" for item in text_variables)
    assert all(item["escapeSingleQuotes"] is True for item in text_variables)
    panels = [panel for tab in body["tabs"] for panel in tab["panels"]]
    assert len(panels) == expected_panels
    assert max(len(tab["panels"]) for tab in body["tabs"]) <= 6
    assert len({panel["id"] for panel in panels}) == len(panels)
    all_queries = "\n".join(panel["queries"][0]["query"] for panel in panels)
    for extra in extra_variables:
        assert f"${extra}" in all_queries

    for panel in panels:
        assert panel["type"] in {"metric", "line", "bar", "h-bar", "donut", "table"}
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
        if panel["type"] == "h-bar":
            assert panel["layout"]["h"] == 12


def validate_visualization_choices(dashboards: list[dict]) -> None:
    """Guard the deliberate chart choices for ranked and identity-heavy data."""
    by_title = {
        panel["title"]: panel["type"]
        for body in dashboards
        for tab in body["tabs"]
        for panel in tab["panels"]
    }
    horizontal_rankings = {
        "Top Header Sender Domains", "Top Envelope Sender Domains", "Top Source IPs",
        "Top Destination IPs", "Top Policies", "Attack Signatures", "Top Applications",
        "Top IPS Signatures", "Top Requested Hosts", "Top URLs", "Top Interfaces",
        "Top Administrative Users", "Top VM IDs", "Top Container IDs", "Top Programs",
        "Top Unknown Source IPs", "Top Reported Hostnames", "Errors by Unknown Source",
    }
    identity_tables = {
        "Real Sender Addresses", "Envelope Senders / Return-Path", "Header Recipients",
        "Envelope Recipients", "Relay Destinations", "Reject Reasons", "TLS Ciphers",
        "Matching Messages", "PMG to Ubersmith Message Correlation",
    }
    assert all(by_title[title] == "h-bar" for title in horizontal_rankings)
    assert all(by_title[title] == "table" for title in identity_tables)


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
        "mail_tls_trust", "mail_tls_direction", "mail_tls_peer_hostname", "mail_tls_peer_ip",
        "mail_tls_peer_port", "mail_tls_protocol", "mail_tls_cipher", "mail_tls_cipher_bits",
    ):
        assert record[field]


def validate_ubersmith_bootstrap() -> None:
    calls: list[tuple[str, list[dict]]] = []
    original = MODULE.api_request

    def capture(_base: str, path: str, _user: str, _password: str,
                _method: str = "GET", data: list[dict] | None = None) -> dict:
        calls.append((path, data or []))
        return {}

    try:
        MODULE.api_request = capture
        MODULE.bootstrap_schema("http://127.0.0.1:5080", "default", "user", "password", "ubersmith")
    finally:
        MODULE.api_request = original

    assert len(calls) == 1
    path, records = calls[0]
    assert path == "/api/default/ubersmith/_json"
    record = records[0]
    assert record["schema_bootstrap"] == "true"
    for field in (
        "mail_message_id", "mail_queue_id", "mail_sender", "mail_recipient", "mail_relay",
        "mail_status", "mail_dsn", "mail_delay", "mail_message_size", "mail_tls_protocol",
    ):
        assert record[field]


def main() -> int:
    pmg = MODULE.pmg_dashboards()
    validate_dashboard(pmg["pmg-reporting"], 40, "6h")
    validate_dashboard(pmg["pmg-investigation"], 9, "6h",
                       extra_variables=("sender", "recipient"))
    validate_bootstrap_exclusion(pmg["pmg-reporting"])
    validate_bootstrap_exclusion(pmg["pmg-investigation"])
    validate_pmg_bootstrap()
    validate_ubersmith_bootstrap()
    fortigate = MODULE.fortigate_dashboard()
    fortigate_investigation = MODULE.fortigate_investigation_dashboard()
    juniper = MODULE.juniper_dashboard()
    proxmox_ve = MODULE.proxmox_ve_dashboard()
    validate_dashboard(fortigate, 70, "6h")
    validate_dashboard(
        fortigate_investigation, 14, "6h", extra_variables=(
            "src_ip", "dst_ip", "fg_user", "session_id", "policy", "vdom", "search_text",
        ),
    )
    validate_dashboard(juniper, 30, "6h")
    validate_dashboard(proxmox_ve, 32, "6h", "source")
    ubersmith = MODULE.ubersmith_dashboard()
    ubersmith_mail = MODULE.ubersmith_mail_dashboard()
    validate_dashboard(ubersmith, 24, "6h")
    validate_dashboard(ubersmith_mail, 15, "6h", None,
                       extra_variables=("sender", "recipient"))
    validate_bootstrap_exclusion(ubersmith)
    validate_bootstrap_exclusion(ubersmith_mail)
    unclassified = MODULE.unclassified_dashboard()
    overview = MODULE.central_overview_dashboard()
    validate_dashboard(unclassified, 24, "6h", "source")
    validate_dashboard(overview, 22, "6h", None)
    validate_visualization_choices([
        pmg["pmg-reporting"], pmg["pmg-investigation"], fortigate,
        fortigate_investigation, juniper, proxmox_ve,
        ubersmith, ubersmith_mail, unclassified, overview,
    ])
    print("Dashboard definitions passed (280 panels).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
