from gf import Dashboard, target

S = 'source=~"$Controller", site_name=~"$Site"'
P = S + ', name=~"$PDU"'

d = Dashboard(
    "UniFi PDU", "feeyvd8ay2ku8b",
    "UniFi power distribution units (USP-PDU): power, current and voltage per outlet. Empty if the site has no "
    "UniFi PDU.",
    ["unifi", "unifi-poller", "pdu"], "unifi-pdu.json")

d.var("Controller", "label_values(unpoller_device_outlet_outlet_power, source)")
d.var("Site", 'label_values(unpoller_device_outlet_outlet_power{source=~"$Controller"}, site_name)')
d.var("PDU", 'label_values(unpoller_device_outlet_outlet_power{source=~"$Controller", site_name=~"$Site"}, name)', all_value=None)

d.stat("Power now", "Total power drawn through the selected PDUs.", f"sum(unpoller_device_outlet_outlet_power{{{P}}})",
       unit="watt", decimals=0)
d.stat("Current now", "Total current through the selected PDUs.", f"sum(unpoller_device_outlet_outlet_current{{{P}}})",
       unit="amp", decimals=2)
d.stat("Voltage", "Supply voltage.", f"avg(unpoller_device_outlet_outlet_voltage{{{P}}})", unit="volt", decimals=0)
d.stat("Peak power", "Highest total power in the selected time range.",
       f"max_over_time(sum(unpoller_device_outlet_outlet_power{{{P}}})[$__range:1m])", unit="watt", decimals=0)
d.stat("Energy used", "Energy used in the selected time range, from average power.",
       f"avg_over_time(sum(unpoller_device_outlet_outlet_power{{{P}}})[$__range:1m]) * $__range_s / 3600",
       unit="watth", decimals=0)
d.stat("Power factor", "Average power factor across outlets (1 is ideal).",
       f"avg(unpoller_device_outlet_outlet_power_factor{{{P}}} > 0)", decimals=2)

oq = lambda expr, ref: target(f"max by (name, outlet_index, outlet_name) ({expr})", ref=ref, instant=True, fmt="table")
d.table("Outlets", "Every outlet with its power, current, voltage and power factor now.",
        [oq(f"unpoller_device_outlet_outlet_power{{{P}}}", "A"), oq(f"unpoller_device_outlet_outlet_current{{{P}}}", "B"),
         oq(f"unpoller_device_outlet_outlet_voltage{{{P}}}", "C"), oq(f"unpoller_device_outlet_outlet_power_factor{{{P}}}", "D")],
        w=24, h=10,
        transformations=[{"id": "merge", "options": {}},
                         {"id": "convertFieldType", "options": {"conversions": [{"targetField": "outlet_index", "destinationType": "number"}]}},
                         {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                        "indexByName": {"name": 0, "outlet_index": 1, "outlet_name": 2},
                                                        "renameByName": {"name": "PDU", "outlet_index": "Outlet", "outlet_name": "Name",
                                                                         "Value #A": "Power", "Value #B": "Current",
                                                                         "Value #C": "Voltage", "Value #D": "Power factor"}}}],
        sort=("Outlet", False),
        overrides=[{"matcher": {"id": "byName", "options": "Power"}, "properties": [{"id": "unit", "value": "watt"}, {"id": "decimals", "value": 1}]},
                   {"matcher": {"id": "byName", "options": "Current"}, "properties": [{"id": "unit", "value": "amp"}, {"id": "decimals", "value": 2}]},
                   {"matcher": {"id": "byName", "options": "Voltage"}, "properties": [{"id": "unit", "value": "volt"}, {"id": "decimals", "value": 0}]},
                   {"matcher": {"id": "byName", "options": "Power factor"}, "properties": [{"id": "decimals", "value": 2}]}])
d.timeseries("Power per PDU", "Total power through each PDU.",
             [target(f"sum by (name) (unpoller_device_outlet_outlet_power{{{P}}})", "{{name}}")], unit="watt", legend="list")
d.timeseries("Power per outlet", "Power drawn by each outlet.",
             [target(f"max by (name, outlet_name) (unpoller_device_outlet_outlet_power{{{P}}})", "{{outlet_name}}")],
             unit="watt", legend="list")
d.timeseries("Current per outlet", "Current drawn by each outlet.",
             [target(f"max by (name, outlet_name) (unpoller_device_outlet_outlet_current{{{P}}})", "{{outlet_name}}")],
             unit="amp", legend="list")
d.timeseries("Voltage", "Supply voltage per PDU.",
             [target(f"avg by (name) (unpoller_device_outlet_outlet_voltage{{{P}}})", "{{name}}")],
             unit="volt", min=None, legend="list")
d.save()
