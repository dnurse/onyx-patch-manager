"""Regression checks for the eight changes requested in OPM v0.4.7.

The tests are intentionally isolated from live servers. The caller must still
verify ICMP capabilities and password-required sudo on the Debian 13 LXC.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from opm.controller import Controller, SUDO_AUTH_TTL_SECONDS
from opm.remote import REMOTE_OS_INSPECT, probe_icmp_reachability

ROOT = Path(__file__).resolve().parents[1] / 'src/opm/web'


def test_os_only_preflight_is_single_json_and_never_calls_sudo():
    """Run the actual payload locally to detect truncated/spliced Python."""
    assert "'--os-only' in sys.argv" in REMOTE_OS_INSPECT
    process = subprocess.run([sys.executable, '-', '--os-only'],
        input=REMOTE_OS_INSPECT, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    parsed = json.loads(process.stdout)
    assert set(parsed) == {'os'}
    assert isinstance(parsed['os']['packages'], list)
    assert 'sudo: a password is required' not in process.stderr


def test_sudo_authorization_scoped_not_saved_and_expires(repo, host_data, monkeypatch):
    """Only a successful authenticated scan may cache a short-lived secret."""
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'password'})
    store.verified(host['id'], True)
    controller = Controller(config)
    scope = 'a' * 64
    other = 'b' * 64
    secret = 'SENSITIVE-TEMP-CREDENTIAL-123'

    def inspection(host_arg, config_arg, password):
        assert password == secret
        return {'os': {'family': 'debian', 'metadata': 'fresh', 'apt_error': '',
                       'package_query_ok': True, 'packages': [
                           {'name': 'curl', 'current': '1', 'candidate': '2'}]},
                'engines': {'docker': {'available': False, 'installed': False,
                                       'containers': [], 'error': ''}}}

    monkeypatch.setattr('opm.controller.inspect_host', inspection)
    monkeypatch.setattr('opm.controller.check_ansible_runtime', lambda: ('/mock/ansible-playbook', '/mock/bin'))

    async def exercise():
        await controller.scan_many([host], {host['id']: secret}, credential_scope=scope)
        assert controller._cached_password(scope, host['id']) == secret
        assert controller._cached_password(other, host['id']) is None
        status = await controller.dispatch('credentials.status',
            {'credential_scope': scope, 'host_ids': [host['id']]})
        assert 0 < status[host['id']] <= SUDO_AUTH_TTL_SECONDS
        plan = store.create_plan([host['id']])
        store.approve_plan(plan['id'], 'operator', False)
        # Systemd is mocked because the source-testing environment doesn't
        # run the user's dedicated Debian LXC service manager.
        with patch('opm.controller.subprocess.run',
            return_value=subprocess.CompletedProcess([], 0, 'ok', '')):
            # A second session cannot leverage someone else's authorization.
            with pytest.raises(ValueError, match='password'):
                await controller.start_job(plan['id'], 'another-session', {}, other)
            # Verify reuse is passed only to the ephemeral worker broker.
            job = await controller.start_job(plan['id'], 'valid-session', {}, scope)
        claimed = await controller.dispatch('credentials.claim', {'job_id': job['id']})
        assert claimed == {host['id']: secret}
        assert secret not in config.database.read_bytes().decode('latin1')
        # Force expiration without sleeping ten minutes.
        controller._sudo_cache[(scope, host['id'])] = (time.monotonic() - 1, secret)
        assert controller._cached_password(scope, host['id']) is None
    asyncio.run(exercise())


def test_failed_sudo_scan_does_not_remember_password(repo, host_data, monkeypatch):
    store, config = repo
    host = store.add_host({**host_data, 'sudo_mode': 'password'})
    controller = Controller(config)
    monkeypatch.setattr('opm.controller.inspect_host', lambda *args: {
        'os': {'family': 'debian', 'metadata': 'cached',
               'apt_error': 'sudo: a password is required', 'packages': []},
        'engines': {}})
    async def exercise():
        await controller.scan_many([host], {host['id']: 'wrong'}, credential_scope='c'*64)
        assert controller._cached_password('c'*64, host['id']) is None
    asyncio.run(exercise())


def test_no_patch_text_field_and_protected_approval_is_explicit():
    template = (ROOT / 'templates/web/review.html').read_text()
    assert 'pattern="PATCH"' not in template
    assert 'Confirm and patch' in template
    assert 'Confirm protected patch' in template
    assert 'name="protected_ack" value="yes"' in template
    assert 'sudo_cached_seconds' in template


def test_navigation_layout_and_missing_engines():
    base = (ROOT / 'templates/web/base.html').read_text()
    assert base.index('href="{% url \'patch_manager\' %}"') < base.index('href="{% url \'servers\' %}"')
    settings = (ROOT / 'templates/web/settings.html').read_text()
    assert 'account-option' in settings
    host = (ROOT / 'templates/web/host_detail.html').read_text()
    patch = (ROOT / 'templates/web/patch.html').read_text()
    assert 'remove-server-form' in host
    assert 'engine_message:engine' in host
    assert 'engine_message:eng' in patch
    css = (ROOT / 'static/web/styles.css').read_text()
    assert '.danger-zone .disclosure-body .remove-server-form' in css
    assert '.heading-actions .btn, .heading-actions .scan-state-badge' in css
    assert '.intro-actions .btn { min-width:154px; }' in css


def test_icmp_reports_permission_error_without_claiming_down():
    completed = subprocess.CompletedProcess(['ping'], 2, '',
                                             'ping: socket: Operation not permitted')
    with patch('opm.remote.subprocess.run', return_value=completed):
        result = probe_icmp_reachability({'address': '192.0.2.20'})
    assert result['state'] == 'unknown'
    assert 'permitted' in result['detail'].lower()
    js = (ROOT / 'static/web/dashboard.js').read_text()
    assert 'data-host-ping-diagnostic' in js


def test_absent_container_status_has_no_raw_enoent():
    # The same remote script can be run in OS-only mode without a Docker
    # installation, and UI text uses a normalized classification.
    from opm.remote import REMOTE_INSPECT
    assert "'installed': False" in REMOTE_INSPECT
    filter_text = (ROOT / 'templatetags/opm_display.py').read_text()
    assert 'not installed' in filter_text
