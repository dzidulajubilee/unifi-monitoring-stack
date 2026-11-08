"""Network reports for the UniFi monitoring stack.

Modules, roughly bottom to top:
  config    settings, paths, the report time zone, report sections
  periods   weeks / months / quarters / custom ranges, report file names and titles
  sources   Prometheus and Loki access, log queries
  collect   figures for each report section
  dns       optional DNS section: Pi-hole and Technitium figures, device naming
  text      formatting, summary sentences, needs-attention lines
  charts    SVG charts for the PDF
  links     links from the PDF into Grafana
  build     builds a report: full and summary PDFs from one data collection
  mail      email delivery
  notify    portal notifications
  schedule  which scheduled reports are due
  cli       command line (report.py)
  web       the reports portal (portal.py)
"""
