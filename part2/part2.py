#!/usr/bin/env python3

import time

import google.auth
from google.api_core.exceptions import NotFound
from google.cloud import compute_v1

ZONE = "us-west1-a"
INSTANCE = "flask-vm"
SNAPSHOT = f"base-snapshot-{INSTANCE}"
CLONES = ["flask-vm-1", "flask-vm-2", "flask-vm-3"]
MACHINE_TYPE = "e2-medium"
TAG = "allow-5000"

# Start Flask when each cloned VM boots.
STARTUP = """#!/bin/bash
set -e
cd /opt/app/flask-tutorial
export FLASK_APP=flaskr
flask init-db
nohup flask run -h 0.0.0.0 > /var/log/flask.log 2>&1 &
"""

credentials, PROJECT = google.auth.default()

instances = compute_v1.InstancesClient(credentials=credentials)
disks = compute_v1.DisksClient(credentials=credentials)
snapshots = compute_v1.SnapshotsClient(credentials=credentials)

# Find the original VM.
instance = instances.get(
    project=PROJECT,
    zone=ZONE,
    instance=INSTANCE
)

# Find its boot disk.
disk_name = instance.disks[0].source.split("/")[-1]

# Delete the old snapshot if it exists.
try:
    snapshots.get(
        project=PROJECT,
        snapshot=SNAPSHOT
    )

    print(f"Deleting old snapshot {SNAPSHOT}...")

    snapshots.delete(
        project=PROJECT,
        snapshot=SNAPSHOT
    ).result()

    print(f"Snapshot {SNAPSHOT} deleted.")

except NotFound:
    print(f"Snapshot {SNAPSHOT} does not exist.")

# Create a fresh snapshot from the original VM's boot disk.
print(f"Creating snapshot {SNAPSHOT}...")

disk = disks.get(
    project=PROJECT,
    zone=ZONE,
    disk=disk_name
)

snapshot = compute_v1.Snapshot(
    name=SNAPSHOT,
    source_disk=disk.self_link
)

snapshots.insert(
    project=PROJECT,
    snapshot_resource=snapshot
).result()

print(f"Snapshot {SNAPSHOT} created.")

# Delete old cloned VMs so that every run creates three fresh VMs.
for name in CLONES:
    try:
        instances.get(
            project=PROJECT,
            zone=ZONE,
            instance=name
        )

        print(f"Deleting old instance {name}...")

        instances.delete(
            project=PROJECT,
            zone=ZONE,
            instance=name
        ).result()

        print(f"Instance {name} deleted.")

    except NotFound:
        pass

# Create three VMs from the snapshot and measure creation time.
timings = []

for name in CLONES:
    disk = compute_v1.AttachedDisk(
        boot=True,
        auto_delete=True,
        initialize_params=compute_v1.AttachedDiskInitializeParams(
            source_snapshot=(
                f"projects/{PROJECT}/global/snapshots/{SNAPSHOT}"
            ),
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

    clone = compute_v1.Instance(
        name=name,
        machine_type=f"zones/{ZONE}/machineTypes/{MACHINE_TYPE}",
        disks=[disk],
        network_interfaces=[network],
        tags=compute_v1.Tags(items=[TAG]),
        metadata=compute_v1.Metadata(
            items=[
                compute_v1.Items(
                    key="startup-script",
                    value=STARTUP
                )
            ]
        )
    )

    print(f"Creating {name}...")

    start = time.time()

    instances.insert(
        project=PROJECT,
        zone=ZONE,
        instance_resource=clone
    ).result()

    elapsed = time.time() - start
    timings.append((name, elapsed))

    print(f"{name} created in {elapsed:.2f} seconds.")

# Write the measured times to TIMING.md.
with open("TIMING.md", "w") as file:
    file.write("# VM Clone Timing\n\n")

    for name, elapsed in timings:
        file.write(f"- {name}: {elapsed:.2f} seconds\n")

print("\nTiming results written to TIMING.md")