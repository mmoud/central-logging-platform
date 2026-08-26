# Proxmox Mail Gateway

Map PMG's fixed sender IP to `proxmox_mail_gateway` in `config/sources.yml`.
Modern PMG releases use rsyslog. Create `/etc/rsyslog.d/60-openobserve.conf`
on the PMG host, replacing `COLLECTOR_IP`:

```rsyslog
# RFC5424 includes an RFC3339 timestamp and explicit UTC offset. This avoids
# ambiguous RFC3164 timestamps and preserves correct EST/EDT conversion.
action(
    type="omfwd"
    target="COLLECTOR_IP"
    port="514"
    protocol="tcp"
    TCP_Framing="octet-counted"
    template="RSYSLOG_SyslogProtocol23Format"
    action.resumeRetryCount="-1"
    queue.type="LinkedList"
    queue.filename="openobserve_fwd"
    queue.size="100000"
    queue.maxDiskSpace="1g"
    queue.saveOnShutdown="on"
)
```

Validate before restarting; a bad forwarding file must not interrupt local PMG
logging:

```bash
install -d -m 0750 /var/spool/rsyslog
rsyslogd -N1
systemctl restart rsyslog
systemctl is-active rsyslog
logger -p mail.info -t openobserve-pmg-test "PMG forwarding test"
```

TCP/514 with RFC5424 is recommended on a trusted management network. Use TCP/TLS/6514
after deploying a company-trusted collector certificate. PMG retains its local
log destinations; the dedicated linked-list action queue prevents a collector
outage from blocking them and spills up to 1 GiB to `/var/spool/rsyslog`.

Postfix emits multiple events per message. The collector keeps the PMG filter ID,
RFC Message-ID, linked Postfix queue ID, and individual Postfix queue IDs as
different fields and correlates records at query time; it does not fabricate a
merged event. This avoids counting one mail several times merely because it had
an inbound queue hop, filtering pass, and outbound queue hop.

PMG 9.1 or newer can log a dedicated, decoded header summary. Enable it with:

```bash
pmgsh set /config/mail --log-headers 1
pmgsh get /config/mail
```

The resulting PMG event includes the SMTP envelope sender/recipients and the
message's decoded `From`, `To`, and `Subject` headers. OpenObserve exposes these
as separate `mail_envelope_*` and `mail_header_*` fields. This distinction is
especially important for Microsoft 365 forwarding because Sender Rewriting
Scheme (SRS) can rewrite the envelope sender while leaving the visible `From`
header unchanged.

Header logging has a privacy and data-volume cost: subject lines and display
names become searchable log data and follow the OpenObserve retention policy.
Enable it only when that is acceptable under the organization's mail and privacy
policy. Disable it with `pmgsh set /config/mail --log-headers 0`.

Other extracted fields include sender and recipient domains, PMG rule and
filter action, quarantine ID, relay and destination IP, source host/IP, message
size, SMTP response/DSN/status, delay stages, rejection, spam, and virus
indicators when present. Every record retains `raw_message` and unmatched PMG
messages are still ingested.

Create or update the bundled mail dashboard through OpenObserve's supported API:

```bash
cd /opt/logging-platform
./scripts/provision-dashboards.py --only pmg --bootstrap-schema
./scripts/provision-dashboards.py --only pmg
./scripts/provision-dashboards.py --only pmg --validate-queries
```

Run the bootstrap once when enabling PMG header logging on an existing stream.
It adds a synthetic `example.invalid` schema record so OpenObserve can compile
all panels before the first real header-summary event arrives. Every bundled PMG
query explicitly excludes that record, so it never changes operational totals
or appears in tables.

The **PMG Mail Reporting** dashboard uses focused tabs for overview, volume and
components, recent mail, senders and recipients, routing and SMTP, delivery,
filtering, queue-ID trace, and raw events. Mail volume is counted by PMG filter
ID—not Postfix queue hop. Original/header sender, envelope sender, header
recipient, and envelope recipient are clearly labelled and reported separately.
It also covers PMG rules/actions, delivery outcomes, domains, relays, source IPs,
size, delay, DSN, SMTP response, spam, malware, rejects, deferrals, and queue-ID
investigation. Keeping each tab small also prevents duplicate lazy-loaded query
results in current OpenObserve.
