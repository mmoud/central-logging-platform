# Proxmox Mail Gateway

Map PMG's fixed sender IP to `proxmox_mail_gateway`. Configure rsyslog forwarding as for PVE, preferably TCP:

```text
*.* @@192.0.2.10:514
```

Postfix emits multiple events per message. Correlate by the queue ID visible in `raw_message` (for example `ABCD1234:`); do not treat separate SMTP, queue-manager, cleanup, spam or delivery events as a single record. Retained raw events support sender/recipient, relay, response, DSN, status, delay, message-size and PMG filter investigations. Add parsing only after collecting representative logs from `smtpd`, `smtp`, `qmgr`, `cleanup`, `pickup`, `anvil`, and `postscreen` from your PMG release.
