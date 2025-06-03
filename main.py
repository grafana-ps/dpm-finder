#!/usr/bin/env python3

"""
main.py - calculate the DPM for a given prometheus cluster
and return the results
"""
import os
import argparse
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

def get_metric_names(url,username,api_key):
    """
    Get the metric names from the Prometheus API
    """

    try:
        response = requests.get(
                url,
                auth=HTTPBasicAuth(username, api_key),
                timeout=15,
                )
        response.raise_for_status()
        data = response.json()
        return data

    except requests.exceptions.RequestException as e:  # This is the correct syntax
        print("There was an error retrieving the data")
        raise SystemExit(e) from e


def get_metric_rates(metric_value_url, username, api_key, metric_names, output_format='csv', min_dpm=1, quiet=False):
    """ 
    Calculate the metric rates
    Args:
        metric_value_url: URL for querying metric values
        username: Prometheus username
        api_key: Prometheus API key
        metric_names: List of metric names to process
        output_format: Format to output results ('csv', 'text'/'txt', or 'json')
        min_dpm: Minimum DPM threshold for showing metrics
        quiet: If True, suppress progress output
    """
    dpm_data = {}
    filtered_metrics = [
        metric for metric in metric_names['data']
        if not any(metric.endswith(suffix) for suffix in ['_count', '_bucket', '_sum'])
      ]
    if not quiet:
        print(f"Found {len(filtered_metrics)} metrics - checking for DPM")
    for metric in filtered_metrics:
        if not quiet:
            print(f".", end="", flush=True)
        metric_name = metric
        query = 'count_over_time(%s{__ignore_usage__=""}[5m])/5'%(metric_name)
        query_response = requests.get(
            metric_value_url,
            auth=HTTPBasicAuth(username, api_key),
            params={"query": query},
            timeout=15,
        )
        query_data = query_response.json().get("data", {}).get("result", [])
        if query_data and len(query_data) > 0 and len(query_data[0].get('value', [])) > 1:
            dpm_data[metric_name] = query_data[0]['value'][1]
        else: 
            continue
    if not quiet:
        print(f" Done \nFound {len(dpm_data)} metrics with DPM")

    metrics_above_threshold = 0
    # Sort items by DPM value in descending order
    sorted_dpm = sorted(dpm_data.items(), key=lambda x: float(x[1]), reverse=True)
    
    if output_format == 'csv':
        with open("metric_rates.csv", "w", encoding="utf-8") as f:
            # Write CSV header
            f.write("metric_name,dpm\n")
            for metric_name, dpm in sorted_dpm:
                if float(dpm) > min_dpm:
                    if not quiet:
                        print(f"{metric_name},{dpm}")
                    f.write(f"{metric_name},{dpm}\n")
                    metrics_above_threshold += 1
    elif output_format == 'json':
        import json
        output_data = {
            "metrics": [
                {"metric_name": metric_name, "dpm": float(dpm)}
                for metric_name, dpm in sorted_dpm
                if float(dpm) > min_dpm
            ],
            "total_metrics_above_threshold": sum(1 for _, dpm in sorted_dpm if float(dpm) > min_dpm)
        }
        with open("metric_rates.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
            if not quiet:
                print(json.dumps(output_data, indent=2))
        metrics_above_threshold = len(output_data["metrics"])
    else:  # text/txt format
        output_filename = "metric_rates.txt"
        with open(output_filename, "w", encoding="utf-8") as f:
            if not quiet:
                print("\nMetrics and their DPM values:")
                print("-" * 50)
            f.write("Metrics and their DPM values:\n")
            f.write("-" * 50 + "\n")
            for metric_name, dpm in sorted_dpm:
                if float(dpm) > min_dpm:
                    if not quiet:
                        print(f"Metric: {metric_name}")
                        print(f"DPM: {dpm}")
                        print("-" * 50)
                    f.write(f"Metric: {metric_name}\n")
                    f.write(f"DPM: {dpm}\n")
                    f.write("-" * 50 + "\n")
                    metrics_above_threshold += 1
    
    if not quiet:
        print(f"\nTotal number of metrics with DPM > {min_dpm}: {metrics_above_threshold}")


def main(): 
    parser = argparse.ArgumentParser(
        description="""
        DPM Finder - A tool to calculate Data Points per Minute (DPM) for Prometheus metrics.
        This script connects to a Prometheus instance, retrieves all metric names,
        calculates their DPM, and outputs the results either in CSV or text format.
        Results are filtered to show only metrics above a specified DPM threshold.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False  # Disable default help to add our own
    )
    
    # Add custom help option
    parser.add_argument(
        '-h', '--help',
        action='help',
        default=argparse.SUPPRESS,
        help='Show this help message and exit'
    )

    parser.add_argument(
        '-f', '--format', 
        choices=['csv', 'text', 'txt', 'json'],
        default='csv',
        help='Output format (default: csv). Note: "text" and "txt" are synonyms'
    )
    parser.add_argument(
        '-m', '--min-dpm',
        type=float,
        default=1.0,
        help='Minimum DPM threshold to show metrics (default: 1.0)'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress progress output and only write results to file in CSV mode'
    )
    args = parser.parse_args()

    if not args.quiet:
        print(f"\nRunning with options:")
        print(f"- Output format: {args.format}")
        print(f"- Minimum DPM threshold: {args.min_dpm}")
        print(f"- Quiet mode: {args.quiet}\n")

    load_dotenv()
    prometheus_endpoint=os.getenv("PROMETHEUS_ENDPOINT")
    username=os.getenv("PROMETHEUS_USERNAME")
    api_key=os.getenv("PROMETHEUS_API_KEY")
    metric_value_url=f"{prometheus_endpoint}/api/prom/api/v1/query"
    metric_name_url=f"{prometheus_endpoint}/api/prom/api/v1/label/__name__/values"

    metric_names = get_metric_names(metric_name_url,username,api_key)
    get_metric_rates(
        metric_value_url,
        username,
        api_key,
        metric_names,
        output_format=args.format,
        min_dpm=args.min_dpm,
        quiet=args.quiet
    )



if __name__ == "__main__":
    main()
