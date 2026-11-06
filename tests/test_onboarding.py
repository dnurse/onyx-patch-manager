"""Provisioning unit tests. No network, SSH daemon or remote account is used."""
from pathlib import Path
from unittest.mock import patch
import json
import subprocess
import sys
import types

import pytest

from opm.onboarding import _payload, bootstrap_account, ensure_managed_key, OnboardingError


def test_key_generation_is_private_and_idempotent(repo, monkeypatch):
    _, config = repo
    calls = []

    def pretend_ssh_keygen(command, **kwargs):
        calls.append(command)
        path = Path(command[command.index("-f") + 1])
        path.write_text("private-placeholder")
        Path(str(path) + ".pub").write_text("ssh-ed25519 AAAAexample opm")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("opm.onboarding.subprocess.run", pretend_ssh_keygen)
    first = ensure_managed_key(config)
    second = ensure_managed_key(config)
    assert first == second
    assert len(calls) == 1
    assert first["key_path"] == str(config.managed_key)
    assert config.managed_key.stat().st_mode & 0o777 == 0o600


def test_partial_keypair_never_overwrites_existing_identity(repo):
    _, config = repo
    config.managed_key.write_text("unpaired private key")
    with pytest.raises(OnboardingError, match="incomplete"):
        ensure_managed_key(config)


def test_password_validation_and_no_output_leakage():
    with pytest.raises(OnboardingError, match="12 characters"):
        _payload("ssh-ed25519 AAAA test", "short")
    with pytest.raises(OnboardingError):
        _payload("ssh-ed25519 AAAA test", "bad\nline-1234567")
    message = json.loads(_payload("ssh-ed25519 AAAA test", "sufficient-pass-123"))
    assert message["account"] == "opm"


def test_bootstrap_uses_trusted_paramiko_and_stdin_only(repo, monkeypatch):
    store, config = repo
    host = store.add_host({"name": "node", "address": "192.0.2.45", "ssh_user": "opm", "ssh_port": 22})
    monkeypatch.setattr("opm.onboarding.ensure_managed_key", lambda _: {
        "key_path": "/tmp/mock-opm-key", "public_key": "ssh-ed25519 AAAA-test opm"})
    calls = {}

    class FakeChannel:
        def shutdown_write(self):
            pass
        def recv_exit_status(self):
            return 0

    class FakeStdin:
        channel = FakeChannel()
        def write(self, value):
            calls["sent"] = value
        def flush(self):
            pass

    class FakeStdout:
        channel = FakeChannel()
        def read(self, _=None):
            return b'{"ok": true, "account": "opm"}\n'

    class FakeClient:
        def load_host_keys(self, path):
            calls["known_hosts"] = path
        def set_missing_host_key_policy(self, policy):
            calls["policy"] = type(policy).__name__
        def connect(self, **kwargs):
            calls["login"] = kwargs
        def exec_command(self, cmd, **kwargs):
            if cmd == "sudo -n true":
                # Simulate a remote account that needs a sudo password.
                class NotAllowed(FakeStdout):
                    class channel:
                        @staticmethod
                        def recv_exit_status():
                            return 1
                return FakeStdin(), NotAllowed(), FakeStdout()
            calls["command"] = cmd
            return FakeStdin(), FakeStdout(), FakeStdout()
        def close(self):
            pass

    fake = types.SimpleNamespace(SSHClient=FakeClient, RejectPolicy=type("RejectPolicy", (), {}),
                                 SSHException=type("SSHException", (Exception,), {}),
                                 AuthenticationException=type("AuthenticationException", (Exception,), {}),
                                 BadHostKeyException=type("BadHostKeyException", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "paramiko", fake)
    result = bootstrap_account(host, config, "admin", "LOGIN-SECRET", "SUDO-SECRET", "OPM-SECRET-12345")
    assert result["account"] == "opm"
    assert calls["policy"] == "RejectPolicy"
    assert calls["login"]["allow_agent"] is False
    assert "sudo -k -S" in calls["command"]
    for secret in ("LOGIN-SECRET", "SUDO-SECRET", "OPM-SECRET-12345"):
        assert secret not in calls["command"]
    assert calls["sent"].startswith("SUDO-SECRET\n")
    assert "OPM-SECRET-12345" in calls["sent"]
