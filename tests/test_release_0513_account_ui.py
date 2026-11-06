"""v0.5.13: account adoption choice semantics and circular radio styles."""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'src/opm/web/templates/web/enroll.html'
STYLES = ROOT / 'src/opm/web/static/web/styles.css'


def test_adoption_options_are_numbered_and_explain_requirements():
    page = PAGE.read_text()
    segment = page.split('<div id="account-choices"', 1)[1].split('</ol>', 1)[0]
    assert 'Choose how the new controller should enroll this server:' in segment
    assert '<ol class="account-mode-list">' in segment
    assert segment.count('class="account-mode-option"') == 2
    for phrase in (
        'Reuse existing opm account',
        'Requires the current <code>opm</code> sudo password.',
        'Preserves the existing account and home directory.',
        'Adds the new controller SSH key after password verification.',
        'Delete and recreate the opm account',
        '(Destructive)',
        'Requires administrator access.',
        'Permanently removes the existing <code>opm</code> account, home directory, and SSH keys.',
        'Creates a fresh account for this controller.',
    ):
        assert phrase in segment
    assert 'for="account-choice-reuse"' in segment
    assert 'id="account-choice-reuse" type="radio" name="account-choice" value="reuse"' in segment
    assert 'for="account-choice-replace"' in segment
    assert 'id="account-choice-replace" type="radio" name="account-choice" value="replace"' in segment


def test_radio_inputs_are_excluded_from_textbox_rules():
    css = STYLES.read_text()
    assert '.enroll-shell input:not([type=checkbox]):not([type=radio]):not([type=hidden])' in css
    assert not re.search(r'\.enroll-shell input:not\(\[type=checkbox\]\)\s*\{', css)
    block = css.split('.enroll-shell .account-mode-label input[type=radio] {',1)[1].split('}',1)[0]
    for style in ('width: 17px;', 'height: 17px;', 'border-radius: 50%;', 'padding: 0;', 'min-height: 0;', 'accent-color: #64ca9e;'):
        assert style in block
    assert ':focus-visible' in css


def test_release_version_is_consistent():
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    assert version == '0.5.13'
    assert f'OPM_UI_VERSION="{version}"' in (ROOT/'OPMTUI.sh').read_text()
    assert f'__version__ = "{version}"' in (ROOT/'src/opm/__init__.py').read_text()
