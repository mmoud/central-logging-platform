# Proxmox Mail Gateway

Map PMG's fixed sender IP to `proxmox_mail_gateway` in `config/sources.yml`.
Modern PMG releases use rsyslog. Create `/etc/rsyslog.d/60-openobserve.conf`
on the PMG host, replacing `COLLECTOR_IP`:

```rsyslog
# RFC3164 does not carry a timezone. Convert the reported timestamp to UTC
# before forwarding so the collector does not have to guess the device zone.
template(
    name="OpenObserveRFC3164UTC"
    type="string"
    string="<%PRI%>%TIMESTAMP:::date-rfc3164,date-utc% %HOSTNAME% %syslogtag:1:32%%msg:::sp-if-no-1st-sp%%msg:::drop-last-lf%\n"
)

action(
    type="omfwd"
    target="COLLECTOR_IP"
    port="514"
    protocol="tcp"
    template="OpenObserveRFC3164UTC"
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

TCP/514 is recommended on a trusted management network. Use TCP/TLS/6514
after deploying a company-trusted collector certificate. PMG retains its local
log destinations; the dedicated linked-list action queue prevents a collector
outage from blocking them and spills up to 1 GiB to `/var/spool/rsyslog`.

Postfix emits multiple events per message. The collector extracts `mail.queue_id`
and correlates records at query time; it does not fabricate a merged event.
Fields include sender and recipient/domain, relay and destination IP, source
host/IP, message ID and size, SMTP response/DSN/status, delay stages, rejection,
spam and virus indicators when present. Every record retains `raw_message` and
unmatched PMG messages are still ingested.

Create or update the bundled mail dashboard through OpenObserve's supported API:

```bash
cd /opt/logging-platform
./scripts/provision-dashboards.py --only pmg
```

The **PMG Mail Reporting** dashboard uses focused tabs for overview, volume and
components, recent mail, senders and recipients, routing and SMTP, delivery,
filtering, queue-ID trace, and raw events. It covers volume, delivery outcomes,
senders/recipients/domains, relays, source IPs, size, delay, DSN, SMTP response,
spam, malware, rejects, deferrals, and queue-ID investigation. Keeping each tab
small also prevents duplicate lazy-loaded query results in current OpenObserve.
