"""Text-only navigation in desktop, narrow sidebar and mobile layouts."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'src/opm/web/templates/web/base.html'
STYLES = ROOT / 'src/opm/web/static/web/styles.css'


def test_sidebar_navigation_is_text_only_on_every_page():
    html = BASE.read_text()
    nav = html.split('<nav class="nav-links">', 1)[1].split('</nav>', 1)[0]
    assert 'nav-icon' not in nav
    assert nav.count('class="nav-link ') == 5
    for label in ('Dashboard', 'Patch Manager', 'Servers', 'History', 'Settings'):
        assert re.search(r'>' + re.escape(label) + r'</a>', nav), label


def test_narrow_and_mobile_navigation_retains_visible_names():
    css = STYLES.read_text()
    assert '.nav-icon' not in css
    narrow = css.split('@media(max-width:900px) {', 1)[1].split('@media(max-width:680px) {', 1)[0]
    nav_rule = narrow.split('.nav-link {', 1)[1].split('}', 1)[0]
    assert 'font-size:0' not in nav_rule
    assert 'font-size:12px' in nav_rule
    mobile = css.split('@media(max-width:680px) {', 1)[1]
    assert 'font-size:10px' in mobile
    assert 'justify-content:center' in mobile
