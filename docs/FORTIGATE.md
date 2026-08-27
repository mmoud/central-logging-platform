# FortiGate

Map the FortiGate's configured source IP in `config/sources.yml` as stream `fortigate`. Prefer reliable TCP/514; FortiOS supports `reliable` (RFC 6587) and TLS via `enc-algorithm` on versions that provide it. Check the CLI reference matching your FortiOS release.

```text
config log syslogd setting
  set status enable
  set server 192.0.2.10
  set mode reliable
  set port 514
  set facility local7
  set format default
  set source-ip 192.0.2.1
end
```

For TLS, configure the collector CA/certificate trust on the appliance and use TCP/6514 only after `ENABLE_SYSLOG_TLS=true`. The package parses FortiGate `key=value` messages conservatively and preserves every parsed `fortigate.*` key (including `vd`/`vdom`), raw text, device/source IP, and normalized syslog fields.

## Licensed security profiles (UTM)

The bundled dashboard has dedicated views for:

- Application Control: application ID/name/category/risk, profile, action, user, host and URL.
- IPS and anomaly detection: signature/attack ID, severity, action, incident, source/destination, policy and VDOM.
- Web and DNS filtering: hostname, URL, HTTP method, action, user, policy and content-risk fields when supplied.
- Antivirus, file filtering, DLP, email filtering, SSL inspection, WAF, CASB and virtual-patch event families.

## Troubleshooting dashboard

Use **FortiGate Event Investigation** when the question is what happened to a
specific connection or event. Set one or more dashboard filters and apply them:

- source or translated IP;
- destination or translated IP;
- username;
- FortiGate session ID;
- policy ID or partial policy name;
- VDOM;
- application, category, hostname, URL, threat signature, or message text.

The dashboard then provides a chronological event list, per-session summary,
session timeline, NAT translation evidence, policy decisions, UTM/threat
evidence, administrative/VPN/HA/routing evidence, and raw syslog. Start with a
narrow time range and source/destination IP, then copy the session ID into its
filter to isolate the complete session trace. Blank filters mean all values.

## Access and VPN operations dashboard

Use **FortiGate Access & VPN Operations** for routine access and remote-access
review rather than connection tracing. It separates authentication success and
failure trends, ranked sources, attempted usernames and protocols from IPsec
and SSL-VPN outcomes, peers, failure reasons and detailed raw evidence. Related
administrative, HA, configuration and FortiGuard/update activity is included in
its own tab.

The dashboard has Device, Source IP, User, VDOM, VPN peer and free-text inputs.
Enter an exact parsed source IP or username where possible; use the free-text
input for a tunnel name, error phrase or value that exists only in the raw
FortiOS message. An empty input means all values. A large failure count can be
internet scanning, a broken client or a real attack, so compare the source,
attempted user, protocol, target/VDOM and the success timeline before deciding.

FortiOS schemas vary by release and licensed profile. The generic key/value parser retains fields that are not yet named in a dashboard, and every event keeps `raw_message`; a new field therefore does not require a collector parser change. The dashboard only counts a security family after the FortiGate actually emits those logs.

Apply the desired security profiles to policies and enable logging for the profile/event types you need. UTM inspection and policy traffic logging are separate decisions: a license alone does not cause every event to be sent. Application-control and web/DNS logs can be high volume, so monitor `/opt/logging-data` after enabling them. Prefer `utm`/security events plus policy violations initially, then add allowed-session logging only where reporting requires it.

FortiGate includes `tz` and nanosecond `eventtime` on its native key/value records. They are preserved as `event_timezone` and `event_timestamp` alongside the collector's `received_at`. For syslog headers without an offset, `America/Toronto` is the default receive timezone, following EST/EDT automatically.

If logs are relayed by FortiAnalyzer, see [FortiManager and FortiAnalyzer](FORTIMANAGER.md). Direct FortiGate forwarding best preserves the network sender IP; relayed logs retain FortiOS `devname`, `devid`, and `vd` for device/VDOM attribution.
