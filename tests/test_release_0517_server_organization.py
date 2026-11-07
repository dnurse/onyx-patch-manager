"""v0.5.17 UI regression tests: grouping, sorting, timestamp and Settings."""
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest
from opm.web import timeprefs

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'
NODE_BIN = shutil.which('node')


def test_display_preference_persists_and_preserves_unrelated_config(tmp_path, monkeypatch):
    web_env = tmp_path / 'config' / 'web.env'
    web_env.parent.mkdir(parents=True)
    web_env.write_text('OPM_WEB_SECRET_KEY=not-really-secret\nOPM_WEB_TIME_ZONE=America/Toronto\n')
    monkeypatch.setattr(timeprefs, 'ENV_FILE', web_env)
    assert timeprefs.get_server_display_preferences() == {'group_servers': False}
    timeprefs.update_server_display_preferences(True)
    assert timeprefs.get_server_display_preferences() == {'group_servers': True}
    assert 'OPM_WEB_SECRET_KEY=not-really-secret' in web_env.read_text()
    assert 'OPM_WEB_TIME_ZONE=America/Toronto' in web_env.read_text()
    assert web_env.stat().st_mode & 0o777 == 0o600
    timeprefs.update_server_display_preferences(False)
    assert timeprefs.get_server_display_preferences() == {'group_servers': False}


def test_dashboard_separates_scan_status_and_time_and_uses_local_sort_menu():
    dashboard = (WEB / 'templates/web/dashboard.html').read_text()
    table = dashboard.split('data-server-table', 1)[1].split('</table>', 1)[0]
    assert '<th>Last scan</th><th>Scan time</th>' in table
    assert '<td data-label="Last scan">' in table
    assert '<td data-label="Scan time" class="scan-time-column">{{ h.scan.finished|scan_time }}</td>' in table
    assert 'colspan="8"' in table
    assert 'data-server-scan="{{ h.scan.finished|default:' in table
    assert 'data-connection-monitor' in dashboard
    assert 'data-server-sort-control' in dashboard
    assert 'View all servers <span aria-hidden' in dashboard
    assert 'text-link text-link-servers' not in dashboard
    assert 'href="{% url \'servers\' %}"' in dashboard  # Manage servers remains available


def test_grouped_sorting_enabled_on_dashboard_and_servers():
    for name in ('dashboard', 'servers'):
        template = (WEB / f'templates/web/{name}.html').read_text()
        assert 'data-server-table' in template
        assert 'data-group-enabled="{% if server_display.group_servers %}1{% else %}0{% endif %}"' in template
        assert 'data-server-group="{{ h.group_name|default:' in template
        assert "web/server_sort.js" in template
        assert 'data-server-sort="group-asc"' in template
        assert 'data-server-sort="updates-desc"' in template
        assert 'data-server-sort="scan-newest"' in template


def test_settings_one_card_and_admin_only_organization_preference():
    template = (WEB / 'templates/web/settings.html').read_text()
    views = (WEB / 'views.py').read_text()
    css = (WEB / 'static/web/styles.css').read_text()
    assert template.count('class="surface settings-master settings-overview-grid"') == 1
    assert template.count('class="settings-section"') == 6
    assert 'class="surface full-width"' not in template
    assert 'name="action" value="update_server_display"' in template
    assert 'name="group_servers" value="yes"' in template
    assert 'server_display.group_servers' in template
    assert "request.POST.get('action') == 'update_server_display'" in views
    assert "raise PermissionDenied('Only the administrator can change server display settings')" in views
    assert "'server_display': get_server_display_preferences()" in views
    assert '.surface.server-table-surface { overflow:visible; }' in css


@pytest.mark.skipif(NODE_BIN is None, reason='Node unavailable')
def test_client_sorting_runtime_groups_and_sort_options(tmp_path):
    """Execute actual browser JS against a minimal DOM, not just source strings."""
    harness = tmp_path / 'sort_harness.js'
    harness.write_text(textwrap.dedent(r'''
        const fs = require('fs');
        const vm = require('vm');
        const assert = require('assert');
        const source = fs.readFileSync(process.argv[2], 'utf8');
        function exercise(groupEnabled) {
            class Item {
                constructor(tag) { this.tagName = tag; this.dataset = {}; this.children = []; this.handlers = {}; this.attributes = {}; }
                append(element) { this.children.push(element); }
                addEventListener(name, fn) { this.handlers[name] = fn; }
                setAttribute(k, v) { this.attributes[k] = v; }
                querySelectorAll(selector) {
                    if (selector === 'tr[data-server-row]') return this.children;
                    if (selector === 'button[data-server-sort]') return buttons;
                    return [];
                }
                replaceChildren(fragment) { this.children = fragment.children; }
            }
            const tbody = new Item('tbody');
            const entries = [
                ['zeta', 'VMs', 9, 1700000000],
                ['alpha', 'Storage', 2, 1800000000],
                ['beta', 'VMs', 3, 1900000000],
                ['omega', 'Storage', 6, 1500000000],
            ];
            tbody.children = entries.map(([n, g, u, t]) => {
                const r = new Item('tr');
                r.dataset = {serverName:n, serverGroup:g, serverUpdates:String(u), serverScan:String(t)};
                return r;
            });
            const buttons = ['name-asc','name-desc','group-asc','updates-desc','scan-newest'].map(value => {
                const b = new Item('button'); b.dataset.serverSort = value; return b;
            });
            const menu = new Item('details');
            menu.querySelectorAll = () => buttons;
            const table = {dataset:{groupEnabled:groupEnabled ? '1':'0'},tBodies:[tbody],tHead:{rows:[{cells:new Array(8)}]}};
            const document = {
                querySelector(sel) { return sel === '[data-server-table]' ? table : menu; },
                createElement(tag) { return new Item(tag); },
                createDocumentFragment() { return new Item('fragment'); },
                addEventListener() {},
            };
            vm.runInNewContext(source, {document,Intl,Number,Array});
            const rows = () => tbody.children.map(x => x.tagName === 'tr' && x.className === 'server-group-row'
                ? `GROUP:${x.children[0].textContent}` : x.dataset.serverName);
            if (groupEnabled) {
                assert.deepStrictEqual(rows(),['GROUP:Storage','alpha','omega','GROUP:VMs','beta','zeta']);
                buttons.find(b=>b.dataset.serverSort==='updates-desc').handlers.click();
                assert.deepStrictEqual(rows(),['GROUP:Storage','omega','alpha','GROUP:VMs','zeta','beta']);
                buttons.find(b=>b.dataset.serverSort==='scan-newest').handlers.click();
                assert.deepStrictEqual(rows(),['GROUP:Storage','alpha','omega','GROUP:VMs','beta','zeta']);
            } else {
                assert.deepStrictEqual(rows(),['alpha','beta','omega','zeta']);
                buttons.find(b=>b.dataset.serverSort==='updates-desc').handlers.click();
                assert.deepStrictEqual(rows(),['zeta','omega','beta','alpha']);
                buttons.find(b=>b.dataset.serverSort==='group-asc').handlers.click();
                assert.deepStrictEqual(rows(),['alpha','omega','beta','zeta']);
                buttons.find(b=>b.dataset.serverSort==='name-desc').handlers.click();
                assert.deepStrictEqual(rows(),['zeta','omega','beta','alpha']);
            }
            assert.equal(buttons.filter(b=>b.attributes['aria-pressed']==='true').length,1);
        }
        exercise(false);
        exercise(true);
    '''))
    subprocess.run([NODE_BIN, str(harness), str(WEB/'static/web/server_sort.js')], check=True)
