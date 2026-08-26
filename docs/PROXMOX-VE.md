# Proxmox VE

PVE is Debian-based and normally uses rsyslog. Send system/journal events remotely from `/etc/rsyslog.d/90-central.conf`, choosing TCP for reliable transport:

```text
*.* @@192.0.2.10:514
```

Use `@` rather than `@@` only for UDP. Restart with `systemctl restart rsyslog`; verify local service health first. Map the PVE node's sender IP to `proxmox_ve`. Standard syslog fields retain `pvedaemon`, `pveproxy`, `pvestatd`, HA/LRM/CRM, corosync, sshd, sudo, kernel and systemd events. Do not forward an unrelated host's logs through NAT if source-IP classification is required.
