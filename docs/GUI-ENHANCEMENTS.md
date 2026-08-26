# OpenObserve GUI enhancements

The deployment provisions GUI objects only through OpenObserve's supported
HTTP APIs. It does not modify the internal SQLite database.

## Dashboards and folders

`scripts/provision-dashboards.py` owns nine dashboards and their folders:

| Folder | Dashboards |
|---|---|
| Logging Overview | Central Logging Overview |
| Messaging | PMG Mail Reporting; PMG Message Investigation; PMG to Ubersmith Mail Handoff |
| Network & Security | FortiGate Security & Traffic; Juniper Router Operations |
| Infrastructure | Proxmox VE Operations |
| Applications | Ubersmith Billing Operations |
| Source Discovery | Unclassified Source Discovery |

Detailed dashboards remain separate. Their query-backed selector filters every
panel by `device_name`; Unclassified Source Discovery filters by `source_ip`.
Select all values to return to the fleet view. Both PMG dashboards default to 7
days because mail reporting and message tracing commonly cross day boundaries;
other dashboards default to 24 hours to keep interactive queries responsive.
PMG Message Investigation and PMG to Ubersmith Mail Handoff have free-form
sender and recipient inputs for message, Message-ID and Postfix queue
correlation.

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

## Cached reports

Four cached reports are active: daily PMG, weekly FortiGate, weekly
infrastructure, and weekly unknown-source reporting. They intentionally have no
destinations and therefore cannot send email. The internal Report Server keeps
each dashboard's curated overview tab warm for quick access through **Reports
→ Cached** and the dashboard's report list. Detailed tabs continue to run live
when selected.

Cached reports are not a durable PDF archive. OpenObserve stores report
definitions and query/render cache data, while the source logs remain governed
by the normal 30-day retention. If a point-in-time artifact must be retained,
export it deliberately and place it in an administrator-managed backup or
records system.

SMTP is optional and disabled. Before adding email delivery later:

1. Configure SMTP values in `.env`.
2. Create and test recipients on a non-cached scheduled report.
3. Review its tabs, local `America/Toronto` schedule, and recipients before
   enabling external delivery.

No alerts are provisioned at this stage.
