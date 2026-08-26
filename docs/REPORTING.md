# Starter searches

Select the appropriate stream, choose a time range, and save these as searches/dashboard panels. Fields with dots may need quoting in OpenObserve SQL depending on the UI version.

```sql
-- FortiGate events by VDOM and severity
SELECT fortigate.vd, syslog.severity, count(*) FROM fortigate GROUP BY fortigate.vd, syslog.severity ORDER BY count(*) DESC

-- Network interface and routing events
SELECT device_name, syslog.severity, message FROM cisco WHERE message LIKE '%UPDOWN%' OR message LIKE '%OSPF%' OR message LIKE '%BGP%'

-- PMG deferred/rejected mail events; queue ID permits manual correlation
SELECT @timestamp, message, raw_message FROM proxmox_mail_gateway WHERE raw_message LIKE '% status=deferred%' OR raw_message LIKE '% reject%'

-- Linux / Proxmox authentication and privilege activity
SELECT @timestamp, device_name, syslog.program, message FROM linux WHERE syslog.program IN ('sshd','sudo')
```

For PMG, dashboards should count individual Postfix events by day/status and offer drill-down by queue ID. It is intentionally not a false one-row-per-email report: delivery lifecycle events are distinct syslog records.
