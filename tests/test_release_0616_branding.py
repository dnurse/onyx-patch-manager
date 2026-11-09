"""v0.6.16 brand identity: offline Shield O and prior UI retention.

The test validates source/package assets; it makes no network calls.
"""
from pathlib import Path
from xml.etree import ElementTree
import tomllib
import struct

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'src/opm/web/static/web'
TEMPLATE = ROOT / 'src/opm/web/templates/web'
BRANDING = STATIC / 'branding'


def test_shield_o_vector_is_embedded_and_decorative_in_sidebar_and_login():
    base = (TEMPLATE / 'base.html').read_text()
    login = (TEMPLATE / 'login.html').read_text()
    css = (STATIC / 'styles.css').read_text()
    svg = ElementTree.parse(BRANDING / 'shield-o.svg').getroot()
    assert svg.tag.endswith('svg')
    assert svg.attrib['viewBox'] == '0 0 128 128'
    assert len(list(svg)) >= 3
    for page in (base, login):
        assert "{% static 'web/branding/shield-o.svg' %}" in page
        assert "{% static 'web/branding/favicon.ico' %}" in page
        assert "{% static 'web/branding/shield-o-32.png' %}" in page
        assert "{% static 'web/branding/shield-o-180.png' %}" in page
    assert '<span class="brand-symbol" aria-hidden="true">' in base
    assert '<div class="login-mark"><img ' in login
    assert '<span class="brand-symbol">O</span>' not in base
    assert '<div class="login-mark">O</div>' not in login
    assert '.brand-symbol img,.login-mark img' in css
    assert 'brand>span:nth-child(2)' in css  # preserve collapsed sidebar labels


def test_all_browser_logo_files_are_real_correctly_sized_transparent_icons():
    for dim in (16, 32, 48, 64, 180, 192):
        png = (BRANDING / f'shield-o-{dim}.png').read_bytes()
        assert png[:8] == b'\x89PNG\r\n\x1a\n'
        assert png[12:16] == b'IHDR'
        width, height = struct.unpack('>II', png[16:24])
        assert (width, height) == (dim, dim)
        assert png[25] == 6  # PNG colour type 6 = RGBA
    ico = (BRANDING / 'favicon.ico').read_bytes()
    reserved, image_type, count = struct.unpack_from('<HHH', ico)
    assert reserved == 0 and image_type == 1
    assert count == 4
    sizes = {(ico[6 + n*16] or 256, ico[7 + n*16] or 256) for n in range(count)}
    assert sizes == {(16, 16), (32, 32), (48, 48), (64, 64)}


def test_brand_assets_in_setuptools_package_data():
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    entry = metadata['tool']['setuptools']['package-data']['opm.web']
    assert 'static/web/branding/*.svg' in entry
    assert 'static/web/branding/*.png' in entry
    assert 'static/web/branding/*.ico' in entry


def test_previous_history_and_dashboard_changes_remain_in_release():
    base = (TEMPLATE / 'base.html').read_text()
    dashboard = (TEMPLATE / 'dashboard.html').read_text()
    history = (TEMPLATE / 'history.html').read_text()
    css = (STATIC / 'styles.css').read_text()
    assert 'Clear alerts' in base
    assert 'Clear all history' in history
    assert 'history-scroll' in history
    assert 'dashboard-layout' in dashboard
    assert '.dashboard-layout > .surface {' in css
    assert '.dashboard-layout .side-panel > .activity-list' in css
