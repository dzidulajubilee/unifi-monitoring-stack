# Development

```
reporter/
  app/            the report and portal code (see app/__init__.py for what each module does;
                  dns.py reads Pi-hole and Technitium)
  templates/      report.html (the PDF) and portal/ (the web pages)
  fonts/          IBM Plex, bundled so PDFs look the same everywhere
  tests/          pytest, no Prometheus or Loki needed
  report.py       command line entry point
  portal.py       portal entry point (what the container runs)
grafana/
  build/          Python generators for the dashboards, plus check.py
  provisioning/   what Grafana loads: dashboards (generated) and data sources
```

How a report is built, and the rules behind each figure, are in [how the reporter works](how-reports-work.md).

## Checks

```bash
pip install -r reporter/requirements.txt pytest ruff
ruff check reporter grafana/build
pytest -q
```

CI runs the same, rebuilds the dashboards from their generators and fails if the JSON differs, and builds the
reporter image.

## Dashboards

Edit the generator in `grafana/build/`, run it, then check its queries against a real Prometheus:

```bash
python3 grafana/build/gateway.py
python3 grafana/build/check.py http://<server>:9090 unifi-gateway.json
```

Edits made in the Grafana UI are overwritten by the next generator run. The firewall dashboard is the exception:
it's edited as JSON. See [grafana/build/README.md](../grafana/build/README.md).

## Trying a report against live data

Run the reporter locally against the server's Prometheus (and Loki through an SSH tunnel), writing to a scratch
folder:

```bash
PROM_URL=http://<server>:9090 LOKI_URL=http://localhost:3100 OUT_DIR=/tmp/reports TZ=Africa/Accra \
  python reporter/report.py run weekly --no-email
```
