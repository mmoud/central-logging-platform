# Ubersmith application logging

Use a dedicated `ubersmith` stream for the billing application and closely
related worker/poller hosts. Source-IP mapping remains authoritative; program
names help an administrator verify a proposed mapping but do not route an
unknown event by themselves.

Example source entries use documentation-only addresses:

```yaml
devices:
  - name: billing01
    ip: 192.0.2.100
    vendor: ubersmith
    product: billing
    stream: ubersmith
  - name: poller01
    ip: 192.0.2.101
    vendor: ubersmith
    product: poller
    stream: ubersmith
```

The **Ubersmith Billing Operations** dashboard has independent tabs for:

- overall volume, severity, hosts and programs;
- application/PHP errors;
- mail, web/PHP, Solr and cron trends;
- Redis, ClamAV and supporting-service events;
- recent and raw-event troubleshooting.

Every event retains the original `raw_message`. The initial dashboard is
deliberately based on stable normalized syslog fields and observed program
families instead of fragile assumptions about proprietary message payloads.
Add focused parsers only after a representative multi-day sample proves that a
field is stable and useful.

Provision only this dashboard with:

```bash
./scripts/provision-dashboards.py --only ubersmith
```
