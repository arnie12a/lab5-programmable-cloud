#!/usr/bin/env python3

import google.auth
from google.cloud import compute_v1

ZONE = "us-west1-a"
NAME = "vm1"
VM2_NAME = "flask-vm"
PROJECT = google.auth.default()[1]
SERVICE_ACCOUNT_FILE = "service-credentials.json"

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

VM2_CODE = """#!/usr/bin/env python3

from google.oauth2 import service_account
from google.cloud import compute_v1

with open("/srv/service-credentials.json") as f:
    credentials = service_account.Credentials.from_service_account_file(
        "/srv/service-credentials.json"
    )

instances = compute_v1.InstancesClient(credentials=credentials)
images = compute_v1.ImagesClient(credentials=credentials)

PROJECT = open("/srv/project").read().strip()
ZONE = "us-west1-a"
NAME = "flask-vm"

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
    metadata=compute_v1.Metadata(
        items=[
            compute_v1.Items(
                key="startup-script",
                value=open("/srv/vm2-startup-script.sh").read()
            )
        ]
    )
)

instances.insert(
    project=PROJECT,
    zone=ZONE,
    instance_resource=vm
).result()

print("VM-2 created.")
"""

credentials, PROJECT = google.auth.default()

instances = compute_v1.InstancesClient(credentials=credentials)

# Create VM-1 startup script. This script runs on VM-1 and
# downloads the files from instance metadata.
VM1_STARTUP = """#!/bin/bash
set -e

apt-get update
apt-get install -y python3 python3-pip curl

mkdir -p /srv
cd /srv

curl "http://metadata/computeMetadata/v1/instance/attributes/vm2-startup-script" \
    -H "Metadata-Flavor: Google" \
    > vm2-startup-script.sh

curl "http://metadata/computeMetadata/v1/instance/attributes/service-credentials" \
    -H "Metadata-Flavor: Google" \
    > service-credentials.json

curl "http://metadata/computeMetadata/v1/instance/attributes/vm1-launch-vm2-code" \
    -H "Metadata-Flavor: Google" \
    > vm1-launch-vm2-code.py

curl "http://metadata/computeMetadata/v1/instance/attributes/project" \
    -H "Metadata-Flavor: Google" \
    > project

python3 -m pip install --upgrade google-cloud-compute google-auth

python3 /srv/vm1-launch-vm2-code.py
"""

# Read the service-account JSON file
with open(SERVICE_ACCOUNT_FILE) as f:
    service_credentials = f.read()

# Create VM-1
instance = compute_v1.Instance(
    name=NAME,
    machine_type=f"zones/{ZONE}/machineTypes/e2-medium",
    disks=[
        compute_v1.AttachedDisk(
            boot=True,
            auto_delete=True,
            initialize_params=compute_v1.AttachedDiskInitializeParams(
                source_image=(
                    "projects/ubuntu-os-cloud/global/"
                    "images/family/ubuntu-2204-lts"
                ),
                disk_size_gb=10,
                disk_type=f"zones/{ZONE}/diskTypes/pd-standard"
            )
        )
    ],
    network_interfaces=[
        compute_v1.NetworkInterface(
            network="global/networks/default",
            access_configs=[
                compute_v1.AccessConfig(
                    name="External NAT",
                    type_=compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
                )
            ]
        )
    ],
    metadata=compute_v1.Metadata(
        items=[
            compute_v1.Items(
                key="startup-script",
                value=VM1_STARTUP
            ),
            compute_v1.Items(
                key="vm2-startup-script",
                value=VM2_STARTUP
            ),
            compute_v1.Items(
                key="service-credentials",
                value=service_credentials
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

print(f"Creating {NAME}...")

instances.insert(
    project=PROJECT,
    zone=ZONE,
    instance_resource=instance
).result()

print(f"{NAME} created.")
print("VM-1 will now create VM-2.")