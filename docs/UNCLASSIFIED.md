# Unclassified source discovery

Every source IP absent from `config/sources.yml` is retained in the
`unclassified` stream. Content-based guessing never decides its permanent
vendor stream, so an unusual message cannot silently be mislabeled or dropped.

## Discover a new sender

Open the **Unclassified Source Discovery** dashboard. Start with **Newest
Sources**, **Source Inventory**, and **Recent Unclassified Events**, then inspect
`source_ip`, `host_name`, `syslog_program`, `message`, and `raw_message`.
Confirm the IP and product administratively before classifying it.

Add the fixed source IP to `config/sources.yml`, using one of the supported
streams, and validate before restarting only syslog-ng:

```bash
sudo -i
cd /opt/logging-platform
editor config/sources.yml
./scripts/validate.sh && docker compose up -d syslog-ng
```

The change affects newly received events. Existing unclassified records remain
searchable until retention expires; the platform does not rewrite historical
data.

## Alerts

Provision the bundled alert rules with:

```bash
./scripts/provision-alerts.py
```

The rules detect any unclassified activity, at least 1,000 events in five
minutes, and error/critical-severity activity. OpenObserve requires a
destination or workflow even for disabled alerts. With no destination
configured, the script therefore makes no API changes and cannot generate
broken notifications.

To enable notifications, first create and test a destination in OpenObserve.
Set its exact name in the protected `.env` file, then rerun provisioning:

```text
UNCLASSIFIED_ALERT_DESTINATION=operations-email
```

OpenObserve notification destinations may contain SMTP or webhook secrets.
Keep those in OpenObserve and never commit them to this repository.

## Limits

The collector can identify the network sender, not whether an unexpected source
is authorized. NAT can make multiple devices appear under one IP, while relays
can intentionally replace the original network peer. Use TCP/TLS where possible,
maintain fixed source addresses, and review discovery results before adding a
mapping. Message content remains visible through `raw_message` even when no
vendor parser applies.
