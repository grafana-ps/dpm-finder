🚧 **Work in Progress** 🚧  

# dpm-finder

This repository contains a Python script designed to identify metrics in Prometheus that exceed a specified rate. It's particularly useful for detecting metrics with high data points per minute (DPM) rates, which can be indicative of issues or important trends.

The script can run in two primary modes, with an **optional OTLP export** on top of either:

- **One-time execution**: Calculate DPM and write results to files (CSV, JSON, text, or Prometheus exposition).
- **Prometheus exporter**: Run as an HTTP server that exposes DPM metrics at `/metrics` for Prometheus to scrape.
- **OTLP (optional)**: After each DPM run, push an OpenTelemetry gauge named `dpm` (label `metric_name`) to an OTLP/HTTP metrics endpoint. Use this with Grafana Cloud OTLP, Grafana Alloy, OpenTelemetry Collector, or any OTLP-compatible receiver. It does **not** replace the scrape exporter: you can use OTLP alone (one-time), OTLP + files, or **exporter + OTLP** so Prometheus scrapes `/metrics` while the same process also pushes to OTLP on each update cycle.

## Overview

The `dpm-finder` script retrieves a list of all metrics from a Prometheus instance, calculates their data points per minute (DPM) rate using PromQL, and identifies metrics whose DPM exceeds a threshold. Results can be written to files, served via a Prometheus-compatible HTTP endpoint (`prometheus_client` exporter), and/or exported over **OTLP/HTTP** when you set an OTLP endpoint.

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
    - **Optional OTLP**: After each successful DPM pass (one-time or each exporter refresh), sends the same “above threshold” series as OTLP metrics (gauge `dpm` with attribute/label `metric_name`)
6.  **Provides detailed logging** with configurable verbosity levels for monitoring progress and debugging.

## How To

### 1. Create .env with the following variables

Please note the prometheus endpoint should not have anything after .net. 

See `.env_example`.

```bash
PROMETHEUS_ENDPOINT=""
PROMETHEUS_USERNAME=""
PROMETHEUS_API_KEY=""

# Optional: OTLP/HTTP metrics export (gauge "dpm"; script appends /v1/metrics)
# OTLP_ENDPOINT="http://localhost:4318"
# OTLP_HEADERS="Authorization=Basic <base64>,Other-Header=value"
```

CLI flags `--otlp-endpoint`, `--otlp-headers`, and `--otlp-timeout` override or supply these when set; otherwise `OTLP_ENDPOINT` / `OTLP_HEADERS` from the environment are used.

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
./dpm-finder.py -e -p 9966
```

**With OTLP push** (works in one-time or exporter mode; requires OpenTelemetry packages in `requirements.txt`):

```bash
./dpm-finder.py -f csv --otlp-endpoint "http://localhost:4318"
./dpm-finder.py -e -p 9966 --otlp-endpoint "https://your-otlp-host" --otlp-headers "Authorization=Bearer YOUR_TOKEN"
```

## Docker Usage

The dpm-finder can be run as a Docker container for easy deployment and isolation.

### Building the Docker Image

```bash
# Build the image
docker build -t dpm-finder:latest .

# Or build with a specific tag
docker build -t dpm-finder:v1.0.0 .
```

### Running with Docker

#### Environment Variables

Set your Prometheus credentials as environment variables:

```bash
export PROMETHEUS_ENDPOINT="https://prometheus-prod-13-prod-us-east-0.grafana.net"
export PROMETHEUS_USERNAME="1234567"
export PROMETHEUS_API_KEY="glc_key-example-..."
```

#### Exporter Mode 

```bash
# Run as Prometheus exporter (default)
docker run -d \
  --name dpm-finder \
  -p 9966:9966 \
  -e PROMETHEUS_ENDPOINT="${PROMETHEUS_ENDPOINT}" \
  -e PROMETHEUS_USERNAME="${PROMETHEUS_USERNAME}" \
  -e PROMETHEUS_API_KEY="${PROMETHEUS_API_KEY}" \
  dpm-finder:latest

# With custom options
docker run -d \
  --name dpm-finder \
  -p 8080:8080 \
  -e PROMETHEUS_ENDPOINT="${PROMETHEUS_ENDPOINT}" \
  -e PROMETHEUS_USERNAME="${PROMETHEUS_USERNAME}" \
  -e PROMETHEUS_API_KEY="${PROMETHEUS_API_KEY}" \
  dpm-finder:latest --exporter --port 8080 --update-interval 43200 --min-dpm 5.0

# Exporter with OTLP push (e.g. to Alloy on the host)
docker run -d \
  --name dpm-finder \
  -p 9966:9966 \
  -e PROMETHEUS_ENDPOINT="${PROMETHEUS_ENDPOINT}" \
  -e PROMETHEUS_USERNAME="${PROMETHEUS_USERNAME}" \
  -e PROMETHEUS_API_KEY="${PROMETHEUS_API_KEY}" \
  -e OTLP_ENDPOINT="http://host.docker.internal:4318" \
  dpm-finder:latest --exporter
```

#### One-time Execution

```bash
# Create output directory
mkdir -p ./output

# Run one-time analysis
docker run --rm \
  -v $(pwd)/output:/app/output \
  -e PROMETHEUS_ENDPOINT="${PROMETHEUS_ENDPOINT}" \
  -e PROMETHEUS_USERNAME="${PROMETHEUS_USERNAME}" \
  -e PROMETHEUS_API_KEY="${PROMETHEUS_API_KEY}" \
  dpm-finder:latest --format csv --min-dpm 2.0 --threads 8

# Results will be in ./output/metric_rates.csv
```

### Using Docker Compose

The included `docker-compose.yml` provides easy orchestration for both exporter and one-time execution modes.

#### Prerequisites

1. Install Docker Compose (comes with Docker Desktop)
2. Create a `.env` file with your Prometheus credentials

#### Environment File Setup

Create a `.env` file in the project directory:

```bash
# .env file
PROMETHEUS_ENDPOINT=https://prometheus-prod-13-prod-us-east-0.grafana.net
PROMETHEUS_USERNAME=1234567
PROMETHEUS_API_KEY=glc_key-example-...
```

#### Basic Exporter Setup

```bash
# Start the exporter service
docker-compose up -d

# View logs
docker-compose logs -f dpm-finder

# View live logs with timestamps
docker-compose logs -f --timestamps dpm-finder

# Stop the service
docker-compose down
```

#### Advanced Exporter Configuration

Override default settings using command line:

```bash
# Custom configuration with environment override
PROMETHEUS_ENDPOINT="https://custom-prometheus.example.com" \
PROMETHEUS_USERNAME="custom-user" \
PROMETHEUS_API_KEY="custom-key" \
docker-compose up -d
```

Or modify the `docker-compose.yml` command section:

```yaml
services:
  dpm-finder:
    # ... other settings ...
    command: ["--exporter", "--port", "9966", "--update-interval", "43200", "--min-dpm", "5.0", "--threads", "16"]
```

#### One-time Analysis

```bash
# Create output directory first
mkdir -p ./output

# Run one-time analysis using the oneshot profile
docker-compose --profile oneshot up dpm-finder-oneshot

# Results will be in ./output/ directory
ls -la ./output/

# Clean up after one-time run
docker-compose --profile oneshot down
```

#### Multiple Configurations

Run different configurations simultaneously:

```bash
# Run main exporter
docker-compose up -d dpm-finder

# Run high-threshold analysis in parallel
docker run --rm \
  --env-file .env \
  -v $(pwd)/output:/app/output \
  dpm-finder:latest --format json --min-dpm 10.0 --threads 4
```

#### Production Deployment

For production use, consider this enhanced configuration:

```yaml
# docker-compose.prod.yml
services:
  dpm-finder:
    build: .
    image: dpm-finder:latest
    container_name: dpm-finder-prod
    restart: always
    ports:
      - "9966:9966"
    environment:
      - PROMETHEUS_ENDPOINT=${PROMETHEUS_ENDPOINT}
      - PROMETHEUS_USERNAME=${PROMETHEUS_USERNAME}  
      - PROMETHEUS_API_KEY=${PROMETHEUS_API_KEY}
    command: ["--exporter", "--port", "9966", "--update-interval", "86400", "--quiet"]
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:9966/metrics', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'
```

Deploy with:

```bash
docker-compose -f docker-compose.prod.yml up -d
```

#### Troubleshooting Docker Compose

```bash
# Check service status
docker-compose ps

# View detailed service information
docker-compose config

# Check container resource usage
docker stats dpm-finder

# Access container shell for debugging
docker-compose exec dpm-finder /bin/bash

# Rebuild and restart after code changes
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# View container logs with specific time range
docker-compose logs --since="1h" --until="30m" dpm-finder
```

#### Integration with Monitoring Stack

Example integration with Prometheus monitoring:

```yaml
# docker-compose.monitoring.yml
services:
  dpm-finder:
    build: .
    container_name: dpm-finder
    ports:
      - "9966:9966"
    environment:
      - PROMETHEUS_ENDPOINT=${PROMETHEUS_ENDPOINT}
      - PROMETHEUS_USERNAME=${PROMETHEUS_USERNAME}
      - PROMETHEUS_API_KEY=${PROMETHEUS_API_KEY}
    networks:
      - monitoring

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    networks:
      - monitoring

networks:
  monitoring:
    driver: bridge
```

### Docker Health Checks

The container includes health checks that verify the `/metrics` endpoint:

```bash
# Check container health
docker ps

# View health check logs
docker inspect dpm-finder --format='{{.State.Health.Status}}'
```

### Container Features

- **Multi-stage build**: Minimized image size (~100MB)
- **Non-root user**: Runs as `dpmfinder` user for security
- **Health checks**: Built-in endpoint monitoring
- **Signal handling**: Graceful shutdown on SIGTERM/SIGINT
- **Optimized**: Python bytecode optimization enabled

## OTLP export (optional)

When `--otlp-endpoint` or `OTLP_ENDPOINT` is set, dpm-finder uses the OpenTelemetry SDK’s **OTLP/HTTP** metric exporter to send a single observable gauge:

| Detail | Value |
|--------|--------|
| Metric name | `dpm` |
| Series | One per metric **above** `--min-dpm` |
| Labels / attributes | `metric_name` = Prometheus metric name |
| Value | Computed DPM |

**Endpoint:** Pass the OTLP **base** URL (e.g. `http://alloy-host:4318` for Alloy’s default OTLP HTTP port, or your Grafana Cloud OTLP gateway base). The script normalizes the URL (adds `https://` if no scheme is given) and posts to `{base}/v1/metrics`.

**Auth:** Use `--otlp-headers` or `OTLP_HEADERS` as comma-separated `Key=Value` pairs (e.g. `Authorization=Basic …` or `Authorization=Bearer …`).

**When it runs:** Once after a successful DPM calculation in **one-time** mode, and after **each** full calculation in **exporter** mode (initial run + every `--update-interval`).

**Dependencies:** `opentelemetry-api`, `opentelemetry-sdk`, and `opentelemetry-exporter-otlp-proto-http` (listed in `requirements.txt`). If they are missing, OTLP export logs an error and skips.

Prometheus does not scrape OTLP directly; see [Metrics not showing in Prometheus](#metrics-not-showing-in-prometheus) for how OTLP fits next to the scrape exporter.

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
      - targets: ['localhost:9966']   # or your dpm-finder host:port
    scrape_interval: 1h  # Match or exceed your update interval
```

**Important:** Prometheus only sees dpm-finder metrics if it actually scrapes this job. Ensure the `targets` host/port are reachable from the Prometheus server (use the hostname or IP Prometheus uses to reach the machine, not `localhost` if Prometheus runs on another host).

### Metrics not showing in Prometheus

**If you use exporter mode (dpm-finder with `-e`):**

1. **Prometheus must scrape the target**  
   Add the `dpm-finder` job to `prometheus.yml` (see above). Use the address Prometheus can reach (e.g. `host.docker.internal:9966` from Docker, or the host IP).

2. **Check the exporter is up and serving**  
   ```bash
   curl -s http://localhost:9966/metrics | head -30
   ```  
   You should see `metric_dpm_rate`, `dpm_finder_runtime_seconds`, etc. If the list is empty or you get "connection refused", start dpm-finder with `-e -p 9966` and wait for the first run to finish.

3. **Wait for the first scrape**  
   Metrics appear only after the first DPM run completes. In exporter mode the default update interval is 1 day; use `-u 300` (5 minutes) for testing.

**If you use OTLP push (`--otlp-endpoint`):**

- dpm-finder pushes a gauge named **`dpm`** (label `metric_name`) to the OTLP URL. It does **not** expose a `/metrics` endpoint for Prometheus to scrape.
- To see those in Prometheus you need either:
  - **Grafana Cloud:** OTLP goes to the cloud OTLP gateway → metrics land in the Grafana Cloud Prometheus/Mimir data source. Query `dpm` (or the name Grafana gives it) in Explore.
  - **Self-hosted Prometheus:** Run an OTLP receiver (e.g. Grafana Alloy or OpenTelemetry Collector) that receives OTLP and then either (a) exposes Prometheus metrics for scrape, or (b) remote-writes to Prometheus/Mimir. Without that bridge, Prometheus will not see OTLP-pushed metrics.

**OTLP push not working?**

1. Run with **`-v`** and check logs for lines like `OTLP export: sending N series to https://.../v1/metrics` and any `OTLP export: force_flush failed` or `failed to create exporter`.
2. **Endpoint:** Use the OTLP **base** URL (e.g. `http://alloy-host:4318` for Alloy, or Grafana Cloud URL without `/v1/metrics`). The script appends `/v1/metrics`.
3. **Alloy as receiver:** Alloy’s OTLP HTTP server listens on port **4318** by default. Point `--otlp-endpoint` (or `OTLP_ENDPOINT`) at that (e.g. `http://localhost:4318` if dpm-finder and Alloy run on the same host).
4. **Grafana Cloud:** Use the gateway URL from the Cloud portal and set auth via `--otlp-headers "Authorization=Basic BASE64_USER_PASS"` or the header they provide. Ensure the token has OTLP write scope.
5. **Network:** Ensure the host running dpm-finder can reach the endpoint (no firewall blocking outbound to that host/port).

## Logging and Verbosity

The script includes comprehensive logging with three verbosity levels:

- **Normal mode**: Shows informational messages about progress and results
- **Quiet mode** (`-q`): Suppresses all output except errors and file writing
- **Verbose mode** (`-v`): Shows detailed debug information including individual metric processing

All log messages include timestamps and severity levels for better monitoring and debugging.

## Usage

```
usage: dpm-finder.py [-h] [-f {csv,text,txt,json,prom}] [-m MIN_DPM] [-q] [-v]
                     [-t THREADS] [-e] [-p PORT] [-u UPDATE_INTERVAL]
                     [--timeout TIMEOUT] [--cost-per-1000-series COST_PER_1000_SERIES]
                     [--otlp-endpoint OTLP_ENDPOINT] [--otlp-headers OTLP_HEADERS]
                     [--otlp-timeout OTLP_TIMEOUT]

DPM Finder - A tool to calculate Data Points per Minute (DPM) for Prometheus metrics.
This script connects to a Prometheus instance, retrieves all metric names,
calculates their DPM, and outputs results in CSV, JSON, text, or Prometheus exposition
format, and/or serves them via an exporter and/or OTLP.

This script is not intended to be run frequently.

optional arguments:
  -h, --help            Show this help message and exit
  -f, --format          Output format: csv, text, txt, json, prom (default: csv)
  -m, --min-dpm         Minimum DPM threshold (default: 1.0)
  -q, --quiet           Suppress most logging; errors and file output remain
  -v, --verbose         Debug logging
  -t, --threads         Concurrent worker threads (default: 10, minimum: 1)
  -e, --exporter        Run as Prometheus scrape exporter (HTTP /metrics)
  -p, --port            Exporter listen port (default: 9966)
  -u, --update-interval Exporter: seconds between DPM refreshes (default: 86400)
  --timeout             Prometheus API request timeout in seconds (default: 60)
  --cost-per-1000-series
                        Optional; adds estimated_cost column and sort by cost
  --otlp-endpoint       OTLP/HTTP base URL for gauge "dpm" (env: OTLP_ENDPOINT)
  --otlp-headers        OTLP request headers, key=value,... (env: OTLP_HEADERS)
  --otlp-timeout        OTLP export timeout in seconds (default: 10)
```

## Filtered Metrics

The script automatically excludes certain metric types to focus on meaningful data:

- **Histogram/Summary components**: Metrics ending with `_count`, `_bucket`, or `_sum`
- **Grafana internal metrics**: Metrics beginning with `grafana_`
- **Aggregated metrics**: Metrics that have aggregation rules defined in the Prometheus cluster

This filtering helps reduce noise and focuses analysis on core application and infrastructure metrics.

## Dependencies

The script requires these Python packages (installed via `requirements.txt`):

- `requests`: HTTP requests to Prometheus API
- `python-dotenv`: Environment variable management
- `prometheus_client`: Prometheus scrape exporter (`/metrics`)

For **OTLP export**, also install (included in `requirements.txt`):

- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`

## Usage Examples

### One-time Analysis
```bash
# Basic CSV output
./dpm-finder.py

# JSON output with high threshold and more threads
./dpm-finder.py -f json -m 10.0 -t 16

# Quiet mode for scripting
./dpm-finder.py -q -f csv -m 2.0

# Verbose debugging
./dpm-finder.py -v -t 8
```

### Exporter Mode
```bash
# Basic exporter on port 9966, daily updates
./dpm-finder.py -e

# Custom port 
./dpm-finder.py -e -p 9090 

# Exporter plus OTLP on each refresh cycle
./dpm-finder.py -e -p 9966 --otlp-endpoint "http://alloy:4318"
```

### OTLP only (one-time run, no `/metrics` server)
```bash
./dpm-finder.py -f json --otlp-endpoint "https://otlp-gateway.example.com" --otlp-headers "Authorization=Bearer $TOKEN"
```


## Notes

- **Threading**: Adjust threads upwards to utilize more parallelism for potentially faster run times
- **Debugging**: Use `-v` for verbose debugging when troubleshooting connection or processing issues
- **Automation**: Use `-q` for silent operation when running in automated scripts or CI/CD pipelines
- **File output**: Format "prom" will output Prometheus exposition style metrics that could be forwarded using Alloy's prometheus.exporter.unix "textfile" collector
- **Update intervals**: In exporter mode, daily updates (1 day) are recommended to balance resource usage with monitoring needs

## Error Handling

The script includes robust error handling with:
- Connection retry logic with exponential backoff for network issues
- Comprehensive error logging for debugging failed requests
- Graceful handling of malformed responses
- Thread-safe error reporting for concurrent processing
- Graceful shutdown handling for exporter mode (SIGINT/SIGTERM)

## Support

This project is not actively supported by Grafana Labs.
