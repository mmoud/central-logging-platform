# FortiManager and FortiAnalyzer

Direct FortiGate-to-collector TCP is preferred because the collector sees the firewall's real sender IP. FortiManager can also send its own audit/system logs, and FortiAnalyzer can relay managed FortiGate logs when that architecture is required.

## FortiManager local logs

Add the FortiManager source address to `config/sources.yml`. Use `stream: fortigate`, `vendor: fortinet`, and `product: fortimanager`; `observer_product` then distinguishes its local logs from direct firewalls. In FortiManager, first define the remote syslog server, then enable local logging. FortiManager 7.6 supports reliable TCP and TLS:

```text
config system syslog
  edit "openobserve-collector"
    set ip 192.0.2.10
    set port 514
    set reliable enable
    set secure-connection disable
  next
end

config system locallog syslogd setting
  set facility local6
  set severity information
  set status enable
  set syslog-name "openobserve-collector"
end
```

For TLS, use port 6514, enable `secure-connection`, configure the peer certificate name and trust, and enable the collector TLS listener first. Check the CLI reference for the exact FortiManager release before applying commands.

## FortiAnalyzer relay

FortiAnalyzer forwarding mode can relay FortiGate traffic, application-control, attack/IPS, DLP, email-filter, virus, web-filter, WAF, DNS and SSH log types to a syslog destination. Choose the native FortiGate (`fgt`) format so this package's key/value parser retains the full FortiOS schema. Use reliable forwarding and near-real-time delivery where supported.

When relaying, add the FortiAnalyzer address to `sources.yml` as a Fortinet source. A relay may make the packet sender IP the FortiAnalyzer address; the original firewall must then be identified by `fortigate_devname`, `fortigate_devid`, and `fortigate_vd`. FortiAnalyzer's `fwd-log-source-ip original_ip` and transparent forwarding behavior vary by topology and version, so validate sender-IP behavior with a test event before relying on source-IP mapping.

Do not forward the same FortiGate logs both directly and through FortiAnalyzer unless duplicate ingestion is intentional.
