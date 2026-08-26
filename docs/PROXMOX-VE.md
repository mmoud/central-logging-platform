# Proxmox VE

PVE is Debian-based and normally uses rsyslog. First map every node's fixed
source IP to the `proxmox_ve` stream in `config/sources.yml`. Keep PMG sources
mapped to `proxmox_mail_gateway`; the streams and dashboards are intentionally
separate.

Send system/journal events from `/etc/rsyslog.d/90-central.conf`. This action
uses TCP and a disk-assisted queue so a collector restart does not immediately
lose node events (replace `192.0.2.10`):

```rsyslog
module(load="omfwd")

action(
  type="omfwd"
  target="192.0.2.10"
  port="514"
  protocol="tcp"
  TCP_Framing="octet-counted"
  template="RSYSLOG_SyslogProtocol23Format"
  queue.type="LinkedList"
  queue.filename="central_openobserve"
  queue.maxDiskSpace="1g"
  queue.saveOnShutdown="on"
  action.resumeRetryCount="-1"
)
```

`TCP_Framing="octet-counted"` is required for this collector's syslog-ng TCP
listener. It preserves message boundaries reliably, including multiline data.
The named queue enables disk assistance under rsyslog's configured work
directory; without `queue.filename`, a `LinkedList` action queue is memory-only.

Validate and restart only after syntax succeeds:

```bash
rsyslogd -N1
systemctl restart rsyslog
logger -t pve-central-test "Proxmox VE central logging test"
ss -tnp state established '( dport = :514 )'
```

For a very simple TCP configuration, `*.* @@192.0.2.10:514` is also valid;
use one `@` only for UDP. The longer action above provides better retry and
local buffering behavior. Use TLS/6514 only after installing the collector CA
and configuring rsyslog's `gtls` settings with certificate verification.

The collector enriches standard PVE/Linux syslog conservatively. It recognizes
authentication outcome/user/source, `UPID` task identifiers and their node,
task, guest ID and user segments, explicit VM/CT IDs, and HA resource names
where present. Standard `syslog_program`, severity, host, sender IP, `message`
and `raw_message` remain available for every record.

The separate **Proxmox VE Operations** dashboard covers:

- node, severity and service trends;
- authentication failures, users and login source IPs;
- VM/container lifecycle, migration and replication activity;
- task/UPID types, users and failures;
- `pve-ha-lrm`, `pve-ha-crm`, `corosync`, `pmxcfs`, quorum and fencing events;
- service failures and raw-event troubleshooting.

Provision only this dashboard without modifying PMG, FortiGate, or Juniper:

```bash
./scripts/provision-dashboards.py --only proxmox-ve
```

Run the logger test from each node and confirm it lands in `proxmox_ve`. Do not
forward nodes through NAT if source-IP mapping is required. PVE task logs are
individual events; UPID/guest fields make query-time correlation possible but
the collector does not fabricate a combined task record.
