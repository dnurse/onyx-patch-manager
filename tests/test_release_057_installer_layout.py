"""v0.5.7 Whiptail theme, persisted web selection and Patch Manager layout.

Uses fake Whiptail and temporary files. Never installs packages, modifies
network settings, or touches service/database state.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _fake_dialog(tmp_path):
    program = tmp_path / 'whiptail'
    program.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import json,os,sys
        a=sys.argv[1:]
        with open(os.environ['OPM_UI_CALLS'],'a') as f:
            f.write(json.dumps(a)+'\\n')
        if '--radiolist' in a:
            # Model real radio behavior: return the ON item, not the first item.
            i=a.index('--radiolist')
            for j in range(i+5,len(a)-2,3):
                if a[j+2]=='ON':
                    print(a[j]); sys.exit(0)
            sys.exit(3)
        if '--inputbox' in a:
            title=a[a.index('--title')+1]
            if 'Host' in title and 'Proxy' not in title: print('opm.example.internal')
            elif 'Trusted source' in title: print('192.168.40.9')
            else: print('192.168.40.10')
        sys.exit(0)
    '''))
    program.chmod(0o755)


@pytest.mark.parametrize('saved_mode,expected_active', [
    ('lan', 'lan'), ('proxy', 'proxy'), ('localhost', 'localhost'),
])
def test_maintenance_web_dialog_remembers_saved_mode(tmp_path,saved_mode,expected_active):
    _fake_dialog(tmp_path)
    env_file=tmp_path/'web.env'
    env_file.write_text(
        'OPM_WEB_SECRET_KEY=KEEP_SECRET_OUT_OF_UI\n'
        f'OPM_WEB_ACCESS_MODE={saved_mode}\n'
        'OPM_WEB_ALLOWED_HOSTS=opm.example.internal,localhost,127.0.0.1\n'
        'OPM_WEB_TRUSTED_PROXY_IPS=192.168.40.9\n'
    )
    env=dict(os.environ,PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
             OPM_UI_CALLS=str(tmp_path/'calls.jsonl'))
    cmd='set -Eeuo pipefail; source ./OPMTUI.sh; '
    cmd+=f'opm_ui_access_from_env {str(env_file)!r}; OPM_UI_SOURCE=.; '
    cmd+='opm_ui_select_access; printf "MODE=%s\\n" "$OPM_UI_MODE"'
    run=subprocess.run(['bash','-c',cmd],cwd=ROOT,env=env,
                       capture_output=True,text=True,timeout=20)
    assert run.returncode==0,run.stderr
    assert 'MODE='+expected_active in run.stdout
    calls=[json.loads(s) for s in (tmp_path/'calls.jsonl').read_text().splitlines()]
    radio=next(a for a in calls if '--radiolist' in a)
    assert radio[radio.index('--default-item')+1]==saved_mode
    i=radio.index('--radiolist')
    selection={radio[j]:radio[j+2] for j in range(i+5,len(radio)-2,3)}
    assert selection[saved_mode]=='ON'
    assert sum(v=='ON' for v in selection.values())==1
    assert 'KEEP_SECRET_OUT_OF_UI' not in json.dumps(calls)
    # The test never changes any persisted configuration.
    assert f'OPM_WEB_ACCESS_MODE={saved_mode}' in env_file.read_text()


def test_new_install_still_defaults_to_lan():
    source=(ROOT/'OPMTUI.sh').read_text()
    assert "OPM_UI_MODE='lan'; OPM_UI_HOST=''; OPM_UI_PROXY_IP=''" in source
    assert 'opm_ui_access_from_env' in source


def test_successful_install_returns_to_shell_instead_of_maintenance_menu():
    maintenance=(ROOT/'OPMMaintenance.sh').read_text()
    case=maintenance.split('if install_release; then',1)[1].split('else',1)[0]
    assert 'return 0' in case
    assert 'opm_ui_message' not in case


def test_native_whiptail_art_and_progress_use_approved_palette():
    source=(ROOT/'OPMTUI.sh').read_text()
    banner=source.split("art=$(cat <<'BANNER'\n",1)[1].split('\nBANNER',1)[0]
    assert len(banner.splitlines())==6
    assert max(map(len,banner.splitlines())) <= 94
    assert '\\' in banner and '/' in banner
    assert 'actbutton=white,green' in source
    assert 'fullscale=,green' in source
    assert "--gauge" in source
    assert "[x] " in source and "[ ] " in source
    assert 'OPM_UI_GAUGE_DIR' in source


def test_patch_manager_select_controls_follow_all_server_rows():
    tpl=(ROOT/'src/opm/web/templates/web/patch.html').read_text()
    loop=tpl.index('{% for h in hosts %}')
    empty=tpl.index('{% empty %}<p class="empty-state">No servers registered yet.')
    master=tpl.index('data-select-all-hosts')
    picker=tpl.index('class="execution-mode-picker"')
    footer=tpl.index('class="selection-footer"')
    assert loop < empty < master < picker < footer
    # We must not move the inputs out of the form or change the backend.
    assert tpl.index('<form method="post"') < loop
    assert tpl.index('</form>') > footer
    js=(ROOT/'src/opm/web/static/web/patch_manager.js').read_text()
    assert "master.addEventListener('change'" in js
    assert "modes.forEach((radio) => radio.addEventListener('change', applyMode))" in js
