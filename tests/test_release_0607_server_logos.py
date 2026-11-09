"""v0.6.7: uniform identity, persistent selection and safe Docker Hub art."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from opm.controller import Controller
from opm.server_logos import (docker_hub_repository, choices_for_host, distro_icon,
                              selected_repositories, cached_logo, _lookup_hub_logo,
                              _valid_image_host)
from opm.store import Store

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT/'src/opm/web/templates/web'
CSS = ROOT/'src/opm/web/static/web/styles.css'
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00'*16)


@pytest.mark.parametrize('image,expected', [
    ('nginx:latest', 'library/nginx'),
    ('docker.io/library/postgres:16', 'library/postgres'),
    ('docker.io/linuxserver/plex:latest', 'linuxserver/plex'),
    ('lscr.io/linuxserver/plex:latest', None),
    ('ghcr.io/blakeblackshear/frigate:stable', None),
    ('registry.example.com:5000/org/app:2', None),
    ('localhost:5000/app', None),
    ('example.com/evil', None),
    ('docker.io/owner/repo@sha256:' + 'a'*64, 'owner/repo'),
    ('docker.io/owner/repo;drop', None),
    ('docker.io/../../etc/passwd', None),
    ('http://192.168.1.1/image', None),
])
def test_docker_hub_repo_parsing(image, expected):
    assert docker_hub_repository(image) == expected


def test_docker_candidates_are_deduplicated_and_distro_always_available():
    host = {'os': {'family': 'debian', 'name': 'Debian GNU/Linux 13'},
            'engines': {'docker': {'containers': [
                {'image': 'linuxserver/plex:latest'},
                {'image': 'linuxserver/plex:old'},
                {'image': 'library/redis:7'},
                {'image': 'ghcr.io/blakeblackshear/frigate:stable'},
                {'image': 'test/vendor:1', 'managed_by': 'vendor-managed'},
            ]}}, 'logo_choice': 'auto'}
    choices = choices_for_host(host)
    assert [c['kind'] for c in choices] == ['auto', 'container', 'container', 'container', 'distro']
    assert [c['repo'] for c in choices if c['kind']=='container'] == ['ghcr.io/blakeblackshear/frigate','library/redis','linuxserver/plex']
    assert selected_repositories(host) == ['ghcr.io/blakeblackshear/frigate','library/redis','linuxserver/plex']
    host['logo_choice'] = choices[3]['value']
    assert selected_repositories(host) == ['linuxserver/plex']
    host['logo_choice'] = 'distro'
    assert selected_repositories(host) == []
    assert selected_repositories(host, 'hub:invalid') == []
    assert distro_icon(host['os']) == 'debian'
    assert distro_icon({'family':'rhel','name':'Red Hat Enterprise Linux'}) == 'redhat'
    assert distro_icon({'family':'unknown'}) == 'linux'


def test_whitelisted_logo_cdn_and_no_arbitrary_url():
    assert _valid_image_host('https://d1q6f0aelx0por.cloudfront.net/logo.png')
    assert _valid_image_host('https://hub.docker.com/logo.png')
    for url in ['http://hub.docker.com/logo', 'https://hub.docker.com.evil.org/logo',
                'https://127.0.0.1/logo', 'https://[::1]/logo',
                'https://user:pass@hub.docker.com/logo',
                'https://whatever.cloudfront.net/logo',
                'file:///etc/shadow', 'https://docker.com:8443/evil']:
        assert not _valid_image_host(url), url


def test_exact_hub_slug_required_and_only_raster(monkeypatch):
    from opm import server_logos as logos
    requests = []
    def fake_get(url, **kwargs):
        requests.append(url)
        if '/products/search?' in url:
            return json.dumps({'summaries': [
                {'slug': 'another/image', 'logo_url': {'large':'https://hub.docker.com/evil.png'}},
                {'slug': 'linuxserver/plex', 'logo_url': {'large':'https://d1q6f0aelx0por.cloudfront.net/plex.png'}},
            ]}).encode()
        return PNG
    monkeypatch.setattr(logos, '_get', fake_get)
    assert _lookup_hub_logo('linuxserver/plex') == (PNG,'image/png')
    assert len(requests) == 2 and 'plex.png' in requests[1]
    requests.clear()
    assert _lookup_hub_logo('linuxserver/jellyfin') is None
    assert len(requests) == 1
    # Same exact slug, but a disallowed redirect/host or HTML response falls back.
    def fake_bad(url, **kwargs):
        if '/products/search?' in url:
            return json.dumps({'summaries': [{'slug': 'linuxserver/plex',
              'logo_url': {'large': 'https://169.254.169.254/metadata'}}]}).encode()
        pytest.fail('Must not fetch disallowed host')
    monkeypatch.setattr(logos, '_get', fake_bad)
    assert _lookup_hub_logo('linuxserver/plex') is None


def test_logo_cache_positive_and_negative(monkeypatch, tmp_path):
    from opm import server_logos as logos
    counter = [0]
    def available(repo):
        counter[0] += 1
        return (PNG, 'image/png')
    monkeypatch.setattr(logos, '_lookup_hub_logo', available)
    assert cached_logo('library/nginx', tmp_path) == (PNG, 'image/png')
    assert cached_logo('library/nginx', tmp_path) == (PNG, 'image/png')
    assert counter[0] == 1
    assert len(list(tmp_path.glob('*.img'))) == 1
    assert list(tmp_path.glob('*.img'))[0].stat().st_mode & 0o077 == 0
    monkeypatch.setattr(logos, '_lookup_hub_logo', lambda repo: None)
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda repo: None)
    assert cached_logo('library/redis', tmp_path) is None
    assert cached_logo('library/redis', tmp_path) is None
    assert len(list(tmp_path.glob('*.miss'))) == 1
    assert not any('/' in path.name for path in tmp_path.iterdir())


def test_store_migrates_previous_host_database_without_loss(repo):
    store, config = repo
    host = store.add_host({'name':'demo','address':'192.0.2.20','ssh_user':'opm'})
    with store.connection() as db:
        db.execute('ALTER TABLE hosts DROP COLUMN logo_choice')
    fresh = Store(config.database)
    assert fresh.host(host['id'])['logo_choice'] == 'auto'
    assert fresh.host(host['id'])['address'] == '192.0.2.20'
    # Selecting a logo never clears verified SSH state or rewrites OS scan.
    fresh.verified(host['id'], True)
    saved = fresh.set_host_logo(host['id'], 'distro')
    assert saved['logo_choice']=='distro' and saved['verified'] == 1
    with pytest.raises(ValueError):
        fresh.set_host_logo(host['id'], 'https://127.0.0.1/logo')


def test_controller_rejects_forged_choice(repo):
    store, config = repo
    host = store.add_host({'name':'cameras','address':'192.0.2.4','ssh_user':'opm'})
    scan = store.begin_scan(host['id'])
    store.finish_scan(scan, {'os':{'family':'debian'}, 'engines':{'docker':{'containers':[
        {'image':'linuxserver/plex:latest'}]}}})
    controller = Controller(config)
    options = choices_for_host({'engines':{'docker':{'containers':[{'image':'linuxserver/plex:latest'}]}}})
    choice = options[1]['value']
    result = asyncio.run(controller.dispatch('hosts.logo.set', {'id':host['id'],'choice':choice}))
    assert result['logo_choice'] == choice
    info = asyncio.run(controller.dispatch('hosts.logo_info', {'id':host['id']}))
    assert info['logo_choice'] == choice
    assert info['engines']['docker']['containers'][0]['image'] == 'linuxserver/plex:latest'
    with pytest.raises(ValueError):
        asyncio.run(controller.dispatch('hosts.logo.set', {'id':host['id'],
                                'choice':'hub:' + '0'*24}))
    with pytest.raises(ValueError):
        asyncio.run(controller.dispatch('hosts.logo.set', {'id':host['id'],
                                'choice':'https://evil.example/file.png'}))


def test_templates_share_squircle_size_and_padlock():
    dashboard = (TEMPLATE/'dashboard.html').read_text()
    servers = (TEMPLATE/'servers.html').read_text()
    patch = (TEMPLATE/'patch.html').read_text()
    detail = (TEMPLATE/'host_detail.html').read_text()
    common = (TEMPLATE/'partials/server_logo.html').read_text()
    form = (TEMPLATE/'host_form.html').read_text()
    css = CSS.read_text()
    assert all("include 'web/partials/server_logo.html' with server=h" in template
               for template in (dashboard, servers, patch))
    assert "include 'web/partials/server_logo.html' with server=host logo_large=True" in detail
    assert 'host-overview-heading' in detail
    assert 'server-logo-lock' in common
    assert 'right: -2px;' in css and 'bottom: -2px;' in css
    assert 'background: transparent;' in css and 'border: 0;' in css
    assert '--server-logo-size: 40px;' in css and '--server-logo-size: 72px;' in css
    assert 'object-fit: contain' in css and 'border-radius: 30%' in css
    assert 'field.help_text' in form
    assert 'logo_choice' in (ROOT/'src/opm/web/views.py').read_text()
    assert 'logo_choice' in (ROOT/'src/opm/store.py').read_text()
    assert 'logos/*.svg' in (ROOT/'pyproject.toml').read_text()
    assert 'partials/*.html' in (ROOT/'pyproject.toml').read_text()


def test_no_container_update_commands_in_logo_module():
    source=(ROOT/'src/opm/server_logos.py').read_text().lower()
    for forbidden in ['docker compose pull', 'docker pull', 'docker compose up', 'docker restart']:
        assert forbidden not in source
