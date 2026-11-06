"""Inventory tests prevent unintended hosts and passwords reaching Ansible."""

import pytest

from opm.inventory import ansible_inventory
from opm.validation import sanitize, validate_host


def test_validated_host_does_not_allow_ssh_options(host_data):
    """A hostile address must not become a new SSH CLI option."""
    with pytest.raises(ValueError):
        validate_host({**host_data, "address": "-oStrictHostKeyChecking=no"})
    with pytest.raises(ValueError):
        validate_host({**host_data, "ssh_user": "root;cat /etc/shadow"})
    with pytest.raises(ValueError):
        validate_host({**host_data, "ssh_port": 70000})


def test_inventory_matches_hosts_without_passwords(repo, host_data):
    store, config = repo
    host = store.add_host(host_data)
    data = ansible_inventory([host], str(config.known_hosts))
    assert set(data["all"]["hosts"]) == {"debian-vm"}
    vars = data["all"]["hosts"]["debian-vm"]
    assert vars["ansible_host"] == "192.0.2.24"
    assert "StrictHostKeyChecking=yes" in vars["ansible_ssh_common_args"]
    assert "password" not in str(vars).lower()


def test_simple_secret_redaction():
    assert sanitize("token=abcd123 secret:xyz") == "token=[REDACTED] secret:[REDACTED]"
