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

The separate **PMG to Ubersmith Mail Ingestion** dashboard uses the observed
`ubersmith/mail` Postfix events. It reports Message-ID and queue-ID activity,
senders, recipients, relay/delivery state, DSN, delay, and raw mail events. Its
sender and recipient inputs correlate the PMG filter ID and RFC Message-ID with
the Ubersmith Postfix queue and timeline.

This proves that Ubersmith's mail layer received and processed a message. It
does not claim that a ticket was created: current `ubersmith/web` and
`ubersmith/php` records contain ticket/queue application activity but do not
share the mail Message-ID or Postfix queue ID. A future application-log change
should emit the incoming RFC Message-ID together with the created Ubersmith
ticket ID; only then should the dashboard add per-ticket correlation.

Every event retains the original `raw_message`. The initial dashboard is
deliberately based on stable normalized syslog fields and observed program
families instead of fragile assumptions about proprietary message payloads.
Add focused parsers only after a representative multi-day sample proves that a
field is stable and useful.

Provision only this dashboard with:

```bash
./scripts/provision-dashboards.py --only ubersmith
```

That command provisions both Ubersmith Billing Operations and PMG to Ubersmith
Mail Ingestion. Bootstrap the mail fields once before the first deployment:

```bash
./scripts/provision-dashboards.py --only ubersmith --bootstrap-schema
./scripts/provision-dashboards.py --only ubersmith --validate-queries
```
