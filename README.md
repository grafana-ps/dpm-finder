🚧 **Work in Progress** 🚧  

# dpm-finder

This repository contains a Python script designed to identify metrics in Prometheus that exceed a specified rate. It's particularly useful for detecting metrics with high data points per minute (DPM) rates, which can be indicative of issues or important trends.

## Overview

The `dpm-finder` script retrieves a list of all metrics from a Prometheus instance, calculates their data points per minute (DPM) rate using PromQL, and identifies metrics whose DPM exceeds a threshold. The script writes these high-DPM metrics to a text file for further analysis.

## Functionality

The script does the following:

1.  **Retrieves all metrics** from a Prometheus instance using the `/api/v1/label/__name__/values` endpoint.
2.  **Calculates DPM rate** for each metric using a PromQL query: `count_over_time({metric_name}[5m])/5`.
3.  **Filters metrics** based on a DPM threshold (metrics with DPM > 1 by default).
4.  **Writes results** to a text file named `metric_rates.txt`, listing metrics that exceed the threshold.

## How To
1.  ## Create .env with the following variables.  Please note the prometheus endpoint should not have anything after .net
```bash
PROMETHEUS_ENDPOINT=""
PROMETHEUS_USERNAME=""
PROMETHEUS_API_KEY=""
```

2. Install all libraries from requirements.txt

## Support

This project is not actively supported by Grafana Labs.
