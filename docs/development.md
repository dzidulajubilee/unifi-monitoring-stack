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

Edit the generator in `grafana/build/`, run it, then check its queries against a real Prometheus. Prometheus
listens only on the server itself, so tunnel to it first:

```bash
ssh -N -L 9090:localhost:9090 you@server &
python3 grafana/build/gateway.py
python3 grafana/build/check.py http://localhost:9090 unifi-gateway.json
```

Edits made in the Grafana UI are overwritten by the next generator run. The firewall dashboard is the exception:
it's edited as JSON. See [grafana/build/README.md](../grafana/build/README.md).

## Trying a report against live data

Run the reporter locally against the server's Prometheus and Loki through SSH tunnels (Prometheus as above),
writing to a scratch folder:

```bash
PROM_URL=http://localhost:9090 LOKI_URL=http://localhost:3100 OUT_DIR=/tmp/reports TZ=Africa/Accra \
  python reporter/report.py run weekly --no-email
```

## Releasing

1. In `CHANGELOG.md`, rename `## Unreleased` to the new version and date, e.g. `## v1.2.0 (2026-12-01)`, and
   add an empty `## Unreleased` above it. Bump the minor version for new features, the patch version for fixes
   only, and the major version when an update needs manual steps on the server.
2. Commit, then tag and push: `git tag -a v1.2.0 -m v1.2.0 && git push origin main v1.2.0`. CI publishes the
   reporter image as `1.2.0`.
3. Make the GitHub release from that version's changelog section:
   `gh release create v1.2.0 --title v1.2.0 --notes-file notes.md`.
