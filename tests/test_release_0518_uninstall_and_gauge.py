"""Regression coverage for 0.5.18, using isolated mock accounts, never real userdel."""
import os
from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def _run(script, *, env=None):
    return subprocess.run(['bash', '-c', script], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=20)


def test_whiptail_starts_with_full_height_checklist_not_two_line_placeholder(tmp_path):
    """A real newt gauge cannot grow its textbox after startup (newt textbox())."""
    whiptail = tmp_path / 'whiptail'
    whiptail.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import os, sys
        args = sys.argv[1:]
        if '--gauge' in args:
            text = args[args.index('--gauge') + 1]
            with open(os.environ['INIT_TEXT'], 'w', encoding='utf-8') as f: f.write(text)
            with open(os.environ['PROGRESS_DATA'], 'w', encoding='utf-8') as f: f.write(sys.stdin.read())
    '''))
    whiptail.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               INIT_TEXT=str(tmp_path / 'initial.txt'),
               PROGRESS_DATA=str(tmp_path / 'updates.txt'))
    code = '''set -Eeuo pipefail
source ./OPMTUI.sh
OPM_UI_ACTIVE=1
OPM_UI_TERM_ROWS=24
OPM_UI_WIDTH=76
OPM_UI_STAGES=( 'one' 'two' 'three' 'four' 'five' 'six' 'seven' 'eight' 'nine' 'ten' 'eleven' 'twelve' 'thirteen' 'fourteen' 'fifteen' )
TOTAL_STEPS=15
COMPLETED=0
CURRENT_STEP='one'
opm_ui_gauge_start
COMPLETED=4
CURRENT_STEP='five'
opm_ui_gauge_update 26 "$CURRENT_STEP" "$COMPLETED"
opm_ui_gauge_stop
'''
    r = _run(code, env=env)
    assert r.returncode == 0, r.stderr
    initial = (tmp_path/'initial.txt').read_text()
    updates = (tmp_path/'updates.txt').read_text()
    assert len(initial.splitlines()) == 16  # header + all fifteen stages, not 2
    assert '▶  ONE' in initial
    assert '□  fifteen' in initial
    assert '✓  one' in updates
    assert '▶  FIVE' in updates
    assert '□  fifteen' in updates
    assert '26\n' in updates


def test_gauge_resume_starts_with_previous_checks_not_blank(tmp_path):
    whiptail = tmp_path / 'whiptail'
    whiptail.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import os, sys
        args = sys.argv[1:]
        if '--gauge' in args:
            with open(os.environ['INIT_TEXT'], 'w') as f: f.write(args[args.index('--gauge') + 1])
            sys.stdin.read()
    '''))
    whiptail.chmod(0o755)
    env=dict(os.environ,PATH=f"{tmp_path}:{os.environ['PATH']}",INIT_TEXT=str(tmp_path/'initial.txt'))
    r=_run('''set -Eeuo pipefail
source ./OPMTUI.sh
OPM_UI_ACTIVE=1
OPM_UI_TERM_ROWS=24
OPM_UI_WIDTH=76
OPM_UI_STAGES=( 'one' 'two' 'three' 'four' 'five' 'six' 'seven' 'eight' 'nine' 'ten' 'eleven' 'twelve' 'thirteen' 'fourteen' 'fifteen' )
TOTAL_STEPS=15
COMPLETED=10
CURRENT_STEP='eleven'
opm_ui_gauge_start
opm_ui_gauge_stop
''',env=env)
    assert r.returncode == 0, r.stderr
    initial=(tmp_path/'initial.txt').read_text()
    assert '✓  one' in initial and '✓  ten' in initial
    assert '▶  ELEVEN' in initial and '□  fifteen' in initial


def _run_mock_uninstall(tmp_path, userdel_result):
    maintenance=(ROOT/'OPMMaintenance.sh').read_text()
    block=maintenance.split('uninstall_system() {',1)[1].split('\ninstall_release() {',1)[0]
    # Fixed root-owned helper locations are redirected to isolated temp files.
    block=block.replace('/usr/local/sbin/opm-installer', str(tmp_path/'helpers'/'opm-installer'))
    block=block.replace('/usr/local/share/opm-installer', str(tmp_path/'helpers'/'share'))
    home=tmp_path/'home'/'opm'
    (home/'app').mkdir(parents=True)
    (home/'keep-secret').write_text('keep when userdel fails')
    (tmp_path/'helpers'/'share').mkdir(parents=True)
    (tmp_path/'helpers'/'opm-installer').write_text('maintenance')
    marker=tmp_path/'account-exists'
    marker.write_text('still here')
    code=f'''set -Eeuo pipefail
APP_USER=opm
APP_HOME={str(home)!r}
APP_DIR="$APP_HOME/app"
VENV="$APP_HOME/opm-venv"
SERVICE_DIR="$APP_HOME/service"
PURGE=1
confirm() {{ return 0; }}
no_active_jobs() {{ return 0; }}
id() {{
  if [[ "${{1:-}}" == '-u' ]]; then echo 1001; return 0; fi
  [[ -f {str(marker)!r} ]]
}}
getent() {{ printf '%s:x:1001:1001::%s:/bin/bash\\n' "$APP_USER" "$APP_HOME"; }}
as_opm() {{ return 0; }}
loginctl() {{ return 0; }}
systemctl() {{ return 0; }}
userdel() {{
  if [[ {userdel_result!r} == 'success' || {userdel_result!r} == 'warning' ]]; then
    command rm -f {str(marker)!r}
    if [[ {userdel_result!r} == 'warning' ]]; then
      echo 'userdel: opm mail spool (/var/mail/opm) not found' >&2
      return 12
    fi
    return 0
  fi
  return 8
}}
uninstall_system() {{{block}
uninstall_system
'''
    result=_run(code,env=dict(os.environ,SUDO_USER='some-admin'))
    return result, home, marker, tmp_path/'helpers'


def test_failed_complete_uninstall_does_not_remove_home_or_claim_success(tmp_path):
    result, home, marker, helpers=_run_mock_uninstall(tmp_path,'failure')
    assert result.returncode != 0
    assert 'INCOMPLETE' in result.stderr
    assert marker.exists() and (home/'keep-secret').exists()
    assert (helpers/'opm-installer').exists()
    assert 'finished' not in result.stdout


def test_successful_complete_uninstall_removes_account_home_and_helpers(tmp_path):
    result, home, marker, helpers=_run_mock_uninstall(tmp_path,'success')
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert not home.exists()
    assert not (helpers/'opm-installer').exists()
    assert not (helpers/'share').exists()
    assert 'Complete uninstall finished' in result.stdout


def test_install_restores_missing_home_before_running_as_opm():
    script=(ROOT/'OPMInstall.sh').read_text()
    account=script.index("start_step 'Preparing the dedicated OPM service account'")
    home=script.index('run_logged install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$APP_HOME"',account)
    as_opm=script.index('as_opm() {', account)
    assert account < home < as_opm
    assert 'The existing opm user has an unexpected home directory' in script
    assert 'The opm home path is a symlink or not a directory' in script
    assert 'Repairing missing home directory for existing opm service account' in script


def test_release_version_matches_source():
    root=ROOT
    import tomllib
    with (root/'pyproject.toml').open('rb') as f:
        version=tomllib.load(f)['project']['version']
    assert version.count('.') == 2
    assert (root/'src/opm/__init__.py').read_text().count('"'+version+'"') >= 1
    assert (root/'OPMTUI.sh').read_text().count('"'+version+'"') >= 1


def test_successful_uninstall_suppresses_nonfatal_mail_spool_warning(tmp_path):
    result, home, marker, helpers = _run_mock_uninstall(tmp_path, 'warning')
    assert result.returncode == 0, result.stderr
    assert not marker.exists() and not home.exists()
    assert 'mail spool' not in result.stderr
    assert 'Complete uninstall finished' in result.stdout
