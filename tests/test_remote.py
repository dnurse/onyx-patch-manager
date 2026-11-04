"""Remote client tests only inspect constructed commands; they never SSH."""

import json
import subprocess
from unittest.mock import patch

from opm.remote import _ssh_arguments, inspect_host


def test_ssh_always_enforces_host_key_checking(repo, host_data):
    store, config = repo
    host = store.add_host(host_data)
    args = _ssh_arguments(host, config)
    assert "StrictHostKeyChecking=yes" in args
    assert "BatchMode=yes" in args
    assert f"UserKnownHostsFile={config.known_hosts}" in args
    assert args[-1] == "ansible@192.0.2.24"


def test_inspect_uses_fixed_read_only_script(repo, host_data):
    store, config = repo
    host = store.add_host(host_data)
    payload = {"os": {"family": "debian", "packages": []}, "engines": {"docker": {"containers": []}}}
    completed = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
    with patch("opm.remote.subprocess.run", return_value=completed) as runner:
        result = inspect_host(host, config)
        assert result['engines'] == payload['engines']
        assert result['os']['metadata'] == 'cached'
        command = runner.call_args.args[0]
        assert command[-3:] == ["python3", "-", "--read-only"]
        assert "apt-get upgrade" not in runner.call_args.kwargs["input"]
