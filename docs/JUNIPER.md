# Juniper Junos and SRX

Map each sender's fixed management or loopback IP to the `juniper` stream in
`config/sources.yml`. The mapping is authoritative; unlisted sources remain in
`unclassified` so no event is lost. Use the address the router will actually
source on `vmbr0`/the routed management network.

For control-plane logging, configure `[edit system syslog]` with the collector,
facility/severity selectors and reliable transport. Replace the example
addresses before committing:

```text
set system syslog host 192.0.2.10 port 514
set system syslog host 192.0.2.10 transport tcp
set system syslog host 192.0.2.10 any any
set system syslog host 192.0.2.10 source-address 192.0.2.30
set system syslog host 192.0.2.10 explicit-priority
```

`any any` is intentionally broad for initial acceptance testing. After the
dashboard is populated, tune facilities/severities to the operational events
you need. Junos controls TCP/TLS support by release and platform. TLS requires
PKI configuration, the collector CA, and a certificate name on the Junos host;
use TCP/514 until that trust is ready rather than bypassing validation.

For SRX data-plane security logs, use event mode for modest volumes or stream
mode with structured `sd-syslog` where supported. Control-plane `system syslog`
and data-plane security logging are separate Junos facilities; configure both
only if the SRX security panels are required. High-volume session-create/close
logging can materially increase storage use.

The collector conservatively extracts Junos event mnemonic, interface,
routing instance/VRF, peer, username, and common SRX source/destination,
port, zone, policy, protocol, and session fields. It always retains `message`
and `raw_message`. The separate **Juniper Router Operations** dashboard covers:

- severity, device and process trends;
- link/interface events and failures;
- BGP, OSPF, IS-IS and neighbor/peer changes;
- authentication, administrative users, commits and configuration activity;
- optional SRX policy, zone and session reporting;
- recent and raw-event troubleshooting.

Provision only this dashboard without touching others:

```bash
./scripts/provision-dashboards.py --only juniper
```

Configure NTP and the correct timezone on every router. Junos timestamps that
omit an offset are interpreted using `PLATFORM_TIMEZONE` (default
`America/Toronto`); collector receive time is stored independently in
`received_at`.
