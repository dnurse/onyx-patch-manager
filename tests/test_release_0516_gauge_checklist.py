"""OPM v0.5.16: Persistent installer progress, no root or real newt required."""
import os
from pathlib import Path
import re
import subprocess

from test_release_054_whiptail import fake_whiptail

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_matches_actual_install_stages():
    source = (ROOT / 'OPMInstall.sh').read_text()
    block = source.split('OPM_UI_STAGES=(\n', 1)[1].split('\n)', 1)[0]
    names = re.findall(r"^  '([^']+)'$", block, re.M)
    steps = re.findall(r"^start_step '([^']+)'$", source, re.M)
    assert len(names) == 15
    assert names == steps
    assert 'opm_ui_gauge_update "$percent" "$CURRENT_STEP" "$COMPLETED"' in source


def _exercise(tmp_path, term_rows=24):
    fake_whiptail(tmp_path)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               OPM_GAUGE_LOG=str(tmp_path / 'gauge.log'))
    code = '''set -Eeuo pipefail
source ./OPMTUI.sh
OPM_UI_ACTIVE=1
OPM_UI_TERM_ROWS=''' + str(term_rows) + '''
OPM_UI_WIDTH=76
OPM_UI_ACTION_LABEL='UPGRADING ONYX PATCH MANAGER'
OPM_UI_STAGES=( 'Step 01' 'Step 02' 'Step 03' 'Step 04' 'Step 05' 'Step 06' 'Step 07' 'Step 08' 'Step 09' 'Step 10' 'Step 11' 'Step 12' 'Step 13' 'Step 14' 'Step 15' )
TOTAL_STEPS=15
COMPLETED=0
CURRENT_STEP='Step 01'
opm_ui_gauge_start
opm_ui_gauge_update 0 "$CURRENT_STEP" 0
COMPLETED=7
CURRENT_STEP='Step 08'
opm_ui_gauge_update 46 "$CURRENT_STEP" "$COMPLETED"
opm_ui_gauge_stop
# Simulate pausing for a credential prompt and restoring the progress window.
opm_ui_gauge_start
opm_ui_gauge_update 46 "$CURRENT_STEP" "$COMPLETED"
COMPLETED=15
CURRENT_STEP='OPM upgraded successfully'
opm_ui_gauge_update 100 "$CURRENT_STEP" "$COMPLETED"
opm_ui_gauge_stop
'''
    result = subprocess.run(['bash', '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    return (tmp_path / 'gauge.log').read_text()


def test_completed_and_future_stages_persist_after_dialog_restart(tmp_path):
    stream = _exercise(tmp_path)
    assert '46\nUPGRADING ONYX PATCH MANAGER' in stream
    assert '✓  Step 01' in stream
    assert '✓  Step 07' in stream
    assert '▶  STEP 08' in stream
    assert '□  Step 09' in stream
    assert '□  Step 15' in stream
    assert '100\nUPGRADING ONYX PATCH MANAGER' in stream
    for i in range(1, 16):
        assert f'✓  Step {i:02}' in stream
    assert stream.count('✓  Step ') >= 22


def test_short_terminal_shows_all_fifteen_stages_in_two_columns(tmp_path):
    stream = _exercise(tmp_path, term_rows=20)
    completed = stream.split('100\nUPGRADING ONYX PATCH MANAGER', 1)[1]
    assert all(f'Step {i:02}' in completed for i in range(1, 16))
    assert 'UPGRADING ONYX PATCH MANAGER' in stream


def test_no_rolling_window_reset_or_four_step_truncation():
    source = (ROOT / 'OPMTUI.sh').read_text()
    assert 'OPM_UI_DONE_STAGES' not in source
    assert 'OPM_UI_LAST_STAGE' not in source
    assert 'opm_ui_gauge_stage_line' in source
    assert 'OPM_UI_TERM_ROWS < 24' in source
