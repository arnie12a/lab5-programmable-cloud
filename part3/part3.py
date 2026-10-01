#!/usr/bin/env python3

import google.auth
from google.cloud import compute_v1

ZONE = "us-west1-a"
VM1_NAME = "part3-vm1"
VM2_NAME = "part3-vm2"
MACHINE_TYPE = "e2-medium"
SERVICE_ACCOUNT_FILE = "service-credentials.json"

# Startup script that will run on VM-2.
VM2_STARTUP = """#!/bin/bash
set -e

apt-get update
apt-get install -y python3 python3-pip git

mkdir -p /opt/app
cd /opt/app

git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial
cd flask-tutorial

python3 setup.py install
pip3 install -e .

export FLASK_APP=flaskr
flask init-db

nohup flask run -h 0.0.0.0 > /var/log/flask.log 2>&1 &
"""

# Python program that will run INSIDE VM-1.
# It uses the service-account credentials to create VM-2.
VM2_CODE = """#!/usr/bin/env python3

from google.oauth2 import service_account
from google.cloud import compute_v1

PROJECT = open("/srv/project").read().strip()
ZONE = "us-west1-a"
NAME = "part3-vm2"

credentials = service_account.Credentials.from_service_account_file(
    "/srv/service-credentials.json"
)

instances = compute_v1.InstancesClient(credentials=credentials)
images = compute_v1.ImagesClient(credentials=credentials)

startup = open("/srv/vm2-startup-script.sh").read()

image = images.get_from_family(
    project="ubuntu-os-cloud",
    family="ubuntu-2204-lts"
)

disk = compute_v1.AttachedDisk(
    boot=True,
    auto_delete=True,
    initialize_params=compute_v1.AttachedDiskInitializeParams(
        source_image=image.self_link,
        disk_size_gb=10,
        disk_type=f"zones/{ZONE}/diskTypes/pd-standard"
    )
)

network = compute_v1.NetworkInterface(
    network="global/networks/default",
    access_configs=[
        compute_v1.AccessConfig(
            name="External NAT",
            type_=compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
        )
    ]
)

vm = compute_v1.Instance(
    name=NAME,
    machine_type=f"zones/{ZONE}/machineTypes/e2-medium",
    disks=[disk],
    network_interfaces=[network],
    tags=compute_v1.Tags(items=["allow-5000"]),
    metadata=compute_v1.Metadata(
        items=[
            compute_v1.Items(
                key="startup-script",
                value=startup
            )
        ]
    )
)

print("Creating VM-2...")

instances.insert(
    project=PROJECT,
    zone=ZONE,
    instance_resource=vm
).result()

print("VM-2 created.")
"""

# Startup script for VM-1.
# This downloads the information passed through VM-1 metadata
# and then runs the VM-2 creation program.
VM1_STARTUP = """#!/bin/bash
set -e

apt-get update
apt-get install -y python3 python3-pip curl

mkdir -p /srv
cd /srv

curl -s \
"http://metadata.google.internal/computeMetadata/v1/instance/attributes/service-credentials" \
-H "Metadata-Flavor: Google" \
> service-credentials.json

curl -s \
"http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm2-startup-script" \
-H "Metadata-Flavor: Google" \
> vm2-startup-script.sh

curl -s \
"http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm1-launch-vm2-code" \
-H "Metadata-Flavor: Google" \
> vm1-launch-vm2-code.py

curl -s \
"http://metadata.google.internal/computeMetadata/v1/instance/attributes/project" \
-H "Metadata-Flavor: Google" \
> project

python3 -m pip install --upgrade google-cloud-compute google-auth

python3 /srv/vm1-launch-vm2-code.py
"""

credentials, PROJECT = google.auth.default()

instances = compute_v1.InstancesClient(
    credentials=credentials
)

# Read the service-account credentials from the local file.
with open(SERVICE_ACCOUNT_FILE) as file:
    service_credentials = file.read()

# Create VM-1's boot disk.
disk = compute_v1.AttachedDisk(
    boot=True,
    auto_delete=True,
    initialize_params=compute_v1.AttachedDiskInitializeParams(
        source_image=(
            "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
        ),
        disk_size_gb=10,
        disk_type=f"zones/{ZONE}/diskTypes/pd-standard"
    )
)

# Create VM-1's network interface.
network = compute_v1.NetworkInterface(
    network="global/networks/default",
    access_configs=[
        compute_v1.AccessConfig(
            name="External NAT",
            type_=compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
        )
    ]
)

# Create VM-1.
vm1 = compute_v1.Instance(
    name=VM1_NAME,
    machine_type=f"zones/{ZONE}/machineTypes/{MACHINE_TYPE}",
    disks=[disk],
    network_interfaces=[network],
    metadata=compute_v1.Metadata(
        items=[
            compute_v1.Items(
                key="startup-script",
                value=VM1_STARTUP
            ),
            compute_v1.Items(
                key="service-credentials",
                value=service_credentials
            ),
            compute_v1.Items(
                key="vm2-startup-script",
                value=VM2_STARTUP
            ),
            compute_v1.Items(
                key="vm1-launch-vm2-code",
                value=VM2_CODE
            ),
            compute_v1.Items(
                key="project",
                value=PROJECT
            )
        ]
    )
)

print(f"Creating {VM1_NAME}...")

instances.insert(
    project=PROJECT,
    zone=ZONE,
    instance_resource=vm1
).result()

print(f"{VM1_NAME} created.")
print("VM-1 will now create VM-2.")
