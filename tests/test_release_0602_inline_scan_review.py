"""v0.6.2: stale patch review uses a selected read-only scan before planning."""
from pathlib import Path
import asyncio
import time
import pytest

from opm.controller import Controller

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def test_scan_selected_targets_not_the_entire_inventory(repo, host_data, monkeypatch):
    store, config = repo
    first = store.add_host(host_data)
    store.verified(first['id'], True)
    store.update_host(first['id'], {**first, 'sudo_mode': 'passwordless'})
    other = store.add_host({**host_data, 'name': 'other-vm', 'address': '192.0.2.25'})
    store.verified(other['id'], True)
    store.update_host(other['id'], {**other, 'sudo_mode': 'passwordless'})
    seen = []

    def fake_scan(host, _, password):
        seen.append(host['id'])
        return {'os': {'family': 'debian', 'metadata': 'fresh', 'packages': []}, 'engines': {}}
    monkeypatch.setattr('opm.controller.inspect_host', fake_scan)

    async def run():
        ctl = Controller(config)
        submission = await ctl.dispatch('scans.start', {'ids': [first['id']]})
        assert submission['scheduled'] == 1
        await asyncio.gather(*tuple(ctl._scans))
        assert (await ctl.dispatch('activities.get', {'id': submission['activity_id']}))['state'] == 'completed'
        assert store.last_scan(first['id'])['state'] == 'completed'
        assert store.last_scan(other['id']) is None
        assert seen == [first['id']]
    asyncio.run(run())


def test_selected_scans_validate_targets_before_remote_execution(repo, host_data):
    store, config = repo
    verified = store.add_host(host_data)
    store.verified(verified['id'], True)
    unverified = store.add_host({**host_data, 'name': 'not-ready', 'address': '192.0.2.26'})

    async def run():
        ctl = Controller(config)
        for value in ([], [verified['id'], verified['id']], [verified['id'], 'missing'],
                      [verified['id'], unverified['id']], 'invalid'):
            with pytest.raises(ValueError):
                await ctl.dispatch('scans.start', {'ids': value})
        assert store.last_scan(verified['id']) is None
        assert store.last_scan(unverified['id']) is None
    asyncio.run(run())


def test_selected_scan_password_scope_and_no_persistence(repo, host_data, monkeypatch):
    store, config = repo
    h = store.add_host({**host_data, 'sudo_mode': 'password'})
    store.verified(h['id'], True)
    password = 'TEST-SUDO-PASSWORD-SHOULD-NOT-BE-SAVED'
    monkeypatch.setattr('opm.controller.inspect_host', lambda h, c, p: {
        'os': {'family': 'debian', 'metadata': 'fresh', 'packages': []}, 'engines': {}})

    async def run():
        ctl = Controller(config)
        with pytest.raises(ValueError, match='sudo password'):
            await ctl.dispatch('scans.start', {'ids': [h['id']], 'credential_scope': 'a' * 64})
        submission = await ctl.dispatch('scans.start', {
            'ids': [h['id']], 'sudo_passwords': {h['id']: password}, 'credential_scope': 'a' * 64})
        await asyncio.gather(*tuple(ctl._scans))
        assert ctl.store.activity(submission['activity_id'])['state'] == 'completed'
    asyncio.run(run())
    assert password.encode() not in config.database.read_bytes()


def test_review_stale_scans_are_redirected_to_inline_authorization():
    views = (WEB / 'views.py').read_text()
    urls = (WEB / 'urls.py').read_text()
    patch = (WEB / 'templates/web/patch.html').read_text()
    auth = (WEB / 'templates/web/review_refresh.html').read_text()
    progress = (WEB / 'templates/web/review_refresh_progress.html').read_text()
    assert 'if _refresh_candidates(ids):' in views
    assert "return redirect('review_refresh')" in views
    assert 'request.session[_REVIEW_SESSION_KEY]' in views
    assert 'request.session[_REVIEW_SESSION_KEY] = {' in views
    # Session data can only contain selected IDs, execution mode, and a timestamp.
    section = views.split('request.session[_REVIEW_SESSION_KEY] = {', 1)[1].split('}', 1)[0]
    assert 'password' not in section
    assert 'refresh_metadata' in section
    assert 'name="shared_sudo_password"' in auth
    assert '{% csrf_token %}' in auth and '{% csrf_token %}' in progress
    assert "name=\"action\" value=\"refresh\"" in auth
    assert 'review_refresh_finish' in urls
    assert '@require_POST\ndef review_refresh_finish' in views
    assert "activity['state'] in ('completed', 'partial')" in views
    assert 'id="continue-patch-review"' in progress
    assert 'window.location' not in progress
    assert 'No patch job was started' in progress
    assert 'If a scan is over an hour old or missing' in patch


def test_controller_does_not_relax_one_hour_immutable_plan_safety():
    source = (ROOT / 'src/opm/store.py').read_text()
    assert 'now() - scan["finished"] > 3600' in source
    assert 'run a fresh scan (within 1 hour)' in source
    assert 'package_query_ok' in source
