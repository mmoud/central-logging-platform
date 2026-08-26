# Linux

Use rsyslog or systemd-journald forwarding. The simplest rsyslog TCP rule is:

```text
*.* @@192.0.2.10:514
```

For a production client, configure rsyslog action queues locally too, use TLS after distributing the collector CA, and retain existing local logs. Map the host's source IP as `linux`. The collector keeps program, severity, host, raw message and source IP for sshd, sudo, su, cron, systemd, kernel, audit and application events. Authentication outcomes and users should be searched from the source message unless a deliberately tested parser is added.
