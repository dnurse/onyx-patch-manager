"""All image registries can supply app logo choices, with controlled lookups."""
from __future__ import annotations

from pathlib import Path
import pytest
from opm import server_logos as logos

PNG = b'\x89PNG\r\n\x1a\n' + b'0'*24


@pytest.mark.parametrize('image,expected', [
    ('jc21/nginx-proxy-manager:latest', 'jc21/nginx-proxy-manager'),
    ('ghcr.io/blakeblackshear/frigate:stable', 'ghcr.io/blakeblackshear/frigate'),
    ('lscr.io/linuxserver/sonarr:latest', 'lscr.io/linuxserver/sonarr'),
    ('registry.internal:5000/team/memos:2.1', 'registry.internal:5000/team/memos'),
    ('docker.io/library/redis:7', 'library/redis'),
    ('ghcr.io/immich-app/immich-server@sha256:' + 'a'*64, 'ghcr.io/immich-app/immich-server'),
    ('registry:5000/image', 'registry:5000/image'),
    ('localhost:5000/image', 'localhost:5000/image'),
    ('https://192.0.2.7:9000/payload', None),
    ('ghcr.io/org/../secret', None),
    ('ghcr.io//image', None),
    ('ghcr.io/org/evil%2Ffoo', None),
    ('ghcr.io/org/evil;name', None),
])
def test_registry_name_normalization(image, expected):
    assert logos.container_repository(image) == expected


def test_all_discovered_registries_are_offered_but_vendor_managed_is_excluded():
    host = {'engines': {'docker': {'containers': [
        {'image':'jc21/nginx-proxy-manager:latest'},
        {'image':'ghcr.io/blakeblackshear/frigate:stable'},
        {'image':'lscr.io/linuxserver/sonarr:latest'},
        {'image':'ghcr.io/known/vendor:latest','managed_by':'vendor-managed'},
    ]}}}
    options = logos.choices_for_host(host)
    sources = [x['repo'] for x in options if x['kind'] == 'container']
    assert sources == ['ghcr.io/blakeblackshear/frigate', 'jc21/nginx-proxy-manager', 'lscr.io/linuxserver/sonarr']
    for item in options:
        if item['kind'] != 'container':
            continue
        assert logos.selected_repositories({'logo_choice':item['value'], 'engines':host['engines']}) == [item['repo']]
    assert len({item['value'] for item in options}) == len(options)
    assert logos.selected_repositories(host) == sources


def test_generic_application_icon_slugs_not_fixed_owner_map():
    assert logos._application_icon_slugs('ghcr.io/blakeblackshear/frigate') == ('frigate',)
    assert logos._application_icon_slugs('ghcr.io/immich-app/immich-server') == ('immich-server','immich')
    assert logos._application_icon_slugs('lscr.io/linuxserver/jellyfin') == ('jellyfin',)
    assert logos._application_icon_slugs('jc21/nginx-proxy-manager') == ('nginx-proxy-manager',)
    assert logos._application_icon_slugs('registry.example.org/org/backend') == ()
    assert logos._application_icon_slugs('http://127.0.0.1/foo') == ()


def test_only_fixed_public_icon_cdn_is_ever_fetched(monkeypatch):
    requested = []
    def fake_get(url, **kw):
        requested.append(url)
        return PNG
    monkeypatch.setattr(logos, '_get', fake_get)
    assert logos._lookup_curated_logo('registry.internal:5000/private/frigate') == (PNG, 'image/png')
    assert requested == ['https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/frigate.png']
    assert all('registry.internal' not in url for url in requested)


def test_suffix_fallback_and_safe_content_validation(monkeypatch):
    requested=[]
    def fake_get(url, **kw):
        requested.append(url)
        if url.endswith('/immich.png'):
            return PNG
        raise OSError('no image')
    monkeypatch.setattr(logos,'_get',fake_get)
    assert logos._lookup_curated_logo('ghcr.io/immich-app/immich-server') == (PNG, 'image/png')
    assert requested[0].endswith('/immich-server.png')
    assert requested[-1].endswith('/immich.png')
    monkeypatch.setattr(logos, '_get', lambda url, **kw:b'<svg><script>1</script></svg>')
    assert logos._lookup_curated_logo('ghcr.io/blakeblackshear/frigate') is None


def test_generic_lookup_skips_docker_hub_api_for_non_hub_images(monkeypatch,tmp_path):
    monkeypatch.setattr(logos, '_lookup_hub_logo', lambda repo: pytest.fail('Should not search Hub for GHCR image'))
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda repo: (PNG,'image/png'))
    assert logos.cached_logo('ghcr.io/blakeblackshear/frigate', tmp_path) == (PNG,'image/png')
    monkeypatch.setattr(logos, '_lookup_curated_logo', lambda repo:pytest.fail('Must serve cached art'))
    assert logos.cached_logo('ghcr.io/blakeblackshear/frigate', tmp_path) == (PNG,'image/png')
    assert len(list(tmp_path.glob('*.img'))) == 1


def test_stale_v2_negative_cache_is_bypassed(monkeypatch, tmp_path):
    import hashlib
    repo='jc21/nginx-proxy-manager'
    stale=tmp_path/(hashlib.sha256(('v2:'+repo).encode()).hexdigest()+'.miss')
    stale.touch()
    monkeypatch.setattr(logos,'_lookup_hub_logo', lambda _repo:None)
    monkeypatch.setattr(logos,'_lookup_curated_logo',lambda _repo:(PNG,'image/png'))
    assert logos.cached_logo(repo,tmp_path)==(PNG,'image/png')


def test_logo_module_does_not_generate_container_commands_or_connect_to_registry():
    path=Path(logos.__file__)
    source=path.read_text().lower()
    for text in ('docker pull', 'docker compose pull', 'docker restart', 'docker compose up'):
        assert text not in source
    assert 'raw.githubusercontent.com/homarr-labs/dashboard-icons' in source
    assert 'cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons' in source
