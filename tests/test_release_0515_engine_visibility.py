"""Container cards hide proven absence without swallowing privilege errors."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/opm/web'
DISPLAY = WEB / 'templatetags/opm_display.py'


def filters():
    """Execute only pure display filters; no Django installation is needed."""
    desired = {'engine_not_installed', 'visible_engines',
               'engine_inventory_message', 'engine_message'}
    nodes = []
    for node in ast.parse(DISPLAY.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in desired:
            node.decorator_list = []
            nodes.append(node)
    assert len(nodes) == len(desired)
    code = compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                   str(DISPLAY), 'exec')
    namespace = {}
    exec(code, namespace)
    return namespace


def absent():
    return {'installed': False, 'available': False, 'containers': [], 'error': ''}


def available():
    return {'installed': True, 'available': True, 'containers':
            [{'name':'service','state':'running'}]}


def unavailable(message='permission denied'):
    return {'installed': True, 'available': False, 'error': message,
            'containers': []}


def test_both_absent_display_single_clear_message():
    f = filters()
    e = {'docker': absent(), 'podman': absent()}
    assert f['visible_engines'](e) == {}
    assert f['engine_inventory_message'](e) == 'Neither Docker nor Podman is installed.'
    assert set(e) == {'docker', 'podman'}  # no mutation of raw scan


def test_mixed_installations_hide_only_absent():
    f = filters()
    e = {'docker': absent(), 'podman': available()}
    assert list(f['visible_engines'](e)) == ['podman']
    e = {'docker': unavailable(), 'podman': absent()}
    assert list(f['visible_engines'](e)) == ['docker']
    assert 'permission denied' in f['engine_message'](e['docker'], 'docker')
    installed_but_errored = {**unavailable(), 'error': "No such file or directory: 'docker'"}
    assert not f['engine_not_installed'](installed_but_errored, 'docker')


def test_privileged_discovery_failure_must_stay_visible():
    f = filters()
    e = {'docker': absent(), 'podman': {**absent(),
        'scope_warning': 'Privileged context discovery failed: sudo password required'}}
    assert list(f['visible_engines'](e)) == ['podman']
    assert not f['engine_not_installed'](e['podman'], 'podman')
    assert 'discovery incomplete' in f['engine_message'](e['podman'], 'podman')
    assert f['engine_inventory_message'](e) != 'Neither Docker nor Podman is installed.'


def test_false_installed_flag_with_error_is_not_hidden():
    f = filters()
    result = {**absent(), 'error': 'permission denied during discovery'}
    assert not f['engine_not_installed'](result, 'docker')
    assert 'docker' in f['visible_engines']({'docker':result})


def test_historical_enoent_engines_are_hidden_but_other_errors_are_not():
    f = filters()
    old = {'available': False, 'error': "[Errno 2] No such file or directory: 'docker'", 'containers': []}
    assert f['engine_not_installed'](old, 'docker')
    assert not f['engine_not_installed'](old, 'podman')
    mixed = {'docker': old, 'podman': absent()}
    assert f['engine_inventory_message'](mixed) == 'Neither Docker nor Podman is installed.'


def test_unscanned_or_partial_data_never_claims_both_absent():
    f = filters()
    assert f['engine_inventory_message']({}) == 'No container scan results yet.'
    assert f['engine_inventory_message'](None) == 'No container scan results yet.'
    assert f['engine_inventory_message']({'docker': absent()}) != 'Neither Docker nor Podman is installed.'
    assert list(f['visible_engines']({'docker': None})) == ['docker']


def test_both_templates_filter_and_share_empty_state():
    for page, key in [('host_detail.html', 'host'), ('patch.html', 'h')]:
        content = (WEB/'templates/web'/page).read_text()
        assert f'{key}.engines|visible_engines' in content
        assert f'{key}.engines|engine_inventory_message' in content
        assert 'engine_message:' in content
        assert 'scope_warning' in content
        assert 'Not installed</span>' not in content


def test_all_release_version_fields():
    import tomllib
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    assert tuple(map(int, version.split('.'))) >= (0, 5, 16)
    assert f'OPM_UI_VERSION="{version}"' in (ROOT/'OPMTUI.sh').read_text()
    assert f'__version__ = "{version}"' in (ROOT/'src/opm/__init__.py').read_text()
