# Data notes

Things learned about UnPoller's data that the reports and dashboards rely on. Check these before changing a query.

- **WAN byte counters**: on UniFi OS gateways `unpoller_device_wan_*_bytes_total` barely move. Internet traffic
  comes from `unpoller_site_receive_rate_bytes{subsystem="wan"}` (and `transmit`), averaged over time. Per-port
  counters (`unpoller_device_port_*_bytes_total`) are fine and give traffic per internet line.
- **Client traffic direction**: counted from the access point's side. `unpoller_client_transmit_bytes_total` is
  what the device downloads; `receive` is its upload.
- **DPI** (`unpoller_client_dpi_*`): each device's traffic per application over the **last 24 hours**, a rolling
  figure that rises and falls. Never use `increase()` or `rate()` on it. Reports average it into shares;
  the dashboard shows the last 24 hours. `mac="TOTAL"` series are site totals by category.
- **Console settings** (`unpoller_controller_*`): always 0 on UniFi OS consoles reached with an API key,
  uptime included. Not used.
- **Speed test time** (`unpoller_speedtest_timestamp_seconds`) is in milliseconds despite its name (UnPoller
  2.x with UniFi Network 10); older versions sent hundredths of a second.
- **Wi-Fi retries** (`unpoller_device_vap_transmit_retries_total`) count every resend attempt. Show them as a share
  of everything sent, `retries / (packets + retries)`; dividing by packets alone passes 100% on a quiet access
  point.
- **Logs** come from the UniFi System Log through UnPoller into Loki (`application="unifi_system_log"`), with
  categories such as SECURITY, AUDIT, INTERNET_AND_WAN, CLIENT_DEVICES and UNIFI_DEVICES. The JSON parameters
  (e.g. `parameters.WAN_NAME`, `parameters.DURATION.id` in seconds) carry the details.
- **Retention**: Prometheus and Loki keep 400 days, so trends and quarterly reports fill in as history builds up.
  Periods without enough data show as missing rather than as low values.
