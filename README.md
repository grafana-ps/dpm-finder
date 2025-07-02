🚧 **Work in Progress** 🚧  

# dpm-finder

This repository contains a Python script designed to identify metrics in Prometheus that exceed a specified rate. It's particularly useful for detecting metrics with high data points per minute (DPM) rates, which can be indicative of issues or important trends.

## Overview

The `dpm-finder` script retrieves a list of all metrics from a Prometheus instance, calculates their data points per minute (DPM) rate using PromQL, and identifies metrics whose DPM exceeds a threshold. The script writes these high-DPM metrics to a file for further analysis.

## Functionality

The script does the following:

1.  **Retrieves all metrics** from a Prometheus instance using the `/api/v1/label/__name__/values` endpoint.
2.  **Filters metrics** automatically to exclude:
    - Metrics ending with `_count`, `_bucket`, or `_sum` (histogram/summary components)
    - Metrics beginning with `grafana_` (Grafana internal metrics)
    - Metrics with aggregation rules defined in the cluster
3.  **Calculates DPM rate** for each metric using a PromQL query: `count_over_time({metric_name}[5m])/5`.
4.  **Filters results** based on a DPM threshold (metrics with DPM > 1 by default).
5.  **Outputs results** in various formats (CSV, JSON, text, or Prometheus exposition format).
6.  **Provides detailed logging** with configurable verbosity levels for monitoring progress and debugging.

## How To
1.  ## Create .env with the following variables.  

Please note the prometheus endpoint should not have anything after .net. 

See .env_example 

```bash
PROMETHEUS_ENDPOINT=""
PROMETHEUS_USERNAME=""
PROMETHEUS_API_KEY=""
```

2. Install all libraries from requirements.txt

```bash
python3 -m venv venv
source ./venv/bin/activate
python3 -m pip install -r requirements.txt 
```

3. Run the script

``` bash
./dpm-finder.py -t 4 -f csv 
```

## Logging and Verbosity

The script includes comprehensive logging with three verbosity levels:

- **Normal mode**: Shows informational messages about progress and results
- **Quiet mode** (`-q`): Suppresses all output except errors and file writing
- **Verbose mode** (`-v`): Shows detailed debug information including individual metric processing

All log messages include timestamps and severity levels for better monitoring and debugging.

## Usage

usage: dpm-finder.py [-h] [-f {csv,text,txt,json,prom}] [-m MIN_DPM] [-q] [-v] [-t THREADS]

        DPM Finder - A tool to calculate Data Points per Minute (DPM) for Prometheus metrics.
        This script connects to a Prometheus instance, retrieves all metric names,
        calculates their DPM, and outputs the results either in CSV or text format.
        Results are filtered to show only metrics above a specified DPM threshold.
        

optional arguments:
  -h, --help            Show this help message and exit
  -f {csv,text,txt,json,prom}, --format {csv,text,txt,json,prom}
                        Output format (default: csv). Note: "text" and "txt" are synonyms
  -m MIN_DPM, --min-dpm MIN_DPM
                        Minimum DPM threshold to show metrics (default: 1.0)
  -q, --quiet           Suppress progress output and only write results to file
  -v, --verbose         Enable debug logging for detailed output
  -t THREADS, --threads THREADS
                        Number of concurrent threads for processing metrics (minimum: 1, default: 10)

## Filtered Metrics

The script automatically excludes certain metric types to focus on meaningful data:

- **Histogram/Summary components**: Metrics ending with `_count`, `_bucket`, or `_sum`
- **Grafana internal metrics**: Metrics beginning with `grafana_`
- **Aggregated metrics**: Metrics that have aggregation rules defined in the Prometheus cluster

This filtering helps reduce noise and focuses analysis on core application and infrastructure metrics.

## Notes

- Adjust threads upwards to utilize more parallelism for potentially faster run times
- Use `-v` for verbose debugging when troubleshooting connection or processing issues
- Use `-q` for silent operation when running in automated scripts or CI/CD pipelines
- File format "prom" will output Prometheus exposition style metrics that could be forwarded using Alloy's prometheus.exporter.unix "textfile" collector

## Error Handling

The script includes robust error handling with:
- Connection retry logic with exponential backoff for network issues
- Comprehensive error logging for debugging failed requests
- Graceful handling of malformed responses
- Thread-safe error reporting for concurrent processing

## Support

This project is not actively supported by Grafana Labs.
