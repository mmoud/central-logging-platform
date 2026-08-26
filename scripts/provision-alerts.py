#!/usr/bin/env python3
"""Idempotently provision OpenObserve alerts for unclassified log discovery."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
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


def definitions(destination: str | None = None) -> list[dict]:
    """Return conservative alerts; no destination means safely disabled."""
    enabled = bool(destination)
    destinations = [destination] if destination else []
    rules = [
        (
            "unclassified_any_activity",
            "Any event arrived from a source that is not yet mapped.",
            'SELECT count(*) AS event_count FROM "unclassified"',
            1,
            60,
            3,
        ),
        (
            "unclassified_high_volume",
            "An unmapped source sent at least 1,000 events in five minutes.",
            'SELECT count(*) AS event_count FROM "unclassified"',
            1000,
            30,
            2,
        ),
        (
            "unclassified_critical_or_error",
            "An unmapped source emitted a critical or error-severity event.",
            (
                'SELECT count(*) AS event_count FROM "unclassified" '
                "WHERE lower(coalesce(syslog_severity,'')) IN "
                "('emerg','emergency','alert','crit','critical','err','error')"
            ),
            1,
            15,
            2,
        ),
    ]
    results = []
    for name, description, sql, threshold, silence, priority in rules:
        results.append({
            "name": name,
            "description": description,
            "stream_type": "logs",
            "stream_name": "unclassified",
            "is_real_time": False,
            "query_condition": {
                "type": "sql",
                "sql": sql,
                "search_event_type": "alerts",
                "vrl_function": None,
                "multi_time_range": None,
            },
            "trigger_condition": {
                "period": 5,
                "operator": ">=",
                "threshold": threshold,
                "frequency": 5,
                "frequency_type": "minutes",
                "silence": silence,
                "timezone": "America/Toronto",
            },
            "destinations": destinations.copy(),
            "enabled": enabled,
            "priority": priority,
            "tags": ["logging-platform", "unclassified", "source-discovery"],
            "row_template": "",
            "row_template_type": "String",
            "tz_offset": -240,
            "creates_incident": False,
            "context_attributes": {"dashboard": "Unclassified Source Discovery"},
            "workflows": [],
        })
    return results


def request_json(method: str, url: str, email: str, password: str,
                 body: dict | None = None) -> object:
    token = base64.b64encode(f"{email}:{password}".encode()).decode("ascii")
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenObserve API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach OpenObserve: {exc.reason}") from exc


def alert_items(response: object) -> list[dict]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        for key in ("list", "alerts", "data"):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def upsert(base: str, org: str, email: str, password: str, rules: list[dict]) -> None:
    collection = f"{base.rstrip('/')}/api/v2/{urllib.parse.quote(org, safe='')}/alerts"
    existing = {item.get("name"): item for item in alert_items(
        request_json("GET", collection, email, password)
    )}
    for rule in rules:
        current = existing.get(rule["name"])
        alert_id = current.get("id") if current else None
        if alert_id:
            url = f"{collection}/{urllib.parse.quote(str(alert_id), safe='')}"
            request_json("PUT", url, email, password, rule)
            action = "updated"
        else:
            request_json("POST", collection, email, password, rule)
            action = "created"
        state = "enabled" if rule["enabled"] else "disabled"
        print(f"{action}: {rule['name']} ({state})")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--export", type=Path, help="write definitions to JSON without API access")
    args = parser.parse_args()

    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(json.dumps(definitions(), indent=2) + "\n", encoding="utf-8")
        print(f"exported: {args.export}")
        return 0

    load_env(args.env_file)
    required = ("ZO_ROOT_USER_EMAIL", "ZO_ROOT_USER_PASSWORD", "ZO_ORG")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError("missing required environment settings: " + ", ".join(missing))

    destination = os.environ.get("UNCLASSIFIED_ALERT_DESTINATION", "").strip() or None
    if destination is None:
        print("No alerts changed: UNCLASSIFIED_ALERT_DESTINATION is unset.")
        print("OpenObserve requires a destination or workflow even for a disabled alert.")
        print("Configure and test a destination, set its name in .env, then rerun this script.")
        return 0
    base = os.environ.get("OPENOBSERVE_INTERNAL_URL", "http://127.0.0.1:5080")
    upsert(base, os.environ["ZO_ORG"], os.environ["ZO_ROOT_USER_EMAIL"],
           os.environ["ZO_ROOT_USER_PASSWORD"], definitions(destination))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print(f"alert provisioning failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
