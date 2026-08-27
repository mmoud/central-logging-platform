# Bundled dashboards

`scripts/provision-dashboards.py` contains the version-controlled definitions
for the fleet overview, PMG, FortiGate, Juniper, Proxmox VE, Ubersmith, and
unclassified-source dashboards and creates or updates them through
OpenObserve's supported dashboard API.

Run `sudo ./scripts/provision-dashboards.py` from the installed platform
directory. To inspect or import the JSON manually, export deterministic copies:

```bash
./scripts/provision-dashboards.py --export-dir openobserve/dashboards/generated
```

The generated directory is ignored by Git. Dashboard SQL uses the selected
OpenObserve time range. PMG records are correlated at query time by
`mail_queue_id`; the collector does not incorrectly merge separate Postfix
events into one synthetic message.

The separate 22-panel **Central Logging Overview** shows event counts, latest
event timestamps, hourly volume, and a small set of triage indicators without
combining vendor schemas. Each detailed reporting dashboard has a query-backed
device selector; the unclassified dashboard selects source IP instead. All
dashboards open at six hours for responsive initial loading, and users can
still choose any time range in the GUI.

Dashboard folders are created through the supported folders API: Logging
Overview, Messaging, Network & Security, Infrastructure, Applications, and
Source Discovery. Existing managed dashboards are moved into those folders;
unrelated dashboards are untouched.

The FortiGate definition includes 70 panels covering traffic, VDOMs, VPN,
administration, HA, routing, application control, IPS, web/DNS filtering,
antivirus/file filtering, DLP, email filtering, SSL, WAF, CASB, anomalies and
virtual-patch events. FortiOS fields not used by a panel remain searchable.
The separate 14-panel **FortiGate Event Investigation** traces connections,
sessions, NAT, policies and UTM evidence. The 34-panel **FortiGate Access & VPN
Operations** dashboard focuses on authentication, IPsec, SSL-VPN,
administrative, HA and update activity.

PMG reporting is split into the 40-panel **PMG Mail Reporting** dashboard and
the focused 9-panel **PMG Message Investigation** workflow. A separate
15-panel PMG-to-Ubersmith view correlates only factual mail handoff evidence.

**Juniper Router Operations** is a separate 30-panel dashboard for Junos
interfaces, routing protocols/peers, authentication, configuration changes,
and optional SRX policy/session logs. **Proxmox VE Operations** is a separate
32-panel dashboard for nodes, services, authentication, VMs/containers, UPID
tasks, HA, corosync/quorum, and failures. Provision either independently with
`--only juniper` or `--only proxmox-ve`; this does not update the other
dashboard definitions.

**Ubersmith Billing Operations** is a separate 24-panel dashboard for
application errors, mail, PHP/web, Solr, cron, Redis and ClamAV activity. It
uses the dedicated `ubersmith` stream and can be provisioned independently with
`--only ubersmith`.

**Unclassified Source Discovery** is a separate 24-panel dashboard for finding
new or unmapped senders. It shows first/last seen time, volume, transport,
facility, severity, host and program inventory, errors, recent samples, and the
preserved raw event. Provision it independently with `--only unclassified`.

Before a future source sends its first log, OpenObserve has no stream/schema and
cannot execute dashboard SQL. To validate a pre-staged Juniper or PVE dashboard,
bootstrap only that schema:

```bash
./scripts/provision-dashboards.py --only juniper --bootstrap-schema
./scripts/provision-dashboards.py --only proxmox-ve --bootstrap-schema
```

Each command ingests one `schema_bootstrap=true` record. All bundled Juniper
and PVE panel SQL explicitly excludes that marker, and normal retention removes
it later; no live-source event is fabricated or counted.

Panels are intentionally distributed across focused tabs. Current OpenObserve
can execute a large tab's below-the-fold panels twice as they enter the viewport;
small tabs keep aggregate counts and limited tables accurate while retaining the
complete reporting set.
