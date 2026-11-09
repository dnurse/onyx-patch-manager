"""Build an Ansible YAML/JSON inventory from OPM's managed host records.

The generated inventory is ephemeral, private to one approved patch job, and
never edited by the operator. It contains only connection settings and no
credentials. Ansible's YAML inventory plugin requires each group's ``hosts``
value to be a *mapping*, not an array of host names.
"""

from __future__ import annotations

import re
from shlex import quote


_ORIGINAL_INVENTORY_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_HOST_ID = re.compile(r"[0-9a-f]{16}\Z")


def inventory_alias(host: dict) -> str:
    """Stable Ansible identity, independent of a server's human display name.

    Preserve historical inventory names for hosts without spaces. For spaced
    display names, use the inventory record's immutable ID. The alias is safe
    in host patterns and Ansible Runner event/recap names; never slugify names
    (which could collide or change when an administrator renames a server).
    """
    name = str(host.get('name') or '')
    if _ORIGINAL_INVENTORY_NAME.fullmatch(name):
        return name
    identifier = str(host.get('id') or '')
    if not _HOST_ID.fullmatch(identifier):
        raise ValueError('Ansible requires a valid inventory ID for this display name')
    return 'opm-' + identifier


def ansible_inventory(hosts: list[dict], known_hosts: str) -> dict:
    """Return a native Ansible YAML/JSON inventory with inline host variables.

    This is the same format used by ``ansible-inventory -i hosts.json --list``.
    Do not include the ``_meta.hostvars`` structure here: that belongs to the
    *output* of dynamic inventory scripts, not this static inventory file.
    """
    hostvars: dict[str, dict] = {}
    for host in hosts:
        name = inventory_alias(host)
        if name in hostvars:
            raise ValueError("Ansible inventory host names must be nonempty and unique")
        params = {
            "ansible_host": host["address"],
            "ansible_user": host["ssh_user"],
            "ansible_port": int(host["ssh_port"]),
            "ansible_connection": "ssh",
            "ansible_ssh_common_args": (
                "-o StrictHostKeyChecking=yes "
                f"-o UserKnownHostsFile={quote(known_hosts)} -o BatchMode=yes"
            ),
            "ansible_become": host.get("sudo_mode") != "root",
            "ansible_become_method": "sudo",
        }
        if host.get("key_path"):
            params["ansible_ssh_private_key_file"] = host["key_path"]
        hostvars[name] = params
    return {"all": {"hosts": hostvars}}
