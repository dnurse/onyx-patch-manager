"""Regression tests for the v0.5.8 Whiptail installer release.

These run without APT, newt, Django, system services or real host changes.
"""
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_supported_newt_color_names_and_high_contrast_focus():
    run = subprocess.run(
        ['bash', '-c', 'source ./OPMTUI.sh; opm_ui_theme; printf "%s" "$NEWT_COLORS"'],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    palette = dict(entry.split('=', 1) for entry in run.stdout.split(':'))
    # These are the actual names newt's SLang palette parser understands.
    recognized = {'black', 'blue', 'green', 'cyan', 'red', 'magenta', 'brown',
                  'lightgray', 'gray', 'brightblue', 'brightgreen', 'brightcyan',
                  'brightred', 'brightmagenta', 'yellow', 'white'}
    assert all(name in recognized for val in palette.values()
               for name in val.split(',') if name)
    assert palette['root'] == 'lightgray,black'
    assert palette['title'] == 'brightcyan,black'
    assert palette['border'] == 'lightgray,black'
    assert palette['actbutton'] == 'white,green'
    assert palette['actlistbox'] == 'white,green'
    assert palette['fullscale'] == ',green'
    assert 'lightgreen' not in run.stdout
    assert 'lightcyan' not in run.stdout


def test_full_button_border_and_fixed_width_ascii_banner():
    s = (ROOT/'OPMTUI.sh').read_text()
    assert 'whiptail --fb --backtitle "$OPM_UI_BACKTITLE" "$@"' in s
    lines = s.split("art=$(cat <<'BANNER'\n", 1)[1].split('\nBANNER', 1)[0].splitlines()
    assert len(lines) == 6
    assert max(map(len, lines)) <= 94
    assert lines[0].startswith('  ___                     ____')
    assert ' / _ \\ _ __  _   ___  __ ' in lines[1]
    assert '|___/' in lines[-1]
    assert all('\\' in line or '/' in line or '_' in line for line in lines)
    assert 'whiptail --fb --backtitle "$OPM_UI_BACKTITLE"' in s  # gauge


def test_direct_install_launches_wizard_and_menu_stays_explicit():
    install = (ROOT/'OPMInstall.sh').read_text()
    assert '  "") ;;' in install  # no args is the install path
    assert '--install) OPM_INSTALL_EXPLICIT=1; shift' in install
    assert '--menu|--repair|--status|--uninstall|--web-access' in install
    assert 'exec bash "$SRC_DIR/OPMMaintenance.sh" --source "$SRC_DIR" "$@"' in install
    # An existing installation must open maintenance, not upgrade immediately.
    assert '-f "$ENV_FILE" && -d "$APP_DIR"' in install
    assert 'exec bash "$SRC_DIR/OPMMaintenance.sh" --source "$SRC_DIR" --menu' in install
    assert "opm_ui_first_install \"$SRC_DIR\"" in install
    assert 'opm_ui_message " ${INSTALL_ACTION_TITLE} complete "' in install
    maintenance = (ROOT/'OPMMaintenance.sh').read_text()
    assert 'if install_release; then' in maintenance
    assert '# A completed installation ends the utility; do not redisplay menu.' in maintenance


def test_preview_enters_real_whiptail_without_host_changes(tmp_path):
    # In a pseudo-terminal, run the actual source entry point in preview mode.
    # A fake Whiptail immediately cancels Welcome, so no OS changes can occur.
    stub = tmp_path / 'whiptail'
    events = tmp_path/'whiptail-events'
    stub.write_text('''#!/bin/sh
printf 'theme=%s\\n' "$NEWT_COLORS" >> "$OPM_FAKE_EVENTS"
printf 'args=%s\\n' "$*" >> "$OPM_FAKE_EVENTS"
exit 1
''')
    stub.chmod(0o755)
    env = dict(os.environ, OPM_FAKE_EVENTS=str(events),
               PATH=str(tmp_path)+os.pathsep+os.environ['PATH'], TERM='xterm')
    # `script` supplies the TTY requirement without installing or modifying OPM.
    command = 'bash ' + str(ROOT/'OPMInstall.sh') + ' --preview-ui'
    proc = subprocess.run(['script','-qefc',command,'/dev/null'],
                          env=env, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    output = events.read_text()
    assert 'actbutton=white,green' in output
    assert 'args=--fb --backtitle' in output
    assert 'Welcome / Introduction' in output


def test_persisted_access_and_patch_controls_still_in_place():
    tui=(ROOT/'OPMTUI.sh').read_text()
    maintenance=(ROOT/'OPMMaintenance.sh').read_text()
    assert 'opm_ui_access_from_env' in maintenance
    assert '--default-item "$current_mode"' in tui
    assert 'lan) lan_state=ON' in tui
    assert 'proxy) proxy_state=ON' in tui
    assert 'localhost) local_state=ON' in tui
    tpl = (ROOT/'src/opm/web/templates/web/patch.html').read_text()
    assert tpl.index('{% for h in hosts %}') < tpl.index('data-select-all-hosts')
    assert tpl.index('data-select-all-hosts') < tpl.index('class="execution-mode-picker"')


def _render_welcome(width):
    # Replace Whiptail with an argument capture to inspect what it will
    # actually receive, with zero terminal/package/service side effects.
    shell = r'''source ./OPMTUI.sh
OPM_UI_WIDTH="$1"
OPM_UI_TERM_ROWS=30
opm_ui_box() {
  while (( $# )); do
    if [[ "$1" == '--yesno' ]]; then
      printf '%s' "$2"
      return 0
    fi
    shift
  done
  return 1
}
opm_ui_welcome'''
    proc=subprocess.run(['bash','-c',shell,'-',str(width)],cwd=ROOT,
                        capture_output=True,text=True,check=True)
    return proc.stdout


def test_original_six_line_wordmark_centred_on_wide_terminals():
    rendered=_render_welcome(112)
    lines=rendered.splitlines()
    logo=[line for line in lines if '____' in line or '_ __' in line or '___/' in line]
    assert '  ___                     ____       _       _       __  __' in rendered
    assert r"| | | | '_ \| | | \ \/ / | |_) / _`" in rendered
    assert '             |___/                                                            |___/' in rendered
    assert len(lines)>=13
    assert all(len(line)<=104 for line in lines), max(map(len,lines))
    lead=lambda line: len(line)-len(line.lstrip(' '))
    bullets=[x for x in lines if x.lstrip().startswith('+ ')]
    assert len(bullets)==4
    assert len({lead(x) for x in bullets})==1
    assert lead(next(x for x in lines if 'Welcome to the Onyx Patch Manager' in x))>0


def test_compact_logo_fallback_on_80_column_terminal():
    rendered=_render_welcome(76)
    assert 'P A T C H   M A N A G E R' in rendered
    assert 'Welcome to the Onyx Patch Manager' in rendered
    assert max(map(len,rendered.splitlines()))<=68
