"""v0.5.22 GM follow-ups: presentation privacy, layout and scan diagnostics.

All scan tests are local SQLite + simulated remote exceptions. They do not
connect to any server or run destructive commands.
"""
import asyncio
from pathlib import Path

from opm.controller import Controller
from opm.web.redaction import redact_ips, redact_for_viewer

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'src/opm/web/templates/web'


def test_viewer_ipv4_ipv6_redaction_in_nested_results():
    raw = {
        'address': '192.168.22.11',
        'events': [
            {'message': 'SSH 10.1.2.3:22 failed. Retry at [2001:db8::1]:8765'},
            {'message': 'Nov 07, 2026 03:11 UTC'},
        ],
        'other': 'http://[fe80::13]:8765',
    }
    result = redact_for_viewer(raw)
    assert result['address'] == 'Address restricted'
    assert '10.1.2.3' not in str(result)
    assert '2001:db8::1' not in str(result)
    assert 'fe80::13' not in str(result)
    assert 'Nov 07, 2026 03:11 UTC' in str(result)
    assert raw['address'] == '192.168.22.11'  # nonmutating
    assert redact_ips('No IP; 5% RAM; 1.2.3.999 is invalid') == 'No IP; 5% RAM; 1.2.3.999 is invalid'


def test_viewer_settings_and_ip_suppression_across_authenticated_endpoints():
    views = (ROOT/'src/opm/web/views.py').read_text()
    base = (TEMPLATES/'base.html').read_text()
    assert 'def settings_view(request):\n    _operator(request)' in views
    assert "{% if request.user.is_staff %}<a class=\"nav-link" in base
    for fragment in (
        "hosts = _public_for(request, hosts)",
        "items = _public_for(request, items)",
        "'job':_public_for(request, job)",
        "'item': _public_for(request, item)",
        "_public_for(request, _call('activities.list'))",
        "_public_for(request, _call('hosts.reachability'))",
    ):
        assert fragment in views
    dashboard = (TEMPLATES/'dashboard.html').read_text()
    server_table = (TEMPLATES/'servers.html').read_text()
    server_profile = (TEMPLATES/'host_detail.html').read_text()
    for template in (dashboard, server_table, server_profile):
        assert 'Address restricted' in template


def test_account_actions_are_buttons_with_confirm_and_full_width():
    settings = (TEMPLATES/'settings.html').read_text()
    css = (ROOT/'src/opm/web/static/web/styles.css').read_text()
    assert "{% url 'create_account' %}" in settings
    assert 'Create New Account' in settings
    assert 'accounts-section-heading' in settings
    assert 'data-account-disable-toggle' in settings
    assert 'name="confirm"' in settings and 'pattern="DISABLE"' in settings
    assert '<details class="disclosure danger-zone"><summary>Disable' not in settings
    assert 'account-permission-note' in settings
    assert '.account-edit-surface, .account-security-surface { width:100%; max-width:none; }' in css
    assert 'account-security-surface' in (TEMPLATES/'change_password.html').read_text()
    assert 'settings_accounts.js' in settings


def test_protected_server_checkbox_restored_and_dashboard_has_css_lock():
    enroll = (TEMPLATES/'enroll.html').read_text()
    dashboard = (TEMPLATES/'dashboard.html').read_text()
    styles = (ROOT/'src/opm/web/static/web/styles.css').read_text()
    assert '{{ form.protected }}' in enroll
    assert 'Require separate approval before patching this server' in enroll
    assert 'is-protected' in dashboard
    assert '.machine-icon.is-protected::before' in styles
    assert '.machine-icon.is-protected::after' in styles


def test_scan_all_history_names_failed_server_and_sanitized_reason(monkeypatch, repo):
    store, config = repo
    one = store.add_host({'name':'cams', 'address':'192.0.2.16', 'ssh_user':'opm'})
    two = store.add_host({'name':'testbox', 'address':'192.0.2.25', 'ssh_user':'opm'})
    controller = Controller(config)
    activity = store.begin_activity('scan', 'Scanning 2 servers',
                                    ['host:'+one['id'], 'host:'+two['id']], total=2)
    secret = 'never-persist-this-password'
    def fake_inspection(host, *args):
        if host['name'] == 'testbox':
            raise ValueError('SSH denied password=' + secret)
        return {'os':{'metadata':'fresh','packages':[]}}
    monkeypatch.setattr('opm.controller.inspect_host', fake_inspection)
    asyncio.run(controller.scan_many([one,two], {two['id']:secret}, activity_id=activity['id']))
    row = store.activity(activity['id'])
    event_log = '\n'.join(e['message'] for e in row['events'])
    assert row['state'] == 'partial'
    assert 'failed servers: testbox' in row['detail']
    assert 'Scan failed for testbox' in event_log
    assert 'Scan succeeded: cams' in event_log
    assert secret not in event_log
    assert store.last_scan(two['id'])['state'] == 'failed'
    assert store.last_scan(one['id'])['state'] == 'completed'
