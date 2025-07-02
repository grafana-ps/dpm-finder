🚧 **Work in Progress** 🚧  

# dpm-finder

This repository contains a Python script designed to identify metrics in Prometheus that exceed a specified rate. It's particularly useful for detecting metrics with high data points per minute (DPM) rates, which can be indicative of issues or important trends.

The script can run in two modes:
- **One-time execution**: Calculate DPM and output results to files
- **Prometheus exporter**: Run as a server that exposes DPM metrics for Prometheus scraping

## Overview

The `dpm-finder` script retrieves a list of all metrics from a Prometheus instance, calculates their data points per minute (DPM) rate using PromQL, and identifies metrics whose DPM exceeds a threshold. Results can be output to files or served via a Prometheus-compatible HTTP endpoint.

## Functionality

The script does the following:

1.  **Retrieves all metrics** from a Prometheus instance using the `/api/v1/label/__name__/values` endpoint.
2.  **Filters metrics** automatically to exclude:
    - Metrics ending with `_count`, `_bucket`, or `_sum` (histogram/summary components)
    - Metrics beginning with `grafana_` (Grafana internal metrics)
    - Metrics with aggregation rules defined in the cluster
3.  **Calculates DPM rate** for each metric using a PromQL query: `count_over_time({metric_name}[5m])/5`.
4.  **Filters results** based on a DPM threshold (metrics with DPM > 1 by default).
5.  **Outputs results** in various formats:
    - **One-time mode**: CSV, JSON, text, or Prometheus exposition format files
    - **Exporter mode**: Live Prometheus metrics endpoint at `/metrics`
6.  **Provides detailed logging** with configurable verbosity levels for monitoring progress and debugging.

## How To

### 1. Create .env with the following variables

Please note the prometheus endpoint should not have anything after .net. 

See .env_example 

```bash
PROMETHEUS_ENDPOINT=""
PROMETHEUS_USERNAME=""
PROMETHEUS_API_KEY=""
```

### 2. Install all libraries from requirements.txt

```bash
python3 -m venv venv
source ./venv/bin/activate
python3 -m pip install -r requirements.txt 
```

### 3. Run the script

**One-time execution (traditional mode):**
``` bash
./dpm-finder.py -t 4 -f csv 
```

**Prometheus exporter mode:**
``` bash
./dpm-finder.py --exporter --port 8000 --update-interval 3600
```

## Prometheus Exporter Mode

The script can run as a Prometheus exporter, serving metrics at an HTTP endpoint for Prometheus to scrape. In this mode:

- **Automatic updates**: Metrics are recalculated periodically (default: daily)
- **Live endpoint**: Serves metrics at `http://localhost:PORT/metrics`
- **Standard format**: Uses official `prometheus_client` library for proper exposition format
- **Performance metrics**: Includes metadata about calculation runtime and processing rates

### Exporter Metrics

The exporter provides these metrics:

- `metric_dpm_rate{metric_name="..."}`: DPM rate for each metric above threshold
- `dpm_finder_runtime_seconds`: Total runtime of last DPM calculation
- `dpm_finder_avg_metric_process_seconds`: Average time to process each metric
- `dpm_finder_metrics_processed_total`: Total number of metrics processed
- `dpm_finder_processing_rate_metrics_per_second`: Rate of metric processing
- `dpm_finder_last_update_timestamp`: Unix timestamp of last update
- `dpm_finder_exporter_info`: Exporter configuration information

### Example Prometheus Configuration

Add this to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'dpm-finder'
    static_configs:
      - targets: ['localhost:8000']
    scrape_interval: 1h  # Match or exceed your update interval
```

## Logging and Verbosity

The script includes comprehensive logging with three verbosity levels:

- **Normal mode**: Shows informational messages about progress and results
- **Quiet mode** (`-q`): Suppresses all output except errors and file writing
- **Verbose mode** (`-v`): Shows detailed debug information including individual metric processing

All log messages include timestamps and severity levels for better monitoring and debugging.

## Usage

usage: dpm-finder.py [-h] [-f {csv,text,txt,json,prom}] [-m MIN_DPM] [-q] [-v] [-t THREADS] [--exporter] [--port PORT] [--update-interval UPDATE_INTERVAL]

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
  --exporter            Run as a Prometheus exporter server instead of one-time execution
  --port PORT           Port to run the exporter server on (default: 8000)
  --update-interval UPDATE_INTERVAL
                        How often to update metrics in exporter mode, in seconds (default: 86400 or 1 day)

## Filtered Metrics

The script automatically excludes certain metric types to focus on meaningful data:

- **Histogram/Summary components**: Metrics ending with `_count`, `_bucket`, or `_sum`
- **Grafana internal metrics**: Metrics beginning with `grafana_`
- **Aggregated metrics**: Metrics that have aggregation rules defined in the Prometheus cluster

This filtering helps reduce noise and focuses analysis on core application and infrastructure metrics.

## Dependencies

The script requires these Python packages (installed via requirements.txt):

- `requests`: HTTP requests to Prometheus API
- `python-dotenv`: Environment variable management
- `prometheus_client`: Official Prometheus client library for exporter mode

## Usage Examples

### One-time Analysis
```bash
# Basic CSV output
./dpm-finder.py

# JSON output with higher threshold and more threads
./dpm-finder.py -f json -m 10.0 -t 16

# Quiet mode for scripting
./dpm-finder.py -q -f csv -m 5.0

# Verbose debugging
./dpm-finder.py -v -t 8
```

### Exporter Mode
```bash
# Basic exporter on port 8000, daily updates
./dpm-finder.py --exporter

# Custom port and hourly updates
./dpm-finder.py --exporter --port 9090 --update-interval 3600

# High-threshold monitoring with frequent updates
./dpm-finder.py --exporter --min-dpm 50.0 --update-interval 1800 --port 8080
```

## Notes

- **Threading**: Adjust threads upwards to utilize more parallelism for potentially faster run times
- **Debugging**: Use `-v` for verbose debugging when troubleshooting connection or processing issues
- **Automation**: Use `-q` for silent operation when running in automated scripts or CI/CD pipelines
- **File output**: Format "prom" will output Prometheus exposition style metrics that could be forwarded using Alloy's prometheus.exporter.unix "textfile" collector
- **Update intervals**: In exporter mode, daily updates (86400s) are recommended to balance resource usage with monitoring needs

## Error Handling

The script includes robust error handling with:
- Connection retry logic with exponential backoff for network issues
- Comprehensive error logging for debugging failed requests
- Graceful handling of malformed responses
- Thread-safe error reporting for concurrent processing
- Graceful shutdown handling for exporter mode (SIGINT/SIGTERM)

## Support

This project is not actively supported by Grafana Labs.
