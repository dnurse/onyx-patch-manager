"""Regression tests for v0.5.12 release metadata, upgrade and Debian 13 ping."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(filename):
    spec = spec_from_file_location(filename[:-3], ROOT / filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_is_not_overwritten_by_os_release():
    installer = (ROOT / 'OPMInstall.sh').read_text()
    assert 'OPM_RELEASE_VERSION=$(python3' in installer
    assert not any(line.startswith('VERSION=$(python3') for line in installer.splitlines())
    assert 'if ! ( # shellcheck disable=SC1091' in installer
    assert '  . /etc/os-release\n' in installer
    assert '"$OPM_RELEASE_VERSION" "$INSTALL_ACTION_RESULT"' in installer
    assert 'v${OPM_RELEASE_VERSION} ${INSTALL_ACTION_RESULT} successfully' in installer
    assert 'OPM_UI_ACTION_LABEL=' in installer
    with (ROOT / 'pyproject.toml').open('rb') as file:
        assert tuple(map(int, tomllib.load(file)['project']['version'].split('.'))) >= (0, 5, 12)


def test_downgrade_rejected_before_changing_packages_or_services():
    installer = (ROOT / 'OPMInstall.sh').read_text()
    assert 'Refusing OPM downgrade from v%s to v%s' in installer
    assert installer.index('if [[ "$VERSION_ORDER" == downgrade ]]') < installer.index("start_step 'Checking existing patch jobs'")
    maintenance = (ROOT / 'OPMMaintenance.sh').read_text()
    assert 'Refusing downgrade from OPM v${installed_version}' in maintenance
    assert maintenance.index('case "$action" in') < maintenance.index('bash "$source/OPMInstall.sh" --install')
    assert 'Upgrade OPM from v${installed_version} to v${release_version}?' in maintenance
    assert 'Reinstall OPM v${release_version}?' in maintenance


def test_lan_url_prefers_configured_assigned_address_not_wildcard():
    net = load_module('OPMAccessURL.py')
    ips = net.interface_addresses('2: eth0 inet 10.0.1.17/24 brd 10.0.1.255 scope global eth0\n'
                                  '3: eth1 inet 192.168.1.50/24 brd 192.168.1.255 scope global eth1\n')
    assert ips == ['10.0.1.17', '192.168.1.50']
    assert net.access_url('lan','0.0.0.0:8765','192.168.1.50,localhost',ips) == 'http://192.168.1.50:8765'
    assert net.access_url('lan','0.0.0.0:8765','localhost',ips) == 'http://10.0.1.17:8765'
    assert 'unavailable' in net.access_url('lan','0.0.0.0:8765','localhost',[])
    assert net.access_url('proxy','0.0.0.0:8765','opm.example.test', ips) == 'https://opm.example.test'
    assert net.access_url('localhost','127.0.0.1:8765','localhost', ips).startswith('http://127.0.0.1:8765')


def test_ping_group_range_minimal_grant_and_persistence(tmp_path):
    ping = load_module('OPMPingAccess.py')
    assert ping.required_range('0 2147483647', 1000) is None
    assert ping.required_range('1 0', 1000) == (1000, 1000)
    assert ping.required_range('10 20', 50) == (10, 50)
    assert ping.required_range('1100 1200', 999) == (999, 1200)
    sysctl = tmp_path / 'ping_group_range'
    config = tmp_path / 'sysctl.d' / '90-opm-ping.conf'
    sysctl.write_text('1\t0\n')
    response = ping.configure(1000, sysctl, config)
    assert 'GID 1000' in response
    assert sysctl.read_text() == '1000 1000\n'
    assert 'net.ipv4.ping_group_range = 1000 1000' in config.read_text()
    assert config.stat().st_mode & 0o777 == 0o644
    assert 'already allowed' in ping.configure(1000, sysctl, config)


def test_ping_setup_does_not_write_persisted_config_on_denied_sysctl(tmp_path):
    ping = load_module('OPMPingAccess.py')
    sysctl = tmp_path / 'nonexistent' / 'ping_group_range'
    config = tmp_path / 'sysctl.d' / '90-opm-ping.conf'
    with pytest.raises(OSError):
        ping.configure(1000, sysctl, config)
    assert not config.exists()
    installer = (ROOT / 'OPMInstall.sh').read_text()
    assert 'python3 "$SRC_DIR/OPMPingAccess.py" --user "$APP_USER"' in installer
    assert installer.count('as_opm ping -n -c 1 -W 1 127.0.0.1') == 2


def test_bash_scripts_still_parse():
    for shell_script in ('OPMInstall.sh', 'OPMMaintenance.sh', 'OPMTUI.sh'):
        subprocess.run(['bash','-n',str(ROOT/shell_script)],check=True)


def test_settings_roles_are_adjacent_to_username_not_actions():
    template = (ROOT / 'src/opm/web/templates/web/settings.html').read_text()
    styles = (ROOT / 'src/opm/web/static/web/styles.css').read_text()
    identity = template.split('<div class="account-identity">', 1)[1].split('</div>\n          <div class="account-controls">', 1)[0]
    assert '<div class="account-name-role"><strong>{{ a.username }}</strong><span class="status' in identity
    assert 'Administrator' in identity and 'Operator' in identity and 'Viewer' in identity
    controls = template.split('<div class="account-controls">', 1)[1].split('</div>', 1)[0]
    assert 'Edit password' in controls and 'Edit</a>' in controls
    assert 'Administrator' not in controls
    assert '.account-name-role {display:flex;align-items:center;gap:10px;flex-wrap:wrap}' in styles
