#!/usr/bin/env python3
"""Create or update the bundled OpenObserve dashboards through the public API."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"missing environment file: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def axis(label: str, alias: str, column: str, *, color: str | None = None,
         sort: str | None = None, aggregation: str | None = None) -> dict:
    item = {"label": label, "alias": alias, "column": column, "color": color,
            "aggregationFunction": aggregation, "isDerived": False}
    if sort:
        item["sortBy"] = sort
    return item


def panel_config(panel_type: str, *, unit: str | None = None) -> dict:
    config = {
        "show_legends": panel_type not in {"metric", "table"},
        "legends_position": None,
        "decimals": 0,
        "top_results_others": False,
        "axis_border_show": False,
        "legend_width": {"unit": "px"},
        "base_map": {"type": "osm"},
        "map_view": {"zoom": 1, "lat": 0, "lng": 0},
        "map_symbol_style": {
            "size": "by Value", "size_by_value": {"min": 1, "max": 100}, "size_fixed": 2
        },
        "drilldown": [],
        "mark_line": [],
        "connect_nulls": False,
        "no_value_replacement": "-",
        "wrap_table_cells": panel_type == "table",
    }
    if unit:
        config["unit"] = unit
    return config


def make_panel(
    stream: str,
    ident: str,
    title: str,
    panel_type: str,
    query: str,
    x_fields: list[tuple[str, str]],
    y_fields: list[tuple[str, str]],
    layout: dict,
    *,
    description: str = "",
    unit: str | None = None,
) -> dict:
    # Pre-staged source schemas may contain one explicit bootstrap marker.
    # Exclude it from every Juniper/PVE query so validation never changes the
    # operational totals shown after real devices begin sending logs.
    if stream in {"juniper", "proxmox_ve"}:
        marker = "coalesce(schema_bootstrap,'false') <> 'true'"
        where_match = re.search(r"(?i)\bWHERE\b", query)
        boundary_pattern = re.compile(r"(?i)\b(GROUP BY|ORDER BY|LIMIT)\b")
        if where_match:
            boundary = boundary_pattern.search(query, where_match.end())
            end = boundary.start() if boundary else len(query)
            condition = query[where_match.end():end].strip()
            query = (query[:where_match.start()] + f"WHERE {marker} AND ({condition}) " +
                     query[end:])
        else:
            boundary = boundary_pattern.search(query)
            end = boundary.start() if boundary else len(query)
            query = query[:end].rstrip() + f" WHERE {marker} " + query[end:]

    # Dashboard schema v5's frontend is most reliable when custom SQL output
    # columns use its canonical x_axis_N/y_axis_N names. Keep friendly labels
    # in the axis metadata while rewriting only SQL alias declarations and
    # GROUP/ORDER references (never source field names).
    # OpenObserve tables model the first selected column as X and every other
    # selected column as Y. A table with every column in X passes the API but
    # the v5 renderer treats it as an incomplete panel and returns no rows.
    if panel_type == "table" and x_fields:
        table_fields = x_fields + y_fields
        x_fields = table_fields[:1]
        y_fields = table_fields[1:]

    x_aliases = [(label, alias, f"x_axis_{index}") for index, (label, alias) in enumerate(x_fields, 1)]
    y_aliases = [(label, alias, f"y_axis_{index}") for index, (label, alias) in enumerate(y_fields, 1)]
    for _label, old, new in x_aliases + y_aliases:
        query, replacements = re.subn(rf"(?i)\bAS\s+{re.escape(old)}\b", f'AS "{new}"', query)
        if replacements == 0:
            query = re.sub(
                rf"(?i)(\bSELECT\s+|,\s*)({re.escape(old)})(\s*)(?=,|\s+FROM)",
                lambda match: f'{match.group(1)}{match.group(2)} AS "{new}"{match.group(3)}',
                query,
                count=1,
            )
        query = re.sub(rf"(?i)\b(GROUP BY|ORDER BY)\s+{re.escape(old)}\b",
                       lambda match: f"{match.group(1)} {new}", query)
    return {
        "id": ident,
        "type": panel_type,
        "title": title,
        "description": description,
        "config": panel_config(panel_type, unit=unit),
        "queryType": "sql",
        "queries": [{
            "query": query,
            "vrlFunctionQuery": "",
            "customQuery": True,
            "fields": {
                "stream": stream,
                "stream_type": "logs",
                "x": [axis(label, canonical, alias, sort="ASC" if alias == "ts" else None,
                           aggregation="histogram" if alias == "ts" else None)
                      for label, alias, canonical in x_aliases],
                "y": [axis(label, canonical, alias, color="#5960b2",
                           aggregation=None if panel_type == "table" else "count")
                      for label, alias, canonical in y_aliases],
                "z": [],
                "breakdown": [],
                "filter": {"filterType": "group", "logicalOperator": "AND", "conditions": []},
            },
            "config": {"promql_legend": "", "layer_type": "scatter", "weight_fixed": 1,
                       "limit": 0, "min": 0, "max": 100},
        }],
        "layout": layout,
        "htmlContent": "",
        "markdownContent": "",
    }


def build_tab(stream: str, tab_id: str, name: str, specs: list[dict]) -> dict:
    panels = []
    row_y = 0
    row_x = 0
    row_h = 0
    for number, spec in enumerate(specs, start=1):
        panel_type = spec[1]
        width = 12 if panel_type == "metric" else 24
        height = 8 if panel_type == "metric" else 10
        if panel_type == "table":
            width, height = 48, 13
        if row_x + width > 48:
            row_y += row_h
            row_x = 0
            row_h = 0
        layout = {"x": row_x, "y": row_y, "w": width, "h": height, "i": number, "moved": False}
        row_x += width
        row_h = max(row_h, height)
        title, typ, sql, xs, ys, *rest = spec
        extra = rest[0] if rest else {}
        panels.append(make_panel(stream, f"Panel_{tab_id}_{number}", title, typ, sql, xs, ys, layout, **extra))
    return {"tabId": tab_id, "name": name, "panels": panels}


def pmg_dashboard() -> dict:
    s = '"proxmox_mail_gateway"'
    overview = [
        ("Unique Messages", "metric", f"SELECT count(DISTINCT mail_queue_id) AS value FROM {s} WHERE mail_queue_id IS NOT NULL", [], [("Messages", "value")]),
        ("Successful Deliveries", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(mail_status) IN ('sent','delivered')", [], [("Delivered", "value")]),
        ("Deferred", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(mail_status) = 'deferred'", [], [("Deferred", "value")]),
        ("Rejected / Bounced", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(mail_status,'')) IN ('bounced','rejected') OR lower(message) LIKE '%reject:%'", [], [("Rejected", "value")]),
        ("Spam Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(message) LIKE '%spam%'", [], [("Spam", "value")]),
        ("Virus / Malware Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(message) LIKE '%virus%' OR lower(message) LIKE '%malware%'", [], [("Malware", "value")]),
        ("Message Volume by Day", "line", f"SELECT histogram(_timestamp, '1 day') AS ts, count(DISTINCT mail_queue_id) AS value FROM {s} WHERE mail_queue_id IS NOT NULL GROUP BY ts ORDER BY ts", [("Day", "ts")], [("Messages", "value")]),
        ("Delivery Status", "donut", f"SELECT coalesce(mail_status,'other') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Status", "label")], [("Events", "value")]),
        ("Postfix / PMG Components", "bar", f"SELECT syslog_program AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 20", [("Component", "label")], [("Events", "value")]),
        ("Recent Mail Events", "table", f"SELECT _timestamp AS event_time, mail_queue_id AS queue_id, syslog_program AS component, mail_sender AS sender, mail_recipient AS recipient, mail_status AS status, mail_dsn AS dsn, message FROM {s} ORDER BY _timestamp DESC LIMIT 200", [("Time", "event_time"), ("Queue ID", "queue_id"), ("Component", "component"), ("Sender", "sender"), ("Recipient", "recipient"), ("Status", "status"), ("DSN", "dsn"), ("Message", "message")], []),
    ]
    flow = [
        ("Top Senders", "bar", f"SELECT mail_sender AS label, count(DISTINCT mail_queue_id) AS value FROM {s} WHERE mail_sender IS NOT NULL AND mail_sender <> '' GROUP BY label ORDER BY value DESC LIMIT 20", [("Sender", "label")], [("Messages", "value")]),
        ("Top Sender Domains", "bar", f"SELECT mail_sender_domain AS label, count(DISTINCT mail_queue_id) AS value FROM {s} WHERE mail_sender_domain IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Domain", "label")], [("Messages", "value")]),
        ("Top Recipients", "bar", f"SELECT mail_recipient AS label, count(*) AS value FROM {s} WHERE mail_recipient IS NOT NULL AND mail_recipient <> '' GROUP BY label ORDER BY value DESC LIMIT 20", [("Recipient", "label")], [("Deliveries", "value")]),
        ("Top Recipient Domains", "bar", f"SELECT mail_recipient_domain AS label, count(*) AS value FROM {s} WHERE mail_recipient_domain IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Domain", "label")], [("Deliveries", "value")]),
        ("Flow Direction (by component)", "donut", f"SELECT CASE WHEN syslog_program = 'postfix/smtpd' THEN 'inbound receive' WHEN syslog_program = 'postfix/smtp' THEN 'outbound delivery' ELSE 'internal processing' END AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Direction", "label")], [("Events", "value")]),
        ("Top Relay Destinations", "bar", f"SELECT mail_relay AS label, count(*) AS value FROM {s} WHERE mail_relay IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Relay", "label")], [("Deliveries", "value")]),
        ("Top Source IPs", "bar", f"SELECT mail_source_ip AS label, count(*) AS value FROM {s} WHERE mail_source_ip IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Source IP", "label")], [("Connections", "value")]),
        ("SMTP Response Codes", "bar", f"SELECT mail_smtp_response_code AS label, count(*) AS value FROM {s} WHERE mail_smtp_response_code IS NOT NULL GROUP BY label ORDER BY value DESC", [("Code", "label")], [("Responses", "value")]),
        ("Transferred Message Bytes", "line", f"SELECT histogram(_timestamp, '1 day') AS ts, sum(try_cast(mail_message_size AS BIGINT)) AS value FROM {s} WHERE mail_message_size IS NOT NULL GROUP BY ts ORDER BY ts", [("Day", "ts")], [("Bytes", "value")], {"unit": "bytes"}),
        ("Slowest Deliveries", "table", f"SELECT _timestamp AS event_time, mail_queue_id AS queue_id, mail_recipient AS recipient, mail_relay AS relay, mail_delay AS delay, mail_delays AS stages, mail_status AS status, message FROM {s} WHERE mail_delay IS NOT NULL ORDER BY try_cast(mail_delay AS DOUBLE) DESC LIMIT 100", [("Time", "event_time"), ("Queue ID", "queue_id"), ("Recipient", "recipient"), ("Relay", "relay"), ("Delay", "delay"), ("Stages", "stages"), ("Status", "status"), ("Message", "message")], []),
    ]
    filtering = [
        ("Rejections Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '%reject:%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Rejected", "value")]),
        ("Top Reject Reasons", "bar", f"SELECT message AS label, count(*) AS value FROM {s} WHERE lower(message) LIKE '%reject:%' GROUP BY label ORDER BY value DESC LIMIT 20", [("Reason", "label")], [("Events", "value")]),
        ("Deferred by Relay", "bar", f"SELECT coalesce(mail_relay,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(mail_status) = 'deferred' GROUP BY label ORDER BY value DESC LIMIT 20", [("Relay", "label")], [("Deferred", "value")]),
        ("DSN Distribution", "donut", f"SELECT mail_dsn AS label, count(*) AS value FROM {s} WHERE mail_dsn IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("DSN", "label")], [("Events", "value")]),
        ("Spam Activity", "line", f"SELECT histogram(_timestamp, '1 day') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '%spam%' GROUP BY ts ORDER BY ts", [("Day", "ts")], [("Spam Events", "value")]),
        ("Virus / Malware Activity", "line", f"SELECT histogram(_timestamp, '1 day') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '%virus%' OR lower(message) LIKE '%malware%' GROUP BY ts ORDER BY ts", [("Day", "ts")], [("Malware Events", "value")]),
        ("Spam / Virus Detail", "table", f"SELECT _timestamp AS event_time, mail_queue_id AS queue_id, syslog_program AS component, mail_sender AS sender, mail_recipient AS recipient, message, raw_message FROM {s} WHERE lower(message) LIKE '%spam%' OR lower(message) LIKE '%virus%' OR lower(message) LIKE '%malware%' ORDER BY _timestamp DESC LIMIT 200", [("Time", "event_time"), ("Queue ID", "queue_id"), ("Component", "component"), ("Sender", "sender"), ("Recipient", "recipient"), ("Message", "message"), ("Raw", "raw_message")], []),
        ("Rejected / Deferred Detail", "table", f"SELECT _timestamp AS event_time, mail_queue_id AS queue_id, mail_sender AS sender, mail_recipient AS recipient, mail_relay AS relay, mail_dsn AS dsn, mail_status AS status, mail_smtp_response_code AS response_code, message FROM {s} WHERE lower(coalesce(mail_status,'')) IN ('deferred','bounced','rejected') OR lower(message) LIKE '%reject:%' ORDER BY _timestamp DESC LIMIT 200", [("Time", "event_time"), ("Queue ID", "queue_id"), ("Sender", "sender"), ("Recipient", "recipient"), ("Relay", "relay"), ("DSN", "dsn"), ("Status", "status"), ("Response", "response_code"), ("Message", "message")], []),
    ]
    trace = [
        ("Queue-ID Correlation Timeline", "table", f"SELECT _timestamp AS event_time, mail_queue_id AS queue_id, syslog_program AS component, mail_sender AS sender, mail_recipient AS recipient, mail_relay AS relay, mail_status AS status, mail_delay AS delay, message, raw_message FROM {s} WHERE mail_queue_id IS NOT NULL ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Queue ID", "queue_id"), ("Component", "component"), ("Sender", "sender"), ("Recipient", "recipient"), ("Relay", "relay"), ("Status", "status"), ("Delay", "delay"), ("Message", "message"), ("Raw", "raw_message")], []),
        ("Raw PMG / Postfix Events", "table", f"SELECT _timestamp AS event_time, host_name AS host, source_ip, syslog_facility AS facility, syslog_severity AS severity, syslog_program AS program, message, raw_message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Host", "host"), ("Collector Source", "source_ip"), ("Facility", "facility"), ("Severity", "severity"), ("Program", "program"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    return dashboard("PMG Mail Reporting", "Comprehensive PMG/Postfix mail-flow, delivery, filtering and queue-ID correlation reporting.", [
        build_tab("proxmox_mail_gateway", "pmg_overview", "Overview", overview[:6]),
        build_tab("proxmox_mail_gateway", "pmg_volume", "Volume & Components", overview[6:9] + flow[8:9]),
        build_tab("proxmox_mail_gateway", "pmg_recent", "Recent Mail", overview[9:]),
        build_tab("proxmox_mail_gateway", "pmg_people", "Senders & Recipients", flow[:4]),
        build_tab("proxmox_mail_gateway", "pmg_routing", "Routing & SMTP", flow[4:8]),
        build_tab("proxmox_mail_gateway", "pmg_delivery", "Delivery Performance", flow[9:]),
        build_tab("proxmox_mail_gateway", "pmg_filtering", "Filtering", filtering[:4]),
        build_tab("proxmox_mail_gateway", "pmg_filter_activity", "Filter Activity", filtering[4:6]),
        build_tab("proxmox_mail_gateway", "pmg_filter_detail", "Spam / Virus Detail", filtering[6:7]),
        build_tab("proxmox_mail_gateway", "pmg_delivery_detail", "Rejected / Deferred", filtering[7:]),
        build_tab("proxmox_mail_gateway", "pmg_trace", "Queue Trace", trace[:1]),
        build_tab("proxmox_mail_gateway", "pmg_raw", "Raw Events", trace[1:]),
    ])


def fortigate_dashboard() -> dict:
    s = '"fortigate"'
    overview = [
        ("Total Events", "metric", f"SELECT count(*) AS value FROM {s}", [], [("Events", "value")]),
        ("Denied / Blocked", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_action,'')) IN ('deny','blocked','block','dropped')", [], [("Denied", "value")]),
        ("Security Threats", "metric", f"SELECT count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL OR lower(coalesce(fortigate_type,'')) IN ('utm','security')", [], [("Threats", "value")]),
        ("Unique Sources", "metric", f"SELECT count(DISTINCT fortigate_srcip) AS value FROM {s} WHERE fortigate_srcip IS NOT NULL", [], [("Sources", "value")]),
        ("Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Severity", "donut", f"SELECT coalesce(fortigate_level,syslog_severity,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Severity", "label")], [("Events", "value")]),
        ("Events by VDOM", "bar", f"SELECT coalesce(fortigate_vd,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("VDOM", "label")], [("Events", "value")]),
        ("Actions", "donut", f"SELECT coalesce(fortigate_action,'none') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 20", [("Action", "label")], [("Events", "value")]),
        ("Log Types", "bar", f"SELECT coalesce(fortigate_type,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Type", "label")], [("Events", "value")]),
        ("Recent FortiGate Events", "table", f"SELECT _timestamp AS event_time, device_name, fortigate_vd AS vdom, fortigate_level AS severity, fortigate_type AS log_type, fortigate_subtype AS subtype, fortigate_action AS action, fortigate_srcip AS source, fortigate_dstip AS destination, message FROM {s} ORDER BY _timestamp DESC LIMIT 200", [("Time", "event_time"), ("Device", "device_name"), ("VDOM", "vdom"), ("Severity", "severity"), ("Type", "log_type"), ("Subtype", "subtype"), ("Action", "action"), ("Source", "source"), ("Destination", "destination"), ("Message", "message")], []),
    ]
    traffic = [
        ("Top Source IPs", "bar", f"SELECT fortigate_srcip AS label, count(*) AS value FROM {s} WHERE fortigate_srcip IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Source IP", "label")], [("Sessions", "value")]),
        ("Top Destination IPs", "bar", f"SELECT fortigate_dstip AS label, count(*) AS value FROM {s} WHERE fortigate_dstip IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Destination IP", "label")], [("Sessions", "value")]),
        ("Top Policies", "bar", f"SELECT coalesce(fortigate_policyname,fortigate_policyid) AS label, count(*) AS value FROM {s} WHERE fortigate_policyid IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Policy", "label")], [("Sessions", "value")]),
        ("Top Services", "bar", f"SELECT fortigate_service AS label, count(*) AS value FROM {s} WHERE fortigate_service IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Service", "label")], [("Sessions", "value")]),
        ("Applications", "bar", f"SELECT coalesce(fortigate_app,fortigate_appcat) AS label, count(*) AS value FROM {s} WHERE fortigate_app IS NOT NULL OR fortigate_appcat IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Application", "label")], [("Sessions", "value")]),
        ("Source Countries", "bar", f"SELECT fortigate_srccountry AS label, count(*) AS value FROM {s} WHERE fortigate_srccountry IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Country", "label")], [("Sessions", "value")]),
        ("Destination Countries", "bar", f"SELECT fortigate_dstcountry AS label, count(*) AS value FROM {s} WHERE fortigate_dstcountry IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Country", "label")], [("Sessions", "value")]),
        ("Bytes Sent", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, sum(try_cast(fortigate_sentbyte AS BIGINT)) AS value FROM {s} WHERE fortigate_sentbyte IS NOT NULL GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Bytes", "value")], {"unit": "bytes"}),
        ("Bytes Received", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, sum(try_cast(fortigate_rcvdbyte AS BIGINT)) AS value FROM {s} WHERE fortigate_rcvdbyte IS NOT NULL GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Bytes", "value")], {"unit": "bytes"}),
        ("Traffic Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_srcip AS source_ip, fortigate_srcport AS source_port, fortigate_dstip AS destination_ip, fortigate_dstport AS destination_port, fortigate_service AS service, fortigate_policyname AS policy, fortigate_action AS action, fortigate_sentbyte AS sent_bytes, fortigate_rcvdbyte AS received_bytes FROM {s} WHERE fortigate_type = 'traffic' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Source", "source_ip"), ("Src Port", "source_port"), ("Destination", "destination_ip"), ("Dst Port", "destination_port"), ("Service", "service"), ("Policy", "policy"), ("Action", "action"), ("Sent", "sent_bytes"), ("Received", "received_bytes")], []),
    ]
    security = [
        ("Attack Signatures", "bar", f"SELECT fortigate_attack AS label, count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Attack", "label")], [("Events", "value")]),
        ("Threat Severity", "donut", f"SELECT coalesce(fortigate_severity,fortigate_level,'unknown') AS label, count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL OR fortigate_severity IS NOT NULL GROUP BY label ORDER BY value DESC", [("Severity", "label")], [("Events", "value")]),
        ("Application Risk", "bar", f"SELECT fortigate_apprisk AS label, count(*) AS value FROM {s} WHERE fortigate_apprisk IS NOT NULL GROUP BY label ORDER BY value DESC", [("Risk", "label")], [("Events", "value")]),
        ("Security Subtypes", "bar", f"SELECT fortigate_subtype AS label, count(*) AS value FROM {s} WHERE fortigate_subtype IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Subtype", "label")], [("Events", "value")]),
        ("Blocked by Policy", "bar", f"SELECT coalesce(fortigate_policyname,fortigate_policyid,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_action,'')) IN ('deny','blocked','block','dropped') GROUP BY label ORDER BY value DESC LIMIT 20", [("Policy", "label")], [("Blocks", "value")]),
        ("Threat Sources", "bar", f"SELECT fortigate_srcip AS label, count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Source IP", "label")], [("Threats", "value")]),
        ("Threat Destinations", "bar", f"SELECT fortigate_dstip AS label, count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Destination IP", "label")], [("Threats", "value")]),
        ("Security Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL OR lower(coalesce(fortigate_type,'')) IN ('utm','security') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Critical Security Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_level AS level, fortigate_severity AS threat_severity, fortigate_attack AS attack, fortigate_action AS action, fortigate_srcip AS source, fortigate_dstip AS destination, fortigate_policyname AS policy, fortigate_msg AS detail, message FROM {s} WHERE fortigate_attack IS NOT NULL OR lower(coalesce(fortigate_level,'')) IN ('critical','alert','emergency','error') ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Level", "level"), ("Threat", "threat_severity"), ("Attack", "attack"), ("Action", "action"), ("Source", "source"), ("Destination", "destination"), ("Policy", "policy"), ("Detail", "detail"), ("Message", "message")], []),
    ]
    system = [
        ("VPN Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) LIKE '%vpn%' OR lower(message) LIKE '%vpn%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("VPN Events", "value")]),
        ("Admin / Authentication Users", "bar", f"SELECT coalesce(fortigate_user,'unknown') AS label, count(*) AS value FROM {s} WHERE fortigate_user IS NOT NULL OR lower(coalesce(fortigate_subtype,'')) LIKE '%admin%' OR lower(message) LIKE '%login%' GROUP BY label ORDER BY value DESC LIMIT 20", [("User", "label")], [("Events", "value")]),
        ("Authentication Outcomes", "donut", f"SELECT coalesce(fortigate_status,fortigate_action,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(message) LIKE '%login%' OR fortigate_authproto IS NOT NULL GROUP BY label ORDER BY value DESC", [("Outcome", "label")], [("Events", "value")]),
        ("HA Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '% ha %' OR lower(message) LIKE '%failover%' OR lower(coalesce(fortigate_subtype,'')) LIKE '%ha%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("HA Events", "value")]),
        ("Routing Events", "bar", f"SELECT coalesce(fortigate_eventtype,fortigate_subtype,'routing') AS label, count(*) AS value FROM {s} WHERE lower(message) LIKE '%bgp%' OR lower(message) LIKE '%ospf%' OR lower(message) LIKE '%route%' GROUP BY label ORDER BY value DESC LIMIT 20", [("Event", "label")], [("Events", "value")]),
        ("Admin / VPN / System Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_level AS severity, fortigate_user AS user_name, fortigate_status AS status, fortigate_action AS action, fortigate_logdesc AS description, fortigate_reason AS reason, fortigate_msg AS detail, message FROM {s} WHERE fortigate_user IS NOT NULL OR lower(message) LIKE '%vpn%' OR lower(message) LIKE '%admin%' OR lower(message) LIKE '%login%' OR lower(message) LIKE '%failover%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Severity", "severity"), ("User", "user_name"), ("Status", "status"), ("Action", "action"), ("Description", "description"), ("Reason", "reason"), ("Detail", "detail"), ("Message", "message")], []),
    ]
    utm_summary = [
        ("UTM / Security Profile Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_type,'')) = 'utm' OR lower(coalesce(fortigate_subtype,'')) IN ('app-ctrl','ips','webfilter','virus','dlp','dns','emailfilter','ssl','file-filter','waf','anomaly','casb','virtual-patch')", [], [("Events", "value")]),
        ("Application Control", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'app-ctrl' OR fortigate_app IS NOT NULL", [], [("Events", "value")]),
        ("IPS / Intrusion", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'ips' OR fortigate_attack IS NOT NULL", [], [("Events", "value")]),
        ("Web / DNS Filtering", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('webfilter','dns')", [], [("Events", "value")]),
        ("Antivirus / File Filter", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('virus','file-filter')", [], [("Events", "value")]),
        ("DLP / Email / SSL", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('dlp','emailfilter','ssl')", [], [("Events", "value")]),
    ]
    utm_trends = [
        ("Security Profile Distribution", "bar", f"SELECT coalesce(fortigate_subtype,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_type,'')) = 'utm' OR lower(coalesce(fortigate_subtype,'')) IN ('app-ctrl','ips','webfilter','virus','dlp','dns','emailfilter','ssl','file-filter','waf','anomaly','casb','virtual-patch') GROUP BY label ORDER BY value DESC", [("Profile Type", "label")], [("Events", "value")]),
        ("UTM Actions", "bar", f"SELECT coalesce(fortigate_utmaction,fortigate_action,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_type,'')) = 'utm' GROUP BY label ORDER BY value DESC LIMIT 20", [("Action", "label")], [("Events", "value")]),
        ("Security Profile Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_type,'')) = 'utm' OR lower(coalesce(fortigate_subtype,'')) IN ('app-ctrl','ips','webfilter','virus','dlp','dns','emailfilter','ssl','file-filter','waf','anomaly','casb','virtual-patch') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("UTM Blocks Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_utmaction,fortigate_action,'')) IN ('block','blocked','deny','dropped','reset','quarantine','reject') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Blocked", "value")]),
    ]
    app_control = [
        ("Top Applications", "bar", f"SELECT fortigate_app AS label, count(*) AS value FROM {s} WHERE fortigate_app IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Application", "label")], [("Events", "value")]),
        ("Application Categories", "bar", f"SELECT fortigate_appcat AS label, count(*) AS value FROM {s} WHERE fortigate_appcat IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Category", "label")], [("Events", "value")]),
        ("Application Risk", "donut", f"SELECT coalesce(fortigate_apprisk,'unknown') AS label, count(*) AS value FROM {s} WHERE fortigate_app IS NOT NULL OR fortigate_apprisk IS NOT NULL GROUP BY label ORDER BY value DESC", [("Risk", "label")], [("Events", "value")]),
        ("Application Actions", "bar", f"SELECT coalesce(fortigate_utmaction,fortigate_action,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'app-ctrl' OR fortigate_app IS NOT NULL GROUP BY label ORDER BY value DESC", [("Action", "label")], [("Events", "value")]),
        ("Application Control Profiles", "bar", f"SELECT coalesce(fortigate_applist,'unknown') AS label, count(*) AS value FROM {s} WHERE fortigate_app IS NOT NULL OR fortigate_applist IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Profile", "label")], [("Events", "value")]),
        ("Application Control Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_applist AS profile, fortigate_appid AS app_id, fortigate_app AS application, fortigate_appcat AS category, fortigate_apprisk AS risk, coalesce(fortigate_utmaction,fortigate_action) AS action, fortigate_user AS user_name, fortigate_srcip AS source, fortigate_dstip AS destination, fortigate_hostname AS host, fortigate_url AS url, message FROM {s} WHERE fortigate_app IS NOT NULL OR lower(coalesce(fortigate_subtype,'')) = 'app-ctrl' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Profile", "profile"), ("App ID", "app_id"), ("Application", "application"), ("Category", "category"), ("Risk", "risk"), ("Action", "action"), ("User", "user_name"), ("Source", "source"), ("Destination", "destination"), ("Host", "host"), ("URL", "url"), ("Message", "message")], []),
    ]
    ips = [
        ("Top IPS Signatures", "bar", f"SELECT fortigate_attack AS label, count(*) AS value FROM {s} WHERE fortigate_attack IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Signature", "label")], [("Events", "value")]),
        ("Top Attack IDs", "bar", f"SELECT fortigate_attackid AS label, count(*) AS value FROM {s} WHERE fortigate_attackid IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Attack ID", "label")], [("Events", "value")]),
        ("IPS Actions", "donut", f"SELECT coalesce(fortigate_utmaction,fortigate_action,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'ips' OR fortigate_attack IS NOT NULL GROUP BY label ORDER BY value DESC", [("Action", "label")], [("Events", "value")]),
        ("IPS by VDOM", "bar", f"SELECT coalesce(fortigate_vd,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'ips' OR fortigate_attack IS NOT NULL GROUP BY label ORDER BY value DESC", [("VDOM", "label")], [("Events", "value")]),
        ("IPS Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'ips' OR fortigate_attack IS NOT NULL GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("IPS Event Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_attackid AS attack_id, fortigate_attack AS signature, fortigate_severity AS severity, coalesce(fortigate_utmaction,fortigate_action) AS action, fortigate_srcip AS source, fortigate_srcport AS source_port, fortigate_dstip AS destination, fortigate_dstport AS destination_port, fortigate_service AS service, fortigate_policyname AS policy, fortigate_incidentserialno AS incident, message, raw_message FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) = 'ips' OR fortigate_attack IS NOT NULL ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Attack ID", "attack_id"), ("Signature", "signature"), ("Severity", "severity"), ("Action", "action"), ("Source", "source"), ("Src Port", "source_port"), ("Destination", "destination"), ("Dst Port", "destination_port"), ("Service", "service"), ("Policy", "policy"), ("Incident", "incident"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    web_dns = [
        ("Top Requested Hosts", "bar", f"SELECT fortigate_hostname AS label, count(*) AS value FROM {s} WHERE fortigate_hostname IS NOT NULL AND lower(coalesce(fortigate_subtype,'')) IN ('webfilter','dns','app-ctrl') GROUP BY label ORDER BY value DESC LIMIT 25", [("Host", "label")], [("Requests", "value")]),
        ("Top URLs", "bar", f"SELECT fortigate_url AS label, count(*) AS value FROM {s} WHERE fortigate_url IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("URL", "label")], [("Requests", "value")]),
        ("Web / DNS Actions", "donut", f"SELECT coalesce(fortigate_utmaction,fortigate_action,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('webfilter','dns') GROUP BY label ORDER BY value DESC", [("Action", "label")], [("Events", "value")]),
        ("HTTP Methods", "bar", f"SELECT fortigate_httpmethod AS label, count(*) AS value FROM {s} WHERE fortigate_httpmethod IS NOT NULL GROUP BY label ORDER BY value DESC", [("Method", "label")], [("Requests", "value")]),
        ("Web / DNS Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('webfilter','dns') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Web / DNS Filter Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_subtype AS profile_type, coalesce(fortigate_utmaction,fortigate_action) AS action, fortigate_user AS user_name, fortigate_srcip AS source, fortigate_dstip AS destination, fortigate_hostname AS host, fortigate_httpmethod AS method, fortigate_url AS url, fortigate_policyname AS policy, fortigate_crlevel AS content_risk, fortigate_crscore AS risk_score, message, raw_message FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('webfilter','dns') ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Profile", "profile_type"), ("Action", "action"), ("User", "user_name"), ("Source", "source"), ("Destination", "destination"), ("Host", "host"), ("Method", "method"), ("URL", "url"), ("Policy", "policy"), ("Content Risk", "content_risk"), ("Risk Score", "risk_score"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    content_security = [
        ("Content Inspection Types", "bar", f"SELECT coalesce(fortigate_subtype,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('virus','file-filter','dlp','emailfilter','ssl','waf','anomaly','casb','virtual-patch') GROUP BY label ORDER BY value DESC", [("Inspection Type", "label")], [("Events", "value")]),
        ("Antivirus / File Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('virus','file-filter') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("DLP / Email Filter Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('dlp','emailfilter') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("SSL / WAF / Anomaly Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('ssl','waf','anomaly','casb','virtual-patch') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Content Security Actions", "bar", f"SELECT coalesce(fortigate_utmaction,fortigate_action,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('virus','file-filter','dlp','emailfilter','ssl','waf','anomaly','casb','virtual-patch') GROUP BY label ORDER BY value DESC", [("Action", "label")], [("Events", "value")]),
        ("Content Security Detail", "table", f"SELECT _timestamp AS event_time, fortigate_vd AS vdom, fortigate_subtype AS profile_type, fortigate_level AS level, fortigate_severity AS severity, coalesce(fortigate_utmaction,fortigate_action) AS action, fortigate_srcip AS source, fortigate_dstip AS destination, fortigate_hostname AS host, fortigate_url AS url, fortigate_policyname AS policy, fortigate_incidentserialno AS incident, fortigate_msg AS detail, message, raw_message FROM {s} WHERE lower(coalesce(fortigate_subtype,'')) IN ('virus','file-filter','dlp','emailfilter','ssl','waf','anomaly','casb','virtual-patch') ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("VDOM", "vdom"), ("Profile", "profile_type"), ("Level", "level"), ("Severity", "severity"), ("Action", "action"), ("Source", "source"), ("Destination", "destination"), ("Host", "host"), ("URL", "url"), ("Policy", "policy"), ("Incident", "incident"), ("Detail", "detail"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    raw = [
        ("Raw FortiGate Events", "table", f"SELECT _timestamp AS event_time, received_at, device_name, source_ip AS collector_source_ip, fortigate_devname AS fortigate_device, fortigate_devid AS serial, fortigate_vd AS vdom, syslog_facility AS facility, syslog_severity AS severity, syslog_program AS program, message, raw_message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Received", "received_at"), ("Mapped Device", "device_name"), ("Sender IP", "collector_source_ip"), ("FortiGate", "fortigate_device"), ("Serial", "serial"), ("VDOM", "vdom"), ("Facility", "facility"), ("Severity", "severity"), ("Program", "program"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    return dashboard("FortiGate Security & Traffic", "FortiGate traffic, UTM/threat, VDOM, VPN, administrative, HA and routing reporting.", [
        build_tab("fortigate", "fg_overview", "Overview", overview[:6]),
        build_tab("fortigate", "fg_classification", "VDOM & Classification", overview[6:9]),
        build_tab("fortigate", "fg_recent", "Recent Events", overview[9:]),
        build_tab("fortigate", "fg_traffic_top", "Traffic Topology", traffic[:4]),
        build_tab("fortigate", "fg_traffic_geo", "Applications & Geography", traffic[4:7]),
        build_tab("fortigate", "fg_traffic_volume", "Traffic Volume", traffic[7:9]),
        build_tab("fortigate", "fg_traffic_detail", "Traffic Detail", traffic[9:]),
        build_tab("fortigate", "fg_security", "Security Summary", security[:4]),
        build_tab("fortigate", "fg_security_action", "Blocking & Threats", security[4:8]),
        build_tab("fortigate", "fg_security_detail", "Security Detail", security[8:]),
        build_tab("fortigate", "fg_system", "Admin, VPN & HA", system[:4]),
        build_tab("fortigate", "fg_routing", "Routing", system[4:5]),
        build_tab("fortigate", "fg_system_detail", "System Detail", system[5:]),
        build_tab("fortigate", "fg_utm_summary", "UTM Overview", utm_summary),
        build_tab("fortigate", "fg_utm_trends", "UTM Trends & Actions", utm_trends),
        build_tab("fortigate", "fg_app_control", "Application Control", app_control),
        build_tab("fortigate", "fg_ips", "IPS & Intrusion", ips),
        build_tab("fortigate", "fg_web_dns", "Web & DNS Filtering", web_dns),
        build_tab("fortigate", "fg_content_security", "Content Security", content_security),
        build_tab("fortigate", "fg_raw", "Raw Events", raw),
    ])


def juniper_dashboard() -> dict:
    """Operational reporting for Junos routers and optional SRX security logs."""
    s = '"juniper"'
    overview = [
        ("Total Events", "metric", f"SELECT count(*) AS value FROM {s}", [], [("Events", "value")]),
        ("Critical / Error", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error')", [], [("Events", "value")]),
        ("Interface Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE juniper_interface IS NOT NULL OR upper(coalesce(juniper_event,'')) LIKE '%IF%' OR lower(message) LIKE '%interface%'", [], [("Events", "value")]),
        ("Routing Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(message) LIKE '%bgp%' OR lower(message) LIKE '%ospf%' OR lower(message) LIKE '%route%' OR lower(message) LIKE '%rpd%'", [], [("Events", "value")]),
        ("Authentication Failures", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(message) LIKE '%failed%' OR lower(message) LIKE '%authentication failure%' OR lower(message) LIKE '%login failure%'", [], [("Failures", "value")]),
        ("Active Devices", "metric", f"SELECT count(DISTINCT device_name) AS value FROM {s} WHERE device_name IS NOT NULL", [], [("Devices", "value")]),
    ]
    trends = [
        ("Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Severity Distribution", "donut", f"SELECT coalesce(syslog_severity,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Severity", "label")], [("Events", "value")]),
        ("Top Processes", "bar", f"SELECT coalesce(syslog_program,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 20", [("Process", "label")], [("Events", "value")]),
        ("Events by Device", "bar", f"SELECT coalesce(device_name,host_name,source_ip,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 20", [("Device", "label")], [("Events", "value")]),
    ]
    interfaces = [
        ("Top Interfaces", "bar", f"SELECT juniper_interface AS label, count(*) AS value FROM {s} WHERE juniper_interface IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Interface", "label")], [("Events", "value")]),
        ("Interface Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE juniper_interface IS NOT NULL OR lower(message) LIKE '%interface%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Link Down / Failure", "metric", f"SELECT count(*) AS value FROM {s} WHERE (juniper_interface IS NOT NULL OR lower(message) LIKE '%interface%') AND (lower(message) LIKE '%down%' OR lower(message) LIKE '%fail%')", [], [("Events", "value")]),
        ("Link Up / Recovery", "metric", f"SELECT count(*) AS value FROM {s} WHERE (juniper_interface IS NOT NULL OR lower(message) LIKE '%interface%') AND (lower(message) LIKE '% up%' OR lower(message) LIKE '%recovered%')", [], [("Events", "value")]),
        ("Interface Event Detail", "table", f"SELECT _timestamp AS event_time, device_name, juniper_interface AS interface, juniper_event AS event, syslog_severity AS severity, syslog_program AS process, message, raw_message FROM {s} WHERE juniper_interface IS NOT NULL OR lower(message) LIKE '%interface%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Device", "device_name"), ("Interface", "interface"), ("Event", "event"), ("Severity", "severity"), ("Process", "process"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    routing = [
        ("Routing Protocol Events", "bar", f"SELECT CASE WHEN lower(message) LIKE '%bgp%' THEN 'BGP' WHEN lower(message) LIKE '%ospf%' THEN 'OSPF' WHEN lower(message) LIKE '%isis%' THEN 'IS-IS' WHEN lower(message) LIKE '%rsvp%' THEN 'RSVP' WHEN lower(message) LIKE '%ldp%' THEN 'LDP' ELSE 'Other' END AS label, count(*) AS value FROM {s} WHERE lower(message) LIKE '%bgp%' OR lower(message) LIKE '%ospf%' OR lower(message) LIKE '%isis%' OR lower(message) LIKE '%rsvp%' OR lower(message) LIKE '%ldp%' OR lower(message) LIKE '%route%' GROUP BY label ORDER BY value DESC", [("Protocol", "label")], [("Events", "value")]),
        ("Neighbor / Peer Events", "bar", f"SELECT juniper_peer_ip AS label, count(*) AS value FROM {s} WHERE juniper_peer_ip IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Peer", "label")], [("Events", "value")]),
        ("Routing Instances", "bar", f"SELECT juniper_routing_instance AS label, count(*) AS value FROM {s} WHERE juniper_routing_instance IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Instance / VRF", "label")], [("Events", "value")]),
        ("Adjacency Down / Routing Failures", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE (lower(message) LIKE '%bgp%' OR lower(message) LIKE '%ospf%' OR lower(message) LIKE '%neighbor%' OR lower(message) LIKE '%peer%') AND (lower(message) LIKE '%down%' OR lower(message) LIKE '%fail%' OR lower(message) LIKE '%closed%') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Failures", "value")]),
        ("Routing Event Detail", "table", f"SELECT _timestamp AS event_time, device_name, juniper_event AS event, juniper_peer_ip AS peer, juniper_routing_instance AS routing_instance, syslog_severity AS severity, syslog_program AS process, message, raw_message FROM {s} WHERE lower(message) LIKE '%bgp%' OR lower(message) LIKE '%ospf%' OR lower(message) LIKE '%isis%' OR lower(message) LIKE '%route%' OR juniper_peer_ip IS NOT NULL ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Device", "device_name"), ("Event", "event"), ("Peer", "peer"), ("Instance", "routing_instance"), ("Severity", "severity"), ("Process", "process"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    access = [
        ("Authentication Outcomes", "donut", f"SELECT CASE WHEN lower(message) LIKE '%accepted%' OR lower(message) LIKE '%success%' THEN 'success' WHEN lower(message) LIKE '%failed%' OR lower(message) LIKE '%failure%' OR lower(message) LIKE '%denied%' THEN 'failure' ELSE 'other' END AS label, count(*) AS value FROM {s} WHERE lower(syslog_program) IN ('sshd','login','mgd') OR lower(message) LIKE '%login%' OR lower(message) LIKE '%authentication%' GROUP BY label ORDER BY value DESC", [("Outcome", "label")], [("Events", "value")]),
        ("Top Administrative Users", "bar", f"SELECT juniper_username AS label, count(*) AS value FROM {s} WHERE juniper_username IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("User", "label")], [("Events", "value")]),
        ("Configuration / Commit Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '%commit%' OR lower(message) LIKE '%configuration%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Authentication & Change Detail", "table", f"SELECT _timestamp AS event_time, device_name, juniper_username AS user_name, juniper_event AS event, syslog_severity AS severity, syslog_program AS process, message, raw_message FROM {s} WHERE juniper_username IS NOT NULL OR lower(message) LIKE '%login%' OR lower(message) LIKE '%authentication%' OR lower(message) LIKE '%commit%' OR lower(message) LIKE '%configuration%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Device", "device_name"), ("User", "user_name"), ("Event", "event"), ("Severity", "severity"), ("Process", "process"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    security = [
        ("SRX Security Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE juniper_source_address IS NOT NULL OR juniper_destination_address IS NOT NULL OR lower(message) LIKE '%rt_flow%'", [], [("Events", "value")]),
        ("Top SRX Policies", "bar", f"SELECT juniper_policy AS label, count(*) AS value FROM {s} WHERE juniper_policy IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Policy", "label")], [("Events", "value")]),
        ("Source to Destination Zones", "bar", f"SELECT concat(coalesce(juniper_source_zone,'unknown'),' -> ',coalesce(juniper_destination_zone,'unknown')) AS label, count(*) AS value FROM {s} WHERE juniper_source_zone IS NOT NULL OR juniper_destination_zone IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Zone Flow", "label")], [("Events", "value")]),
        ("SRX Session Detail", "table", f"SELECT _timestamp AS event_time, device_name, juniper_event AS event, juniper_source_address AS source_ip, juniper_source_port AS source_port, juniper_destination_address AS destination_ip, juniper_destination_port AS destination_port, juniper_protocol AS protocol, juniper_policy AS policy, juniper_source_zone AS source_zone, juniper_destination_zone AS destination_zone, juniper_session_id AS session_id, message FROM {s} WHERE juniper_source_address IS NOT NULL OR juniper_destination_address IS NOT NULL OR lower(message) LIKE '%rt_flow%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Device", "device_name"), ("Event", "event"), ("Source", "source_ip"), ("Src Port", "source_port"), ("Destination", "destination_ip"), ("Dst Port", "destination_port"), ("Protocol", "protocol"), ("Policy", "policy"), ("Source Zone", "source_zone"), ("Destination Zone", "destination_zone"), ("Session", "session_id"), ("Message", "message")], []),
    ]
    raw = [
        ("Recent Juniper Events", "table", f"SELECT _timestamp AS event_time, received_at, device_name, host_name AS host, source_ip AS collector_source_ip, syslog_facility AS facility, syslog_severity AS severity, syslog_program AS process, juniper_event AS event, message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Received", "received_at"), ("Device", "device_name"), ("Host", "host"), ("Sender IP", "collector_source_ip"), ("Facility", "facility"), ("Severity", "severity"), ("Process", "process"), ("Event", "event"), ("Message", "message")], []),
        ("Raw Juniper Events", "table", f"SELECT _timestamp AS event_time, device_name, source_ip, syslog_program AS process, message, raw_message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Device", "device_name"), ("Sender IP", "source_ip"), ("Process", "process"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    return dashboard("Juniper Router Operations", "Separate Junos routing, interface, authentication, configuration and optional SRX security reporting.", [
        build_tab("juniper", "jnpr_overview", "Overview", overview),
        build_tab("juniper", "jnpr_trends", "Trends & Sources", trends),
        build_tab("juniper", "jnpr_interfaces", "Interfaces", interfaces),
        build_tab("juniper", "jnpr_routing", "Routing", routing),
        build_tab("juniper", "jnpr_access", "Access & Changes", access),
        build_tab("juniper", "jnpr_security", "SRX Security", security),
        build_tab("juniper", "jnpr_raw", "Raw Events", raw),
    ])


def proxmox_ve_dashboard() -> dict:
    """Operational reporting for Proxmox VE nodes, guests, tasks, and HA."""
    s = '"proxmox_ve"'
    overview = [
        ("Total Events", "metric", f"SELECT count(*) AS value FROM {s}", [], [("Events", "value")]),
        ("Errors / Critical", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') OR lower(message) LIKE '% error%' OR lower(message) LIKE '%failed%'", [], [("Events", "value")]),
        ("Authentication Failures", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(proxmox_authentication_result,'')) LIKE '%fail%' OR lower(message) LIKE '%authentication failure%' OR lower(message) LIKE '%failed password%'", [], [("Failures", "value")]),
        ("Guest Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE proxmox_vmid IS NOT NULL OR proxmox_ctid IS NOT NULL OR proxmox_resource IS NOT NULL", [], [("Events", "value")]),
        ("Task Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE proxmox_upid IS NOT NULL OR lower(message) LIKE '%starting task%' OR lower(message) LIKE '%end task%'", [], [("Events", "value")]),
        ("Active Nodes", "metric", f"SELECT count(DISTINCT device_name) AS value FROM {s} WHERE device_name IS NOT NULL", [], [("Nodes", "value")]),
    ]
    trends = [
        ("Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Severity Distribution", "donut", f"SELECT coalesce(syslog_severity,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Severity", "label")], [("Events", "value")]),
        ("Events by Node", "bar", f"SELECT coalesce(device_name,host_name,source_ip,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 20", [("Node", "label")], [("Events", "value")]),
        ("Top Services", "bar", f"SELECT coalesce(syslog_program,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 25", [("Service", "label")], [("Events", "value")]),
    ]
    authentication = [
        ("Authentication Outcomes", "donut", f"SELECT CASE WHEN lower(coalesce(proxmox_authentication_result,'')) IN ('accepted','successful','success') OR lower(message) LIKE '%accepted%' THEN 'success' WHEN lower(coalesce(proxmox_authentication_result,'')) LIKE '%fail%' OR lower(coalesce(proxmox_authentication_result,'')) = 'denied' OR lower(message) LIKE '%failed%' THEN 'failure' ELSE 'other' END AS label, count(*) AS value FROM {s} WHERE proxmox_authentication_result IS NOT NULL OR lower(syslog_program) IN ('sshd','pvedaemon','pveproxy','sudo') GROUP BY label ORDER BY value DESC", [("Outcome", "label")], [("Events", "value")]),
        ("Top Users", "bar", f"SELECT proxmox_user AS label, count(*) AS value FROM {s} WHERE proxmox_user IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("User", "label")], [("Events", "value")]),
        ("Top Login Source IPs", "bar", f"SELECT proxmox_source_ip AS label, count(*) AS value FROM {s} WHERE proxmox_source_ip IS NOT NULL AND lower(syslog_program) IN ('sshd','pvedaemon','pveproxy') GROUP BY label ORDER BY value DESC LIMIT 25", [("Source IP", "label")], [("Events", "value")]),
        ("Authentication / Sudo Detail", "table", f"SELECT _timestamp AS event_time, device_name AS node, proxmox_user AS user_name, proxmox_source_ip AS source_ip, proxmox_authentication_result AS outcome, syslog_program AS service, syslog_severity AS severity, message, raw_message FROM {s} WHERE proxmox_authentication_result IS NOT NULL OR proxmox_user IS NOT NULL OR lower(syslog_program) IN ('sshd','sudo','su') ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Node", "node"), ("User", "user_name"), ("Source IP", "source_ip"), ("Outcome", "outcome"), ("Service", "service"), ("Severity", "severity"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    guests = [
        ("Top VM IDs", "bar", f"SELECT proxmox_vmid AS label, count(*) AS value FROM {s} WHERE proxmox_vmid IS NOT NULL AND proxmox_vmid <> '' GROUP BY label ORDER BY value DESC LIMIT 25", [("VM ID", "label")], [("Events", "value")]),
        ("Top Container IDs", "bar", f"SELECT proxmox_ctid AS label, count(*) AS value FROM {s} WHERE proxmox_ctid IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("CT ID", "label")], [("Events", "value")]),
        ("Guest Start / Stop", "donut", f"SELECT CASE WHEN lower(message) LIKE '%start%' THEN 'start' WHEN lower(message) LIKE '%stop%' OR lower(message) LIKE '%shutdown%' THEN 'stop/shutdown' WHEN lower(message) LIKE '%migrat%' THEN 'migration' ELSE 'other' END AS label, count(*) AS value FROM {s} WHERE proxmox_vmid IS NOT NULL OR proxmox_ctid IS NOT NULL OR proxmox_resource IS NOT NULL GROUP BY label ORDER BY value DESC", [("Action", "label")], [("Events", "value")]),
        ("Guest Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE proxmox_vmid IS NOT NULL OR proxmox_ctid IS NOT NULL OR proxmox_resource IS NOT NULL GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Migration / Replication Events", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '%migrat%' OR lower(message) LIKE '%replicat%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Guest Activity Detail", "table", f"SELECT _timestamp AS event_time, device_name AS node, proxmox_vmid AS vmid, proxmox_ctid AS ctid, proxmox_resource AS resource, proxmox_task AS task, proxmox_user AS user_name, syslog_program AS service, message, raw_message FROM {s} WHERE proxmox_vmid IS NOT NULL OR proxmox_ctid IS NOT NULL OR proxmox_resource IS NOT NULL ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Node", "node"), ("VM ID", "vmid"), ("CT ID", "ctid"), ("Resource", "resource"), ("Task", "task"), ("User", "user_name"), ("Service", "service"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    tasks = [
        ("Task Types", "bar", f"SELECT coalesce(proxmox_task,'unknown') AS label, count(*) AS value FROM {s} WHERE proxmox_upid IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("Task", "label")], [("Events", "value")]),
        ("Tasks by User", "bar", f"SELECT coalesce(proxmox_user,'unknown') AS label, count(*) AS value FROM {s} WHERE proxmox_upid IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 25", [("User", "label")], [("Tasks", "value")]),
        ("Task Failures", "metric", f"SELECT count(*) AS value FROM {s} WHERE (proxmox_upid IS NOT NULL OR lower(message) LIKE '%task%') AND (lower(message) LIKE '%error%' OR lower(message) LIKE '%fail%')", [], [("Failures", "value")]),
        ("Task Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE proxmox_upid IS NOT NULL OR lower(message) LIKE '%starting task%' OR lower(message) LIKE '%end task%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Task / UPID Detail", "table", f"SELECT _timestamp AS event_time, coalesce(proxmox_node,device_name) AS node, proxmox_task AS task, proxmox_vmid AS vmid, proxmox_user AS user_name, proxmox_upid AS upid, syslog_program AS service, message, raw_message FROM {s} WHERE proxmox_upid IS NOT NULL OR lower(message) LIKE '%task%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Node", "node"), ("Task", "task"), ("VM ID", "vmid"), ("User", "user_name"), ("UPID", "upid"), ("Service", "service"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    cluster = [
        ("HA / Cluster Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(syslog_program) IN ('pve-ha-lrm','pve-ha-crm','corosync','pmxcfs') OR lower(message) LIKE '%quorum%' OR lower(message) LIKE '%cluster%'", [], [("Events", "value")]),
        ("Quorum / Membership Changes", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(message) LIKE '%quorum%' OR lower(message) LIKE '%membership%' OR lower(message) LIKE '%member%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("HA Service Distribution", "bar", f"SELECT coalesce(syslog_program,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(syslog_program) IN ('pve-ha-lrm','pve-ha-crm','corosync','pmxcfs') GROUP BY label ORDER BY value DESC", [("Service", "label")], [("Events", "value")]),
        ("HA Errors / Fencing", "metric", f"SELECT count(*) AS value FROM {s} WHERE (lower(message) LIKE '% ha %' OR lower(syslog_program) LIKE 'pve-ha-%' OR lower(message) LIKE '%fenc%') AND (lower(message) LIKE '%error%' OR lower(message) LIKE '%fail%' OR lower(message) LIKE '%fenc%')", [], [("Events", "value")]),
        ("HA / Cluster Detail", "table", f"SELECT _timestamp AS event_time, device_name AS node, syslog_program AS service, syslog_severity AS severity, proxmox_resource AS resource, message, raw_message FROM {s} WHERE lower(syslog_program) IN ('pve-ha-lrm','pve-ha-crm','corosync','pmxcfs') OR lower(message) LIKE '%quorum%' OR lower(message) LIKE '%cluster%' OR lower(message) LIKE '%fenc%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Node", "node"), ("Service", "service"), ("Severity", "severity"), ("Resource", "resource"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    raw = [
        ("Service Failures & System Errors", "table", f"SELECT _timestamp AS event_time, device_name AS node, syslog_severity AS severity, syslog_program AS service, message, raw_message FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') OR lower(message) LIKE '%failed%' OR lower(message) LIKE '% error%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Node", "node"), ("Severity", "severity"), ("Service", "service"), ("Message", "message"), ("Raw", "raw_message")], []),
        ("Raw Proxmox VE Events", "table", f"SELECT _timestamp AS event_time, received_at, device_name AS node, host_name AS host, source_ip AS collector_source_ip, syslog_facility AS facility, syslog_severity AS severity, syslog_program AS service, message, raw_message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Received", "received_at"), ("Node", "node"), ("Host", "host"), ("Sender IP", "collector_source_ip"), ("Facility", "facility"), ("Severity", "severity"), ("Service", "service"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    return dashboard("Proxmox VE Operations", "Separate Proxmox VE node, authentication, guest, task, HA, cluster and service reporting.", [
        build_tab("proxmox_ve", "pve_overview", "Overview", overview),
        build_tab("proxmox_ve", "pve_trends", "Trends & Services", trends),
        build_tab("proxmox_ve", "pve_auth", "Authentication", authentication),
        build_tab("proxmox_ve", "pve_guests", "VMs & Containers", guests),
        build_tab("proxmox_ve", "pve_tasks", "Tasks", tasks),
        build_tab("proxmox_ve", "pve_cluster", "HA & Cluster", cluster),
        build_tab("proxmox_ve", "pve_raw", "System & Raw", raw),
    ])


def ubersmith_dashboard() -> dict:
    """Operational reporting for Ubersmith application and supporting services."""
    s = '"ubersmith"'
    overview = [
        ("Total Events", "metric", f"SELECT count(*) AS value FROM {s}", [], [("Events", "value")]),
        ("Errors / Critical", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error')", [], [("Events", "value")]),
        ("Mail Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%mail%'", [], [("Events", "value")]),
        ("Web / PHP Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%web%' OR lower(coalesce(syslog_program,'')) LIKE '%php%'", [], [("Events", "value")]),
        ("Background Service Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%cron%' OR lower(coalesce(syslog_program,'')) LIKE '%solr%' OR lower(coalesce(syslog_program,'')) LIKE '%redis%'", [], [("Events", "value")]),
        ("Active Hosts", "metric", f"SELECT count(DISTINCT source_ip) AS value FROM {s} WHERE source_ip IS NOT NULL", [], [("Hosts", "value")]),
    ]
    trends = [
        ("Events Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Severity Distribution", "donut", f"SELECT coalesce(syslog_severity,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Severity", "label")], [("Events", "value")]),
        ("Events by Program", "bar", f"SELECT coalesce(syslog_program,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 25", [("Program", "label")], [("Events", "value")]),
        ("Events by Host", "bar", f"SELECT coalesce(device_name,host_name,source_ip,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 20", [("Host", "label")], [("Events", "value")]),
    ]
    errors = [
        ("Errors Over Time", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Errors", "value")]),
        ("Errors by Program", "bar", f"SELECT coalesce(syslog_program,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') GROUP BY label ORDER BY value DESC LIMIT 25", [("Program", "label")], [("Errors", "value")]),
        ("PHP Error Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%php%' AND lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error')", [], [("Errors", "value")]),
        ("Recent Application Errors", "table", f"SELECT _timestamp AS event_time, device_name, host_name AS host, source_ip, syslog_program AS program, syslog_severity AS severity, message, raw_message FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Mapped Device", "device_name"), ("Host", "host"), ("Source IP", "source_ip"), ("Program", "program"), ("Severity", "severity"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    applications = [
        ("Mail Activity", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%mail%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Web / PHP Activity", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%web%' OR lower(coalesce(syslog_program,'')) LIKE '%php%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Solr Activity", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%solr%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Cron Activity", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%cron%' GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
    ]
    services = [
        ("Redis Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%redis%'", [], [("Events", "value")]),
        ("ClamAV Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%clamav%'", [], [("Events", "value")]),
        ("Events by Source IP", "bar", f"SELECT source_ip AS label, count(*) AS value FROM {s} WHERE source_ip IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 20", [("Source IP", "label")], [("Events", "value")]),
        ("Service Event Detail", "table", f"SELECT _timestamp AS event_time, device_name, host_name AS host, source_ip, syslog_program AS program, syslog_severity AS severity, message FROM {s} WHERE lower(coalesce(syslog_program,'')) LIKE '%solr%' OR lower(coalesce(syslog_program,'')) LIKE '%redis%' OR lower(coalesce(syslog_program,'')) LIKE '%cron%' OR lower(coalesce(syslog_program,'')) LIKE '%clamav%' ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Mapped Device", "device_name"), ("Host", "host"), ("Source IP", "source_ip"), ("Program", "program"), ("Severity", "severity"), ("Message", "message")], []),
    ]
    raw = [
        ("Recent Ubersmith Events", "table", f"SELECT _timestamp AS event_time, received_at, device_name, host_name AS host, source_ip, syslog_facility AS facility, syslog_severity AS severity, syslog_program AS program, message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Received", "received_at"), ("Mapped Device", "device_name"), ("Host", "host"), ("Source IP", "source_ip"), ("Facility", "facility"), ("Severity", "severity"), ("Program", "program"), ("Message", "message")], []),
        ("Raw Ubersmith Events", "table", f"SELECT _timestamp AS event_time, device_name, source_ip, syslog_program AS program, message, raw_message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Mapped Device", "device_name"), ("Source IP", "source_ip"), ("Program", "program"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    return dashboard("Ubersmith Billing Operations", "Separate Ubersmith application, mail, PHP/web, Solr, cron, Redis and ClamAV operational reporting.", [
        build_tab("ubersmith", "uber_overview", "Overview", overview),
        build_tab("ubersmith", "uber_trends", "Trends & Sources", trends),
        build_tab("ubersmith", "uber_errors", "Errors", errors),
        build_tab("ubersmith", "uber_apps", "Application Activity", applications),
        build_tab("ubersmith", "uber_services", "Supporting Services", services),
        build_tab("ubersmith", "uber_raw", "Raw Events", raw),
    ])


def unclassified_dashboard() -> dict:
    """Discovery reporting for every source not yet mapped by an administrator."""
    s = '"unclassified"'
    overview = [
        ("Unclassified Events", "metric", f"SELECT count(*) AS value FROM {s}", [], [("Events", "value")]),
        ("Unknown Source IPs", "metric", f"SELECT count(DISTINCT source_ip) AS value FROM {s} WHERE source_ip IS NOT NULL", [], [("Sources", "value")]),
        ("Reported Hostnames", "metric", f"SELECT count(DISTINCT host_name) AS value FROM {s} WHERE host_name IS NOT NULL", [], [("Hostnames", "value")]),
        ("Programs Detected", "metric", f"SELECT count(DISTINCT syslog_program) AS value FROM {s} WHERE syslog_program IS NOT NULL", [], [("Programs", "value")]),
        ("Critical / Error Events", "metric", f"SELECT count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error')", [], [("Events", "value")]),
        ("Events Without Raw Message", "metric", f"SELECT count(*) AS value FROM {s} WHERE raw_message IS NULL OR raw_message = ''", [], [("Events", "value")]),
    ]
    trends = [
        ("Unclassified Volume", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Severity Distribution", "donut", f"SELECT coalesce(syslog_severity,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Severity", "label")], [("Events", "value")]),
        ("Transport Distribution", "donut", f"SELECT coalesce(transport,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Transport", "label")], [("Events", "value")]),
        ("Facility Distribution", "bar", f"SELECT coalesce(syslog_facility,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 25", [("Facility", "label")], [("Events", "value")]),
    ]
    sources = [
        ("Top Unknown Source IPs", "bar", f"SELECT source_ip AS label, count(*) AS value FROM {s} WHERE source_ip IS NOT NULL GROUP BY label ORDER BY value DESC LIMIT 30", [("Source IP", "label")], [("Events", "value")]),
        ("Top Reported Hostnames", "bar", f"SELECT coalesce(host_name,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 30", [("Hostname", "label")], [("Events", "value")]),
        ("Source First / Last Seen", "table", f"SELECT source_ip, min(_timestamp) AS first_seen, max(_timestamp) AS last_seen, count(*) AS events FROM {s} WHERE source_ip IS NOT NULL GROUP BY source_ip ORDER BY last_seen DESC LIMIT 100", [("Source IP", "source_ip"), ("First Seen", "first_seen"), ("Last Seen", "last_seen"), ("Events", "events")], []),
        ("Source and Program Inventory", "table", f"SELECT source_ip, coalesce(host_name,'unknown') AS host, coalesce(syslog_program,'unknown') AS program, coalesce(syslog_severity,'unknown') AS severity, count(*) AS events FROM {s} GROUP BY (source_ip), coalesce(host_name,'unknown'), coalesce(syslog_program,'unknown'), coalesce(syslog_severity,'unknown') ORDER BY events DESC LIMIT 200", [("Source IP", "source_ip"), ("Hostname", "host"), ("Program", "program"), ("Severity", "severity"), ("Events", "events")], []),
        ("Newest Sources in Selected Range", "table", f"SELECT source_ip, coalesce(host_name,'unknown') AS host, min(_timestamp) AS first_seen, max(_timestamp) AS last_seen, count(*) AS events FROM {s} WHERE source_ip IS NOT NULL GROUP BY source_ip, coalesce(host_name,'unknown') ORDER BY first_seen DESC LIMIT 100", [("Source IP", "source_ip"), ("Hostname", "host"), ("First Seen", "first_seen"), ("Last Seen", "last_seen"), ("Events", "events")], []),
    ]
    programs = [
        ("Top Programs", "bar", f"SELECT coalesce(syslog_program,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC LIMIT 30", [("Program", "label")], [("Events", "value")]),
        ("Message Formats", "donut", f"SELECT coalesce(msgformat,'unknown') AS label, count(*) AS value FROM {s} GROUP BY label ORDER BY value DESC", [("Format", "label")], [("Events", "value")]),
        ("Programs by Source", "table", f"SELECT source_ip, coalesce(host_name,'unknown') AS host, coalesce(syslog_program,'unknown') AS program, count(*) AS events FROM {s} GROUP BY source_ip, coalesce(host_name,'unknown'), coalesce(syslog_program,'unknown') ORDER BY events DESC LIMIT 200", [("Source IP", "source_ip"), ("Hostname", "host"), ("Program", "program"), ("Events", "events")], []),
        ("PID / Program Detail", "table", f"SELECT _timestamp AS event_time, source_ip, host_name AS host, syslog_program AS program, pid, syslog_severity AS severity, message FROM {s} WHERE pid IS NOT NULL ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Source IP", "source_ip"), ("Hostname", "host"), ("Program", "program"), ("PID", "pid"), ("Severity", "severity"), ("Message", "message")], []),
    ]
    errors = [
        ("Critical / Error Trend", "line", f"SELECT histogram(_timestamp, '1 hour') AS ts, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') GROUP BY ts ORDER BY ts", [("Time", "ts")], [("Events", "value")]),
        ("Errors by Unknown Source", "bar", f"SELECT coalesce(source_ip,'unknown') AS label, count(*) AS value FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') GROUP BY label ORDER BY value DESC LIMIT 30", [("Source IP", "label")], [("Events", "value")]),
        ("Critical / Error Detail", "table", f"SELECT _timestamp AS event_time, source_ip, host_name AS host, syslog_program AS program, syslog_facility AS facility, syslog_severity AS severity, message, raw_message FROM {s} WHERE lower(coalesce(syslog_severity,'')) IN ('emerg','alert','crit','err','emergency','critical','error') ORDER BY _timestamp DESC LIMIT 300", [("Time", "event_time"), ("Source IP", "source_ip"), ("Hostname", "host"), ("Program", "program"), ("Facility", "facility"), ("Severity", "severity"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    raw = [
        ("Recent Unclassified Events", "table", f"SELECT _timestamp AS event_time, received_at, source_ip, host_name AS host, syslog_facility AS facility, syslog_severity AS severity, syslog_program AS program, message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Received", "received_at"), ("Source IP", "source_ip"), ("Hostname", "host"), ("Facility", "facility"), ("Severity", "severity"), ("Program", "program"), ("Message", "message")], []),
        ("Raw Unclassified Events", "table", f"SELECT _timestamp AS event_time, source_ip, host_name AS host, syslog_program AS program, message, raw_message FROM {s} ORDER BY _timestamp DESC LIMIT 500", [("Time", "event_time"), ("Source IP", "source_ip"), ("Hostname", "host"), ("Program", "program"), ("Message", "message"), ("Raw", "raw_message")], []),
    ]
    return dashboard("Unclassified Source Discovery", "Unknown-source inventory, first/last seen, volume, programs, severity, transport, errors and raw-event triage.", [
        build_tab("unclassified", "unknown_overview", "Overview", overview),
        build_tab("unclassified", "unknown_trends", "Trends & Protocol", trends),
        build_tab("unclassified", "unknown_sources", "Source Discovery", sources),
        build_tab("unclassified", "unknown_programs", "Programs & Processes", programs),
        build_tab("unclassified", "unknown_errors", "Errors", errors),
        build_tab("unclassified", "unknown_raw", "Raw Events", raw),
    ])


def dashboard(title: str, description: str, tabs: list[dict]) -> dict:
    return {
        "version": 5,
        "dashboardId": "",
        "title": title,
        "description": description,
        "role": "",
        "owner": "",
        "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tabs": tabs,
        "variables": {"list": [], "showDynamicFilters": True},
        "defaultDatetimeDuration": {"type": "relative", "relativeTimePeriod": "30d"},
    }


def api_request(base: str, path: str, user: str, password: str, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(f"{base.rstrip('/')}{path}", data=data, method=method,
                                 headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"OpenObserve API {method} {path} failed ({exc.code}): {detail}") from exc


def bootstrap_schema(base: str, org: str, user: str, password: str, name: str) -> None:
    """Create a future source's stream/schema with one excluded marker."""
    common = {
        "_timestamp": int(datetime.now(timezone.utc).timestamp() * 1_000_000),
        "schema_bootstrap": "true",
        "received_at": "2026-01-01T00:00:00Z",
        "device_name": "schema-bootstrap",
        "host_name": "schema-bootstrap",
        "source_ip": "192.0.2.254",
        "syslog_facility": "local7",
        "syslog_severity": "notice",
        "syslog_program": "schema-bootstrap",
        "message": "schema-bootstrap: not an operational event",
        "raw_message": "schema-bootstrap: not an operational event",
    }
    records = {
        "juniper": {
            **common,
            "juniper_event": "SCHEMA_BOOTSTRAP",
            "juniper_interface": "xe-0/0/0.0",
            "juniper_routing_instance": "bootstrap-vrf",
            "juniper_peer_ip": "192.0.2.1",
            "juniper_username": "bootstrap-user",
            "juniper_source_address": "192.0.2.10",
            "juniper_source_port": "12345",
            "juniper_destination_address": "198.51.100.10",
            "juniper_destination_port": "443",
            "juniper_policy": "bootstrap-policy",
            "juniper_source_zone": "bootstrap-source",
            "juniper_destination_zone": "bootstrap-destination",
            "juniper_protocol": "tcp",
            "juniper_session_id": "1",
        },
        "proxmox-ve": {
            **common,
            "proxmox_authentication_result": "success",
            "proxmox_user": "bootstrap@pam",
            "proxmox_source_ip": "192.0.2.10",
            "proxmox_upid": "UPID:schema-bootstrap:0:0:0:qmstart:100:bootstrap@pam:",
            "proxmox_node": "schema-bootstrap",
            "proxmox_task": "qmstart",
            "proxmox_vmid": "100",
            "proxmox_ctid": "101",
            "proxmox_resource": "vm:100",
        },
    }
    if name not in records:
        raise RuntimeError("schema bootstrap is only supported for juniper and proxmox-ve")
    stream = "proxmox_ve" if name == "proxmox-ve" else name
    encoded_org = urllib.parse.quote(org, safe="")
    encoded_stream = urllib.parse.quote(stream, safe="")
    api_request(base, f"/api/{encoded_org}/{encoded_stream}/_json", user, password,
                "POST", [records[name]])
    print(f"bootstrapped stream schema: {stream} (marker is excluded from bundled dashboards)")


def upsert(base: str, org: str, user: str, password: str, body: dict) -> None:
    encoded_org = urllib.parse.quote(org, safe="")
    listing = api_request(base, f"/api/{encoded_org}/dashboards?folder=default", user, password)
    existing = next((item for item in listing.get("dashboards", []) if item.get("title") == body["title"]), None)
    if existing:
        dash_id = urllib.parse.quote(existing["dashboard_id"], safe="")
        query = urllib.parse.urlencode({"folder": "default", "hash": existing.get("hash", "")})
        api_request(base, f"/api/{encoded_org}/dashboards/{dash_id}?{query}", user, password, "PUT", body)
        print(f"updated dashboard: {body['title']}")
    else:
        api_request(base, f"/api/{encoded_org}/dashboards?folder=default", user, password, "POST", body)
        print(f"created dashboard: {body['title']}")


def validate_queries(base: str, org: str, user: str, password: str,
                     dashboards: dict[str, dict]) -> None:
    """Execute every panel query over the dashboard's default 30-day range."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    path = f"/api/{urllib.parse.quote(org, safe='')}/_search?type=logs"
    failures: list[str] = []
    checked = 0
    for body in dashboards.values():
        for tab in body["tabs"]:
            for panel in tab["panels"]:
                checked += 1
                request = {
                    "query": {
                        "sql": panel["queries"][0]["query"],
                        "start_time": int(start.timestamp() * 1_000_000),
                        "end_time": int(end.timestamp() * 1_000_000),
                        "from": 0,
                        "size": 100,
                    },
                    "search_type": "dashboards",
                }
                try:
                    api_request(base, path, user, password, "POST", request)
                except RuntimeError as exc:
                    failures.append(f"{body['title']} / {tab['name']} / {panel['title']}: {exc}")
    if failures:
        raise RuntimeError("dashboard query validation failed:\n" + "\n".join(failures))
    print(f"dashboard queries passed: {checked}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--only", choices=("pmg", "fortigate", "juniper", "proxmox-ve", "ubersmith", "unclassified"))
    parser.add_argument("--bootstrap-schema", action="store_true",
                        help="create the selected future stream schema with one excluded marker")
    parser.add_argument("--validate-queries", action="store_true",
                        help="execute every panel SQL query over the past 30 days")
    args = parser.parse_args()
    dashboards = {
        "pmg": pmg_dashboard(),
        "fortigate": fortigate_dashboard(),
        "juniper": juniper_dashboard(),
        "proxmox-ve": proxmox_ve_dashboard(),
        "ubersmith": ubersmith_dashboard(),
        "unclassified": unclassified_dashboard(),
    }
    if args.only:
        dashboards = {args.only: dashboards[args.only]}
    if args.export_dir:
        args.export_dir.mkdir(parents=True, exist_ok=True)
        for name, body in dashboards.items():
            target = args.export_dir / f"{name}.json"
            target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
            print(f"exported: {target}")
        return 0
    load_env(args.env_file)
    required = ("ZO_ROOT_USER_EMAIL", "ZO_ROOT_USER_PASSWORD", "ZO_ORG")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError("missing required environment settings: " + ", ".join(missing))
    base = os.environ.get("OPENOBSERVE_INTERNAL_URL", "http://127.0.0.1:5080")
    if args.bootstrap_schema:
        if args.only not in {"juniper", "proxmox-ve"}:
            raise RuntimeError("--bootstrap-schema requires --only juniper or --only proxmox-ve")
        bootstrap_schema(base, os.environ["ZO_ORG"], os.environ["ZO_ROOT_USER_EMAIL"],
                         os.environ["ZO_ROOT_USER_PASSWORD"], args.only)
        return 0
    if args.validate_queries:
        validate_queries(base, os.environ["ZO_ORG"], os.environ["ZO_ROOT_USER_EMAIL"],
                         os.environ["ZO_ROOT_USER_PASSWORD"], dashboards)
        return 0
    for body in dashboards.values():
        upsert(base, os.environ["ZO_ORG"], os.environ["ZO_ROOT_USER_EMAIL"], os.environ["ZO_ROOT_USER_PASSWORD"], body)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print(f"dashboard provisioning failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
