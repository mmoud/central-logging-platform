#!/usr/bin/env python3
"""Validate sources.yml and generate safe, explicit syslog-ng routing paths."""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

VALID_STREAMS = {
    "fortigate", "cisco", "juniper", "proxmox_ve", "proxmox_mail_gateway",
    "linux", "ubersmith", "unclassified"
}
IDENT = re.compile(r"^[a-zA-Z0-9_.-]{1,63}$")


def q(value: str) -> str:
    """syslog-ng quoted string; source values are otherwise strictly validated."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def load_devices(path: Path) -> list[dict[str, str]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    devices = document.get("devices")
    if not isinstance(devices, list):
        raise ValueError("sources.yml must contain a devices: list")
    seen_ips: set[str] = set()
    result = []
    for index, item in enumerate(devices, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"devices[{index}] must be a mapping")
        missing = {"name", "ip", "vendor", "product", "stream"} - set(item)
        if missing:
            raise ValueError(f"devices[{index}] missing: {', '.join(sorted(missing))}")
        values = {key: str(item[key]).strip() for key in ("name", "ip", "vendor", "product", "stream")}
        try:
            address = ipaddress.ip_address(values["ip"])
        except ValueError as exc:
            raise ValueError(f"devices[{index}].ip is not an IP address: {values['ip']}") from exc
        values["ip"] = str(address)
        if values["ip"] in seen_ips:
            raise ValueError(f"duplicate source IP: {values['ip']}")
        seen_ips.add(values["ip"])
        if values["stream"] not in VALID_STREAMS - {"unclassified"}:
            raise ValueError(f"devices[{index}].stream must be one of: {', '.join(sorted(VALID_STREAMS - {'unclassified'}))}")
        for key in ("name", "vendor", "product"):
            if not IDENT.fullmatch(values[key]):
                raise ValueError(f"devices[{index}].{key} contains unsupported characters")
        result.append(values)
    return result


def source_conf(enable_tls: bool) -> str:
    entries = [
        'source s_network {',
        '    syslog(ip("0.0.0.0") port(514) transport("udp") flags(store-raw-message));',
        '    syslog(ip("0.0.0.0") port(514) transport("tcp") max-connections(200) flags(store-raw-message));',
    ]
    if enable_tls:
        entries.extend([
            '    syslog(ip("0.0.0.0") port(6514) transport("tls") max-connections(200) flags(store-raw-message)',
            '        tls(key-file("/etc/syslog-ng/tls/server.key") cert-file("/etc/syslog-ng/tls/server.crt") ca-dir("/etc/syslog-ng/tls/ca") peer-verify(optional-untrusted)));',
        ])
    entries += ['};', '']
    return "\n".join(entries)


def route_conf(devices: list[dict[str, str]]) -> str:
    lines = ["# Generated; do not edit.  Update config/sources.yml, then run validate.sh."]
    filters = []
    for n, dev in enumerate(devices, start=1):
        ident = f"device_{n}"
        filters.append(f"f_{ident}")
        parsers = ""
        if dev["stream"] == "fortigate":
            parsers = "parser(p_fortigate_payload); parser(p_fortigate_kv); rewrite(r_fortigate_normalized); "
        elif dev["stream"] == "juniper":
            parsers = (
                "parser(p_juniper_event); parser(p_juniper_interface); "
                "parser(p_juniper_routing_instance); parser(p_juniper_peer); "
                "parser(p_juniper_user); parser(p_juniper_source_address); "
                "parser(p_juniper_destination_address); parser(p_juniper_source_port); "
                "parser(p_juniper_destination_port); parser(p_juniper_policy); "
                "parser(p_juniper_source_zone); parser(p_juniper_destination_zone); "
                "parser(p_juniper_protocol); parser(p_juniper_session); "
                "rewrite(r_juniper_normalized); "
            )
        elif dev["stream"] == "proxmox_ve":
            parsers = (
                "parser(p_proxmox_auth); parser(p_proxmox_user); "
                "parser(p_proxmox_source); parser(p_proxmox_upid); "
                "parser(p_proxmox_upid_fields); parser(p_proxmox_guest); "
                "rewrite(r_proxmox_normalized); "
            )
        elif dev["stream"] == "proxmox_mail_gateway":
            parsers = (
                "parser(p_mail_filter_id); parser(p_mail_queue_id); parser(p_mail_headers); "
                "parser(p_mail_header_sender); parser(p_mail_header_sender_domain); "
                "parser(p_mail_envelope_sender_domain); "
                "parser(p_mail_header_recipient_domain); parser(p_mail_envelope_recipient_domain); "
                "parser(p_mail_pmg_message); parser(p_mail_pmg_rule); "
                "parser(p_mail_pmg_processing_time); "
                "parser(p_mail_sender); parser(p_mail_recipient); "
                "parser(p_mail_sender_domain); parser(p_mail_recipient_domain); "
                "parser(p_mail_delivery); parser(p_mail_status); parser(p_mail_dsn); "
                "parser(p_mail_delay); parser(p_mail_delays); parser(p_mail_size); "
                "parser(p_mail_message_id); parser(p_mail_client); parser(p_mail_relay_ip); "
                "parser(p_mail_spam); parser(p_mail_auth_summary); "
                "parser(p_mail_spf_result); parser(p_mail_dkim_result); "
                "parser(p_mail_dmarc_result); parser(p_mail_arc_result); parser(p_mail_tls); "
            )
        lines += [
            f'filter f_{ident} {{ {"netmask6" if ":" in dev["ip"] else "netmask"}("{dev["ip"]}/{128 if ":" in dev["ip"] else 32}"); }};',
            f'rewrite r_{ident} {{ set("{q(dev["name"])}" value("device_name")); set("{q(dev["vendor"])}" value("observer.vendor")); set("{q(dev["product"])}" value("observer.product")); set("{q(dev["stream"])}" value("stream")); }};',
            f'log {{ source(s_network); filter(f_{ident}); rewrite(r_common); rewrite(r_{ident}); {parsers}' + f'destination(d_{dev["stream"]}); flags(flow-control); }};',
        ]
    if filters:
        lines.append("filter f_unclassified { " + " and ".join(f"not filter({f})" for f in filters) + "; };")
    else:
        lines.append("filter f_unclassified { filter(f_all); };")
    lines += [
        'rewrite r_unclassified { set("unclassified" value("stream")); set("unknown" value("observer.vendor")); set("unknown" value("observer.product")); };',
        'log { source(s_network); filter(f_unclassified); rewrite(r_common); rewrite(r_unclassified); destination(d_unclassified); flags(flow-control); };',
        '',
    ]
    return "\n".join(lines)


def runtime_conf() -> str:
    defaults = {
        "BATCH_LINES": "200", "BATCH_BYTES": "1048576", "BATCH_TIMEOUT_MS": "1000",
        "BUFFER_BYTES": "1073741824",
    }
    environment_names = {
        "BATCH_LINES": "SYSLOG_BATCH_LINES", "BATCH_BYTES": "SYSLOG_BATCH_BYTES",
        "BATCH_TIMEOUT_MS": "SYSLOG_BATCH_TIMEOUT_MS", "BUFFER_BYTES": "SYSLOG_DISK_BUFFER_BYTES_PER_STREAM",
    }
    values = {}
    for key, default in defaults.items():
        value = os.environ.get(environment_names[key], default)
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError(f"{environment_names[key]} must be a positive integer")
        values[key] = value
    user = os.environ.get("ZO_ROOT_USER_EMAIL", "")
    password = os.environ.get("ZO_ROOT_USER_PASSWORD", "")
    if not user or not password:
        raise ValueError("ZO_ROOT_USER_EMAIL and ZO_ROOT_USER_PASSWORD must be set to render syslog-ng credentials")
    source_time_zone = os.environ.get("PLATFORM_TIMEZONE", "America/Toronto")
    try:
        ZoneInfo(source_time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"PLATFORM_TIMEZONE is not a valid IANA timezone: {source_time_zone}") from exc
    safe_user = q(user)
    safe_password = q(password)
    safe_time_zone = q(source_time_zone)
    lines = ["# Generated runtime settings. Contains credentials; mode 0640 and Git ignored."]
    lines.extend(f"@define {key} {value}" for key, value in values.items())
    lines.extend([
        f'@define SOURCE_TIME_ZONE "{safe_time_zone}"',
        f'@define ZO_HTTP_USER "{safe_user}"',
        f'@define ZO_HTTP_PASSWORD "{safe_password}"',
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=Path("config/sources.yml"))
    parser.add_argument("--conf-dir", type=Path, default=Path("syslog-ng/conf.d"))
    parser.add_argument("--enable-tls", default=os.environ.get("ENABLE_SYSLOG_TLS", "false"))
    args = parser.parse_args()
    try:
        devices = load_devices(args.sources)
        enable_tls = args.enable_tls.lower() == "true"
        if enable_tls:
            required = [Path("syslog-ng/tls/server.key"), Path("syslog-ng/tls/server.crt"), Path("syslog-ng/tls/ca")]
            missing = [str(p) for p in required if not p.exists()]
            if missing:
                raise ValueError("TLS enabled but required TLS assets are missing: " + ", ".join(missing))
        args.conf_dir.mkdir(parents=True, exist_ok=True)
        (args.conf_dir / "10-sources.conf").write_text(source_conf(enable_tls), encoding="utf-8")
        (args.conf_dir / "25-runtime.conf").write_text(runtime_conf(), encoding="utf-8")
        (args.conf_dir / "45-device-routes.conf").write_text(route_conf(devices), encoding="utf-8")
    except ValueError as exc:
        print(f"sources validation failed: {exc}", file=sys.stderr)
        return 2
    print(f"rendered {len(devices)} mapped device route(s); TLS {'enabled' if enable_tls else 'disabled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
