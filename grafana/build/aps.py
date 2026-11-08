from gf import AQUA, BLUE, ORANGE, Dashboard, color, steps, target

S = 'source=~"$Controller", site_name=~"$Site"'
A = S + ', name=~"$AP"'

d = Dashboard(
    "UniFi Access Points", "g5wFWqxZk",
    "Wi-Fi access points: clients, traffic, how busy each channel is, signal, retries and device health. "
    "The table at the top compares every access point at a glance; the charts show how that changed over time.",
    ["unifi", "unifi-poller", "uap"], "unifi-access-points.json")

d.var("Controller", "label_values(unpoller_device_info, source)")
d.var("Site", 'label_values(unpoller_device_info{source=~"$Controller"}, site_name)')
d.var("AP", 'label_values(unpoller_device_info{source=~"$Controller", site_name=~"$Site", type="uap"}, name)',
      label="Access point", all_value=None)

# ---- now
d.stat("Access points", "Access points selected.", f'count(unpoller_device_info{{{A}, type="uap"}})')
d.stat("Wi-Fi clients", "Devices connected over Wi-Fi now, staff and guests.",
       f"sum(unpoller_device_radio_stations{{{A}}})")
d.stat("Wi-Fi experience", "UniFi's satisfaction score for connected Wi-Fi devices, averaged over access points. "
       "Orange below 80%, red below 60%.",
       f"avg(unpoller_device_vap_satisfaction_ratio{{{A}}} >= 0)", unit="percentunit", decimals=0,
       thresholds=steps("red", 0.6, "orange", 0.8, "text"))
d.stat("Weak signal", "Wi-Fi devices with a signal below −75 dBm now. They're slow and drop often, usually "
       "because they're far from an access point.",
       f'count(unpoller_client_radio_signal_db{{{S}, ap_name=~"$AP"}} < -75) or vector(0)',
       thresholds=steps("text", 1, "orange"))
d.stat("Busiest channel", "The highest channel use on any selected access point and band now. Above 60% clients "
       "start to feel slow; above 80% it's congested.",
       f"max(unpoller_device_radio_channel_utilization_total_ratio{{{A}}})", unit="percentunit", decimals=0,
       thresholds=steps("text", 0.6, "orange", 0.8, "red"))
d.stat("Retries", "Share of Wi-Fi packets that had to be sent again, across selected access points (last 5 min). "
       "Above 15% points to interference or weak signal.",
       f"sum(rate(unpoller_device_vap_transmit_retries_total{{{A}}}[5m])) / "
       f"(sum(rate(unpoller_device_vap_transmit_packets_total{{{A}}}[5m])) + "
       f"sum(rate(unpoller_device_vap_transmit_retries_total{{{A}}}[5m])))",
       unit="percentunit", decimals=0, thresholds=steps("text", 0.15, "orange", 0.3, "red"))

# ---- comparison table
q = lambda expr, ref: target(f"max by (name) ({expr})", ref=ref, instant=True, fmt="table")
pct = lambda name: {"matcher": {"id": "byName", "options": name}, "properties": [
    {"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 0}]}
d.table(
    "Access points",
    "Every access point side by side. Channel use above 60% and retries above 15% are highlighted; "
    "average client signal below −75 dBm means devices are too far away or blocked by walls.",
    [target(f'max by (name, model, version, ip) (unpoller_device_info{{{A}, type="uap"}})', ref="A", instant=True, fmt="table"),
     q(f"sum by (name) (unpoller_device_radio_stations{{{A}}})", "B"),
     q(f'unpoller_device_radio_channel_utilization_total_ratio{{{A}, band="2.4"}}', "C"),
     q(f'unpoller_device_radio_channel_utilization_total_ratio{{{A}, band="5"}}', "D"),
     q(f"avg by (name) (unpoller_device_vap_satisfaction_ratio{{{A}}} >= 0)", "E"),
     q(f"avg by (name) (unpoller_device_vap_average_client_signal{{{A}}} < 0)", "F"),
     q(f"sum by (name) (rate(unpoller_device_vap_transmit_retries_total{{{A}}}[5m])) / "
       f"(sum by (name) (rate(unpoller_device_vap_transmit_packets_total{{{A}}}[5m])) + "
       f"sum by (name) (rate(unpoller_device_vap_transmit_retries_total{{{A}}}[5m])))", "G"),
     q(f"sum by (name) (rate(unpoller_device_bytes_total{{{A}, type=\"uap\"}}[5m])) * 8", "H"),
     q(f"unpoller_device_uptime_seconds{{{A}}}", "I")],
    w=24, h=11,
    transformations=[
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value #A": True},
            "indexByName": {"name": 0, "Value #B": 1, "Value #H": 2, "Value #C": 3, "Value #D": 4, "Value #E": 5,
                            "Value #F": 6, "Value #G": 7, "Value #I": 8, "model": 9, "version": 10, "ip": 11},
            "renameByName": {"name": "Access point", "Value #B": "Clients", "Value #H": "Traffic",
                             "Value #C": "2.4 GHz busy", "Value #D": "5 GHz busy", "Value #E": "Experience",
                             "Value #F": "Client signal", "Value #G": "Retries", "Value #I": "Up for",
                             "model": "Model", "version": "Firmware", "ip": "IP"}}}],
    sort=("Clients", True),
    overrides=[
        pct("2.4 GHz busy"), pct("5 GHz busy"), pct("Experience"), pct("Retries"),
        {"matcher": {"id": "byRegexp", "options": "2.4 GHz busy|5 GHz busy"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": steps("text", 0.6, "orange", 0.8, "red")}]},
        {"matcher": {"id": "byName", "options": "Retries"}, "properties": [
            {"id": "mappings", "value": [{"type": "special", "options": {"match": "nan", "result": {"text": "–"}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": steps("text", 0.15, "orange", 0.3, "red")}]},
        {"matcher": {"id": "byName", "options": "Experience"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": steps("red", 0.6, "orange", 0.8, "text")}]},
        {"matcher": {"id": "byName", "options": "Client signal"}, "properties": [
            {"id": "unit", "value": "dBm"}, {"id": "decimals", "value": 0},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": steps("red", -80, "orange", -75, "text")}]},
        {"matcher": {"id": "byName", "options": "Traffic"}, "properties": [
            {"id": "unit", "value": "bps"}, {"id": "decimals", "value": 1}]},
        {"matcher": {"id": "byName", "options": "Up for"}, "properties": [
            {"id": "unit", "value": "dtdurations"}, {"id": "decimals", "value": 0}]},
    ])

# ---- over time
by_ap = lambda expr: [target(expr, "{{name}}")]
d.timeseries("Clients per access point", "Connected Wi-Fi devices on each access point.",
             by_ap(f"sum by (name) (unpoller_device_radio_stations{{{A}}})"), decimals=0, legend="list")
d.timeseries("Traffic per access point", "Wi-Fi traffic through each access point, both directions.",
             by_ap(f"sum by (name) (rate(unpoller_device_bytes_total{{{A}, type=\"uap\"}}[$__rate_interval])) * 8"),
             unit="bps", legend="list")
for band, note in (("2.4", "2.4 GHz reaches further but has only three usable channels, so it fills up first."),
                   ("5", "5 GHz is faster with more channels, but reaches less far through walls.")):
    d.timeseries(f"{band} GHz channel use",
                 f"Share of time each access point's {band} GHz channel was busy, from its own and neighbouring "
                 f"traffic. Dashed line at 60%. {note}",
                 by_ap(f'max by (name) (unpoller_device_radio_channel_utilization_total_ratio{{{A}, band="{band}"}})'),
                 unit="percentunit", max=1, decimals=0, legend="list", thresholds=steps("transparent", 0.6, "orange"))
d.timeseries("Wi-Fi experience", "UniFi's satisfaction score for devices on each access point. Dashed line at 80%.",
             by_ap(f"avg by (name) (unpoller_device_vap_satisfaction_ratio{{{A}}} >= 0)"),
             unit="percentunit", max=1, min=None, decimals=0, legend="list", thresholds=steps("transparent", 0.8, "orange"))
d.timeseries("Average client signal", "Average signal of the devices on each access point, in dBm (closer to 0 is "
             "stronger). Dashed line at −75 dBm; below it devices are slow and drop.",
             by_ap(f"avg by (name) (unpoller_device_vap_average_client_signal{{{A}}} < 0)"),
             unit="dBm", min=None, decimals=0, legend="list", thresholds=steps("transparent", -75, "transparent"))
d.timeseries("Retries", "Share of Wi-Fi packets each access point had to send again. Dashed line at 15%.",
             by_ap(f"sum by (name) (rate(unpoller_device_vap_transmit_retries_total{{{A}}}[$__rate_interval])) / "
                   f"(sum by (name) (rate(unpoller_device_vap_transmit_packets_total{{{A}}}[$__rate_interval])) + "
                   f"sum by (name) (rate(unpoller_device_vap_transmit_retries_total{{{A}}}[$__rate_interval])))"),
             unit="percentunit", decimals=0, legend="list", thresholds=steps("transparent", 0.15, "orange"))
d.timeseries("Clients by band", "How many devices use each band across the selected access points. More on 5 GHz "
             "is better.",
             [target(f'sum by (band) (unpoller_device_radio_stations{{{A}}})', "{{band}} GHz")],
             decimals=0, legend="list", stack=True, fill=40,
             overrides=[color("2.4 GHz", ORANGE), color("5 GHz", BLUE), color("6 GHz", AQUA)])

# ---- networks
d.row("Wi-Fi networks", collapsed=True)
d.bars("Clients per Wi-Fi network", "Devices on each Wi-Fi network (SSID) now, across selected access points.",
       f"sort_desc(sum by (essid) (unpoller_device_vap_satisfaction_stations{{{A}}}))", "{{essid}}", w=12, h=8)
d.table("Wi-Fi networks by access point", "Each Wi-Fi network on each access point and band, with its devices "
        "and their experience.",
        [target(f"sum by (name, essid, band) (unpoller_device_vap_satisfaction_stations{{{A}}})", ref="A", instant=True, fmt="table"),
         target(f"avg by (name, essid, band) (unpoller_device_vap_satisfaction_ratio{{{A}}} >= 0)", ref="B", instant=True, fmt="table")],
        w=12, h=8,
        transformations=[{"id": "merge", "options": {}},
                         {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                        "indexByName": {"essid": 0, "name": 1, "band": 2, "Value #A": 3, "Value #B": 4},
                                                        "renameByName": {"essid": "Network", "name": "Access point", "band": "Band",
                                                                         "Value #A": "Clients", "Value #B": "Experience"}}}],
        sort=("Clients", True), overrides=[pct("Experience")])

# ---- radios
d.row("Radios", collapsed=True)
d.table("Channels and power", "Current channel and transmit power of each radio.",
        [target(f"max by (name, band) (unpoller_device_radio_channel{{{A}}})", ref="A", instant=True, fmt="table"),
         target(f"max by (name, band) (unpoller_device_radio_transmit_power{{{A}}})", ref="B", instant=True, fmt="table"),
         target(f"max by (name, band) (unpoller_device_radio_stations{{{A}}})", ref="C", instant=True, fmt="table")],
        w=12, h=18,
        transformations=[{"id": "merge", "options": {}},
                         {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                        "indexByName": {"name": 0, "band": 1, "Value #A": 2, "Value #B": 3, "Value #C": 4},
                                                        "renameByName": {"name": "Access point", "band": "Band", "Value #A": "Channel",
                                                                         "Value #B": "Power", "Value #C": "Clients"}}}],
        sort=("Access point", False),
        overrides=[{"matcher": {"id": "byName", "options": "Power"}, "properties": [{"id": "unit", "value": "dBm"}]}])
d.timeseries("Channel", "Channel each radio used over time. Frequent changes mean the access point keeps finding "
             "interference.",
             [target(f"max by (name, band) (unpoller_device_radio_channel{{{A}}})", "{{name}} {{band}} GHz")],
             w=12, h=18, decimals=0, min=None, legend="list", interp="stepAfter")

# ---- health
d.row("Device health", collapsed=True)
d.timeseries("CPU", "CPU use of each access point.",
             by_ap(f'max by (name) (unpoller_device_cpu_utilization_ratio{{{A}, type="uap"}})'),
             w=8, unit="percentunit", max=1, decimals=0, legend="list")
d.timeseries("Memory", "Memory use of each access point.",
             by_ap(f'max by (name) (unpoller_device_memory_utilization_ratio{{{A}, type="uap"}})'),
             w=8, unit="percentunit", max=1, decimals=0, legend="list")
d.timeseries("Uptime", "Time since each access point last started. A drop to zero is a restart.",
             by_ap(f'max by (name) (unpoller_device_uptime_seconds{{{A}, type="uap"}})'),
             w=8, unit="dtdurations", legend="list")
d.timeseries("Drops and errors", "Wi-Fi packets dropped or received with errors, per access point. Should stay "
             "near zero.",
             by_ap(f"sum by (name) (rate(unpoller_device_vap_receive_dropped_total{{{A}}}[$__rate_interval]) + "
                   f"rate(unpoller_device_vap_transmit_dropped_total{{{A}}}[$__rate_interval]) + "
                   f"rate(unpoller_device_vap_receive_errors_total{{{A}}}[$__rate_interval]) + "
                   f"rate(unpoller_device_vap_transmit_errors_total{{{A}}}[$__rate_interval]))"),
             w=24, unit="pps", legend="list")
d.save()
