---
name: analyze
description: Analyze Prometheus metric DPM rates for a Grafana Cloud stack. Identifies which metrics drive high data points per minute with per-label breakdown. Optionally uses gcx for stack discovery. Usage: /grafana-dpm-finder:analyze [stack-name-or-options]
context: fork
agent: grafana-dpm-finder
disable-model-invocation: false
---

Analyze DPM rates for the specified Grafana Cloud stack: $ARGUMENTS

Instructions:
1. If $ARGUMENTS contains a stack name, use it for gcx lookup or .env configuration.
2. If $ARGUMENTS is empty, check for gcx availability and present the active stack context for confirmation.
3. If no gcx and no arguments, check if a .env file exists in the dpm-finder repo with valid credentials.
4. Follow the complete workflow: discover stack -> setup environment -> run analysis -> present results.
5. Always confirm the target stack with the user before running the analysis.
6. Present results as a formatted table sorted by DPM descending.
