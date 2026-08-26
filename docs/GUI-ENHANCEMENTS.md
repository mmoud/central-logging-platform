# OpenObserve GUI enhancements

The deployment provisions GUI objects only through OpenObserve's supported
HTTP APIs. It does not modify the internal SQLite database.

## Dashboards and folders

`scripts/provision-dashboards.py` owns seven dashboards and their folders:

| Folder | Dashboards |
|---|---|
| Logging Overview | Central Logging Overview |
| Messaging | PMG Mail Reporting |
| Network & Security | FortiGate Security & Traffic; Juniper Router Operations |
| Infrastructure | Proxmox VE Operations |
| Applications | Ubersmith Billing Operations |
| Source Discovery | Unclassified Source Discovery |

Detailed dashboards remain separate. Their query-backed selector filters every
panel by `device_name`; Unclassified Source Discovery filters by `source_ip`.
Select all values to return to the fleet view. PMG defaults to 7 days because
mail reporting is commonly daily/weekly; other dashboards default to 24 hours
to keep interactive queries responsive.

## Saved investigation views

`scripts/provision-gui.py` owns 12 saved views for denied FortiGate traffic,
VPN and admin activity, FortiGate UTM detections, PMG queue tracing,
deferred/rejected mail, spam/virus mail, Juniper interface/routing activity,
Proxmox authentication and task/service failures, Ubersmith errors, and new
unclassified sources. A saved view is a starting query: adjust its selected
time range and add a queue ID, IP, user, policy, VDOM, or device constraint as
needed.

## Stream search tuning

The GUI provisioner intersects requested fields with each live schema, then
sets a small set of exact-match indexes, high-cardinality Bloom filters, and
full-text keys for `message` and `raw_message`. It does not set partition keys,
change the global 30-day retention, or force a per-stream retention override.
Index changes primarily benefit newly compacted data; historical segments may
not immediately gain the same acceleration.

Run after a parser or schema change:

```bash
cd /opt/logging-platform
./scripts/provision-gui.py
```

Use `--validate-only` to execute every saved-view query without changing GUI
objects. `--skip-stream-settings` and `--skip-reports` provide narrower runs.

## Prepared reports

Four report templates are created disabled: daily PMG, weekly FortiGate,
weekly infrastructure, and weekly unknown-source reporting. They intentionally
have no destination. Before enabling one:

1. Configure SMTP values in `.env` and enable the `reports` Compose profile.
2. Start and health-check the report server.
3. Create and test an OpenObserve email destination.
4. Attach the tested destination to the template.
5. Review its tabs, local `America/Toronto` schedule, and recipients, then
   enable it.

No alerts are provisioned at this stage.
