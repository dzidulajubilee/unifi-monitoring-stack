"""Run every query of generated dashboards against Prometheus with all variables set to match everything.

python3 grafana/build/check.py http://prometheus:9090 unifi-gateway.json unifi-sites.json ...
Prints ok / NONE (no data) / ERR per query. NONE is fine for hardware the network doesn't have.
"""
import json, re, sys, urllib.parse, urllib.request
from gf import OUT

prom = sys.argv[1].rstrip("/")
bad = 0
for name in sys.argv[2:]:
    d = json.loads((OUT / name).read_text())
    print("==", d["title"])
    def flat(ps):
        for p in ps:
            yield p
            yield from flat(p.get("panels", []))
    for p in flat(d["panels"]):
        for t in p.get("targets", []):
            q = re.sub(r"\$\{?[A-Za-z_]+\}?", lambda m: {"$__rate_interval": "5m", "$__interval": "5m",
                       "$__range": "1d", "$__range_s": "86400"}.get(m.group(0), ".*"), t["expr"])
            try:
                r = json.load(urllib.request.urlopen(f"{prom}/api/v1/query?" + urllib.parse.urlencode({"query": q}), timeout=60))
                n = len(r["data"]["result"])
                print(f"  {'ok  ' if n else 'NONE'} {p['title'][:40]:40} {t['refId']} {n}")
            except urllib.error.HTTPError as e:
                bad += 1
                print(f"  ERR  {p['title'][:40]:40} {t['refId']} {e.read()[:200]}")
sys.exit(1 if bad else 0)
