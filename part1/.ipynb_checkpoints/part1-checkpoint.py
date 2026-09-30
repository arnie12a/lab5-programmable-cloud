#!/usr/bin/env python3

import argparse
import os
import time
from pprint import pprint

import googleapiclient.discovery
import google.auth
from google.api_core.exceptions import NotFound
from google.cloud import compute_v1

ZONE = 'us-west1-b'
MACHINE_TYPE = 'f1-micro'

credentials, project = google.auth.default()
service = googleapiclient.discovery.build('compute', 'v1', credentials=credentials)

#
# Stub code - just lists all instances
#
def list_instances(compute, project, zone):
    result = compute.instances().list(project=project, zone=zone).execute()
    return result['items'] if 'items' in result else None

print("Your running instances are:")
for instance in list_instances(service, project, 'us-west1-b'):
    print(instance['name'])