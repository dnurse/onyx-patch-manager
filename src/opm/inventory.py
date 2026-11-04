"""Build an Ansible YAML/JSON inventory from OPM's managed host records.

The generated inventory is ephemeral, private to one approved patch job, and
never edited by the operator. It contains only connection settings and no
credentials. Ansible's YAML inventory plugin requires each group's ``hosts``
value to be a *mapping*, not an array of host names.
"""

from __future__ import annotations

from shlex import quote


def ansible_inventory(hosts: list[dict], known_hosts: str) -> dict:
    """Return a native Ansible YAML/JSON inventory with inline host variables.

    This is the same format used by ``ansible-inventory -i hosts.json --list``.
    Do not include the ``_meta.hostvars`` structure here: that belongs to the
    *output* of dynamic inventory scripts, not this static inventory file.
    """
    hostvars: dict[str, dict] = {}
    for host in hosts:
        name = str(host["name"])
        if not name or name in hostvars:
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
