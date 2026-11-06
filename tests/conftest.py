"""Shared test fixtures with private temporary controller state."""

import pytest

from opm.config import Config
from opm.store import Store


@pytest.fixture
def repo(tmp_path):
    """Create a real SQLite repository without requiring SSH or systemd."""
    config = Config(tmp_path / "opm")
    config.ensure()
    return Store(config.database), config


@pytest.fixture
def host_data():
    return {"name": "debian-vm", "address": "192.0.2.24", "ssh_user": "ansible",
            "ssh_port": 22, "group_name": "Linux VMs", "protected": False}
