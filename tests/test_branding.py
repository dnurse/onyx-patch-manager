"""Regression tests for the public OPM product identity.

A project rename is more than an interface string: the executable, Python
module, service units and storage path must agree or detached patch workers
will no longer be able to locate the controller database.
"""

from pathlib import Path
import tomllib

from opm import __version__
from opm.config import Config


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_command_and_version():
    """Make sure installation exposes the renamed console command."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["name"] == "onyx-patch-manager"
    assert metadata["project"]["scripts"] == {"opm": "opm.cli:main"}
    assert metadata["project"]["version"] == __version__


def test_default_storage_and_worker_service(monkeypatch, tmp_path):
    """Controller storage and systemd unit naming must use the same prefix."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPM_HOME", raising=False)
    assert Config.load().base == tmp_path / ".local" / "share" / "opm"
    service = (ROOT / "systemd" / "opm-controller.service").read_text()
    assert "opm controller" in service
    assert "opm-venv" in service
