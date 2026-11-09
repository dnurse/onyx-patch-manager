"""Regression: selected Docker Hub branding must not silently fall back to Debian."""
from __future__ import annotations
import hashlib
from pathlib import Path
import pytest
from opm import server_logos as logos

ROOT = Path(__file__).resolve().parents[1]
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20


def test_app_icon_resolves_from_container_basename_without_hardcoded_repository(monkeypatch):
    requests = []
    def fake_get(url, **kwargs):
        requests.append(url)
        if 'cdn.jsdelivr.net' in url:
            return PNG
        raise OSError('Docker Hub unavailable')
    monkeypatch.setattr(logos, '_get', fake_get)
    assert logos._lookup_curated_logo('jc21/nginx-proxy-manager') == (PNG, 'image/png')
    assert requests == ['https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/nginx-proxy-manager.png']
    requests.clear()
    assert logos._lookup_curated_logo('anotherowner/nginx-proxy-manager') == (PNG, 'image/png')
    assert requests == ['https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/nginx-proxy-manager.png']


def test_logo_uses_curated_source_when_docker_hub_lacks_art(monkeypatch, tmp_path):
    def missing_hub(repo):
        assert repo == 'jc21/nginx-proxy-manager'
        return None
    monkeypatch.setattr(logos, '_lookup_hub_logo', missing_hub)
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda repo: (PNG, 'image/png'))
    assert logos.cached_logo('jc21/nginx-proxy-manager', tmp_path) == (PNG, 'image/png')
    # Cached result is not looked up repeatedly.
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda repo: pytest.fail('Must use cache'))
    assert logos.cached_logo('jc21/nginx-proxy-manager', tmp_path) == (PNG, 'image/png')


def test_previous_negative_cache_does_not_hide_new_art(monkeypatch, tmp_path):
    repo='jc21/nginx-proxy-manager'
    old_key=hashlib.sha256(repo.encode()).hexdigest()
    (tmp_path / (old_key+'.miss')).touch()
    monkeypatch.setattr(logos, '_lookup_hub_logo', lambda _repo: None)
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda _repo: (PNG, 'image/png'))
    assert logos.cached_logo(repo, tmp_path) == (PNG, 'image/png')


def test_empty_registry_and_curated_results_show_distro(monkeypatch, tmp_path):
    monkeypatch.setattr(logos, '_lookup_hub_logo', lambda repo: None)
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda repo: None)
    assert logos.cached_logo('jc21/nginx-proxy-manager', tmp_path) is None
    assert logos.cached_logo('jc21/nginx-proxy-manager', tmp_path) is None
    assert len(list(tmp_path.glob('*.miss'))) == 1


def test_manual_docker_logo_choice_persists_in_host_inventory(repo):
    store, config=repo
    host=store.add_host({'name':'reverseproxy', 'address':'192.0.2.111', 'ssh_user':'opm'})
    scan=store.begin_scan(host['id'])
    store.finish_scan(scan, {'os': {'family':'debian'}, 'engines': {'docker': {
        'containers': [{'image':'jc21/nginx-proxy-manager:latest'}]}}})
    selected=logos.choices_for_host({'engines': {'docker':{'containers':[
        {'image':'jc21/nginx-proxy-manager:latest'}]}}})[1]['value']
    store.set_host_logo(host['id'], selected)
    before=store.host(host['id'])
    fields={k: before[k] for k in ('name','address','ssh_user','ssh_port','key_path','group_name','sudo_mode','protected')}
    store.update_host(host['id'], fields)
    assert store.host(host['id'])['logo_choice'] == selected
    assert logos.selected_repositories({'logo_choice': selected, 'engines': {
        'docker':{'containers':[{'image':'jc21/nginx-proxy-manager:latest'}]}}}) == ['jc21/nginx-proxy-manager']


def test_editor_has_live_logo_preview_and_no_stale_hourly_browser_cache():
    form=(ROOT/'src/opm/web/templates/web/host_form.html').read_text()
    views=(ROOT/'src/opm/web/views.py').read_text()
    javascript=(ROOT/'src/opm/web/static/web/logo_preview.js').read_text()
    assert 'data-opm-logo-preview' in form and 'data-opm-logo-status' in form
    assert 'data-opm-logo-select' in views
    assert "'logo_preview_host':existing" in views
    assert "@never_cache\ndef server_logo" in views
    assert "request.GET.get('status')" in views
    assert "url.searchParams.set('choice', select.value)" in javascript
    assert "url.searchParams.set('status', '1')" in javascript
    assert 'Logo selection' not in views or 'Server profile updated' in views


def test_curated_lookup_is_bounded_and_immutable(monkeypatch):
    requested=[]
    def fail_then_succeed(url, **kw):
        requested.append(url)
        if 'jsdelivr' in url:
            raise OSError('blocked')
        return PNG
    monkeypatch.setattr(logos, '_get', fail_then_succeed)
    assert logos._lookup_curated_logo('jc21/nginx-proxy-manager') == (PNG,'image/png')
    assert len(requested) == 2
    assert requested[1] == 'https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/nginx-proxy-manager.png'
    monkeypatch.setattr(logos, '_get', lambda url, **kw: b'<svg></svg>')
    assert logos._lookup_curated_logo('jc21/nginx-proxy-manager') is None
