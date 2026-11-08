from gf import BLUE, DOWN, GREY, ORANGE, UP, Dashboard, color, mirror, steps, target

S = 'source=~"$Controller", site_name=~"$Site"'
G = S + ', name=~"$Gateway"'
GW_TYPES = "udm|usg|uxg|ugw|ucg|udr|uxgpro"

d = Dashboard(
    "UniFi Gateway", "4Yo8IZ-Wk",
    "Internet lines, throughput, latency and the health of the UniFi gateway (UDM, UCG, USG, UXG). "
    "Internet traffic comes from UniFi's WAN rate and the gateway's per-port counters.",
    ["unifi", "unifi-poller", "usg"], "unifi-gateway.json")

d.var("Controller", "label_values(unpoller_device_info, source)")
d.var("Site", 'label_values(unpoller_device_info{source=~"$Controller"}, site_name)')
d.var("Gateway", f'label_values(unpoller_device_info{{source=~"$Controller", site_name=~"$Site", type=~"{GW_TYPES}"}}, name)',
      all_value=None)

# ---- now
d.stat("Uptime", "Time since the gateway last started.", f"max(unpoller_device_uptime_seconds{{{G}}})",
       unit="dtdurations", w=4)
d.stat("CPU", "Gateway CPU use now. Orange above 70%, red above 90%.",
       f"max(unpoller_device_cpu_utilization_ratio{{{G}}})", unit="percentunit", decimals=0,
       thresholds=steps("text", 0.7, "orange", 0.9, "red"), w=4)
d.stat("Memory", "Gateway memory use now. Orange above 80%, red above 90%. A slow climb over days is worth "
       "watching; it usually resets after a firmware update or restart.",
       f"max(unpoller_device_memory_utilization_ratio{{{G}}})", unit="percentunit", decimals=0,
       thresholds=steps("text", 0.8, "orange", 0.9, "red"), w=4)
d.stat("CPU temperature", "Hottest CPU sensor. Orange above 75 °C, red above 85 °C.",
       f'max(unpoller_device_temperature_celsius{{{G}, temp_area="CPU"}})', unit="celsius", decimals=0,
       thresholds=steps("text", 75, "orange", 85, "red"), w=4)
d.stat("Internet latency", "UniFi's latency check to the internet, now. Orange above 100 ms, red above 200 ms.",
       f'max(unpoller_site_latency_seconds{{{S}, subsystem="www"}}) * 1000', unit="ms", decimals=0,
       thresholds=steps("text", 100, "orange", 200, "red"), w=4)
d.stat("Clients online", "Devices connected to the site now, wired and wireless.",
       f"count(unpoller_client_uptime_seconds{{{S}}})", w=4)

# ---- internet lines: state, availability, last speed test, one row per line
wan = lambda x, lbl: f'label_replace({x}, "wan", "$1", "{lbl}", "(.*)")'
d.table(
    "Internet lines",
    "Each internet line (WAN). State is UniFi's view now: Active carries traffic, Backup waits for a failover. "
    "Up is UniFi's availability figure for the line (standby lines have none). Speed test columns show the most "
    "recent test UniFi ran on that line.",
    [target(f'max by (wan, wan_interface, state) ({wan(f"unpoller_wan_interface_state{{{S}}}", "wan_networkgroup")})',
            ref="A", instant=True, fmt="table"),
     target(f'max by (wan) ({wan(f"unpoller_wan_uptime_percentage{{{S}}}", "wan_networkgroup")})',
            ref="B", instant=True, fmt="table"),
     target(f'max by (wan) ({wan(f"unpoller_speedtest_download_mbps{{{S}}}", "wan_group")})',
            ref="C", instant=True, fmt="table"),
     target(f'max by (wan) ({wan(f"unpoller_speedtest_upload_mbps{{{S}}}", "wan_group")})',
            ref="D", instant=True, fmt="table"),
     target(f'max by (wan) ({wan(f"unpoller_speedtest_latency_ms{{{S}}}", "wan_group")})',
            ref="E", instant=True, fmt="table"),
     # UnPoller reports this in hundredths of a second; * 10 gives milliseconds for Grafana
     target(f'max by (wan) ({wan(f"unpoller_speedtest_timestamp_seconds{{{S}}}", "wan_group")})',
            ref="F", instant=True, fmt="table")],
    w=24, h=6,
    transformations=[
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value #A": True},
            "indexByName": {"wan_interface": 0, "wan": 1, "state": 2, "Value #B": 3, "Value #C": 4,
                            "Value #D": 5, "Value #E": 6, "Value #F": 7},
            "renameByName": {"wan_interface": "Line", "wan": "WAN", "state": "State", "Value #B": "Up",
                             "Value #C": "Download (test)", "Value #D": "Upload (test)",
                             "Value #E": "Latency (test)", "Value #F": "Tested"}}}],
    sort=("WAN", False),
    overrides=[
        {"matcher": {"id": "byName", "options": "State"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "mappings", "value": [{"type": "value", "options": {
                "ACTIVE": {"text": "Active", "color": "green", "index": 0},
                "BACKUP": {"text": "Backup", "color": "text", "index": 1},
                "DOWN": {"text": "Down", "color": "red", "index": 2}}}]}]},
        {"matcher": {"id": "byName", "options": "Up"}, "properties": [
            {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 2},
            {"id": "mappings", "value": [{"type": "range", "options": {"from": -100, "to": -0.001,
                                                                      "result": {"text": "–", "index": 0}}}]}]},
        {"matcher": {"id": "byRegexp", "options": "Download \\(test\\)|Upload \\(test\\)"}, "properties": [
            {"id": "unit", "value": "Mbits"}, {"id": "decimals", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Latency (test)"}, "properties": [
            {"id": "unit", "value": "ms"}, {"id": "decimals", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Tested"}, "properties": [{"id": "unit", "value": "dateTimeFromNow"}]},
    ])

# ---- traffic
d.timeseries(
    "Internet throughput",
    "Download above the line, upload below it, across all internet lines. From UniFi's WAN rate, which is "
    "reliable on UniFi OS gateways where the WAN byte counters are not.",
    [target(f'max(unpoller_site_receive_rate_bytes{{{S}, subsystem="wan"}}) * 8', "Download"),
     target(f'max(unpoller_site_transmit_rate_bytes{{{S}, subsystem="wan"}}) * 8', "Upload", ref="B")],
    w=24, h=9, unit="bps", legend="table", calcs=("mean", "max", "lastNotNull"),
    overrides=[color("Download", DOWN), color("Upload", UP), mirror("Upload")])

d.timeseries(
    "Traffic by internet line",
    "Download per internet line, from the gateway port each line is plugged into. Shows which line is carrying "
    "traffic and when a failover moved it. Needs the gateway ports to have the same names as the internet lines; "
    "if this panel is empty, see Traffic by port under Ports.",
    # only ports named after an internet line (UniFi names WAN ports after the line by default)
    [target(f'sum by (port_name) (rate(unpoller_device_port_receive_bytes_total{{{G}}}[$__rate_interval])) * 8 '
            f'and on (port_name) label_replace(max by (wan_interface) (unpoller_wan_interface_state{{{S}}}), '
            f'"port_name", "$1", "wan_interface", "(.*)")', "{{port_name}}")],
    w=12, h=8, unit="bps", legend="list")
d.timeseries(
    "Internet latency",
    "UniFi's latency check to the internet. Dashed line at 100 ms; above it, calls and video start to suffer.",
    [target(f'max(unpoller_site_latency_seconds{{{S}, subsystem="www"}}) * 1000', "Latency")],
    w=12, h=8, unit="ms", decimals=0, legend="hidden", palette=False,
    thresholds=steps("transparent", 100, "orange"))

# ---- health
d.row("Gateway health")
d.timeseries("CPU and memory", "Share of CPU and memory in use.",
             [target(f"max(unpoller_device_cpu_utilization_ratio{{{G}}})", "CPU"),
              target(f"max(unpoller_device_memory_utilization_ratio{{{G}}})", "Memory", ref="B")],
             unit="percentunit", max=1, decimals=0, legend="list",
             overrides=[color("CPU", BLUE), color("Memory", ORANGE)])
d.timeseries("Temperatures", "Each temperature sensor in the gateway: CPU, board, storage.",
             [target(f"max by (temp_area) (unpoller_device_temperature_celsius{{{G}}})", "{{temp_area}}")],
             unit="celsius", decimals=0, min=None, legend="list")
d.timeseries("Load average", "Processes waiting for CPU, averaged over 1, 5 and 15 minutes.",
             [target(f"max(unpoller_device_load_average_1{{{G}}})", "1 min"),
              target(f"max(unpoller_device_load_average_5{{{G}}})", "5 min", ref="B"),
              target(f"max(unpoller_device_load_average_15{{{G}}})", "15 min", ref="C")],
             decimals=2, legend="list", overrides=[color("1 min", GREY), color("5 min", BLUE), color("15 min", ORANGE)])
d.timeseries("LAN throughput", "Traffic between the gateway and your local networks. Received above the line, "
             "sent below it.",
             [target(f"sum(rate(unpoller_device_lan_receive_bytes_total{{{G}}}[$__rate_interval])) * 8", "Received"),
              target(f"sum(rate(unpoller_device_lan_transmit_bytes_total{{{G}}}[$__rate_interval])) * 8", "Sent", ref="B")],
             unit="bps", legend="list", overrides=[color("Received", DOWN), color("Sent", UP), mirror("Sent")])

# ---- ports
d.row("Ports and fibre modules", collapsed=True)
d.table("Ports", "Every gateway port with its link speed and current traffic.",
        [target(f"max by (port_num, port_name) (unpoller_device_port_port_speed_bps{{{G}}})", ref="A", instant=True, fmt="table"),
         target(f"sum by (port_num, port_name) (rate(unpoller_device_port_receive_bytes_total{{{G}}}[5m])) * 8", ref="B", instant=True, fmt="table"),
         target(f"sum by (port_num, port_name) (rate(unpoller_device_port_transmit_bytes_total{{{G}}}[5m])) * 8", ref="C", instant=True, fmt="table")],
        w=24, h=8,
        transformations=[{"id": "merge", "options": {}},
                         {"id": "convertFieldType", "options": {"conversions": [{"targetField": "port_num", "destinationType": "number"}]}},
                         {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                        "indexByName": {"port_num": 0, "port_name": 1, "Value #A": 2, "Value #B": 3, "Value #C": 4},
                                                        "renameByName": {"port_num": "Port", "port_name": "Name", "Value #A": "Link speed",
                                                                         "Value #B": "Receiving", "Value #C": "Sending"}}}],
        sort=("Port", False),
        overrides=[{"matcher": {"id": "byRegexp", "options": "Link speed|Receiving|Sending"},
                    "properties": [{"id": "unit", "value": "bps"}, {"id": "decimals", "value": 1}]},
                   {"matcher": {"id": "byName", "options": "Port"}, "properties": [{"id": "custom.width", "value": 70}]}])
d.timeseries("Traffic by port", "Received traffic per port.",
             [target(f"sum by (port_name) (rate(unpoller_device_port_receive_bytes_total{{{G}}}[$__rate_interval])) * 8", "{{port_name}}")],
             w=12, unit="bps", legend="list")
d.timeseries("Port errors and drops", "Packets dropped or received with errors, per port. Should stay at zero; a "
             "steady rate points to a bad cable, fibre or module.",
             [target(f"sum by (port_name) (rate(unpoller_device_port_receive_errors_total{{{G}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_transmit_errors_total{{{G}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_receive_dropped_total{{{G}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_transmit_dropped_total{{{G}}}[$__rate_interval]))", "{{port_name}}")],
             w=12, unit="pps", legend="list")
d.timeseries("Fibre module signal", "Optical power the SFP modules receive and send, in dBm. A falling receive level "
             "means a dirty or damaged fibre, or a problem at the provider's end.",
             [target(f"max by (port_name) (unpoller_device_port_sfp_rx_power{{{G}}})", "{{port_name}} receive"),
              target(f"max by (port_name) (unpoller_device_port_sfp_tx_power{{{G}}})", "{{port_name}} send", ref="B")],
             w=12, unit="dBm", decimals=1, min=None, legend="list")
d.timeseries("Fibre module temperature", "Temperature of each SFP module. Most are rated to 70–85 °C.",
             [target(f"max by (port_name) (unpoller_device_port_sfp_temperature{{{G}}})", "{{port_name}}")],
             w=12, unit="celsius", decimals=0, min=None, legend="list")

# ---- device
d.row("Device", collapsed=True)
d.table("Gateway details", "Model, firmware and addresses.",
        [target(f"max by (name, model, version, ip, mac, serial) (unpoller_device_info{{{G}}})", ref="A", instant=True, fmt="table")],
        w=24, h=4,
        transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True},
                                                        "indexByName": {"name": 0, "model": 1, "version": 2, "ip": 3, "mac": 4, "serial": 5},
                                                        "renameByName": {"name": "Name", "model": "Model", "version": "Firmware",
                                                                         "ip": "Public IP", "mac": "MAC", "serial": "Serial"}}}])

d.save()
