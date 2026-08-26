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

For TLS, configure the collector CA/certificate trust on the appliance and use TCP/6514 only after `ENABLE_SYSLOG_TLS=true`. The package parses FortiGate `key=value` messages conservatively and preserves all parsed `fortigate.*` keys (including `vd`/`vdom`), raw text, device/source IP, and normalized syslog fields. Enable event/system/security/VPN/admin/HA/routing as needed; enable traffic and UTM selectively since session logging drives retention volume.
