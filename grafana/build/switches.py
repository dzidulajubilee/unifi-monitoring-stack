from gf import Dashboard, steps, target

S = 'source=~"$Controller", site_name=~"$Site"'
D = S + ', name=~"$Switch"'
P = D + ', port_name=~"$Port"'

d = Dashboard(
    "UniFi Switches", "FsfxpWaZz",
    "Switch ports on UniFi switches and on gateways with built-in switch ports: link speed, traffic, errors, "
    "broadcast traffic and PoE power, plus the health of each switch.",
    ["unifi", "unifi-poller", "usw"], "unifi-switches.json")

d.var("Controller", "label_values(unpoller_device_port_port_speed_bps, source)")
d.var("Site", 'label_values(unpoller_device_port_port_speed_bps{source=~"$Controller"}, site_name)')
d.var("Switch", 'label_values(unpoller_device_port_port_speed_bps{source=~"$Controller", site_name=~"$Site"}, name)',
      label="Device", all_value=None)
d.var("Port", 'label_values(unpoller_device_port_port_speed_bps{source=~"$Controller", site_name=~"$Site", name=~"$Switch"}, port_name)')

# ---- now
d.stat("Ports up", "Ports with a link now.", f"count(unpoller_device_port_port_speed_bps{{{P}}} > 0)")
d.stat("Receiving now", "Traffic coming into the selected ports.",
       f"sum(rate(unpoller_device_port_receive_bytes_total{{{P}}}[5m])) * 8", unit="bps", decimals=1)
d.stat("Sending now", "Traffic going out of the selected ports.",
       f"sum(rate(unpoller_device_port_transmit_bytes_total{{{P}}}[5m])) * 8", unit="bps", decimals=1)
d.stat("Errors and drops", "Packets dropped or with errors per second on the selected ports (last 5 min). "
       "Should be zero.",
       f"sum(rate(unpoller_device_port_receive_errors_total{{{P}}}[5m]) + rate(unpoller_device_port_transmit_errors_total{{{P}}}[5m]) + "
       f"rate(unpoller_device_port_receive_dropped_total{{{P}}}[5m]) + rate(unpoller_device_port_transmit_dropped_total{{{P}}}[5m]))",
       unit="pps", decimals=1, thresholds=steps("text", 0.1, "orange", 10, "red"))
d.stat("PoE power", "Power supplied over PoE by the selected ports now.",
       f"sum(unpoller_device_port_poe_watts{{{P}}}) or vector(0)", unit="watt", decimals=1)
d.stat("Hottest", "Highest temperature reported by the selected devices.",
       f"max(unpoller_device_temperature_celsius{{{D}}})", unit="celsius", decimals=0,
       thresholds=steps("text", 70, "orange", 85, "red"))

# ---- ports table
pq = lambda expr, ref: target(f"max by (name, port_num, port_name) ({expr})", ref=ref, instant=True, fmt="table")
d.table("Ports", "Every port with its link speed, current traffic, errors and PoE draw. Ports without a link show "
        "a speed of 0.",
        [pq(f"unpoller_device_port_port_speed_bps{{{P}}}", "A"),
         pq(f"rate(unpoller_device_port_receive_bytes_total{{{P}}}[5m]) * 8", "B"),
         pq(f"rate(unpoller_device_port_transmit_bytes_total{{{P}}}[5m]) * 8", "C"),
         pq(f"rate(unpoller_device_port_receive_errors_total{{{P}}}[1h]) + rate(unpoller_device_port_transmit_errors_total{{{P}}}[1h])", "D"),
         pq(f"unpoller_device_port_poe_watts{{{P}}}", "E")],
        w=24, h=10,
        transformations=[{"id": "merge", "options": {}},
                         {"id": "convertFieldType", "options": {"conversions": [{"targetField": "port_num", "destinationType": "number"}]}},
                         {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                        "indexByName": {"name": 0, "port_num": 1, "port_name": 2, "Value #A": 3,
                                                                        "Value #B": 4, "Value #C": 5, "Value #D": 6, "Value #E": 7},
                                                        "renameByName": {"name": "Device", "port_num": "Port", "port_name": "Name",
                                                                         "Value #A": "Link speed", "Value #B": "Receiving",
                                                                         "Value #C": "Sending", "Value #D": "Errors (1 h)",
                                                                         "Value #E": "PoE"}}}],
        sort=("Port", False),
        overrides=[{"matcher": {"id": "byRegexp", "options": "Link speed|Receiving|Sending"},
                    "properties": [{"id": "unit", "value": "bps"}, {"id": "decimals", "value": 1}]},
                   {"matcher": {"id": "byName", "options": "Errors (1 h)"},
                    "properties": [{"id": "unit", "value": "pps"}, {"id": "decimals", "value": 2},
                                   {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                   {"id": "thresholds", "value": steps("text", 0.01, "orange")}]},
                   {"matcher": {"id": "byName", "options": "PoE"},
                    "properties": [{"id": "unit", "value": "watt"}, {"id": "decimals", "value": 1}]},
                   {"matcher": {"id": "byName", "options": "Port"}, "properties": [{"id": "custom.width", "value": 70}]}])

# ---- traffic
by_port = lambda expr: [target(expr, "{{name}} {{port_name}}")]
d.timeseries("Received per port", "Traffic coming into each port.",
             by_port(f"sum by (name, port_name) (rate(unpoller_device_port_receive_bytes_total{{{P}}}[$__rate_interval])) * 8"),
             unit="bps", legend="list")
d.timeseries("Sent per port", "Traffic going out of each port.",
             by_port(f"sum by (name, port_name) (rate(unpoller_device_port_transmit_bytes_total{{{P}}}[$__rate_interval])) * 8"),
             unit="bps", legend="list")
d.timeseries("Errors and drops per port", "Packets dropped or with errors. A steady rate on one port points to a "
             "bad cable, a failing device on that port or a duplex mismatch.",
             by_port(f"sum by (name, port_name) (rate(unpoller_device_port_receive_errors_total{{{P}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_transmit_errors_total{{{P}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_receive_dropped_total{{{P}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_transmit_dropped_total{{{P}}}[$__rate_interval]))"),
             unit="pps", legend="list")
d.timeseries("Broadcast and multicast per port", "Broadcast and multicast packets each port receives. A sudden "
             "jump can mean a network loop or a chatty device.",
             by_port(f"sum by (name, port_name) (rate(unpoller_device_port_receive_broadcast_total{{{P}}}[$__rate_interval]) + "
                     f"rate(unpoller_device_port_receive_multicast_total{{{P}}}[$__rate_interval]))"),
             unit="pps", legend="list")

# ---- PoE
d.row("PoE", collapsed=True)
d.timeseries("PoE power per port", "Power each port supplies to the device plugged into it.",
             by_port(f"max by (name, port_name) (unpoller_device_port_poe_watts{{{P}}})"), unit="watt", legend="list")
d.timeseries("PoE voltage and current", "Voltage and current per PoE port.",
             [target(f"max by (name, port_name) (unpoller_device_port_poe_volts{{{P}}})", "{{name}} {{port_name}} V"),
              target(f"max by (name, port_name) (unpoller_device_port_poe_amperes{{{P}}})", "{{name}} {{port_name}} A", ref="B")],
             legend="list",
             overrides=[{"matcher": {"id": "byRegexp", "options": ".* V$"}, "properties": [{"id": "unit", "value": "volt"}]},
                        {"matcher": {"id": "byRegexp", "options": ".* A$"}, "properties": [{"id": "unit", "value": "amp"}]}])

# ---- health
d.row("Device health", collapsed=True)
dev = lambda expr: [target(expr, "{{name}}")]
d.timeseries("CPU", "CPU use per device.", dev(f"max by (name) (unpoller_device_cpu_utilization_ratio{{{D}}})"),
             w=8, unit="percentunit", max=1, decimals=0, legend="list")
d.timeseries("Memory", "Memory use per device.", dev(f"max by (name) (unpoller_device_memory_utilization_ratio{{{D}}})"),
             w=8, unit="percentunit", max=1, decimals=0, legend="list")
d.timeseries("Temperature", "Hottest sensor per device.", dev(f"max by (name) (unpoller_device_temperature_celsius{{{D}}})"),
             w=8, unit="celsius", decimals=0, min=None, legend="list")
d.save()
