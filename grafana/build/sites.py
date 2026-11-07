from gf import AQUA, BLUE, DOWN, GREY, ORANGE, RED, UP, YELLOW, Dashboard, color, mirror, steps, target

S = 'source=~"$Controller", site_name=~"$Site"'

d = Dashboard(
    "UniFi Sites", "9WaGWZaZk",
    "Overview of each UniFi site: devices, clients, internet availability and latency, health by subsystem, "
    "VPN use and traffic. Start here, then open the Gateway, Access Points or Clients dashboards for detail.",
    ["unifi", "unifi-poller", "sites"], "unifi-sites.json")

d.var("Controller", "label_values(unpoller_site_adopted, source)")
d.var("Site", 'label_values(unpoller_site_adopted{source=~"$Controller"}, site_name)')

# ---- now
d.stat("UniFi devices", "Gateways, switches and access points adopted on the site.",
       f"sum(unpoller_site_adopted{{{S}}})")
d.stat("Offline devices", "Adopted devices that aren't connected to the controller. Red when any are offline.",
       f"sum(unpoller_site_disconnected{{{S}}})", thresholds=steps("text", 1, "red"))
d.stat("Waiting for adoption", "Devices that have been found but not adopted yet.",
       f"sum(unpoller_site_pending{{{S}}})", thresholds=steps("text", 1, "orange"))
d.stat("Clients", "Devices connected now, wired and wireless.",
       f'sum(unpoller_site_users{{{S}, subsystem=~"lan|wlan"}}) + sum(unpoller_site_guests{{{S}}})')
d.stat("Internet up for", "Time since the internet connection last dropped, including failovers.",
       f'min(unpoller_site_uptime_seconds{{{S}, subsystem="www"}})', unit="dtdurations")
d.stat("Internet drops", "Times the internet connection dropped in the selected time range.",
       f'sum(increase(unpoller_site_intenet_drops_total{{{S}}}[$__range]))', decimals=0,
       thresholds=steps("text", 1, "orange"))

# ---- traffic + health
d.timeseries("Internet throughput", "Download above the line, upload below it, across all internet lines.",
             [target(f'max(unpoller_site_receive_rate_bytes{{{S}, subsystem="wan"}}) * 8', "Download"),
              target(f'max(unpoller_site_transmit_rate_bytes{{{S}, subsystem="wan"}}) * 8', "Upload", ref="B")],
             w=16, h=9, unit="bps", legend="list",
             overrides=[color("Download", DOWN), color("Upload", UP), mirror("Upload")])
d.table("Health by subsystem",
        "UniFi's health status for each part of the site: internet (www), WAN, LAN, Wi-Fi (wlan) and VPN.",
        [target(f'max by (subsystem, status) ({{__name__=~"unpoller_site_(adopted|users|uptime_seconds|stations)", {S}}})',
                ref="A", instant=True, fmt="table")],
        w=8, h=9, filterable=False,
        transformations=[{"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value": True},
            "indexByName": {"subsystem": 0, "status": 1},
            "renameByName": {"subsystem": "Subsystem", "status": "Status"}}}],
        overrides=[
            {"matcher": {"id": "byName", "options": "Subsystem"}, "properties": [
                {"id": "mappings", "value": [{"type": "value", "options": {
                    "www": {"text": "Internet", "index": 0}, "wan": {"text": "WAN", "index": 1},
                    "lan": {"text": "LAN", "index": 2}, "wlan": {"text": "Wi-Fi", "index": 3},
                    "vpn": {"text": "VPN", "index": 4}}}]}]},
            {"matcher": {"id": "byName", "options": "Status"}, "properties": [
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "mappings", "value": [{"type": "value", "options": {
                    "ok": {"text": "OK", "color": "green", "index": 0},
                    "warning": {"text": "Warning", "color": "orange", "index": 1},
                    "error": {"text": "Error", "color": "red", "index": 2},
                    "unknown": {"text": "Unknown", "color": "text", "index": 3}}}]}]}])

# ---- people and devices
d.timeseries("Clients", "Connected devices over time by how they connect.",
             [target(f'sum(unpoller_site_users{{{S}, subsystem="wlan"}})', "Wi-Fi"),
              target(f'sum(unpoller_site_users{{{S}, subsystem="lan"}})', "Wired", ref="B"),
              target(f'sum(unpoller_site_guests{{{S}}})', "Guests", ref="C"),
              target(f'sum(unpoller_site_users{{{S}, subsystem="vpn"}})', "VPN", ref="D")],
             decimals=0, legend="list", stack=True, fill=40,
             overrides=[color("Wi-Fi", BLUE), color("Wired", AQUA), color("Guests", YELLOW), color("VPN", ORANGE)])
d.timeseries("UniFi devices", "Adopted, offline and waiting devices over time. Steps mean a device was added, "
             "removed or went offline.",
             [target(f"sum(unpoller_site_adopted{{{S}}})", "Adopted"),
              target(f"sum(unpoller_site_disconnected{{{S}}})", "Offline", ref="B"),
              target(f"sum(unpoller_site_pending{{{S}}})", "Waiting", ref="C")],
             decimals=0, legend="list", interp="stepAfter",
             overrides=[color("Adopted", GREY), color("Offline", RED), color("Waiting", ORANGE)])

# ---- internet quality
d.timeseries("Internet latency", "UniFi's latency check to the internet. Dashed line at 100 ms.",
             [target(f'max(unpoller_site_latency_seconds{{{S}, subsystem="www"}}) * 1000', "Latency")],
             unit="ms", decimals=0, legend="hidden", palette=False, thresholds=steps("transparent", 100, "orange"))
d.timeseries("Internet drops per hour", "How many times the internet connection dropped in each hour.",
             [target(f"sum(increase(unpoller_site_intenet_drops_total{{{S}}}[1h]))", "Drops", interval="1h")],
             decimals=0, legend="hidden", palette=False, draw="bars", fill=80)

# ---- vpn and local traffic
d.timeseries("VPN users", "Remote users connected over UniFi VPN.",
             [target(f'sum(unpoller_site_remote_user_active{{{S}}})', "Connected")],
             decimals=0, legend="hidden", palette=False, interp="stepAfter")
d.timeseries("Wi-Fi and wired traffic", "Traffic on the local networks, from UniFi's per-subsystem rates.",
             [target(f'max(unpoller_site_receive_rate_bytes{{{S}, subsystem="wlan"}}) * 8', "Wi-Fi"),
              target(f'max(unpoller_site_receive_rate_bytes{{{S}, subsystem="lan"}}) * 8', "Wired", ref="B")],
             unit="bps", legend="list", overrides=[color("Wi-Fi", BLUE), color("Wired", AQUA)])
d.save()
