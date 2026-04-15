# AI Enablement Design Spec

**Date:** 2026-04-15
**Status:** Draft
**Branch:** ai-enablement

## Goal

Make the dpm-finder repository consumable by AI agents across platforms (Claude Code, Copilot, Cursor, A2A-compatible systems) and provide a Claude Code plugin installable via the marketplace that automates DPM analysis.

## Deliverables

1. **CLAUDE.md** -- Comprehensive tool documentation at repo root (single source of truth)
2. **AGENTS.md** -- Symlink to CLAUDE.md for cross-platform agent compatibility
3. **Claude Code plugin** -- Self-contained marketplace with `/grafana-dpm-finder:analyze` skill
4. **A2A agent card** -- Static `.well-known/agent.json` for machine-readable discovery

## File Structure

```
dpm-finder/
├── CLAUDE.md                              # Comprehensive tool docs (the heavy lifter)
├── AGENTS.md -> CLAUDE.md                 # Symlink for cross-platform agents
├── .well-known/
│   └── agent.json                         # Static A2A agent card
├── .claude-plugin/
│   └── marketplace.json                   # Self-contained marketplace manifest
├── grafana-dpm-finder/                    # Plugin directory
│   ├── .claude-plugin/
│   │   └── plugin.json                    # Plugin metadata (name, version, author)
│   ├── agents/
│   │   └── grafana-dpm-finder.md          # Light agent -- procedural automation
│   └── skills/
│       └── analyze/
│           └── SKILL.md                   # Skill trigger (context: fork)
├── dpm-finder.py                          # (existing, unchanged)
├── README.md                              # (existing, unchanged)
├── requirements.txt                       # (existing, unchanged)
├── .env_example                           # (existing, unchanged)
└── ...                                    # (all other existing files unchanged)
```

---

## Component 1: CLAUDE.md

The primary AI documentation file. Read automatically by Claude Code at session start. Also the target of the AGENTS.md symlink so Copilot, Cursor, and other tools benefit equally.

### Content Structure

```markdown
# dpm-finder

A Grafana Professional Services tool for identifying which Prometheus metrics
drive high Data Points per Minute (DPM). Analyzes metric-level DPM with
per-label breakdown to help optimize Grafana Cloud costs.

## Quick Start

### Prerequisites
- Python 3.9+
- Access to a Grafana Cloud Prometheus endpoint (or any Prometheus-compatible API)

### Setup
1. Clone the repo and create a virtual environment:
   git clone https://github.com/grafana-ps/dpm-finder.git
   cd dpm-finder
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

2. Configure credentials by copying .env_example to .env and filling in values:
   - PROMETHEUS_ENDPOINT: The Prometheus endpoint URL (must end in .net, nothing after)
   - PROMETHEUS_USERNAME: Tenant ID / stack ID
   - PROMETHEUS_API_KEY: Grafana Cloud API key (glc_... format)

### Stack Discovery with gcx
If gcx is available, use it to find stack details:
   gcx config check                    # Show active stack context
   gcx config list-contexts            # List all configured stacks

The Prometheus endpoint follows the pattern:
   https://prometheus-{cluster_slug}.grafana.net

The username is the numeric stack ID.

### Stack Discovery without gcx
Look up the stack in Grafana Cloud portal, or query:
   grafanacloud_instance_info{name=~"STACK_NAME.*"}
Extract cluster_slug for the endpoint URL and id for the username.

## Running the Tool

### One-Shot Analysis (primary use case)
   ./dpm-finder.py -f json -m 2.0 -t 8 --timeout 120 -l 10

### CLI Flags Reference
| Flag | Default | Description |
|------|---------|-------------|
| -f, --format | csv | Output format: csv, text, txt, json, prom |
| -m, --min-dpm | 1.0 | Minimum DPM threshold |
| -t, --threads | 10 | Concurrent processing threads |
| -l, --lookback | 10 | Lookback window in minutes |
| --timeout | 60 | API request timeout in seconds |
| --cost-per-1000-series | (none) | Dollar cost per 1000 series for cost estimation |
| -q, --quiet | false | Suppress progress output |
| -v, --verbose | false | Enable debug logging |
| -e, --exporter | false | Run as Prometheus exporter instead |
| -p, --port | 9966 | Exporter server port |
| -u, --update-interval | 86400 | Exporter update interval in seconds |

## Output Formats

### JSON (-f json) -> metric_rates.json
Best for programmatic analysis. Includes per-series DPM breakdown:
- metrics[].metric_name, metrics[].dpm, metrics[].series_count
- metrics[].series_detail[] -- per-label-set DPM breakdown
- total_metrics count
- performance timing stats

### CSV (-f csv) -> metric_rates.csv
Columns: metric_name, dpm, series_count [, estimated_cost]

### Text (-f text) -> metric_rates.txt
Human-readable with per-series breakdown and performance stats.

### Prometheus (-f prom) -> metric_rates.prom
Exposition format for Alloy's prometheus.exporter.unix textfile collector.

## Interpreting Results

- **DPM** = data points per minute across all series for a metric
- **series_count** = number of active time series for that metric
- **series_detail** (JSON/text) = per-label-combination DPM breakdown
- Sort by DPM descending to find the noisiest metrics
- For top metrics, examine series_detail to identify which label
  combinations drive the highest DPM

## Rate Limiting

When running dpm-finder against multiple stacks, limit to max 3 concurrent
runs. Batch the stacks and wait for each batch to complete before starting
the next.

## Metric Filtering

The tool automatically excludes:
- Histogram/summary components: *_count, *_bucket, *_sum suffixes
- Grafana internal metrics: grafana_* prefix
- Metrics with aggregation rules defined in the cluster

## Docker

Alternative to local Python setup:
   docker build -t dpm-finder:latest .
   docker run --rm --env-file .env -v $(pwd)/output:/app/output \
     dpm-finder:latest --format json --min-dpm 2.0

See README.md for full Docker and docker-compose documentation.

## Troubleshooting

### Common errors
- **Authentication failures (401/403)**: Check API key is valid and has
  metrics:read scope. Verify PROMETHEUS_USERNAME matches the stack ID.
- **Timeouts**: Increase --timeout for large metric sets. Default is 60s.
- **HTTP 422 errors**: Usually means the metric has aggregation rules.
  The tool logs a warning and skips these automatically.
- **Empty results**: Lower the --min-dpm threshold or check that the
  Prometheus endpoint URL does not have a trailing path after .net.

### Retry behavior
The tool retries failed requests with exponential backoff (up to 10 retries).
For rate-limited (429) responses, it backs off automatically.
```

The exact content will be refined during implementation, but this captures the structure and key information.

---

## Component 2: AGENTS.md

A symlink to CLAUDE.md:

```bash
ln -s CLAUDE.md AGENTS.md
```

This ensures Copilot, Cursor, Windsurf, and other AI tools that look for AGENTS.md get the same comprehensive documentation without content duplication.

---

## Component 3: Claude Code Plugin

### 3a. Marketplace Manifest

**File:** `.claude-plugin/marketplace.json`

```json
{
  "name": "grafana-ps-dpm-finder",
  "owner": {
    "name": "Grafana Professional Services"
  },
  "plugins": [
    {
      "name": "grafana-dpm-finder",
      "source": "./grafana-dpm-finder",
      "description": "Analyze Prometheus metric DPM rates to identify cost drivers in Grafana Cloud"
    }
  ]
}
```

**Installation flow:**
```bash
/plugin marketplace add https://github.com/grafana-ps/dpm-finder
/plugin install grafana-dpm-finder@grafana-ps-dpm-finder
```

### 3b. Plugin Metadata

**File:** `grafana-dpm-finder/.claude-plugin/plugin.json`

```json
{
  "name": "grafana-dpm-finder",
  "version": "1.0.0",
  "description": "Analyze Prometheus metric DPM rates with per-series breakdown to identify cost drivers in Grafana Cloud. Supports gcx-based stack discovery and automatic environment setup.",
  "author": {
    "name": "Grafana PS Team (Rob Knight)"
  }
}
```

### 3c. Agent Definition

**File:** `grafana-dpm-finder/agents/grafana-dpm-finder.md`

**Frontmatter:**
```yaml
---
name: grafana-dpm-finder
description: Analyzes Prometheus metric DPM rates to identify cost drivers. Handles stack discovery, tool setup, and result presentation.
tools: Bash, Read, Grep, Glob
model: sonnet
---
```

**System prompt body (light -- references CLAUDE.md):**

The agent handles the procedural automation that goes beyond what CLAUDE.md documents:

1. **gcx detection and stack discovery**
   - Check if `gcx` is available (`which gcx`)
   - If yes: run `gcx config check` to get active stack details
   - Present stack name/URL to user and ask for confirmation
   - If no gcx or user prefers manual: ask for Prometheus endpoint, username, and API key

2. **Environment setup**
   - Locate the dpm-finder repo: when running as a plugin, the repo root is the plugin's grandparent directory (two levels up from `grafana-dpm-finder/`). The agent should resolve this relative to its own skill/agent path. If the repo was cloned elsewhere, search for `dpm-finder.py` in common locations (`$HOME/repos`, `$HOME/src`, working directory).
   - Check if venv exists at `{repo_root}/venv/`; if not, create it and install dependencies from `{repo_root}/requirements.txt`
   - Check if `.env` is configured; if not, create it from `.env_example` using discovered/provided credentials

3. **Execution**
   - Read CLAUDE.md from the repo root for CLI flag reference
   - Run dpm-finder.py with appropriate flags (default: `-f json -m 2.0 -t 8 --timeout 120 -l 10`)
   - Use `-q` flag for cleaner output when running programmatically

4. **Result presentation**
   - Parse `metric_rates.json` output
   - Present as a sorted table: metric name, DPM, active series, estimated cost (if available)
   - For the top 5 metrics by DPM, include per-series breakdown showing which label combinations drive the highest DPM

5. **Error handling**
   - If authentication fails, guide user to check credentials
   - If timeouts occur, suggest increasing `--timeout`
   - Reference CLAUDE.md troubleshooting section for detailed guidance

The agent prompt will be approximately 80-120 lines -- enough to orchestrate the workflow without duplicating CLAUDE.md content.

### 3d. Skill Definition

**File:** `grafana-dpm-finder/skills/analyze/SKILL.md`

```yaml
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
```

---

## Component 4: A2A Agent Card

**File:** `.well-known/agent.json`

A static agent card following the A2A protocol spec. Not a live endpoint, but signals to A2A-compatible systems what this tool can do.

```json
{
  "name": "dpm-finder",
  "description": "Analyzes Prometheus metric DPM (Data Points per Minute) rates to identify cost drivers in Grafana Cloud. Provides metric-level granularity with per-label breakdown.",
  "url": "https://github.com/grafana-ps/dpm-finder",
  "version": "1.0.0",
  "provider": {
    "organization": "Grafana Professional Services",
    "url": "https://grafana.com"
  },
  "capabilities": {
    "streaming": false,
    "pushNotifications": false
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json", "text/csv", "text/plain"],
  "skills": [
    {
      "id": "analyze-dpm",
      "name": "DPM Analysis",
      "description": "Calculate per-metric DPM rates from a Prometheus endpoint, filtered by threshold, with per-series label breakdown",
      "tags": ["prometheus", "metrics", "dpm", "grafana-cloud", "cost-optimization"],
      "examples": [
        "Analyze DPM for my-production-stack",
        "Find metrics with DPM above 10",
        "Show per-label breakdown for high-DPM metrics"
      ]
    }
  ]
}
```

---

## Design Decisions

### Why CLAUDE.md is the heavy lifter (not the agent)
Users who clone the repo without installing the plugin still benefit from comprehensive AI guidance. The agent reads CLAUDE.md at runtime, avoiding duplication and ensuring a single source of truth.

### Why a self-contained marketplace
The dpm-finder repo is public. Hosting its own marketplace means anyone can install the plugin without needing access to the private grafana-ps-claude-marketplace repo.

### Why a static A2A agent card
The full A2A protocol requires a running HTTP service (JSON-RPC, task lifecycle). dpm-finder is a CLI tool. A static agent card provides machine-readable discovery at near-zero cost, positioned for future A2A tooling adoption.

### Why AGENTS.md is a symlink
Maintains one source of truth. AGENTS.md is the emerging cross-platform standard (Copilot, Cursor, Windsurf). Rather than duplicating content or maintaining two files, a symlink ensures they always match.

### Why sonnet model for the agent
The workflow is procedural (run commands, parse output, present tables). sonnet is faster and cheaper, with sufficient capability for this task.

### Why context: fork
Isolates the DPM analysis workflow in a subagent session, keeping the user's main Claude Code context clean. Matches the established pattern in the reference marketplace.
