"""Filesystem locations and restrictive defaults for the local controller.

V1 uses one dedicated, unprivileged Unix account with a lingering systemd user
manager. Its SSH keys and known_hosts file are exclusively owned by that user.
No network server is opened by this application.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Resolved per-user paths shared by CLI, controller and detached workers."""

    base: Path

    @classmethod
    def load(cls) -> "Config":
        """Use an explicit override in tests or XDG data paths in production."""
        default = Path.home() / ".local" / "share" / "opm"
        return cls(Path(os.environ.get("OPM_HOME", default)).expanduser().resolve())

    @property
    def database(self) -> Path:
        return self.base / "state.db"

    @property
    def socket(self) -> Path:
        return self.base / "controller.sock"

    @property
    def known_hosts(self) -> Path:
        return self.base / "known_hosts"

    @property
    def keys(self) -> Path:
        """Private key directory for the dedicated remote OPM identity."""
        return self.base / "keys"

    @property
    def managed_key(self) -> Path:
        return self.keys / "opm_ed25519"

    @property
    def jobs(self) -> Path:
        return self.base / "jobs"

    def ensure(self) -> None:
        """Create private directories, never relaxing existing file ownership."""
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base.chmod(0o700)
        self.keys.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.keys.chmod(0o700)
        self.jobs.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.jobs.chmod(0o700)
        self.known_hosts.touch(mode=0o600, exist_ok=True)
        self.known_hosts.chmod(0o600)
