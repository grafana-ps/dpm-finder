#!/usr/bin/env python3

"""
Cardinality Analyzer - A tool to investigate metric spike issues in Grafana Cloud Mimir
by analyzing cardinality changes across time windows.
"""

import os
import sys
import time
import argparse
import requests
import json
import csv
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urljoin
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class CardinalityAnalyzer:
    """Main class for analyzing metric cardinality in Mimir"""
    
    def __init__(self, endpoint: str, username: str, api_key: str):
        self.endpoint = endpoint.rstrip('/')
        self.auth = HTTPBasicAuth(username, api_key)
        self.session = requests.Session()
        self.session.auth = self.auth
        
    def parse_time_window(self, window: str, start_time: Optional[str] = None) -> Tuple[int, int]:
        """Parse time window and return start/end timestamps"""
        # Parse duration units
        duration_map = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800
        }
        
        # Extract number and unit from window
        import re
        match = re.match(r'^(\d+)([smhdw])$', window)
        if not match:
            raise ValueError(f"Invalid time window format: {window}. Use format like '30m', '1h', '7d'")
        
        value = int(match.group(1))
        unit = match.group(2)
        window_seconds = value * duration_map[unit]
        
        # Calculate timestamps
        if start_time:
            # Parse start time if provided
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            except ValueError:
                raise ValueError(f"Invalid start time format: {start_time}. Use ISO format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS+00:00 (timezone optional, assumes local time if not specified)")
            end_dt = start_dt + timedelta(seconds=window_seconds)
        else:
            # Use current time if no start time provided (in UTC)
            end_dt = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(seconds=window_seconds)
        
        return int(start_dt.timestamp()), int(end_dt.timestamp())
    
    def query_prometheus(self, query: str, start: int, end: int, step: int = 60) -> Dict[str, Any]:
        """Execute a Prometheus query over a time range"""
        url = urljoin(self.endpoint, '/api/prom/api/v1/query_range')
        params = {
            'query': query,
            'start': start,
            'end': end,
            'step': step
        }
        
        try:
            response = self.session.get(url, params=params, timeout=300)
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') != 'success':
                raise Exception(f"Query failed: {data.get('error', 'Unknown error')}")
            
            return data['data']
        except requests.exceptions.RequestException as e:
            logger.error(f"Error querying Prometheus: {e}")
            raise
    
    def get_top_metrics(self, start: int, end: int, top_n: int = 20) -> List[str]:
        """Get top N metrics by cardinality"""
        query = f'topk({top_n}, count by (__name__)({{__name__=~".+"}}))' 
        
        logger.info(f"Fetching top {top_n} metrics by cardinality...")
        data = self.query_prometheus(query, start, end, step=end-start)
        
        metrics = []
        for result in data.get('result', []):
            metric_name = result['metric'].get('__name__', '')
            if metric_name:
                metrics.append(metric_name)
        
        return metrics
    
    def analyze_metric_cardinality(self, metric_name: str, start: int, end: int) -> Dict[str, Dict[str, Any]]:
        """Analyze cardinality for a specific metric by label"""
        results = {}
        
        # Get all label names for this metric
        url = urljoin(self.endpoint, f'/api/prom/api/v1/labels')
        params = {
            'match[]': f'{metric_name}',
            'start': start,
            'end': end
        }
        
        try:
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            label_names = response.json().get('data', [])
        except Exception as e:
            logger.warning(f"Could not fetch labels for {metric_name}: {e}")
            label_names = []
        
        # Analyze cardinality for each label
        for label in label_names:
            if label.startswith('__'):  # Skip internal labels
                continue
                
            query = f'count by ({label}) ({metric_name})'
            try:
                data = self.query_prometheus(query, start, end, step=end-start)
                
                # Calculate average cardinality over the time window
                label_cardinalities = defaultdict(list)
                for result in data.get('result', []):
                    label_value = result['metric'].get(label, 'unknown')
                    values = [float(v[1]) for v in result['values'] if v[1] != 'NaN']
                    if values:
                        label_cardinalities[label_value].append(max(values))
                
                # Calculate total unique label values
                total_cardinality = len(label_cardinalities)
                
                # Get top label values by cardinality
                top_values = sorted(
                    [(k, max(v)) for k, v in label_cardinalities.items()],
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
                
                results[label] = {
                    'total_cardinality': total_cardinality,
                    'top_values': top_values,
                    'all_values': dict(label_cardinalities)
                }
                
            except Exception as e:
                logger.warning(f"Error analyzing label {label} for metric {metric_name}: {e}")
        
        # Also get overall cardinality
        if label_names:
            label_list = ", ".join(label_names)
            query = f'count(count by (__name__, {label_list}) ({metric_name}))'
        else:
            query = f'count(count by (__name__) ({metric_name}))'
        try:
            data = self.query_prometheus(query, start, end, step=300)
            if data.get('result'):
                values = [float(v[1]) for v in data['result'][0]['values'] if v[1] != 'NaN']
                if values:
                    results['__total__'] = {
                        'max_cardinality': max(values),
                        'avg_cardinality': sum(values) / len(values),
                        'min_cardinality': min(values)
                    }
        except Exception as e:
            logger.warning(f"Error getting total cardinality for {metric_name}: {e}")
        
        return results
    
    def compare_time_windows(self, metric_name: str, window1: Dict, window2: Dict) -> Dict[str, Any]:
        """Compare cardinality between two time windows"""
        comparison = {
            'metric': metric_name,
            'window1': window1,
            'window2': window2,
            'changes': {}
        }
        
        # Get all labels from both windows
        all_labels = set(window1.keys()) | set(window2.keys())
        
        for label in all_labels:
            if label == '__total__':
                continue
                
            w1_data = window1.get(label, {})
            w2_data = window2.get(label, {})
            
            w1_card = w1_data.get('total_cardinality', 0)
            w2_card = w2_data.get('total_cardinality', 0)
            
            if w1_card > 0:
                change_pct = ((w2_card - w1_card) / w1_card) * 100
            else:
                change_pct = 100 if w2_card > 0 else 0
            
            comparison['changes'][label] = {
                'before': w1_card,
                'after': w2_card,
                'change': w2_card - w1_card,
                'change_pct': change_pct
            }
        
        # Sort by absolute change
        comparison['sorted_changes'] = sorted(
            comparison['changes'].items(),
            key=lambda x: abs(x[1]['change']),
            reverse=True
        )
        
        return comparison

def generate_html_output(analyses: List[Dict], comparisons: Optional[List[Dict]] = None, 
                        window: str = "", start_time: str = "") -> str:
    """Generate interactive HTML report"""
    
    # Prepare data for JavaScript
    analyses_json = json.dumps(analyses, default=str)
    comparisons_json = json.dumps(comparisons or [], default=str)
    
    # Format values for the template
    comparison_tab = '<div class="tab" onclick="switchTab(\'comparison\')">Comparison</div>' if comparisons else ''
    start_time_str = f"(starting {start_time})" if start_time else ""
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    # Use string replacement instead of format to avoid conflicts with JavaScript template literals
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Metric Cardinality Analysis Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1, h2, h3 {
            color: #333;
        }
        .info-box {
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
        }
        .metric-section {
            margin: 30px 0;
            padding: 20px;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #e0e0e0;
        }
        th {
            background: #f5f5f5;
            font-weight: 600;
            cursor: pointer;
            user-select: none;
        }
        th:hover {
            background: #e0e0e0;
        }
        tr:hover {
            background: #f9f9f9;
        }
        .chart-container {
            position: relative;
            height: 400px;
            margin: 20px 0;
        }
        .filter-container {
            margin: 20px 0;
            padding: 15px;
            background: #f9f9f9;
            border-radius: 4px;
        }
        .filter-container input {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            width: 300px;
            font-size: 14px;
        }
        .positive-change {
            color: #d32f2f;
            font-weight: 600;
        }
        .negative-change {
            color: #388e3c;
            font-weight: 600;
        }
        .tabs {
            display: flex;
            border-bottom: 2px solid #e0e0e0;
            margin-bottom: 20px;
        }
        .tab {
            padding: 10px 20px;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: all 0.3s;
        }
        .tab:hover {
            background: #f5f5f5;
        }
        .tab.active {
            border-bottom-color: #2196f3;
            color: #2196f3;
            font-weight: 600;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .summary-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        .summary-card {
            padding: 20px;
            background: #f9f9f9;
            border-radius: 8px;
            text-align: center;
        }
        .summary-card h3 {
            margin: 0 0 10px 0;
            color: #666;
            font-size: 14px;
            font-weight: normal;
        }
        .summary-card .value {
            font-size: 32px;
            font-weight: 600;
            color: #333;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Metric Cardinality Analysis Report</h1>
        <p><strong>Analysis Window:</strong> {window} {start_time_str}</p>
        <p><strong>Generated:</strong> {timestamp}</p>
        
        <div class="info-box">
            <h3>How to Use This Report</h3>
            <ul>
                <li><strong>Cardinality</strong> refers to the number of unique time series for a metric</li>
                <li><strong>High cardinality labels</strong> are those with many unique values, which can cause metric spikes</li>
                <li>Click on table headers to sort by that column</li>
                <li>Use the filter box to search for specific metrics or labels</li>
                <li>Charts show the top contributors to cardinality for each metric</li>
                <li>In comparison mode, positive changes (red) indicate increased cardinality</li>
            </ul>
        </div>
        
        <div class="tabs">
            <div class="tab active" onclick="switchTab('analysis')">Cardinality Analysis</div>
            {comparison_tab}
        </div>
        
        <div id="analysis-tab" class="tab-content active">
            <div class="filter-container">
                <input type="text" id="filter" placeholder="Filter metrics or labels..." onkeyup="filterResults()">
            </div>
            
            <div id="analysis-content"></div>
        </div>
        
        <div id="comparison-tab" class="tab-content">
            <div id="comparison-content"></div>
        </div>
    </div>
    
    <script>
        const analysisData = {analyses_json};
        const comparisonData = {comparisons_json};
        let charts = [];
        
        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            if (tab === 'analysis') {
                document.querySelector('.tab:nth-child(1)').classList.add('active');
                document.getElementById('analysis-tab').classList.add('active');
            } else {
                document.querySelector('.tab:nth-child(2)').classList.add('active');
                document.getElementById('comparison-tab').classList.add('active');
            }
        }
        
        function renderAnalysis() {
            const container = document.getElementById('analysis-content');
            let html = '';
            
            analysisData.forEach((analysis, idx) => {
                const metricName = analysis.metric;
                const data = analysis.data;
                const totalInfo = data.__total__ || {};
                
                html += `
                    <div class="metric-section" data-metric="${metricName}">
                        <h2>${metricName}</h2>
                        <div class="summary-cards">
                            <div class="summary-card">
                                <h3>Max Cardinality</h3>
                                <div class="value">${totalInfo.max_cardinality || 'N/A'}</div>
                            </div>
                            <div class="summary-card">
                                <h3>Avg Cardinality</h3>
                                <div class="value">${totalInfo.avg_cardinality ? totalInfo.avg_cardinality.toFixed(0) : 'N/A'}</div>
                            </div>
                            <div class="summary-card">
                                <h3>Labels Analyzed</h3>
                                <div class="value">${Object.keys(data).filter(k => k !== '__total__').length}</div>
                            </div>
                        </div>
                        
                        <div class="chart-container">
                            <canvas id="chart-${idx}"></canvas>
                        </div>
                        
                        <h3>Cardinality by Label</h3>
                        <table id="table-${idx}">
                            <thead>
                                <tr>
                                    <th onclick="sortTable(${idx}, 0)">Label Name ⬍</th>
                                    <th onclick="sortTable(${idx}, 1)">Total Cardinality ⬍</th>
                                    <th onclick="sortTable(${idx}, 2)">Top Values ⬍</th>
                                </tr>
                            </thead>
                            <tbody>
                `;
                
                Object.entries(data).forEach(([label, info]) => {
                    if (label === '__total__') return;
                    
                    const topValues = info.top_values || [];
                    const topValuesStr = topValues.slice(0, 5)
                        .map(([val, count]) => `${val} (${count})`)
                        .join(', ');
                    
                    html += `
                        <tr data-label="${label}">
                            <td>${label}</td>
                            <td>${info.total_cardinality || 0}</td>
                            <td>${topValuesStr}</td>
                        </tr>
                    `;
                });
                
                html += `
                            </tbody>
                        </table>
                    </div>
                `;
            });
            
            container.innerHTML = html;
            
            // Render charts
            analysisData.forEach((analysis, idx) => {
                renderChart(analysis, idx);
            });
        }
        
        function renderChart(analysis, idx) {
            const ctx = document.getElementById(`chart-${idx}`).getContext('2d');
            const data = analysis.data;
            
            // Prepare chart data
            const labels = [];
            const values = [];
            
            Object.entries(data).forEach(([label, info]) => {
                if (label !== '__total__' && info.total_cardinality) {
                    labels.push(label);
                    values.push(info.total_cardinality);
                }
            });
            
            // Sort and take top 10
            const sorted = labels.map((label, i) => ({label, value: values[i]}))
                .sort((a, b) => b.value - a.value)
                .slice(0, 10);
            
            const chart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: sorted.map(item => item.label),
                    datasets: [{
                        label: 'Cardinality',
                        data: sorted.map(item => item.value),
                        backgroundColor: 'rgba(33, 150, 243, 0.6)',
                        borderColor: 'rgba(33, 150, 243, 1)',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: {
                            display: true,
                            text: `Top Labels by Cardinality - ${analysis.metric}`
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: 'Unique Values'
                            }
                        }
                    }
                }
            });
            
            charts.push(chart);
        }
        
        function renderComparison() {
            const container = document.getElementById('comparison-content');
            if (!comparisonData || comparisonData.length === 0) {
                container.innerHTML = '<p>No comparison data available</p>';
                return;
            }
            
            let html = '<h2>Cardinality Changes Between Time Windows</h2>';
            
            comparisonData.forEach(comp => {
                html += `
                    <div class="metric-section">
                        <h3>${comp.metric}</h3>
                        <table>
                            <thead>
                                <tr>
                                    <th>Label</th>
                                    <th>Before</th>
                                    <th>After</th>
                                    <th>Change</th>
                                    <th>Change %</th>
                                </tr>
                            </thead>
                            <tbody>
                `;
                
                comp.sorted_changes.forEach(([label, change]) => {
                    const changeClass = change.change > 0 ? 'positive-change' : 
                                      change.change < 0 ? 'negative-change' : '';
                    
                    html += `
                        <tr>
                            <td>${label}</td>
                            <td>${change.before}</td>
                            <td>${change.after}</td>
                            <td class="${changeClass}">${change.change > 0 ? '+' : ''}${change.change}</td>
                            <td class="${changeClass}">${change.change_pct > 0 ? '+' : ''}${change.change_pct.toFixed(1)}%</td>
                        </tr>
                    `;
                });
                
                html += `
                            </tbody>
                        </table>
                    </div>
                `;
            });
            
            container.innerHTML = html;
        }
        
        function sortTable(tableIdx, columnIdx) {
            const table = document.getElementById(`table-${tableIdx}`);
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            // Determine sort direction
            const th = table.querySelectorAll('th')[columnIdx];
            const isAsc = th.textContent.includes('⬍');
            
            // Update header arrows
            table.querySelectorAll('th').forEach(header => {
                header.textContent = header.textContent.replace(/[⬍⬆]/g, '⬍');
            });
            th.textContent = th.textContent.replace('⬍', isAsc ? '⬆' : '⬍');
            
            // Sort rows
            rows.sort((a, b) => {
                const aVal = a.cells[columnIdx].textContent;
                const bVal = b.cells[columnIdx].textContent;
                
                if (columnIdx === 1) { // Numeric column
                    return isAsc ? 
                        parseInt(aVal) - parseInt(bVal) : 
                        parseInt(bVal) - parseInt(aVal);
                } else { // Text columns
                    return isAsc ? 
                        aVal.localeCompare(bVal) : 
                        bVal.localeCompare(aVal);
                }
            });
            
            // Reorder rows
            rows.forEach(row => tbody.appendChild(row));
        }
        
        function filterResults() {
            const filterValue = document.getElementById('filter').value.toLowerCase();
            
            // Filter metric sections
            document.querySelectorAll('.metric-section').forEach(section => {
                const metricName = section.dataset.metric;
                const showSection = !filterValue || metricName.toLowerCase().includes(filterValue);
                
                if (showSection) {
                    section.style.display = 'block';
                    
                    // Filter rows within the section
                    section.querySelectorAll('tbody tr').forEach(row => {
                        const label = row.dataset.label;
                        const showRow = !filterValue || 
                                      metricName.toLowerCase().includes(filterValue) ||
                                      (label && label.toLowerCase().includes(filterValue));
                        row.style.display = showRow ? '' : 'none';
                    });
                } else {
                    section.style.display = 'none';
                }
            });
        }
        
        // Initialize
        renderAnalysis();
        renderComparison();
    </script>
</body>
</html>
"""
    
    # Replace placeholders without using format() to avoid conflicts with JavaScript ${} syntax
    html_output = html_template.replace('{window}', window)
    html_output = html_output.replace('{start_time_str}', start_time_str)
    html_output = html_output.replace('{timestamp}', timestamp)
    html_output = html_output.replace('{analyses_json}', analyses_json)
    html_output = html_output.replace('{comparisons_json}', comparisons_json)
    html_output = html_output.replace('{comparison_tab}', comparison_tab)
    
    return html_output

def generate_csv_output(analyses: List[Dict], filename: str = "cardinality_analysis.csv"):
    """Generate CSV output"""
    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Metric', 'Label', 'Cardinality', 'Top Values'])
        
        for analysis in analyses:
            metric_name = analysis['metric']
            data = analysis['data']
            
            for label, info in data.items():
                if label == '__total__':
                    continue
                    
                cardinality = info.get('total_cardinality', 0)
                top_values = info.get('top_values', [])
                top_values_str = '; '.join([f"{val}:{count}" for val, count in top_values[:5]])
                
                writer.writerow([metric_name, label, cardinality, top_values_str])
    
    return filename

def generate_cli_output(analyses: List[Dict], comparisons: Optional[List[Dict]] = None):
    """Generate CLI output"""
    print("\n" + "="*80)
    print("METRIC CARDINALITY ANALYSIS")
    print("="*80)
    
    for analysis in analyses:
        metric_name = analysis['metric']
        data = analysis['data']
        total_info = data.get('__total__', {})
        
        print(f"\nMetric: {metric_name}")
        print("-" * 40)
        
        if total_info:
            print(f"  Max Cardinality: {total_info.get('max_cardinality', 'N/A')}")
            print(f"  Avg Cardinality: {total_info.get('avg_cardinality', 'N/A'):.0f}")
        
        print("\n  Top Labels by Cardinality:")
        
        # Sort labels by cardinality
        sorted_labels = sorted(
            [(k, v) for k, v in data.items() if k != '__total__'],
            key=lambda x: x[1].get('total_cardinality', 0),
            reverse=True
        )[:10]
        
        for label, info in sorted_labels:
            cardinality = info.get('total_cardinality', 0)
            print(f"    {label}: {cardinality} unique values")
            
            # Show top 3 values
            top_values = info.get('top_values', [])[:3]
            if top_values:
                values_str = ', '.join([f"{val} ({count})" for val, count in top_values])
                print(f"      Top values: {values_str}")
    
    if comparisons:
        print("\n" + "="*80)
        print("COMPARISON BETWEEN TIME WINDOWS")
        print("="*80)
        
        for comp in comparisons:
            print(f"\nMetric: {comp['metric']}")
            print("-" * 40)
            
            # Show top changes
            for label, change in comp['sorted_changes'][:10]:
                if abs(change['change']) == 0:
                    continue
                    
                sign = '+' if change['change'] > 0 else ''
                print(f"  {label}: {change['before']} → {change['after']} "
                      f"({sign}{change['change']}, {sign}{change['change_pct']:.1f}%)")

def main():
    parser = argparse.ArgumentParser(
        description="Analyze metric cardinality in Grafana Cloud Mimir to investigate spike issues",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('-w', '--window', required=True,
                       help='Time window (e.g., 30m, 1h, 24h, 7d)')
    parser.add_argument('-s', '--start-time',
                       help='Start time in ISO format (e.g., 2024-01-15T10:00:00 for local time, 2024-01-15T10:00:00Z or 2024-01-15T10:00:00+00:00 for UTC). If not provided, uses current UTC time minus window')
    parser.add_argument('-m', '--metric',
                       help='Specific metric to analyze. If not provided, analyzes top metrics')
    parser.add_argument('--top-n', type=int, default=20,
                       help='Number of top metrics to analyze when no specific metric is provided (default: 20)')
    parser.add_argument('-o', '--output', default='html',
                       choices=['cli', 'csv', 'html', 'all'],
                       help='Output format (default: html)')
    
    # Comparison options
    parser.add_argument('--compare', action='store_true',
                       help='Enable comparison mode')
    parser.add_argument('--compare-window',
                       help='Time window for comparison (required if --compare is used)')
    parser.add_argument('--compare-start-time',
                       help='Start time for comparison window (same format as --start-time)')
    
    args = parser.parse_args()
    
    # Validate environment variables
    endpoint = os.getenv('PROMETHEUS_ENDPOINT')
    username = os.getenv('PROMETHEUS_USERNAME')
    api_key = os.getenv('PROMETHEUS_API_KEY')
    
    if not all([endpoint, username, api_key]):
        logger.error("Missing required environment variables. Please ensure PROMETHEUS_ENDPOINT, "
                    "PROMETHEUS_USERNAME, and PROMETHEUS_API_KEY are set in your .env file")
        sys.exit(1)
    
    # Validate comparison arguments
    if args.compare and not args.compare_window:
        logger.error("--compare-window is required when using --compare")
        sys.exit(1)
    
    # Initialize analyzer
    analyzer = CardinalityAnalyzer(endpoint, username, api_key)
    
    try:
        # Parse time windows
        start_ts, end_ts = analyzer.parse_time_window(args.window, args.start_time)
        logger.info(f"Analyzing window: {datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} to {datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Determine which metrics to analyze
        if args.metric:
            metrics_to_analyze = [args.metric]
        else:
            # Get top metrics by cardinality
            metrics_to_analyze = analyzer.get_top_metrics(start_ts, end_ts, args.top_n)
            if not metrics_to_analyze:
                logger.error("No metrics found to analyze")
                sys.exit(1)
            logger.info(f"Analyzing top {len(metrics_to_analyze)} metrics")
        
        # Analyze each metric
        analyses = []
        for metric in metrics_to_analyze:
            logger.info(f"Analyzing cardinality for metric: {metric}")
            try:
                cardinality_data = analyzer.analyze_metric_cardinality(metric, start_ts, end_ts)
                analyses.append({
                    'metric': metric,
                    'data': cardinality_data,
                    'window': {'start': start_ts, 'end': end_ts}
                })
            except Exception as e:
                logger.warning(f"Failed to analyze {metric}: {e}")
        
        if not analyses:
            logger.error("No successful analyses completed")
            sys.exit(1)
        
        # Handle comparison if requested
        comparisons = None
        if args.compare:
            comp_start, comp_end = analyzer.parse_time_window(args.compare_window, args.compare_start_time)
            logger.info(f"Comparing with window: {datetime.fromtimestamp(comp_start, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} to {datetime.fromtimestamp(comp_end, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
            
            comparisons = []
            for metric in metrics_to_analyze:
                try:
                    logger.info(f"Analyzing comparison window for metric: {metric}")
                    comp_data = analyzer.analyze_metric_cardinality(metric, comp_start, comp_end)
                    
                    # Find the original analysis
                    orig_analysis = next((a for a in analyses if a['metric'] == metric), None)
                    if orig_analysis:
                        comparison = analyzer.compare_time_windows(
                            metric,
                            orig_analysis['data'],
                            comp_data
                        )
                        comparisons.append(comparison)
                except Exception as e:
                    logger.warning(f"Failed to compare {metric}: {e}")
        
        # Generate outputs
        if args.output in ['cli', 'all']:
            generate_cli_output(analyses, comparisons)
        
        if args.output in ['csv', 'all']:
            csv_file = generate_csv_output(analyses)
            logger.info(f"CSV output written to: {csv_file}")
        
        if args.output in ['html', 'all']:
            html_content = generate_html_output(
                analyses, comparisons,
                args.window,
                args.start_time or ""
            )
            html_file = "cardinality_analysis.html"
            with open(html_file, 'w') as f:
                f.write(html_content)
            logger.info(f"HTML report written to: {html_file}")
            if args.output == 'html':
                print(f"\nOpen {html_file} in your browser to view the interactive report")
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()