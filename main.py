"""
main.py - calculate the DPM for a given prometheus cluster
and return the results
"""
import os
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


def get_metric_rates(url,username,api_key,metric_names):
    """ 
    Calculate the metric rates and active series count
    """
    filtered_metrics = [
        metric for metric in metric_names['data']
        if not any(metric.endswith(suffix) for suffix in ['_count', '_bucket', '_sum'])
      ]

    with open("metric_rates.txt", "w", encoding="utf-8") as f:
        # Write header row
        f.write("METRIC_NAME DPM ACTIVE_SERIES TOTAL_ACTIVE_SERIES\n")
        
        for metric in filtered_metrics:
            metric_name = metric
            
            # Query 1: Get DPM (Data Points per Minute)
            dpm_query = f"count_over_time({metric_name}[5m])/5"
            dpm_response = requests.get(
                url,
                auth=HTTPBasicAuth(username, api_key),
                params={"query": dpm_query},
                timeout=15,
            )
            dpm_data = dpm_response.json().get("data", {}).get("result", [])
            
            # Query 2: Get active series count
            series_query = f"count by (__name__) ({metric_name})"
            series_response = requests.get(
                url,
                auth=HTTPBasicAuth(username, api_key),
                params={"query": series_query},
                timeout=15,
            )
            series_data = series_response.json().get("data", {}).get("result", [])
            
            # Process DPM data
            dpm = 0
            if dpm_data and len(dpm_data) > 0 and len(dpm_data[0].get('value', [])) > 1:
                dpm = float(dpm_data[0]['value'][1])
            
            # Process series count data
            active_series = 0
            if series_data and len(series_data) > 0 and len(series_data[0].get('value', [])) > 1:
                active_series = int(float(series_data[0]['value'][1]))
            
            # Only output metrics with data
            if dpm > 0 and active_series > 0:
                active_series_dpm = active_series * dpm
                output_line = f"{metric_name} {dpm} {active_series} {active_series_dpm}"
                print(output_line)
                f.write(f"{output_line}\n")
            else:
                continue

load_dotenv()
prometheus_endpoint=os.getenv("PROMETHEUS_ENDPOINT")
username=os.getenv("PROMETHEUS_USERNAME")
api_key=os.getenv("PROMETHEUS_API_KEY")
metric_value_url=f"{prometheus_endpoint}/api/prom/api/v1/query"
metric_name_url=f"{prometheus_endpoint}/api/prom/api/v1/label/__name__/values"


metric_names = get_metric_names(metric_name_url,username,api_key)
get_metric_rates(metric_value_url,username,api_key,metric_names)
