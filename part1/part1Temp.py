#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from typing import Any

import google.auth
from google.api_core.exceptions import NotFound
from google.api_core.extended_operation import ExtendedOperation
from google.cloud import compute_v1


ZONE = 'us-west1-b'
MACHINE_TYPE = 'f1-micro'          # use 'e2-medium' while developing if it's too slow
IMAGE_PROJECT = 'ubuntu-os-cloud'
IMAGE_FAMILY = 'ubuntu-2204-lts'
DISK_SIZE_GB = 10
FIREWALL_NAME = 'allow-5000'
NETWORK_TAG = 'allow-5000'

STARTUP_SCRIPT = """#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
export HOME=/root

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


def wait_for_extended_operation(
    operation: ExtendedOperation, verbose_name: str = "operation", timeout: int = 300
) -> Any:
    """
    Waits for the extended (long-running) operation to complete.

    If the operation is successful, it will return its result.
    If the operation ends with an error, an exception will be raised.
    If there were any warnings during the execution of the operation
    they will be printed to sys.stderr.

    Args:
        operation: a long-running operation you want to wait on.
        verbose_name: (optional) a more verbose name of the operation,
            used only during error and warning reporting.
        timeout: how long (in seconds) to wait for operation to finish.
            If None, wait indefinitely.

    Returns:
        Whatever the operation.result() returns.

    Raises:
        This method will raise the exception received from `operation.exception()`
        or RuntimeError if there is no exception set, but there is an `error_code`
        set for the `operation`.

        In case of an operation taking longer than `timeout` seconds to complete,
        a `concurrent.futures.TimeoutError` will be raised.
    """
    result = operation.result(timeout=timeout)

    if operation.error_code:
        print(
            f"Error during {verbose_name}: [Code: {operation.error_code}]: {operation.error_message}",
            file=sys.stderr,
            flush=True,
        )
        print(f"Operation ID: {operation.name}", file=sys.stderr, flush=True)
        raise operation.exception() or RuntimeError(operation.error_message)

    if operation.warnings:
        print(f"Warnings during {verbose_name}:\n", file=sys.stderr, flush=True)
        for warning in operation.warnings:
            print(f" - {warning.code}: {warning.message}", file=sys.stderr, flush=True)

    return result


def get_image_from_family(project: str, family: str) -> compute_v1.Image:
    """
    Retrieve the newest image that is part of a given family in a project.

    Args:
        project: project ID or project number of the Cloud project you want to get image from.
        family: name of the image family you want to get image from.

    Returns:
        An Image object.
    """
    image_client = compute_v1.ImagesClient()
    # List of public operating system (OS) images: https://cloud.google.com/compute/docs/images/os-details
    newest_image = image_client.get_from_family(project=project, family=family)
    return newest_image

def firewall_exists(firewalls_client, project, name):
    try:
        firewalls_client.get(project=project, firewall=name)
        return True
    except:
        return False

def create_firewall_rule(
    project_id: str, firewall_rule_name: str, network: str = "global/networks/default"
) -> compute_v1.Firewall:
    """
    Creates a simple firewall rule allowing for incoming HTTP and HTTPS access from the entire Internet.

    Args:
        project_id: project ID or project number of the Cloud project you want to use.
        firewall_rule_name: name of the rule that is created.
        network: name of the network the rule will be applied to. Available name formats:
            * https://www.googleapis.com/compute/v1/projects/{project_id}/global/networks/{network}
            * projects/{project_id}/global/networks/{network}
            * global/networks/{network}

    Returns:
        A Firewall object.
    """
    firewall_rule = compute_v1.Firewall()
    firewall_rule.name = firewall_rule_name
    firewall_rule.direction = "INGRESS"

    allowed_ports = compute_v1.Allowed()
    allowed_ports.I_p_protocol = "tcp"
    allowed_ports.ports = ["80", "443"]

    firewall_rule.allowed = [allowed_ports]
    firewall_rule.source_ranges = ["0.0.0.0/0"]
    firewall_rule.network = network
    firewall_rule.description = "Allowing TCP traffic on port 80 and 443 from Internet."

    firewall_rule.target_tags = ["web"]

    # Note that the default value of priority for the firewall API is 1000.
    # If you check the value of `firewall_rule.priority` at this point it
    # will be equal to 0, however it is not treated as "set" by the library and thus
    # the default will be applied to the new rule. If you want to create a rule that
    # has priority == 0, you need to explicitly set it so:
    # TODO: Uncomment to set the priority to 0
    # firewall_rule.priority = 0

    firewall_client = compute_v1.FirewallsClient()
    operation = firewall_client.insert(
        project=project_id, firewall_resource=firewall_rule
    )

    wait_for_extended_operation(operation, "firewall rule creation")

    return firewall_client.get(project=project_id, firewall=firewall_rule_name)

def ensure_firewall_rule(firewalls_client, project):
    if firewall_exists(firewalls_client, project, FIREWALL_NAME):
        print(f'Firewall rule {FIREWALL_NAME} already exists.')
    else:
        print(f'Creating firewall rule {FIREWALL_NAME}...')
        create_firewall_rule(firewalls_client, project)
        
def instance_exists(instances_client, project, zone, name):
    try:
        instances_client.get(project=project, zone=zone, instance=name)
        return True
    except NotFound:
        return False
        

def add_network_tag(instances_client, project, zone, name, tag):
    """Apply a network tag with setTags (needs the current tags fingerprint)."""
    instance = instances_client.get(project=project, zone=zone, instance=name)
    items = set(instance.tags.items)
    items.add(tag)

    tags = compute_v1.Tags(items=sorted(items),
                           fingerprint=instance.tags.fingerprint)
    op = instances_client.set_tags(
        project=project, zone=zone, instance=name, tags_resource=tags)
    wait_for_extended_operation(op, 'setTags')


def get_external_ip(instances_client, project, zone, name):
    instance = instances_client.get(project=project, zone=zone, instance=name)
    return instance.network_interfaces[0].access_configs[0].nat_i_p

def create_instance(
    project_id: str,
    zone: str,
    instance_name: str,
    disks: list[compute_v1.AttachedDisk],
    machine_type: str = "n1-standard-1",
    network_link: str = "global/networks/default",
    subnetwork_link: str = None,
    internal_ip: str = None,
    external_access: bool = False,
    external_ipv4: str = None,
    accelerators: list[compute_v1.AcceleratorConfig] = None,
    preemptible: bool = False,
    spot: bool = False,
    instance_termination_action: str = "STOP",
    custom_hostname: str = None,
    delete_protection: bool = False,
) -> compute_v1.Instance:
    """
    Send an instance creation request to the Compute Engine API and wait for it to complete.

    Args:
        project_id: project ID or project number of the Cloud project you want to use.
        zone: name of the zone to create the instance in. For example: "us-west3-b"
        instance_name: name of the new virtual machine (VM) instance.
        disks: a list of compute_v1.AttachedDisk objects describing the disks
            you want to attach to your new instance.
        machine_type: machine type of the VM being created. This value uses the
            following format: "zones/{zone}/machineTypes/{type_name}".
            For example: "zones/europe-west3-c/machineTypes/f1-micro"
        network_link: name of the network you want the new instance to use.
            For example: "global/networks/default" represents the network
            named "default", which is created automatically for each project.
        subnetwork_link: name of the subnetwork you want the new instance to use.
            This value uses the following format:
            "regions/{region}/subnetworks/{subnetwork_name}"
        internal_ip: internal IP address you want to assign to the new instance.
            By default, a free address from the pool of available internal IP addresses of
            used subnet will be used.
        external_access: boolean flag indicating if the instance should have an external IPv4
            address assigned.
        external_ipv4: external IPv4 address to be assigned to this instance. If you specify
            an external IP address, it must live in the same region as the zone of the instance.
            This setting requires `external_access` to be set to True to work.
        accelerators: a list of AcceleratorConfig objects describing the accelerators that will
            be attached to the new instance.
        preemptible: boolean value indicating if the new instance should be preemptible
            or not. Preemptible VMs have been deprecated and you should now use Spot VMs.
        spot: boolean value indicating if the new instance should be a Spot VM or not.
        instance_termination_action: What action should be taken once a Spot VM is terminated.
            Possible values: "STOP", "DELETE"
        custom_hostname: Custom hostname of the new VM instance.
            Custom hostnames must conform to RFC 1035 requirements for valid hostnames.
        delete_protection: boolean value indicating if the new virtual machine should be
            protected against deletion or not.
    Returns:
        Instance object.
    """
    instance_client = compute_v1.InstancesClient()

    # Use the network interface provided in the network_link argument.
    network_interface = compute_v1.NetworkInterface()
    network_interface.network = network_link
    if subnetwork_link:
        network_interface.subnetwork = subnetwork_link

    if internal_ip:
        network_interface.network_i_p = internal_ip

    if external_access:
        access = compute_v1.AccessConfig()
        access.type_ = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
        access.name = "External NAT"
        access.network_tier = access.NetworkTier.PREMIUM.name
        if external_ipv4:
            access.nat_i_p = external_ipv4
        network_interface.access_configs = [access]

    # Collect information into the Instance object.
    instance = compute_v1.Instance()
    instance.network_interfaces = [network_interface]
    instance.name = instance_name
    instance.disks = disks
    if re.match(r"^zones/[a-z\d\-]+/machineTypes/[a-z\d\-]+$", machine_type):
        instance.machine_type = machine_type
    else:
        instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"

    instance.scheduling = compute_v1.Scheduling()
    if accelerators:
        instance.guest_accelerators = accelerators
        instance.scheduling.on_host_maintenance = (
            compute_v1.Scheduling.OnHostMaintenance.TERMINATE.name
        )

    if preemptible:
        # Set the preemptible setting
        warnings.warn(
            "Preemptible VMs are being replaced by Spot VMs.", DeprecationWarning
        )
        instance.scheduling = compute_v1.Scheduling()
        instance.scheduling.preemptible = True

    if spot:
        # Set the Spot VM setting
        instance.scheduling.provisioning_model = (
            compute_v1.Scheduling.ProvisioningModel.SPOT.name
        )
        instance.scheduling.instance_termination_action = instance_termination_action

    if custom_hostname is not None:
        # Set the custom hostname for the instance
        instance.hostname = custom_hostname

    if delete_protection:
        # Set the delete protection bit
        instance.deletion_protection = True

    # Prepare the request to insert an instance.
    request = compute_v1.InsertInstanceRequest()
    request.zone = zone
    request.project = project_id
    request.instance_resource = instance

    # Wait for the create operation to complete.
    print(f"Creating the {instance_name} instance in {zone}...")

    operation = instance_client.insert(request=request)

    wait_for_extended_operation(operation, "instance creation")

    print(f"Instance {instance_name} created.")
    return instance_client.get(project=project_id, zone=zone, instance=instance_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='flask-vm', help='instance name')
    parser.add_argument('--project', default=None,
                        help='GCP project id (defaults to the ADC project)')
    args = parser.parse_args()

    credentials, default_project = google.auth.default()
    project = args.project or default_project
    if not project:
        sys.exit('No project found. Pass --project or run '
                 '"gcloud config set project <id>".')

    instances_client = compute_v1.InstancesClient(credentials=credentials)
    firewalls_client = compute_v1.FirewallsClient(credentials=credentials)

    # 1. Firewall rule (only created if missing)
    ensure_firewall_rule(firewalls_client, project)

    # 2. Create the VM (skip if it's already there)
    if instance_exists(instances_client, project, ZONE, args.name):
        print(f'Instance {args.name} already exists; reusing it.')
    else:
        create_instance(instances_client, project, ZONE, args.name)

    # 3. Apply the network tag so the firewall rule covers this VM
    print(f'Applying network tag {NETWORK_TAG}...')
    add_network_tag(instances_client, project, ZONE, args.name, NETWORK_TAG)

    # 4. Report the URL
    ip = get_external_ip(instances_client, project, ZONE, args.name)
    print('\nThe Flask application is available at:\n')
    print(f'http://{ip}:5000\n')
    print('Note: the startup script takes a minute or two to finish '
          'installing, so give it a moment before visiting.')


if __name__ == '__main__':
    main()