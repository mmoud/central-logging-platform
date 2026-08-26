# Bundled dashboards

`scripts/provision-dashboards.py` contains the version-controlled definitions
for the PMG and FortiGate dashboards and creates or updates them through
OpenObserve's supported dashboard API.

Run `sudo ./scripts/provision-dashboards.py` from the installed platform
directory. To inspect or import the JSON manually, export deterministic copies:

```bash
./scripts/provision-dashboards.py --export-dir openobserve/dashboards/generated
```

The generated directory is ignored by Git. Dashboard SQL uses the selected
OpenObserve time range. PMG records are correlated at query time by
`mail_queue_id`; the collector does not incorrectly merge separate Postfix
events into one synthetic message.

Panels are intentionally distributed across focused tabs. Current OpenObserve
can execute a large tab's below-the-fold panels twice as they enter the viewport;
small tabs keep aggregate counts and limited tables accurate while retaining the
complete reporting set.
