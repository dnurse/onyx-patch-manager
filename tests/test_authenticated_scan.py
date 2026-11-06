"""Authenticated read-only scanning never persists supplied sudo credentials."""
import asyncio
import json
from unittest.mock import patch

import pytest

from opm.controller import Controller
from opm.remote import inspect_host


def test_password_required_for_a_verified_host(repo, host_data):
    store, config = repo
    host_data['sudo_mode'] = 'password'
    host = store.add_host(host_data)
    store.verified(host['id'])

    async def exercise():
        controller = Controller(config)
        with pytest.raises(ValueError, match='sudo password'):
            await controller.dispatch('scans.start', {})
        # Non-matching host IDs cannot receive an unexpected secret.
        with pytest.raises(ValueError, match='sudo password'):
            await controller.dispatch('scans.start', {
                'sudo_passwords': {'different-host': 'do-not-log'}})
    asyncio.run(exercise())
    assert 'do-not-log' not in config.database.read_bytes().decode('utf-8', 'ignore')


def test_authenticated_scan_separates_user_and_elevated_context(repo, host_data):
    store, config = repo
    host_data['sudo_mode'] = 'password'
    host = store.add_host(host_data)
    password = 'SensitivePass-ForTesting1'
    user = {
        'os': {'family': 'debian', 'packages': []},
        'engines': {
            'docker': {'available': False, 'error': 'permission denied', 'containers': []},
            'podman': {'available': True, 'error': '', 'containers': [{'name':'rootless'}]},
        },
    }
    elevated = {'engines': {
        'docker': {'available': True, 'error': '', 'containers':[{'name':'engine'}]},
        'podman': {'available': True, 'error': '', 'containers':[{'name':'rootful'}]},
    }}
    calls = []

    def fake_ssh(host_arg, config_arg, args, input_text=None, timeout=45):
        calls.append((args, input_text))
        if 'apt-get' in args:
            return ''
        if '--privileged-engines' in args:
            return json.dumps(elevated)
        return json.dumps(user)

    with patch('opm.remote.ssh_command', side_effect=fake_ssh):
        result = inspect_host(host, config, password)
    assert result['os']['metadata'] == 'fresh'
    assert result['engines']['docker']['available']
    assert result['engines']['podman']['containers'][0]['name'] == 'rootless'
    assert calls[0][1] == password + '\n'
    assert calls[1][0][-1] == '--read-only'
    assert calls[2][1] == password + '\n' + __import__('opm.remote', fromlist=['REMOTE_INSPECT']).REMOTE_INSPECT
    assert all(password not in str(args) for args, _ in calls)


def test_auth_scan_failed_apt_does_not_claim_fresh(repo, host_data):
    from opm.remote import RemoteError
    store, config = repo
    host_data['sudo_mode'] = 'password'
    host = store.add_host(host_data)
    user = {'os': {'family': 'debian', 'packages': []}, 'engines': {
        'docker': {'available': True, 'containers': []},
        'podman': {'available': True, 'containers': []}}}
    def fake_ssh(host, config, args, input_text=None, timeout=45):
        if 'apt-get' in args:
            raise RemoteError('sudo: sorry, try again')
        return json.dumps(user)
    with patch('opm.remote.ssh_command', side_effect=fake_ssh):
        result = inspect_host(host, config, 'wrong-password')
    assert result['os']['metadata'] == 'cached'
    assert 'not refreshed' in result['os']['apt_error']


def test_web_scan_has_no_direct_ssh_and_uses_csrf():
    from pathlib import Path
    template = (Path(__file__).parents[1] / 'src/opm/web/templates/web/scan_auth.html').read_text()
    views = (Path(__file__).parents[1] / 'src/opm/web/views.py').read_text()
    assert '{% csrf_token %}' in template
    assert 'type="password"' in template
    assert "sudo_passwords=passwords" in views
    assert "request.session['sudo" not in views
