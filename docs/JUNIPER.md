# Juniper Junos and SRX

Map each sender IP as `juniper`. For control-plane logging, configure `[edit system syslog]` with the host, port, facility/severity selectors and `transport tcp`; Junos supports `transport tls` on applicable releases, which requires PKI configuration and device release support. For SRX data-plane security logging, configure stream mode and structured `sd-syslog` where supported.

```text
set system syslog host 192.0.2.10 port 514
set system syslog host 192.0.2.10 transport tcp
set system syslog host 192.0.2.10 any any
set system syslog host 192.0.2.10 source-address 192.0.2.30
```

Junos controls transport support by release/platform (TCP/TLS was added later on several families). Preserve process/event/security fields in raw syslog and configure NTP/timezone on the device. TLS needs the collector CA and, for mutual auth, device certificates.
