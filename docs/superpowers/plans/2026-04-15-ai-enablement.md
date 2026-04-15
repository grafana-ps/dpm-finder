# AI Enablement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dpm-finder repo consumable by AI agents across platforms and provide a Claude Code plugin for automated DPM analysis.

**Architecture:** Four independent deliverables -- CLAUDE.md (comprehensive docs), AGENTS.md (symlink), Claude Code plugin (marketplace + skill + agent), and A2A agent card (static discovery). CLAUDE.md is the single source of truth; the agent reads it at runtime rather than duplicating content.

**Tech Stack:** Markdown, JSON, YAML frontmatter, symlinks. No code changes to dpm-finder.py.

---

### Task 1: Create CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

- [ ] **Step 1: Create CLAUDE.md with full content**

Write the following to `CLAUDE.md`:

```markdown
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
| `-q`, `--quiet` | `false` | Suppress progress output |
| `-v`, `--verbose` | `false` | Enable debug logging |
| `-e`, `--exporter` | `false` | Run as Prometheus exporter instead of one-shot |
| `-p`, `--port` | `9966` | Exporter server port |
| `-u`, `--update-interval` | `86400` | Exporter metric refresh interval in seconds |

## Output Formats

### JSON (`-f json`) -> `metric_rates.json`
Best for programmatic analysis. Includes per-series DPM breakdown:
- `metrics[].metric_name` -- the metric name
- `metrics[].dpm` -- data points per minute (max across all series)
- `metrics[].series_count` -- number of active time series
- `metrics[].series_detail[]` -- per-label-set DPM breakdown (sorted by DPM descending)
- `total_metrics` -- count of metrics above threshold
- Performance timing statistics

### CSV (`-f csv`) -> `metric_rates.csv`
Columns: `metric_name`, `dpm`, `series_count` (plus `estimated_cost` if `--cost-per-1000-series` is set).

### Text (`-f text`) -> `metric_rates.txt`
Human-readable format with per-series breakdown and performance statistics.

### Prometheus (`-f prom`) -> `metric_rates.prom`
Prometheus exposition format suitable for Alloy's `prometheus.exporter.unix` textfile collector.

## Interpreting Results

- **DPM** = data points per minute across all series for a metric (takes the max across series)
- **series_count** = number of active time series for that metric
- **series_detail** (JSON/text only) = per-label-combination DPM breakdown
- Sort by DPM descending to find the noisiest metrics
- For top metrics, examine `series_detail` to identify which label combinations drive the highest DPM
- If `--cost-per-1000-series` is set, use `estimated_cost` to prioritize by spend

## Rate Limiting

When running dpm-finder against multiple stacks, limit to **max 3 concurrent** runs. Batch the stacks and wait for each batch to complete before starting the next.

## Metric Filtering

The tool automatically excludes:
- Histogram/summary components: `*_count`, `*_bucket`, `*_sum` suffixes
- Grafana internal metrics: `grafana_*` prefix
- Metrics with aggregation rules defined in the cluster (fetched from `/aggregations/rules`)

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
- **HTTP 422 errors**: Usually means the metric has aggregation rules. The tool logs a warning and skips these automatically.
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
```

- [ ] **Step 2: Verify CLAUDE.md reads correctly**

Run: `head -5 CLAUDE.md`
Expected: First 5 lines of the file showing the title and description.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md for AI agent guidance"
```

---

### Task 2: Create AGENTS.md Symlink

**Files:**
- Create: `AGENTS.md` (symlink -> `CLAUDE.md`)

- [ ] **Step 1: Create the symlink**

```bash
ln -s CLAUDE.md AGENTS.md
```

- [ ] **Step 2: Verify the symlink resolves**

```bash
ls -la AGENTS.md
# Expected: AGENTS.md -> CLAUDE.md

head -3 AGENTS.md
# Expected: Same first 3 lines as CLAUDE.md (# dpm-finder)
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add AGENTS.md symlink to CLAUDE.md for cross-platform agents"
```

---

### Task 3: Create Marketplace Manifest

**Files:**
- Create: `.claude-plugin/marketplace.json`

- [ ] **Step 1: Create directory and manifest**

```bash
mkdir -p .claude-plugin
```

Write the following to `.claude-plugin/marketplace.json`:

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

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -c "import json; json.load(open('.claude-plugin/marketplace.json')); print('Valid JSON')"
```

Expected: `Valid JSON`

- [ ] **Step 3: Commit**

```bash
git add .claude-plugin/marketplace.json
git commit -m "feat: add Claude Code marketplace manifest"
```

---

### Task 4: Create Plugin Metadata

**Files:**
- Create: `grafana-dpm-finder/.claude-plugin/plugin.json`

- [ ] **Step 1: Create directory and plugin.json**

```bash
mkdir -p grafana-dpm-finder/.claude-plugin
```

Write the following to `grafana-dpm-finder/.claude-plugin/plugin.json`:

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

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -c "import json; json.load(open('grafana-dpm-finder/.claude-plugin/plugin.json')); print('Valid JSON')"
```

Expected: `Valid JSON`

- [ ] **Step 3: Commit**

```bash
git add grafana-dpm-finder/.claude-plugin/plugin.json
git commit -m "feat: add grafana-dpm-finder plugin metadata"
```

---

### Task 5: Create Agent Definition

**Files:**
- Create: `grafana-dpm-finder/agents/grafana-dpm-finder.md`

- [ ] **Step 1: Create directory and agent file**

```bash
mkdir -p grafana-dpm-finder/agents
```

Write the following to `grafana-dpm-finder/agents/grafana-dpm-finder.md`:

```markdown
---
name: grafana-dpm-finder
description: Analyzes Prometheus metric DPM rates to identify cost drivers. Handles stack discovery, tool setup, and result presentation.
tools: Bash, Read, Grep, Glob
model: sonnet
---

# DPM Finder Agent

You help users analyze Prometheus metric DPM (Data Points per Minute) rates using the dpm-finder tool. You handle stack discovery, environment setup, tool execution, and result presentation.

## Step 1: Locate the dpm-finder Repository

Find the dpm-finder repo root by searching for `dpm-finder.py`:

1. Check the current working directory
2. Check common locations: `$HOME/repos/dpm-finder`, `$HOME/src/dpm-finder`
3. Use `find $HOME -maxdepth 3 -name "dpm-finder.py" -type f 2>/dev/null | head -5` as a fallback

Store the repo root path for all subsequent steps. Read `CLAUDE.md` from the repo root for full tool documentation.

## Step 2: Stack Discovery

Determine the Prometheus endpoint, username, and API key:

**Try gcx first:**
```bash
which gcx
```

If gcx is available:
```bash
gcx config check
```

Present the active stack details to the user and ask them to confirm this is the correct stack. Extract the Prometheus endpoint URL and stack ID.

**If gcx is not available or user prefers manual setup:**

Check if `{repo_root}/.env` exists and has non-placeholder values:
```bash
grep -v '^#' {repo_root}/.env 2>/dev/null | grep -v '=$' | head -5
```

If `.env` is missing or unconfigured, tell the user:
- Copy `.env_example` to `.env`
- Fill in `PROMETHEUS_ENDPOINT`, `PROMETHEUS_USERNAME`, `PROMETHEUS_API_KEY`
- Refer to the "Stack Discovery" section in CLAUDE.md for how to find these values

**Always confirm the target stack with the user before proceeding.** Show them the endpoint URL and ask for explicit confirmation.

## Step 3: Environment Setup

Check and set up the Python virtual environment:

```bash
# Check if venv exists
ls {repo_root}/venv/bin/python3 2>/dev/null
```

If venv does not exist:
```bash
cd {repo_root} && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

If venv exists, activate it:
```bash
source {repo_root}/venv/bin/activate
```

## Step 4: Run DPM Analysis

Execute the tool with JSON output for structured parsing:

```bash
cd {repo_root} && source venv/bin/activate && python3 dpm-finder.py -f json -m 2.0 -t 8 --timeout 120 -l 10
```

If the user provided custom flags or thresholds, adjust accordingly. See CLAUDE.md for the full CLI flags reference.

Monitor the output for errors:
- **401/403**: Authentication issue -- check API key and username
- **Timeouts**: Suggest increasing `--timeout`
- **422**: Aggregation rules -- these are skipped automatically

## Step 5: Present Results

Read and parse `{repo_root}/metric_rates.json`:

1. Present a summary table sorted by DPM descending:

| Metric | DPM | Active Series | Est. Cost |
|--------|-----|---------------|-----------|
| metric_name | value | count | cost_if_available |

2. For the **top 5 metrics** by DPM, show the per-series breakdown:
   - List the label combinations driving the highest DPM
   - Use `series_detail` from the JSON output

3. Include totals:
   - Total metrics above threshold
   - Processing time from performance stats

## Error Handling

If the tool fails:
1. Read the error output carefully
2. Check CLAUDE.md Troubleshooting section for known issues
3. Common fixes:
   - Auth errors: re-check `.env` values
   - Timeouts: increase `--timeout` to 180 or 300
   - Empty results: lower `--min-dpm` to 1.0 or 0.5
4. If the issue persists, show the full error to the user and suggest running with `-v` for debug output
```

- [ ] **Step 2: Verify frontmatter is valid YAML**

```bash
head -6 grafana-dpm-finder/agents/grafana-dpm-finder.md
```

Expected: YAML frontmatter block with `name`, `description`, `tools`, `model` fields between `---` delimiters.

- [ ] **Step 3: Commit**

```bash
git add grafana-dpm-finder/agents/grafana-dpm-finder.md
git commit -m "feat: add grafana-dpm-finder agent definition"
```

---

### Task 6: Create Skill Definition

**Files:**
- Create: `grafana-dpm-finder/skills/analyze/SKILL.md`

- [ ] **Step 1: Create directory and skill file**

```bash
mkdir -p grafana-dpm-finder/skills/analyze
```

Write the following to `grafana-dpm-finder/skills/analyze/SKILL.md`:

```markdown
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

- [ ] **Step 2: Verify frontmatter is valid YAML**

```bash
head -7 grafana-dpm-finder/skills/analyze/SKILL.md
```

Expected: YAML frontmatter with `name: analyze`, `context: fork`, `agent: grafana-dpm-finder`.

- [ ] **Step 3: Commit**

```bash
git add grafana-dpm-finder/skills/analyze/SKILL.md
git commit -m "feat: add /grafana-dpm-finder:analyze skill"
```

---

### Task 7: Create A2A Agent Card

**Files:**
- Create: `.well-known/agent.json`

- [ ] **Step 1: Create directory and agent card**

```bash
mkdir -p .well-known
```

Write the following to `.well-known/agent.json`:

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

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -c "import json; json.load(open('.well-known/agent.json')); print('Valid JSON')"
```

Expected: `Valid JSON`

- [ ] **Step 3: Commit**

```bash
git add .well-known/agent.json
git commit -m "feat: add static A2A agent card for machine-readable discovery"
```

---

### Task 8: Final Verification

**Files:**
- None (verification only)

- [ ] **Step 1: Verify complete directory structure**

```bash
find CLAUDE.md AGENTS.md .well-known .claude-plugin grafana-dpm-finder -type f -o -type l | sort
```

Expected output:
```
AGENTS.md
CLAUDE.md
.claude-plugin/marketplace.json
.well-known/agent.json
grafana-dpm-finder/.claude-plugin/plugin.json
grafana-dpm-finder/agents/grafana-dpm-finder.md
grafana-dpm-finder/skills/analyze/SKILL.md
```

- [ ] **Step 2: Verify AGENTS.md symlink target**

```bash
readlink AGENTS.md
```

Expected: `CLAUDE.md`

- [ ] **Step 3: Verify all JSON files are valid**

```bash
python3 -c "
import json, sys
files = ['.claude-plugin/marketplace.json', 'grafana-dpm-finder/.claude-plugin/plugin.json', '.well-known/agent.json']
for f in files:
    try:
        json.load(open(f))
        print(f'OK: {f}')
    except Exception as e:
        print(f'FAIL: {f} - {e}')
        sys.exit(1)
print('All JSON files valid')
"
```

Expected: `OK` for each file, ending with `All JSON files valid`.

- [ ] **Step 4: Verify marketplace references valid plugin path**

```bash
python3 -c "
import json, os
m = json.load(open('.claude-plugin/marketplace.json'))
for p in m['plugins']:
    source = p['source']
    plugin_json = os.path.join(source, '.claude-plugin', 'plugin.json')
    assert os.path.exists(plugin_json), f'Missing: {plugin_json}'
    print(f'OK: {p[\"name\"]} -> {plugin_json}')
print('Marketplace structure valid')
"
```

Expected: `OK: grafana-dpm-finder -> ./grafana-dpm-finder/.claude-plugin/plugin.json` and `Marketplace structure valid`.

- [ ] **Step 5: Verify skill references valid agent**

```bash
python3 -c "
import os
# Check that agent referenced in SKILL.md exists
skill = open('grafana-dpm-finder/skills/analyze/SKILL.md').read()
assert 'agent: grafana-dpm-finder' in skill, 'Skill does not reference agent'
agent_path = 'grafana-dpm-finder/agents/grafana-dpm-finder.md'
assert os.path.exists(agent_path), f'Agent file missing: {agent_path}'
print('Skill -> Agent reference valid')
"
```

Expected: `Skill -> Agent reference valid`

- [ ] **Step 6: Log final git status**

```bash
git log --oneline -10
```

Expected: 7 new commits on the `ai-enablement` branch (one per task 1-7).
