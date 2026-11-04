"""Regression checks for non-blocking, controller-backed activity feedback."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'


def test_common_shell_uses_persistent_activity_not_fullscreen_overlay():
    base = (WEB / 'templates/web/base.html').read_text()
    assert 'web/actions.js' in base
    assert 'web/activity.js' in base
    assert 'id="opm-activity-toggle"' in base
    assert 'id="opm-activity-panel"' in base
    assert 'id="opm-action-overlay"' not in base
    assert 'aria-live="polite"' in base


def test_task_specific_labels_stay_on_action_forms():
    templates = {
        'scan_auth.html': 'Scanning servers and containers',
        'host_detail.html': 'Testing SSH connection',
        'enroll.html': 'Setting up and testing your server',
        'review.html': 'Submitting approved patch job',
        'patch.html': 'Preparing patch plan',
    }
    for filename, marker in templates.items():
        assert marker in (WEB / 'templates/web' / filename).read_text()


def test_activity_polling_is_read_only_and_navigation_is_available():
    script = (WEB / 'static/web/activity.js').read_text()
    actions = (WEB / 'static/web/actions.js').read_text()
    assert "fetch('/activity/feed/'" in script
    assert "document.visibilityState !== 'visible'" in script
    assert 'data-activity-detail' in script
    assert 'pageshow' in actions
    assert 'event.defaultPrevented' in actions
    assert 'form.dataset.opmLoading' in actions
    assert 'opm-action-overlay' not in actions
    assert 'document.body.classList.add("opm-busy")' not in actions
    assert 'localStorage' not in actions
    assert '.value' not in actions
    assert '100%' not in actions
    css = (WEB / 'static/web/styles.css').read_text()
    assert '.opm-action-overlay' not in css
    assert '.activity-dropdown' in css


def test_dashboard_has_indeterminate_ping_activity():
    script = (WEB / 'static/web/dashboard.js').read_text()
    assert "classList.add('probing')" in script
    assert "classList.remove('probing', 'loading')" in script
    css = (WEB / 'static/web/styles.css').read_text()
    assert '@media(prefers-reduced-motion:reduce)' in css
