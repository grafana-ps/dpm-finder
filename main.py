import json
import requests
import time
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os

def get_metric_names(url,username,api_key):

  response = requests.get(
        url,
        auth=HTTPBasicAuth(username, api_key)
    )
  data = response.json()
  return data


def get_metric_rates(url,username,api_key,metric_names):
  filtered_metrics = [
      metric for metric in metric_names['data'] 
      if not any(metric.endswith(suffix) for suffix in ['_count', '_bucket', '_sum'])
    ]

  with open("metric_rates.txt", "w") as f:
    for metric in filtered_metrics:
      metric_name = metric
      query = f"count_over_time({metric_name}[5m])/5"
      query_response = requests.get(
          query_url,
          auth=HTTPBasicAuth(username, api_key),
          params={"query": query}
      )
      query_data = query_response.json().get( "data", {}).get("result", [])
      if query_data and len(query_data) > 0 and len(query_data[0].get('value', [])) > 1:
        dpm=(query_data[0]['value'][1])
        if float(dpm) > 0:
          print(metric_name, dpm)
          f.write(f"{metric_name} {dpm}\n")
      else:
        continue

load_dotenv()
prometheus_endpoint=os.getenv("PROMETHEUS_ENDPOINT")
username=os.getenv("PROMETHEUS_USERNAME")
api_key=os.getenv("PROMETHEUS_API_KEY")
query_url = "%s/api/prom/api/v1/query"%prometheus_endpoint
url = "%s/api/prom/api/v1/label/__name__/values"%prometheus_endpoint


metric_names = get_metric_names(url,username,api_key)
get_metric_rates(query_url,username,api_key,metric_names)
