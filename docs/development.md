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
shellcheck install.sh
pytest -q
```

CI runs these checks, rebuilds the dashboards from their generators and fails if the JSON differs, and builds the
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

The reporter container can reach Prometheus and Loki over the Compose network. To try the current reporter against
live data without publishing Loki or writing into `reporter/out`, build the image and run a one-off container:

```bash
docker compose build reporter
docker compose run --rm -e OUT_DIR=/tmp/reports -e TZ=Africa/Accra reporter \
  python report.py run weekly --no-email
```

For a locally edited reporter, use `docker compose build reporter` first so the one-off container includes the
changes. Prometheus remains available for query checks through the SSH tunnel above; Loki is intentionally internal
only, as described in [deployment](deployment.md#ports).

## Releasing

1. In `CHANGELOG.md`, rename `## Unreleased` to the new version and date, e.g. `## v1.2.0 (2026-12-01)`, and
   add an empty `## Unreleased` above it. Bump the minor version for new features, the patch version for fixes
   only, and the major version when an update needs manual steps on the server.
2. Commit, then tag and push: `git tag -a v1.2.0 -m v1.2.0 && git push origin main v1.2.0`. CI publishes the
   reporter image as `1.2.0`.
3. Make the GitHub release from that version's changelog section:
   `awk '/^## v1.2.0 /{found=1; next} found && /^## /{exit} found{print}' CHANGELOG.md > /tmp/release-notes.md`
   followed by `gh release create v1.2.0 --title v1.2.0 --notes-file /tmp/release-notes.md`.
