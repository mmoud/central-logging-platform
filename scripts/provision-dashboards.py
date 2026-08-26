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
        build_tab("fortigate", "fg_raw", "Raw Events", raw),
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
    parser.add_argument("--only", choices=("pmg", "fortigate"))
    parser.add_argument("--validate-queries", action="store_true",
                        help="execute every panel SQL query over the past 30 days")
    args = parser.parse_args()
    dashboards = {"pmg": pmg_dashboard(), "fortigate": fortigate_dashboard()}
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
