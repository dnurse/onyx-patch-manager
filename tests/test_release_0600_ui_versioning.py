"""v0.6.0: GitHub milestone, numeric IPv4 ordering, privacy and SSH repair UX."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'
NODE_BIN = shutil.which('node')

# Pure functions: this test suite does not require Django to be installed.
from opm.web.ssh_status import is_ssh_key_failure, needs_ssh_repair


def host(scan=None):
    return {'id': 'host-1', 'name': 'TestServer', 'ssh_user': 'opm',
            'verified': True, 'scan': scan or {}}


def activity(state, detail, updated, *, kind='ssh_test'):
    return {'kind':kind, 'label':'Testing SSH: TestServer', 'result':{'host_id':'host-1'},
            'state':state, 'detail':detail, 'created':updated, 'updated':updated}


@pytest.mark.parametrize('message', [
    'opm@10.0.0.8: Permission denied (publickey).',
    'SSH authentication failed',
    'No more authentication methods available',
])
def test_key_auth_failures_are_recognized(message):
    assert is_ssh_key_failure(message)


@pytest.mark.parametrize('message', [
    'ping: Operation not permitted', 'Connection timed out',
    'Connection refused', 'Host key verification failed',
    'sudo: a password is required', 'Partial scan',
])
def test_network_sudo_and_trust_errors_do_not_show_key_repair(message):
    assert not is_ssh_key_failure(message)


def test_action_shown_only_after_latest_ssh_authentication_failure():
    earlier_ok = {'state':'completed', 'finished':100, 'error':''}
    fail = activity('failed', 'SSH test failed: Permission denied (publickey).', 200)
    assert needs_ssh_repair(host(earlier_ok), [fail])
    assert not needs_ssh_repair(host({'state':'completed','finished':300}), [fail])
    assert not needs_ssh_repair(host({'state':'failed','finished':300,'error':'Connection refused'}), [])
    assert needs_ssh_repair(host({'state':'failed','finished':300,'error':'Permission denied (publickey)'}), [])
    assert not needs_ssh_repair(host(earlier_ok), [fail, activity('completed','SSH key login verified', 350)])
    assert not needs_ssh_repair(host({'state':'failed','finished':400,'error':'Permission denied (publickey)'}),
                                [activity('completed','SSH recovered',500,kind='ssh_repair')])
    partial = activity('partial', 'SSH restored but sudo requires attention', 500, kind='ssh_repair')
    partial['result']['ssh_recovered'] = True
    assert not needs_ssh_repair(host({'state':'failed','finished':400,'error':'Permission denied (publickey)'}), [partial])
    assert not needs_ssh_repair({**host(), 'verified':False}, [fail])
    assert not needs_ssh_repair({**host(), 'ssh_user':'admin'}, [fail])
    assert not needs_ssh_repair(host(), [])
    assert not needs_ssh_repair(host(), [activity('failed','SSH test failed: Connection timed out',200)])


def test_legacy_ssh_test_activity_without_result_is_still_recognized():
    entry = activity('failed','SSH test failed: Permission denied (publickey).', 200)
    entry['result'] = {}
    assert needs_ssh_repair(host(), [entry])
    entry['label']='Testing SSH: DifferentServer'
    assert not needs_ssh_repair(host(), [entry])


def test_repair_control_is_conditional_but_recovery_route_remains_available():
    page=(WEB/'templates/web/host_detail.html').read_text()
    views=(WEB/'views.py').read_text()
    assert '{% if ssh_repair_needed %}' in page
    assert "'ssh_repair_needed': repair_needed" in views
    assert "recent = _call('activities.list')" in views
    assert 'needs_ssh_repair(host, recent)' in views
    assert "'host_id': host['id']" in (ROOT/'src/opm/controller.py').read_text()
    # The action is still admin-only server-side even when its link is hidden.
    assert '_admin(request)' in views.split('def server_repair_ssh(request, host_id):')[1].split('def server_edit(')[0]


def test_duplicate_settings_password_button_removed():
    settings=(WEB/'templates/web/settings.html').read_text()
    assert 'Change my password</a>' not in settings
    assert 'Edit password' in settings  # keep the account-specific action


def test_dashboard_default_is_numeric_ip_without_exposing_ips_to_viewers():
    dashboard=(WEB/'templates/web/dashboard.html').read_text()
    views=(WEB/'views.py').read_text()
    js=(WEB/'static/web/server_sort.js').read_text()
    assert 'data-default-sort="ip-asc"' in dashboard
    assert 'data-server-ip-rank="{{ forloop.counter0 }}"' in dashboard
    assert 'data-server-ip="{{ h.address }}"' not in dashboard
    assert 'data-server-sort="ip-asc"' in dashboard
    assert 'hosts = sorted(hosts, key=_dashboard_ip_order)' in views
    assert 'hosts = _public_for(request, hosts)' in views
    assert views.index('hosts = sorted(hosts, key=_dashboard_ip_order)') < views.index('hosts = _public_for(request, hosts)')
    assert "case 'ip-asc': return ipRank(left) - ipRank(right)" in js
    # Actual numeric IP comparison, independent of Django runtime.
    import ast
    namespace={'ipaddress': __import__('ipaddress')}
    tree=ast.parse(views)
    definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_dashboard_ip_order')
    exec(compile(ast.Module(body=[definition],type_ignores=[]),'<ip-sort-test>','exec'), namespace)
    key=namespace['_dashboard_ip_order']
    addresses=['10.0.0.100','10.0.0.2','10.0.0.11','example.local','2001:db8::1','192.168.1.3']
    sorted_addresses=sorted(addresses,key=lambda addr:key({'address':addr,'name':addr}))
    assert sorted_addresses==['10.0.0.2','10.0.0.11','10.0.0.100','192.168.1.3','2001:db8::1','example.local']


def test_release_version_consistency_and_policy():
    import tomllib
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())
    assert project['project']['version']=='0.6.0'
    assert '0.6.0' in (ROOT/'src/opm/__init__.py').read_text()
    assert '0.6.0' in (ROOT/'OPMTUI.sh').read_text()
    policy=(ROOT/'VERSIONING.md').read_text()
    assert 'GitHub' in policy and 'Internal' in policy and '1.0.0' in policy
    assert not (ROOT/'GITHUB_RELEASE_v0.6.0.md').exists()
    readme=(ROOT/'README.md').read_text()
    assert 'https://github.com/dnurse/onyx-patch-manager/releases/tag/v0.6.0' in readme


def test_ip_default_sort_executes_in_browser_logic(tmp_path):
    """Exercise real sorting JS: initial DOM may arrive in an arbitrary order."""
    if not NODE_BIN:
        pytest.skip('Node is unavailable')
    harness = tmp_path/'ip_sort_harness.js'
    harness.write_text(r'''
const assert = require('assert');
const vm = require('vm');
const fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={}; this.listeners={}; }
  append(item) { this.children.push(item); }
  replaceChildren(fragment) { this.children=fragment.children; }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener(evt,fn) { this.listeners[evt]=fn; }
  querySelectorAll(sel) {
    if(sel==='tr[data-server-row]') return this.children;
    if(sel==='button[data-server-sort]') return buttons;
    return [];
  }
}
const tbody=new Element('tbody');
for(const [name,rank] of [['alpha',2],['zeta',0],['beta',1]]) {
  const row=new Element('tr');
  row.dataset={serverIpRank:String(rank),serverName:name,serverGroup:'VMs',serverUpdates:'0',serverScan:'0'};
  tbody.append(row);
}
const buttons=['ip-asc','name-asc'].map(value=>{
  const button=new Element('button'); button.dataset.serverSort=value; return button;
});
const table={dataset:{defaultSort:'ip-asc',groupEnabled:'0'},tBodies:[tbody],tHead:{rows:[{cells:[{},{}]}]}};
const menu=new Element('details'); menu.querySelectorAll=()=>buttons;
const document={
  querySelector(sel) { return sel==='[data-server-table]' ? table : menu; },
  createDocumentFragment() { return new Element('fragment'); },
  createElement(tag) { return new Element(tag); },
  addEventListener() {},
};
vm.runInNewContext(script, {document,Intl,Number,Array});
assert.deepStrictEqual(tbody.children.map(row=>row.dataset.serverName), ['zeta','beta','alpha']);
assert.equal(buttons[0].attributes['aria-pressed'], 'true');
buttons[1].listeners.click();
assert.deepStrictEqual(tbody.children.map(row=>row.dataset.serverName), ['alpha','beta','zeta']);
buttons[0].listeners.click();
assert.deepStrictEqual(tbody.children.map(row=>row.dataset.serverName), ['zeta','beta','alpha']);
''')
    subprocess.run([NODE_BIN,str(harness),str(WEB/'static/web/server_sort.js')],check=True)
