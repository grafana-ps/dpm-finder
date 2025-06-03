#!/usr/bin/env python3

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
    Calculate the metric rates
    """
    dpm_data = {}
    filtered_metrics = [
        metric for metric in metric_names['data']
        if not any(metric.endswith(suffix) for suffix in ['_count', '_bucket', '_sum'])
      ]
    print(f"Found {len(filtered_metrics)} metrics - checking for DPM")
    for metric in filtered_metrics:
        print(f".", end="", flush=True )
        metric_name = metric
        query = 'count_over_time(%s{__ignore_usage__=""}[5m])/5'%(metric_name)
        query_response = requests.get(
            metric_value_url,
            auth=HTTPBasicAuth(username, api_key),
            params={"query": query},
            timeout=15,
        )
        query_data = query_response.json().get( "data", {}).get("result", [])
        if query_data and len(query_data) > 0 and len(query_data[0].get('value', [])) > 1:
            dpm_data[metric_name] = query_data[0]['value'][1]
        else: 
            continue
    print(f" Done \n Found {len(dpm_data)} metrics with DPM")

    with open("metric_rates.csv", "w", encoding="utf-8") as f:
        # Write CSV header
        f.write("metric_name,dpm\n")
        # Sort items by DPM value in descending order
        sorted_dpm = sorted(dpm_data.items(), key=lambda x: float(x[1]), reverse=True)
        for metric_name, dpm in sorted_dpm:
            if float(dpm) > 1:
                print(f"{metric_name},{dpm}")
                f.write(f"{metric_name},{dpm}\n")


load_dotenv()
prometheus_endpoint=os.getenv("PROMETHEUS_ENDPOINT")
username=os.getenv("PROMETHEUS_USERNAME")
api_key=os.getenv("PROMETHEUS_API_KEY")
metric_value_url=f"{prometheus_endpoint}/api/prom/api/v1/query"
metric_name_url=f"{prometheus_endpoint}/api/prom/api/v1/label/__name__/values"


metric_names = get_metric_names(metric_name_url,username,api_key)
get_metric_rates(metric_value_url,username,api_key,metric_names)
