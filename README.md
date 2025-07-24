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
4.  **Filters results** based on:
    - DPM threshold (metrics with DPM > 1 by default)
    - Label patterns (e.g., `job=myapp` or `env=~prod.*`)
    - Top N metrics by DPM value
5.  **Outputs results** in various formats:
    - **One-time mode**: CSV, JSON, text, or Prometheus exposition format files
      - With `--show-labels`: Includes label information for each metric
      - Sorted by DPM (default) or metric name
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
./dpm-finder.py -e -p 9966
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
      - targets: ['localhost:9966']
    scrape_interval: 1h  # Match or exceed your update interval
```

## Logging and Verbosity

The script includes comprehensive logging with three verbosity levels:

- **Normal mode**: Shows informational messages about progress and results
- **Quiet mode** (`-q`): Suppresses all output except errors and file writing
- **Verbose mode** (`-v`): Shows detailed debug information including individual metric processing

All log messages include timestamps and severity levels for better monitoring and debugging.

## Usage


usage: dpm-finder.py [-h] [-f {csv,text,txt,json,prom}] [-m MIN_DPM] [-q] [-v] [-t THREADS] 
                     [-l] [-n TOP_N] [-s {dpm,name}] [--filter-labels FILTER_LABELS]
                     [-e] [-p PORT] [-u UPDATE_INTERVAL]


        DPM Finder - A tool to calculate Data Points per Minute (DPM) for Prometheus metrics.
        This script connects to a Prometheus instance, retrieves all metric names,
        calculates their DPM, and outputs the results either in CSV or text format.
        Results are filtered to show only metrics above a specified DPM threshold.
        
        This script is not intended to be run frequently.

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

  -l, --show-labels     Display labels associated with each metric (requires additional queries)
  -n TOP_N, --top-n TOP_N
                        Limit output to top N metrics by DPM value
  -s {dpm,name}, --sort-by {dpm,name}
                        Sort output by dpm (default) or name
  --filter-labels FILTER_LABELS
                        Filter metrics by label patterns (e.g., "job=myapp" or "env=~prod.*")

  -e, --exporter        Run as a Prometheus exporter server instead of one-time execution
  -p PORT, --port PORT   Port to run the exporter server on (default: 9966)
  -u UPDATE_INTERVAL, --update-interval UPDATE_INTERVAL
                         How often to update metrics in exporter mode, in seconds (default: 1 day or 86400 seconds)

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

# JSON output with high threshold and more threads
./dpm-finder.py -f json -m 10.0 -t 16

# Top 10 metrics with labels
./dpm-finder.py --show-labels --top-n 10

# Filter by job and environment
./dpm-finder.py --filter-labels "job=myapp,env=production" -f json

# Sort by metric name with minimum 5 DPM
./dpm-finder.py --min-dpm 5 --sort-by name -f csv

# Quiet mode for scripting
./dpm-finder.py -q -f csv -m 2.0

# Verbose debugging
./dpm-finder.py -v -t 8
```

### Exporter Mode
```bash
# Basic exporter on port 9966, daily updates
./dpm-finder.py -e

# Custom port with filtering
./dpm-finder.py -e -p 9090 --min-dpm 5.0 --filter-labels "env=production"
```


## Notes

- **Threading**: Adjust threads upwards to utilize more parallelism for potentially faster run times
- **Debugging**: Use `-v` for verbose debugging when troubleshooting connection or processing issues
- **Automation**: Use `-q` for silent operation when running in automated scripts or CI/CD pipelines
- **File output**: Format "prom" will output Prometheus exposition style metrics that could be forwarded using Alloy's prometheus.exporter.unix "textfile" collector
- **Update intervals**: In exporter mode, daily updates (1 day) are recommended to balance resource usage with monitoring needs
- **Label queries**: Using `--show-labels` requires additional queries which may increase execution time
- **Filter efficiency**: Label filters are applied at query time for better performance

## Error Handling

The script includes robust error handling with:
- Connection retry logic with exponential backoff for network issues
- Comprehensive error logging for debugging failed requests
- Graceful handling of malformed responses
- Thread-safe error reporting for concurrent processing
- Graceful shutdown handling for exporter mode (SIGINT/SIGTERM)

## Support

This project is not actively supported by Grafana Labs.
