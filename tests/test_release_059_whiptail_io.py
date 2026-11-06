"""Regression coverage for whiptail input capture and preflight UI failures.

The live-path test uses a pseudo-terminal and a whiptail replacement that
REFUSES to render unless its stdout is a terminal. This reproduces the
System Requirements -> Web Access disappearance seen in v0.5.8.
"""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_whiptail_capture_preserves_tty_and_uses_fd3(tmp_path):
    whiptail = tmp_path / 'whiptail'
    calls = tmp_path / 'calls'
    whiptail.write_text('''#!/usr/bin/env bash
printf '%q ' "$@" >> "$OPM_TEST_UI_CALLS"
printf '\\n' >> "$OPM_TEST_UI_CALLS"
if [[ "$*" == *'--radiolist'* ]]; then
  if [[ ! -t 1 ]]; then echo 'stdout is not a terminal' >&2; exit 255; fi
  # Newt writes its selected value to the specified output fd.
  [[ "$*" == *'--output-fd 3'* ]] || exit 254
  printf 'lan' >&3
fi
exit 0
''')
    whiptail.chmod(0o755)
    runner = tmp_path / 'run.sh'
    runner.write_text(f'''#!/usr/bin/env bash
set -Eeuo pipefail
source {str(ROOT / 'OPMTUI.sh')!r}
OPM_UI_WIDTH=76
OPM_UI_TERM_ROWS=24
opm_ui_welcome
opm_ui_system_check
chosen=$(opm_ui_capture --title 'Web Access Configuration' --radiolist 'Mode' 16 76 3 lan 'LAN HTTP' ON proxy 'Proxy' OFF localhost 'Local' OFF)
[[ "$chosen" == lan ]] || exit 15
printf 'REACHED_WEB_ACCESS=%s\\n' "$chosen"
''')
    runner.chmod(0o755)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'TERM': 'xterm-256color', 'OPM_TEST_UI_CALLS': str(calls)}
    proc = subprocess.run(['script', '-qefc', f'bash {runner}', '/dev/null'],
                          env=env, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'REACHED_WEB_ACCESS=lan' in proc.stdout
    events = calls.read_text().splitlines()
    assert len(events) == 3
    assert '--yesno' in events[0] and 'Welcome' in events[0]
    assert '--yesno' in events[1] and 'System' in events[1] and 'requirements' in events[1]
    assert '--radiolist' in events[2] and '--output-fd 3' in events[2]


def test_all_interactive_form_dialogs_share_capture_helper():
    ui = (ROOT / 'OPMTUI.sh').read_text()
    maintenance = (ROOT / 'OPMMaintenance.sh').read_text()
    assert '3>&1 1>/dev/tty' in ui
    assert 'selected=$(opm_ui_capture' in ui
    assert 'password=$(opm_ui_capture' in ui
    assert 'confirm_password=$(opm_ui_capture' in ui
    assert 'choice=$(opm_ui_capture' in maintenance
    assert 'selected=$(opm_ui_box' not in ui
    assert 'choice=$(opm_ui_box' not in maintenance


def test_preflight_stop_reports_screen_and_restores_terminal():
    install = (ROOT / 'OPMInstall.sh').read_text()
    ui = (ROOT / 'OPMTUI.sh').read_text()
    assert 'OPM_UI_WIZARD_STEP' in ui
    assert 'Setup ended at %s (dialog status %s)' in install
    assert 'No OPM packages, services or users were changed' in install
    assert "trap 'opm_ui_terminal_cleanup' EXIT" in install
    assert 'stty sane' in ui and 'tput rmcup' in ui


def test_native_palette_improves_active_button_readability():
    p = subprocess.run(['bash', '-c', 'source ./OPMTUI.sh; opm_ui_theme; printf "%s" "$NEWT_COLORS"'],
                       cwd=ROOT, text=True, capture_output=True, check=True)
    assert 'actbutton=white,green' in p.stdout
    assert 'actlistbox=white,green' in p.stdout
    assert 'fullscale=,green' in p.stdout
    assert 'border=lightgray,black' in p.stdout
