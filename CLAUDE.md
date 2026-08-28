# dpm-finder

A Grafana Professional Services tool for identifying which Prometheus metrics
drive high Data Points per Minute (DPM). Analyzes metric-level DPM with
per-label breakdown to help optimize Grafana Cloud costs.

Source: https://github.com/grafana-ps/dpm-finder

## Quick Start

### Prerequisites
- Python 3.9+
- Access to a Grafana Cloud Prometheus endpoint (or any Prometheus-compatible API)

### Setup

1. Clone the repo and create a virtual environment:

```bash
git clone https://github.com/grafana-ps/dpm-finder.git
cd dpm-finder
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Configure credentials by copying `.env_example` to `.env` and filling in values:
   - `PROMETHEUS_ENDPOINT` -- The Prometheus endpoint URL (must end in `.net`, nothing after)
   - `PROMETHEUS_USERNAME` -- Tenant ID / stack ID (numeric)
   - `PROMETHEUS_API_KEY` -- Grafana Cloud API key (`glc_...` format)

   Alternatively, use `--gcx` to run queries through an existing GCX login without a `.env` file.

### Stack Discovery with gcx

If [gcx](https://github.com/grafana/gcx) is available, use it to find stack details:

```bash
gcx config check              # Show active stack context
gcx config list-contexts      # List all configured stacks
gcx config view               # Full config with endpoints
```

The Prometheus endpoint follows the pattern:
```
https://prometheus-{cluster_slug}.grafana.net
```

The username is the numeric stack ID. gcx auto-discovers service URLs from the stack slug via GCOM.

### Stack Discovery without gcx

Look up the stack in the Grafana Cloud portal, or query the usage datasource:
```
grafanacloud_instance_info{name=~"STACK_NAME.*"}
```
Extract `cluster_slug` for the endpoint URL and `id` for the username.

## Running the Tool

### One-Shot Analysis (primary use case)

```bash
./dpm-finder.py -f json -m 2.0 -t 8 --timeout 120 -l 10

# Or reuse the current GCX context and configured Prometheus datasource
./dpm-finder.py --gcx -f json -m 2.0 -t 8 --timeout 120 -l 10
```

### CLI Flags Reference

| Flag | Default | Description |
|------|---------|-------------|
| `-f`, `--format` | `csv` | Output format: `csv`, `text`, `txt`, `json`, `prom` |
| `-m`, `--min-dpm` | `1.0` | Minimum DPM threshold to include a metric |
| `-t`, `--threads` | `10` | Concurrent processing threads |
| `-l`, `--lookback` | `10` | Lookback window in minutes for DPM calculation |
| `--timeout` | `60` | API request timeout in seconds |
| `--cost-per-1000-series` | _(none)_ | Dollar cost per 1000 series; adds estimated_cost column |
| `--include-histograms` | `false` | Include classic `_bucket`, `_count`, and `_sum` components |
| `--no-series-detail` | `false` | Avoid retaining per-series labels for JSON/text output |
| `--gcx` | `false` | Run all queries through the authenticated GCX context |
| `--gcx-context` | current | Select a GCX context without changing the current context |
| `--gcx-datasource` | configured | Override the GCX Prometheus datasource UID |
| `--gcx-binary` | `gcx` | GCX executable name or path |
| `-q`, `--quiet` | `false` | Suppress progress output |
| `-v`, `--verbose` | `false` | Enable debug logging |
| `-e`, `--exporter` | `false` | Run as Prometheus exporter instead of one-shot |
| `-p`, `--port` | `9966` | Exporter server port |
| `-u`, `--update-interval` | `86400` | Exporter metric refresh interval in seconds |

## Output Formats

Output files are written to the current working directory.

### JSON (`-f json`) -> `metric_rates.json`
Best for programmatic analysis. Includes per-series DPM breakdown:
- `metrics[].metric_name` -- the metric name
- `metrics[].dpm` -- data points per minute (maximum across this metric's individual series)
- `metrics[].series_count` -- number of active time series
- `metrics[].series_detail[]` -- per-label-set DPM breakdown (sorted by DPM descending)
- `total_metrics_above_threshold` -- count of metrics above threshold
- `performance_metrics.total_runtime_seconds` -- total processing time
- `performance_metrics.average_metric_processing_seconds` -- avg time per metric
- `performance_metrics.total_metrics_processed` -- total metrics analyzed
- `performance_metrics.metrics_per_second` -- processing throughput

### CSV (`-f csv`) -> `metric_rates.csv`
Columns: `metric_name`, `dpm`, `series_count` (plus `estimated_cost` if `--cost-per-1000-series` is set).

### Text (`-f text`) -> `metric_rates.txt`
Human-readable format with per-series breakdown and performance statistics.

### Prometheus (`-f prom`) -> `metric_rates.prom`
Prometheus exposition format suitable for Alloy's `prometheus.exporter.unix` textfile collector.

## Interpreting Results

- **DPM** = data points per minute (maximum across this metric's individual series)
- **series_count** = number of active time series for that metric
- **series_detail** (JSON/text only) = per-label-combination DPM breakdown
- Sort by DPM descending to find the noisiest metrics
- For top metrics, examine `series_detail` to identify which label combinations drive the highest DPM
- If `--cost-per-1000-series` is set, use `estimated_cost` to prioritize by spend

## Rate Limiting

When running dpm-finder against multiple stacks, limit to **max 3 concurrent** runs. Batch the stacks and wait for each batch to complete before starting the next.

## Metric Filtering

The tool automatically excludes:
- Verified classic histogram families with `*_bucket`, `*_count`, and `*_sum` siblings; ambiguous
  suffixed metrics remain included
- Grafana internal metrics: `grafana_*` prefix

Adaptive Metrics series and native histogram base metrics remain included and are analyzed directly.

## Exporter Mode

Run as a long-lived Prometheus exporter instead of one-shot analysis:

```bash
./dpm-finder.py -e -p 9966 -u 86400
```

Serves metrics at `http://localhost:PORT/metrics`. Recalculates at the configured interval (default: daily). See `README.md` for full exporter and Docker documentation.

## Docker

Alternative to local Python setup:

```bash
docker build -t dpm-finder:latest .
docker run --rm --env-file .env -v $(pwd)/output:/app/output \
  dpm-finder:latest --format json --min-dpm 2.0
```

See `README.md` for full Docker Compose, production deployment, and monitoring integration docs.

## Troubleshooting

### Common Errors

- **Authentication failures (401/403)**: Verify the API key is valid and has `metrics:read` scope. Confirm `PROMETHEUS_USERNAME` matches the numeric stack ID.
- **Timeouts**: Increase `--timeout` for large metric sets. The default is 60s; use 120s or higher for stacks with thousands of metrics.
- **Adaptive Metrics HTTP 422 errors**: The tool retries the stored aggregated series automatically. Unrelated 422 responses are logged and skipped.
- **GCX authentication failures**: Validate the selected context with `gcx config check --context NAME`. Adaptive Metrics rule lookup can require a separate cloud login; the script continues with stored-series and 422 detection if only that lookup fails.
- **Empty results**: Lower the `--min-dpm` threshold. Check that `PROMETHEUS_ENDPOINT` does not have a trailing path after `.net`.
- **Connection errors**: Verify network connectivity to the Prometheus endpoint. The tool retries with exponential backoff (up to 10 retries).

### Retry Behavior

The tool retries failed API requests with exponential backoff (up to 10 retries). Rate-limited responses (HTTP 429) are backed off automatically. HTTP 4xx errors other than 429 are not retried.

## Project Structure

```
dpm-finder.py          # Main CLI tool (one-shot + exporter modes)
requirements.txt       # Python dependencies
.env_example           # Template for credential configuration
Dockerfile             # Multi-stage Docker build
docker-compose.yml     # Docker Compose orchestration
README.md              # Full project documentation
```

## CI and releases

- Keep `.github/workflows/ci.yml` green on pull requests and release branches. It is the minimum
  quality gate for changes and releases.
- Use Conventional Commit subjects for new commits so a changelog can be generated reliably when
  the Grafana organisation has an approved release token broker.
- Prefer a proper semantic-versioned GitHub release for meaningful, user-facing milestones instead
  of leaving release-ready work only on `main`. The first planned stable release is `v1.0.0`.
- Until the organisation provides an approved Vault-backed token broker, do not add Release Please,
  PATs, repository secrets, or workflows that depend on write-enabled `GITHUB_TOKEN` credentials.
  A maintainer must create the annotated tag, release notes, and GitHub release through Grafana's
  approved release process after CI passes.
