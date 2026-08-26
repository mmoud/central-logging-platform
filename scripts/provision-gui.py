#!/usr/bin/env python3
"""Provision OpenObserve saved views, cached reports, and safe stream settings."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


SAVED_VIEWS = [
    ("FortiGate - Denied Traffic", "fortigate", "24h",
     "SELECT _timestamp, device_name, fortigate_vd, fortigate_srcip, fortigate_dstip, fortigate_dstport, fortigate_policyid, fortigate_action, message, raw_message FROM \"fortigate\" WHERE lower(coalesce(fortigate_action,'')) IN ('deny','blocked','block','dropped') ORDER BY _timestamp DESC LIMIT 500"),
    ("FortiGate - VPN Events", "fortigate", "24h",
     "SELECT _timestamp, device_name, fortigate_vd, fortigate_user, fortigate_srcip, fortigate_action, fortigate_status, message, raw_message FROM \"fortigate\" WHERE lower(coalesce(fortigate_subtype,'')) LIKE '%vpn%' OR lower(message) LIKE '%vpn%' ORDER BY _timestamp DESC LIMIT 500"),
    ("FortiGate - Admin Activity", "fortigate", "24h",
     "SELECT _timestamp, device_name, fortigate_vd, fortigate_user, fortigate_srcip, fortigate_action, message, raw_message FROM \"fortigate\" WHERE lower(coalesce(fortigate_subtype,'')) IN ('system','admin') OR lower(message) LIKE '%admin%' ORDER BY _timestamp DESC LIMIT 500"),
    ("FortiGate - UTM Detections", "fortigate", "24h",
     "SELECT _timestamp, device_name, fortigate_vd, fortigate_type, fortigate_subtype, fortigate_action, fortigate_srcip, fortigate_dstip, fortigate_app, fortigate_attack, message, raw_message FROM \"fortigate\" WHERE fortigate_attack IS NOT NULL OR lower(coalesce(fortigate_type,'')) IN ('utm','security') ORDER BY _timestamp DESC LIMIT 500"),
    ("PMG - Queue ID Trace", "proxmox_mail_gateway", "24h",
     "SELECT _timestamp, mail_queue_id, syslog_program, mail_sender, mail_recipient, mail_relay, mail_status, mail_dsn, mail_delay, message, raw_message FROM \"proxmox_mail_gateway\" WHERE mail_queue_id IS NOT NULL ORDER BY _timestamp DESC LIMIT 1000"),
    ("PMG - Deferred and Rejected", "proxmox_mail_gateway", "7d",
     "SELECT _timestamp, mail_queue_id, mail_sender, mail_recipient, mail_relay, mail_status, mail_dsn, mail_smtp_response_code, message, raw_message FROM \"proxmox_mail_gateway\" WHERE lower(coalesce(mail_status,'')) IN ('deferred','bounced','rejected') OR lower(message) LIKE '%reject:%' ORDER BY _timestamp DESC LIMIT 1000"),
    ("PMG - Spam and Virus", "proxmox_mail_gateway", "7d",
     "SELECT _timestamp, mail_queue_id, mail_sender, mail_recipient, mail_spam_action, message, raw_message FROM \"proxmox_mail_gateway\" WHERE lower(message) LIKE '%spam%' OR lower(message) LIKE '%virus%' OR lower(message) LIKE '%malware%' ORDER BY _timestamp DESC LIMIT 1000"),
    ("Juniper - Interface and Routing Events", "juniper", "24h",
     "SELECT _timestamp, device_name, juniper_event, juniper_interface, juniper_routing_instance, juniper_peer_ip, message, raw_message FROM \"juniper\" WHERE coalesce(schema_bootstrap,'false') <> 'true' AND (juniper_interface IS NOT NULL OR juniper_peer_ip IS NOT NULL OR lower(message) LIKE '%ospf%' OR lower(message) LIKE '%bgp%') ORDER BY _timestamp DESC LIMIT 500"),
    ("Proxmox - Authentication Failures", "proxmox_ve", "24h",
     "SELECT _timestamp, device_name, syslog_program, proxmox_user, proxmox_source_ip, proxmox_authentication_result, message, raw_message FROM \"proxmox_ve\" WHERE coalesce(schema_bootstrap,'false') <> 'true' AND lower(coalesce(proxmox_authentication_result,'')) IN ('failure','failed','denied') ORDER BY _timestamp DESC LIMIT 500"),
    ("Proxmox - Failed Tasks and Services", "proxmox_ve", "24h",
     "SELECT _timestamp, device_name, syslog_program, proxmox_upid, proxmox_task, proxmox_vmid, proxmox_ctid, message, raw_message FROM \"proxmox_ve\" WHERE coalesce(schema_bootstrap,'false') <> 'true' AND (lower(message) LIKE '%fail%' OR lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','error')) ORDER BY _timestamp DESC LIMIT 500"),
    ("Ubersmith - Application Errors", "ubersmith", "24h",
     "SELECT _timestamp, device_name, source_ip, syslog_program, syslog_severity, message, raw_message FROM \"ubersmith\" WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') ORDER BY _timestamp DESC LIMIT 500"),
    ("Unclassified - New Sources", "unclassified", "24h",
     "SELECT source_ip, coalesce(host_name,'unknown') AS host, min(_timestamp) AS first_seen, max(_timestamp) AS last_seen, count(*) AS events FROM \"unclassified\" WHERE source_ip IS NOT NULL GROUP BY source_ip, coalesce(host_name,'unknown') ORDER BY first_seen DESC LIMIT 200"),
]

STREAM_SETTINGS = {
    "fortigate": {
        "index_fields": ["device_name", "fortigate_vd", "fortigate_type", "fortigate_subtype", "fortigate_action", "fortigate_level"],
        "bloom_filter_fields": ["fortigate_srcip", "fortigate_dstip", "fortigate_sessionid"],
    },
    "proxmox_mail_gateway": {
        "index_fields": ["device_name", "mail_status", "syslog_program", "mail_smtp_response_code"],
        "bloom_filter_fields": ["mail_queue_id", "mail_sender", "mail_recipient"],
    },
    "juniper": {
        "index_fields": ["device_name", "juniper_event", "syslog_severity"],
        "bloom_filter_fields": ["juniper_peer_ip", "juniper_source_address", "juniper_destination_address"],
    },
    "proxmox_ve": {
        "index_fields": ["device_name", "syslog_program", "proxmox_authentication_result"],
        "bloom_filter_fields": ["proxmox_upid", "proxmox_source_ip"],
    },
    "ubersmith": {
        "index_fields": ["device_name", "syslog_program", "syslog_severity", "mail_status"],
        "bloom_filter_fields": ["source_ip", "mail_queue_id", "mail_message_id", "mail_sender", "mail_recipient"],
    },
    "unclassified": {
        "index_fields": ["syslog_program", "syslog_severity", "transport"],
        "bloom_filter_fields": ["source_ip"],
    },
}

REPORTS = [
    ("Daily-PMG-Mail-Report", "Messaging", "PMG Mail Reporting", "24h", "days", 1),
    ("Weekly-FortiGate-Security-Report", "Network & Security", "FortiGate Security & Traffic", "7d", "weeks", 1),
    ("Weekly-Infrastructure-Report", "Infrastructure", "Proxmox VE Operations", "7d", "weeks", 1),
    ("Weekly-Unknown-Source-Report", "Source Discovery", "Unclassified Source Discovery", "7d", "weeks", 1),
]


def load_env(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"missing environment file: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_request(base: str, path: str, user: str, password: str, method: str = "GET",
                body: dict | None = None) -> dict | list:
    data = None if body is None else json.dumps(body).encode("utf-8")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}", data=data, method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"OpenObserve API {method} {path} failed ({exc.code}): {detail}") from exc


def saved_view_data(org: str, stream: str, period: str, sql: str) -> dict:
    return {
        "organizationIdentifier": org,
        "runQuery": False,
        "loading": False,
        "loadingHistogram": False,
        "communicationMethod": "streaming",
        "meta": {
            "logsVisualizeToggle": "logs", "refreshInterval": 0,
            "refreshIntervalLabel": "Off", "showFields": True, "showQuery": True,
            "showHistogram": True, "showPatterns": False, "showDetailTab": False,
            "showTransformEditor": False, "sqlMode": True, "quickMode": False,
            "resultGrid": {"rowsPerPage": 50, "wrapCells": True,
                           "manualRemoveFields": False, "showPagination": True},
            "regions": [],
        },
        "data": {
            "query": sql, "editorValue": sql,
            "datetime": {"type": "relative", "relativeTimePeriod": period,
                         "startTime": 0, "endTime": 0, "selectedDate": {},
                         "selectedTime": {}, "queryRangeRestrictionMsg": "",
                         "queryRangeRestrictionInHour": 100000},
            "stream": {"streamType": "logs", "selectedStream": [stream],
                       "selectedStreamFields": [], "selectedFields": [],
                       "filterField": "", "addToFilter": "", "removeFilterField": ""},
            "resultGrid": {"columns": [], "currentPage": 1, "colOrder": {}, "colSizes": {}},
            "tempFunctionName": "", "tempFunctionContent": "", "transformType": "function",
            "timezone": os.environ.get("PLATFORM_TIMEZONE", "America/Toronto"),
        },
    }


def validate_saved_view_queries(base: str, org: str, user: str, password: str) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    path = f"/api/{urllib.parse.quote(org, safe='')}/_search?type=logs"
    failures = []
    for name, _stream, _period, sql in SAVED_VIEWS:
        body = {"query": {"sql": sql, "start_time": int(start.timestamp() * 1_000_000),
                          "end_time": int(end.timestamp() * 1_000_000), "from": 0, "size": 10}}
        try:
            api_request(base, path, user, password, "POST", body)
        except RuntimeError as exc:
            failures.append(f"{name}: {exc}")
    if failures:
        raise RuntimeError("saved-view SQL validation failed:\n" + "\n".join(failures))
    print(f"saved-view queries passed: {len(SAVED_VIEWS)}")


def provision_saved_views(base: str, org: str, user: str, password: str) -> None:
    encoded_org = urllib.parse.quote(org, safe="")
    path = f"/api/{encoded_org}/savedviews"
    listing = api_request(base, path, user, password)
    existing = {item.get("view_name"): item for item in listing.get("views", [])}
    for name, stream, period, sql in SAVED_VIEWS:
        body = {"view_name": name, "data": saved_view_data(org, stream, period, sql)}
        item = existing.get(name)
        if item:
            view_id = urllib.parse.quote(str(item.get("view_id") or item.get("id")), safe="")
            api_request(base, f"{path}/{view_id}", user, password, "PUT", body)
            print(f"updated saved view: {name}")
        else:
            api_request(base, path, user, password, "POST", body)
            print(f"created saved view: {name}")


def provision_stream_settings(base: str, org: str, user: str, password: str) -> None:
    encoded_org = urllib.parse.quote(org, safe="")
    listing = api_request(base, f"/api/{encoded_org}/streams?type=logs&fetchSchema=true",
                          user, password)
    streams = {item["name"]: item for item in listing.get("list", [])}
    for stream, requested in STREAM_SETTINGS.items():
        item = streams.get(stream)
        if not item:
            print(f"skipped absent stream settings: {stream}")
            continue
        fields = {field["name"] for field in item.get("schema", [])}
        current = item.get("settings", {})
        desired = {
            "index_fields": [field for field in requested["index_fields"] if field in fields],
            "bloom_filter_fields": [field for field in requested["bloom_filter_fields"] if field in fields],
            "full_text_search_keys": [field for field in ("message", "raw_message") if field in fields],
        }
        # The current API consumes add/remove diffs. Keep administrator-added
        # fields and add only the package's supported, schema-present fields.
        body = {
            key: {"add": sorted(set(values) - set(current.get(key, []))), "remove": []}
            for key, values in desired.items()
        }
        encoded_stream = urllib.parse.quote(stream, safe="")
        api_request(base, f"/api/{encoded_org}/streams/{encoded_stream}/settings?type=logs",
                    user, password, "PUT", body)
        print(f"tuned stream search fields: {stream}")

    verified = api_request(base, f"/api/{encoded_org}/streams?type=logs&fetchSchema=true",
                           user, password)
    verified_streams = {item["name"]: item for item in verified.get("list", [])}
    failures = []
    for stream, requested in STREAM_SETTINGS.items():
        item = verified_streams.get(stream)
        if not item:
            continue
        fields = {field["name"] for field in item.get("schema", [])}
        settings = item.get("settings", {})
        expected = {
            "index_fields": {field for field in requested["index_fields"] if field in fields},
            "bloom_filter_fields": {field for field in requested["bloom_filter_fields"] if field in fields},
            "full_text_search_keys": {field for field in ("message", "raw_message") if field in fields},
        }
        for key, values in expected.items():
            missing = values - set(settings.get(key, []))
            if missing:
                failures.append(f"{stream} {key}: {', '.join(sorted(missing))}")
    if failures:
        raise RuntimeError("stream setting verification failed:\n" + "\n".join(failures))


def ensure_folder(base: str, org: str, user: str, password: str, folder_type: str,
                  name: str, description: str) -> str:
    encoded_org = urllib.parse.quote(org, safe="")
    path = f"/api/v2/{encoded_org}/folders/{folder_type}"
    listing = api_request(base, path, user, password)
    existing = next((item for item in listing.get("list", []) if item.get("name") == name), None)
    if existing:
        return existing["folderId"]
    created = api_request(base, path, user, password, "POST",
                          {"name": name, "description": description})
    return created["folderId"]


def provision_reports(base: str, org: str, user: str, password: str) -> None:
    """Create active destination-less cached reports; they never send email."""
    encoded_org = urllib.parse.quote(org, safe="")
    by_title = {}
    for _name, _folder, dashboard_title, _period, _freq_type, _interval in REPORTS:
        query = urllib.parse.urlencode({"title": dashboard_title})
        result = api_request(base, f"/api/{encoded_org}/dashboards?{query}", user, password)
        item = next((candidate for candidate in result.get("dashboards", [])
                     if candidate.get("title") == dashboard_title), None)
        if item:
            by_title[dashboard_title] = item
    path = f"/api/v2/{encoded_org}/reports"
    existing_list = api_request(base, path, user, password)
    existing = {item.get("name"): item for item in existing_list}
    folder_id = ensure_folder(base, org, user, password, "reports", "Prepared Reports",
                              "Destination-less cached dashboard reports; no email is sent.")
    for name, _dashboard_folder, dashboard_title, period, freq_type, interval in REPORTS:
        item = by_title.get(dashboard_title)
        if not item:
            raise RuntimeError(f"report dashboard not found: {dashboard_title}")
        dashboard_body = (
            item.get("v8")
            or item.get("v7")
            or item.get("v6")
            or item.get("v5")
            or {}
        )
        body = {
            "name": name, "orgId": org, "folderId": folder_id, "enabled": True,
            "dashboards": [{
                "dashboard": item["dashboard_id"], "folder": item["folder_id"],
                # Cached reports warm one dashboard tab. Use the curated
                # overview tab; detailed tabs remain available live in the UI.
                "tabs": [dashboard_body["tabs"][0]["tabId"]],
                "attachment_dimensions": None, "email_attachment_type": "standard",
                "report_type": "pdf",
                "timerange": {"type": "relative", "period": period, "from": 0, "to": 0},
                "variables": [],
            }],
            "destinations": [],
            "frequency": {"align_time": True, "cron": "", "interval": interval,
                          "type": freq_type},
            "timezone": os.environ.get("PLATFORM_TIMEZONE", "America/Toronto"),
            "title": name.replace("-", " "),
            "message": "Cached dashboard report; no email destination is configured.",
        }
        report = existing.get(name)
        if report:
            report_id = urllib.parse.quote(str(report.get("id") or report.get("report_id")), safe="")
            api_request(base, f"{path}/{report_id}", user, password, "PUT", body)
            print(f"updated cached report: {name}")
        else:
            api_request(base, path, user, password, "POST", body)
            print(f"created cached report: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--skip-query-validation", action="store_true",
                        help="use only during first install before source streams exist")
    parser.add_argument("--skip-stream-settings", action="store_true")
    parser.add_argument("--skip-reports", action="store_true")
    args = parser.parse_args()
    load_env(args.env_file)
    required = ("ZO_ROOT_USER_EMAIL", "ZO_ROOT_USER_PASSWORD", "ZO_ORG")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError("missing required environment settings: " + ", ".join(missing))
    base = os.environ.get("OPENOBSERVE_INTERNAL_URL", "http://127.0.0.1:5080")
    user = os.environ["ZO_ROOT_USER_EMAIL"]
    password = os.environ["ZO_ROOT_USER_PASSWORD"]
    org = os.environ["ZO_ORG"]
    if not args.skip_query_validation:
        validate_saved_view_queries(base, org, user, password)
    if args.validate_only:
        return 0
    provision_saved_views(base, org, user, password)
    if not args.skip_stream_settings:
        provision_stream_settings(base, org, user, password)
    if not args.skip_reports:
        provision_reports(base, org, user, password)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print(f"GUI provisioning failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
