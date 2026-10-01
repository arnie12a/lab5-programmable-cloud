#!/usr/bin/env python3

import google.auth
from google.api_core.exceptions import NotFound
from google.cloud import compute_v1

ZONE = "us-west1-b"
NAME = "flask-vm"
IMAGE_PROJECT = "ubuntu-os-cloud"
IMAGE_FAMILY = "ubuntu-2204-lts"
FIREWALL = "allow-5000"
TAG = "allow-5000"

startup = """#!/bin/bash
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

credentials, PROJECT = google.auth.default()
firewalls = compute_v1.FirewallsClient(credentials=credentials)
instances = compute_v1.InstancesClient(credentials=credentials)
images = compute_v1.ImagesClient(credentials=credentials)

# Check/create firewall rule
try:
    firewalls.get(project=PROJECT, firewall=FIREWALL)
    print(f"Firewall rule {FIREWALL} already exists.")
except NotFound:
    print(f"Creating firewall rule {FIREWALL}...")
    rule = compute_v1.Firewall(
        name=FIREWALL,
        network="global/networks/default",
        direction="INGRESS",
        source_ranges=["0.0.0.0/0"],
        target_tags=[TAG],
        allowed=[
            compute_v1.Allowed(
                I_p_protocol="tcp",
                ports=["5000"]
            )
        ]
    )
    firewalls.insert(
        project=PROJECT,
        firewall_resource=rule
    ).result()

# Check/create VM
try:
    instance = instances.get(
        project=PROJECT,
        zone=ZONE,
        instance=NAME
    )
    print(f"Instance {NAME} already exists.")
except NotFound:
    print(f"Creating the {NAME} instance in {ZONE}...")

    image = images.get_from_family(
        project=IMAGE_PROJECT,
        family=IMAGE_FAMILY
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
                type_=compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name,
                network_tier=compute_v1.AccessConfig.NetworkTier.PREMIUM.name
            )
        ]
    )

    instance = compute_v1.Instance(
        name=NAME,
        machine_type=f"zones/{ZONE}/machineTypes/f1-micro",
        disks=[disk],
        network_interfaces=[network],
        metadata=compute_v1.Metadata(
            items=[
                compute_v1.Items(
                    key="startup-script",
                    value=startup
                )
            ]
        )
    )

    instances.insert(
        project=PROJECT,
        zone=ZONE,
        instance_resource=instance
    ).result()

    instance = instances.get(
        project=PROJECT,
        zone=ZONE,
        instance=NAME
    )

# Add network tag only if it is missing
current_tags = instance.tags.items or []

if TAG not in current_tags:
    print(f"Adding network tag {TAG}...")
    tags = compute_v1.Tags(
        items=list(current_tags) + [TAG],
        fingerprint=instance.tags.fingerprint
    )

    instances.set_tags(
        project=PROJECT,
        zone=ZONE,
        instance=NAME,
        tags_resource=tags
    ).result()
else:
    print(f"Network tag {TAG} already exists.")

# Get external IP
instance = instances.get(
    project=PROJECT,
    zone=ZONE,
    instance=NAME
)

ip = instance.network_interfaces[0].access_configs[0].nat_i_p

print(f"\nFlask application: http://{ip}:5000")