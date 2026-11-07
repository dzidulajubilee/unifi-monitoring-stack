from gf import Dashboard, target, steps, color, mirror, DOWN, UP, BLUE, ORANGE, AQUA, YELLOW, GREY

S = 'source=~"$Controller", site_name=~"$Site"'
C = S + ', network=~"$Network", wired=~"$Connection", name=~"$Client"'
W = S + ', network=~"$Network", name=~"$Client"'  # Wi-Fi-only metrics have no wired label

# UniFi counts client traffic from the access point's side: "transmit" is what the device
# downloads, "receive" is what it uploads.
DL = f"unpoller_client_transmit_bytes_total{{{C}}}"
UL = f"unpoller_client_receive_bytes_total{{{C}}}"

d = Dashboard(
    "UniFi Clients", "jMfvAjxWz",
    "Every connected device: where it's connected, how much it's downloading and uploading, and for Wi-Fi "
    "devices how good the connection is. Use the selectors to narrow down by network, connection type or device.",
    ["unifi", "unifi-poller", "clients"], "unifi-clients.json")

d.var("Controller", "label_values(unpoller_client_uptime_seconds, source)")
d.var("Site", 'label_values(unpoller_client_uptime_seconds{source=~"$Controller"}, site_name)')
d.var("Network", 'label_values(unpoller_client_uptime_seconds{source=~"$Controller", site_name=~"$Site"}, network)')
d.vars.append({"allValue": ".*", "current": {"selected": True, "text": "All", "value": "$__all"}, "hide": 0,
               "includeAll": True, "label": "Connection", "multi": False, "name": "Connection",
               "options": [], "query": "Wi-Fi : false, Wired : true", "skipUrlSync": False, "type": "custom"})
d.var("Client", 'label_values(unpoller_client_uptime_seconds{source=~"$Controller", site_name=~"$Site", '
      'network=~"$Network", wired=~"$Connection"}, name)', label="Device")

# ---- now
d.stat("Devices", "Devices connected now, for the current selection.", f"count(unpoller_client_uptime_seconds{{{C}}})")
d.stat("Wi-Fi", "Devices connected over Wi-Fi now.", f'count(unpoller_client_uptime_seconds{{{C}, wired="false"}}) or vector(0)')
d.stat("Wired", "Devices connected by cable now.", f'count(unpoller_client_uptime_seconds{{{C}, wired="true"}}) or vector(0)')
d.stat("Downloading now", "Combined download rate of the selected devices.",
       f"sum(rate({DL}[5m])) * 8", unit="bps", decimals=1)
d.stat("Wi-Fi experience", "Average UniFi satisfaction score of the selected Wi-Fi devices. Orange below 80%.",
       f"avg(unpoller_client_satisfaction_ratio{{{W}}} >= 0)", unit="percentunit", decimals=0,
       thresholds=steps("red", 0.6, "orange", 0.8, "text"))
d.stat("Weak signal", "Wi-Fi devices with a signal below −75 dBm now.",
       f"count(unpoller_client_radio_signal_db{{{W}}} < -75) or vector(0)", thresholds=steps("text", 1, "orange"))

# ---- the table
by_mac = lambda expr, ref: target(f"max by (mac) ({expr})", ref=ref, instant=True, fmt="table")
d.table(
    "Devices",
    "Every connected device in the selection. Connected to shows the access point for Wi-Fi devices and the "
    "switch port for wired ones. Signal and experience apply to Wi-Fi only. Filter any column with the funnel icon.",
    [target(f"max by (mac, name, ip, network, ap_name, sw_name, sw_port, wired) (unpoller_client_uptime_seconds{{{C}}})",
            ref="A", instant=True, fmt="table"),
     by_mac(f"rate({DL}[5m]) * 8", "B"),
     by_mac(f"rate({UL}[5m]) * 8", "C"),
     by_mac(f"unpoller_client_radio_signal_db{{{W}}}", "D"),
     by_mac(f"unpoller_client_satisfaction_ratio{{{W}}} >= 0", "E")],
    w=24, h=12,
    transformations=[
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True},
            "indexByName": {"name": 0, "ip": 1, "network": 2, "wired": 3, "ap_name": 4, "sw_name": 5, "sw_port": 6,
                            "Value #B": 7, "Value #C": 8, "Value #D": 9, "Value #E": 10, "Value #A": 11, "mac": 12},
            "renameByName": {"name": "Device", "ip": "IP", "network": "Network", "wired": "Connection",
                             "ap_name": "Access point", "sw_name": "Switch", "sw_port": "Port",
                             "Value #B": "Downloading", "Value #C": "Uploading", "Value #D": "Signal",
                             "Value #E": "Experience", "Value #A": "Connected for", "mac": "MAC"}}}],
    sort=("Downloading", True),
    overrides=[
        {"matcher": {"id": "byName", "options": "Connection"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {"true": {"text": "Wired", "index": 0},
                                                                      "false": {"text": "Wi-Fi", "index": 1}}}]},
            {"id": "custom.width", "value": 100}]},
        {"matcher": {"id": "byRegexp", "options": "Downloading|Uploading"}, "properties": [
            {"id": "unit", "value": "bps"}, {"id": "decimals", "value": 1}]},
        {"matcher": {"id": "byName", "options": "Signal"}, "properties": [
            {"id": "unit", "value": "dBm"}, {"id": "decimals", "value": 0},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": steps("red", -80, "orange", -75, "text")}]},
        {"matcher": {"id": "byName", "options": "Experience"}, "properties": [
            {"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 0},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": steps("red", 0.6, "orange", 0.8, "text")}]},
        {"matcher": {"id": "byName", "options": "Connected for"}, "properties": [
            {"id": "unit", "value": "dtdurations"}, {"id": "decimals", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Port"}, "properties": [{"id": "custom.width", "value": 60}]},
    ])

# ---- traffic
d.timeseries("Top downloaders", "The 10 devices downloading the most at each moment.",
             [target(f"topk(10, sum by (name) (rate({DL}[$__rate_interval]))) * 8", "{{name}}")],
             unit="bps", legend="list")
d.timeseries("Top uploaders", "The 10 devices uploading the most at each moment.",
             [target(f"topk(10, sum by (name) (rate({UL}[$__rate_interval]))) * 8", "{{name}}")],
             unit="bps", legend="list")
d.timeseries("Devices by network", "Connected devices on each network over time.",
             [target(f"count by (network) (unpoller_client_uptime_seconds{{{C}}})", "{{network}}")],
             decimals=0, legend="list", stack=True, fill=40)
d.timeseries("Wi-Fi and wired", "Connected devices by connection type over time.",
             [target(f'count(unpoller_client_uptime_seconds{{{C}, wired="false"}})', "Wi-Fi"),
              target(f'count(unpoller_client_uptime_seconds{{{C}, wired="true"}})', "Wired", ref="B")],
             decimals=0, legend="list", stack=True, fill=40, overrides=[color("Wi-Fi", BLUE), color("Wired", AQUA)])

# ---- Wi-Fi quality
d.row("Wi-Fi quality")
d.timeseries("Weakest signal", "The 10 Wi-Fi devices with the weakest signal at each moment, in dBm. Dashed line "
             "at −75 dBm; below it a device is slow and drops.",
             [target(f"bottomk(10, max by (name) (unpoller_client_radio_signal_db{{{W}}}))", "{{name}}")],
             unit="dBm", min=None, decimals=0, legend="list", thresholds=steps("transparent", -75, "transparent"))
d.timeseries("Lowest experience", "The 10 Wi-Fi devices with the lowest UniFi satisfaction score at each moment. "
             "Dashed line at 80%.",
             [target(f"bottomk(10, max by (name) (unpoller_client_satisfaction_ratio{{{W}}} >= 0))", "{{name}}")],
             unit="percentunit", min=None, max=1, decimals=0, legend="list", thresholds=steps("transparent", 0.8, "orange"))
d.timeseries("Link speed", "Wi-Fi link speed towards each device, as negotiated with its access point. With all "
             "devices selected this shows the 10 slowest; pick devices with the Device selector to follow them.",
             [target(f"bottomk(10, max by (name) (unpoller_client_radio_transmit_rate_bps{{{W}}}))", "{{name}}")],
             unit="bps", legend="list")
d.timeseries("Moves between access points", "How often devices moved from one access point to another, per hour, "
             "for the 10 that moved most. Constant moving usually means the device sits between two access points "
             "with weak signal from both.",
             [target(f"topk(10, sum by (name) (increase(unpoller_client_roam_count_total{{{W}}}[1h])))", "{{name}}",
                     interval="1h")],
             decimals=0, legend="list", draw="bars", fill=70)
d.save()
