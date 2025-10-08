🚧 **Work in Progress** 🚧  

# dpm-finder

This repository contains a Python script designed to identify metrics in Prometheus that exceed a specified rate. It's particularly useful for detecting metrics with high data points per minute (DPM) rates, which can be indicative of issues or important trends.

## Overview

The `dpm-finder` script retrieves a list of all metrics from a Prometheus instance, calculates their data points per minute (DPM) rate using PromQL, and identifies metrics whose DPM exceeds a threshold. The script writes these high-DPM metrics to a text file for further analysis.

## Functionality

The script does the following:

1.  **Retrieves all metrics** from a Prometheus instance using the `/api/v1/label/__name__/values` endpoint.
2.  **Calculates DPM rate** for each metric using a PromQL query: `count_over_time({metric_name}[5m])/5`.
3.  **Counts active series** for each metric using a PromQL query: `count by (__name__) ({metric_name})`.
4.  **Calculates impact score** by multiplying DPM by the number of active series to quantify the true impact on Prometheus.
5.  **Writes results** to a text file named `metric_rates.txt` with the format: `METRIC_NAME DPM ACTIVE_SERIES TOTAL_ACTIVE_SERIES`.

## Output Format

The script generates a tab-separated file with the following columns:

- **METRIC_NAME**: The name of the metric
- **DPM**: Data Points per Minute (total data points divided by 5 minutes)
- **ACTIVE_SERIES**: Number of unique time series for this metric
- **TOTAL_ACTIVE_SERIES**: Impact score calculated as `DPM × ACTIVE_SERIES`

This format helps identify metrics that have the highest impact on your Prometheus instance, considering both data volume and cardinality.

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
