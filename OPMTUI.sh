#!/usr/bin/env bash
# Shared, root-owned-on-install Whiptail presentation layer for OPM.
# Sourced by OPMInstall.sh and OPMMaintenance.sh. No executable install logic
# lives here: it only gathers validated answers and renders native newt dialogs.
# NEWT_COLORS uses 16 terminal colour names; navy/slate/teal are approximations.

OPM_UI_VERSION="0.6.0"
OPM_UI_BACKTITLE=" ONYX PATCH MANAGER  |  ${OPM_UI_VERSION}  |  INSTALLATION & MAINTENANCE "
OPM_UI_WIDTH=76
OPM_UI_TERM_ROWS=24
OPM_UI_ACTIVE=0
OPM_UI_GAUGE_PID=''
OPM_UI_GAUGE_DIR=''
OPM_UI_GAUGE_FD=''

opm_ui_theme() {
  # NEWT_COLORS accepts ANSI/SLang names, not RGB/hex values. Prefer the
  # closest reproducible OPM colours: navy/charcoal, light slate and teal.
  # Keep the active control WHITE on GREEN for legibility. v0.5.8 used
  # black-on-brightgreen, which several SSH console palettes rendered as
  # low-contrast dark text on a muted green button.
  export NEWT_COLORS='root=lightgray,black:border=lightgray,black:window=white,black:shadow=black,black:title=brightcyan,black:button=lightgray,black:actbutton=white,green:compactbutton=lightgray,black:checkbox=lightgray,black:actcheckbox=white,green:entry=white,blue:disentry=gray,black:label=brightcyan,black:listbox=lightgray,black:actlistbox=white,green:sellistbox=white,green:actsellistbox=white,green:textbox=lightgray,black:acttextbox=white,green:emptyscale=,blue:fullscale=,green:helpline=lightgray,black:roottext=brightcyan,black'
  unset NEWT_COLORS_FILE
}

opm_ui_available() { [[ -t 0 && -t 1 && "${TERM:-dumb}" != dumb ]]; }

# Only the interactive entry points bootstrap whiptail. Noninteractive installs
# never install a UI dependency or require a terminal.
opm_ui_bootstrap() {
  opm_ui_available || return 1
  if ! command -v whiptail >/dev/null 2>&1; then
    [[ $EUID == 0 ]] || { printf 'Whiptail requires root to install.\n' >&2; return 1; }
    printf 'Preparing OPM terminal installer (installing whiptail)...\n'
    DEBIAN_FRONTEND=noninteractive apt-get -qq update &&
      DEBIAN_FRONTEND=noninteractive apt-get -qq install -y whiptail || {
        printf 'Could not install whiptail. Check APT connectivity and retry.\n' >&2
        return 1
      }
  fi
  opm_ui_theme
  opm_ui_dimensions
  OPM_UI_ACTIVE=1
}

# Recover the calling terminal if a dialog fails or the operator presses
# Escape. In particular, do not leave input echo disabled after newt exits.
# Only used by interactive entrypoints, never by headless installs.
opm_ui_terminal_cleanup() {
  opm_ui_gauge_stop
  if [[ -t 0 && -t 1 && -r /dev/tty && -w /dev/tty ]]; then
    stty sane </dev/tty >/dev/null 2>&1 || true
    tput rmcup >/dev/tty 2>/dev/null || true
    printf '\033[0m\033[?25h' >/dev/tty 2>/dev/null || true
  fi
}

opm_ui_box() {
  # Native newt --fb buttons and the same backtitle on every screen.
  whiptail --fb --backtitle "$OPM_UI_BACKTITLE" "$@"
}

# CRITICAL: whiptail draws to stdout even with --output-fd set. Capturing its
# stdout in $(...) prevents newt from seeing the terminal and may make a
# selection screen disappear with exit 255. Save the command-substitution
# pipe on FD 3, send only the selected value to FD 3, and restore stdout to
# the controlling terminal for rendering. No secrets are written to logs.
opm_ui_capture() {
  if [[ -t 0 && -r /dev/tty && -w /dev/tty ]]; then
    opm_ui_box --output-fd 3 "$@" 3>&1 1>/dev/tty
  else
    # Headless test doubles have no controlling terminal; keep their output
    # capturable without altering the live-terminal path above.
    opm_ui_box --output-fd 1 "$@"
  fi
}

opm_ui_message() {
  local title="$1" message="$2"
  message=${message//\\n/$'\n'}
  opm_ui_box --title "$title" --msgbox "$message" 15 "$OPM_UI_WIDTH"
}

opm_ui_confirm() {
  local title="$1" message="$2" yes="${3:-Continue}" no="${4:-Back}"
  message=${message//\\n/$'\n'}
  opm_ui_box --title "$title" --yes-button "$yes" --no-button "$no" \
    --yesno "$message" 17 "$OPM_UI_WIDTH"
}

opm_ui_input() {
  local title="$1" question="$2" initial="${3:-}"
  question=${question//\\n/$'\n'}
  opm_ui_capture --title "$title" --ok-button Continue --cancel-button Back \
    --inputbox "$question" 13 "$OPM_UI_WIDTH" "$initial"
}

# Whiptail is character-cell based. The requested six-row Onyx Patch Manager
# wordmark is 94 columns wide and needs a dialog at least 102 columns wide.
# Use the original wordmark in large SSH consoles and a legible condensed one
# in 80-column consoles. Never wrap the logo: that destroys its letter shapes.
opm_ui_dimensions() {
  local cols=0 rows=0 measure
  # stty reports the *actual* terminal size, unlike a potentially stale
  # inherited COLUMNS environment variable.
  if [[ -t 0 ]]; then
    measure=$(stty size 2>/dev/null || true)
    IFS=" " read -r rows cols <<< "$measure"
  fi
  if [[ ! "$cols" =~ ^[0-9]+$ || "$cols" -lt 40 ]]; then
    cols=$(tput cols 2>/dev/null || printf '80')
  fi
  if [[ ! "$rows" =~ ^[0-9]+$ || "$rows" -lt 16 ]]; then
    rows=$(tput lines 2>/dev/null || printf '24')
  fi
  if [[ ! "$cols" =~ ^[0-9]+$ || "$cols" -lt 40 ]]; then cols=80; fi
  if [[ ! "$rows" =~ ^[0-9]+$ || "$rows" -lt 16 ]]; then rows=24; fi
  OPM_UI_TERM_ROWS=$rows
  if (( cols >= 106 )); then
    OPM_UI_WIDTH=$((cols - 4))
    (( OPM_UI_WIDTH > 114 )) && OPM_UI_WIDTH=114
  else
    OPM_UI_WIDTH=$((cols - 4))
    (( OPM_UI_WIDTH > 76 )) && OPM_UI_WIDTH=76
  fi
}

# Space-pad a single line within the dialog body. Do not use a global newt
# alignment setting, because the numbered tasks should remain left-aligned.
opm_ui_center_line() {
  local line="$1" inner=$((OPM_UI_WIDTH - 8)) pad
  pad=$(((inner - ${#line}) / 2))
  (( pad > 0 )) || pad=0
  printf '%*s%s' "$pad" '' "$line"
}

# Align the ASCII rows as one block, preserving every internal character.
# Similarly, the four bullets form ONE left-aligned, centered block.
opm_ui_center_block() {
  local block="$1" max=0 line pad inner=$((OPM_UI_WIDTH - 8))
  while IFS= read -r line; do
    (( ${#line} > max )) && max=${#line}
  done <<< "$block"
  pad=$(((inner - max) / 2))
  (( pad > 0 )) || pad=0
  while IFS= read -r line; do
    printf '%*s%s\n' "$pad" '' "$line"
  done <<< "$block"
}

opm_ui_welcome() {
  local art bullet_block description height
  if (( OPM_UI_WIDTH >= 102 )); then
    # EXACT original artwork supplied for the installer. The long wordmark
    # cannot be fitted into a standard 80-column console without distortion.
    art=$(cat <<'BANNER'
  ___                     ____       _       _       __  __
 / _ \ _ __  _   ___  __ |  _ \ __ _| |_ ___| |__   |  \/  | __ _ _ __   __ _  __ _  ___ _ __
| | | | '_ \| | | \ \/ / | |_) / _` | __/ __| '_ \  | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \ '__|
| |_| | | | | |_| |>  <  |  __/ (_| | || (__| | | | | |  | | (_| | | | | (_| | (_| |  __/ |
 \___/|_| |_|\__, /_/\_\ |_|   \__,_|\__\___|_| |_| |_|  |_|\__,_|_| |_|\__,_|\__, |\___|_|
             |___/                                                            |___/
BANNER
)
  else
    # Readable fallback only when the terminal physically cannot show 94
    # columns of art. Never replace the approved full-width design on a wide terminal.
    art=$(cat <<'BANNER'
   ___  _   _ __   ____  __
  / _ \| \ | |\ \ / /\ \/ /
 | | | |  \| | \ V /  >  <
 | |_| | |\  |  | |  / . \
  \___/|_| \_|  |_| /_/ \_\
     P A T C H   M A N A G E R
BANNER
)
  fi
  bullet_block=$(cat <<'BULLETS'
  + Check system requirements
  + Install OPM and its components
  + Configure web access
  + Create the administrator account
BULLETS
)
  description="$(opm_ui_center_block "$art")"
  description+=$'\n'"$(opm_ui_center_line 'Welcome to the Onyx Patch Manager (OPM) Installer')"
  description+=$'\n'"$(opm_ui_center_line 'This installer will:')"
  description+=$'\n'"$(opm_ui_center_block "$bullet_block")"
  description+=$'\n'"$(opm_ui_center_line 'Press <Next> to continue, or <Cancel> to exit.')"
  height=23
  (( OPM_UI_TERM_ROWS < 26 )) && height=22
  (( OPM_UI_TERM_ROWS < 24 )) && height=$((OPM_UI_TERM_ROWS - 2))
  opm_ui_box --title ' OPM Installer | Welcome / Introduction ' \
    --yes-button Next --no-button Cancel \
    --yesno "$description" "$height" "$OPM_UI_WIDTH"
}

opm_ui_system_check() {
  local disk_kb status
  local dependency
  for dependency in apt-get python3 systemctl; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
      opm_ui_message ' Missing system requirement ' "The required program $dependency was not found. Install it before continuing."
      return 1
    fi
  done
  status='Operating system: Debian 13\nAPT package manager: available\nPython 3: available\nsystemd: available' 
  disk_kb=$(df -Pk / | awk 'NR==2 {print $4}')
  if [[ -z "$disk_kb" || ! "$disk_kb" =~ ^[0-9]+$ || "$disk_kb" -lt 262144 ]]; then
    opm_ui_message ' Insufficient disk space ' 'OPM requires at least 256 MiB of free space for installation. More space is recommended for backups and Python dependencies.'
    return 1
  fi
  opm_ui_confirm ' System requirements ' "${status}\nFree disk space: $((disk_kb / 1024)) MiB\n\nChecks passed. Continue to web configuration?" Next Back
}

# Load only the web access fields from an existing root-protected env file.
# Never `source` it: it contains a signing secret and is not shell syntax.
opm_ui_access_from_env() {
  local env_file="$1" mode host proxy
  [[ -f "$env_file" && ! -L "$env_file" ]] || return 1
  mode=$(awk -F= '$1=="OPM_WEB_ACCESS_MODE" {print $2; exit}' "$env_file")
  case "$mode" in lan|proxy|localhost) ;; *) return 1 ;; esac
  host=$(awk -F= '$1=="OPM_WEB_ALLOWED_HOSTS" {print $2; exit}' "$env_file")
  host=${host%%,*}
  proxy=$(awk -F= '$1=="OPM_WEB_TRUSTED_PROXY_IPS" {print $2; exit}' "$env_file")
  OPM_UI_MODE="$mode"
  OPM_UI_HOST="$host"
  OPM_UI_PROXY_IP="$proxy"
  return 0
}

opm_ui_select_access() {
  # The first installation defaults to LAN. Returning to this screen (or
  # opening it through maintenance later) preserves the active selection.
  local selected access_prompt lan_state proxy_state local_state
  local current_mode="${OPM_UI_MODE:-lan}"
  case "$current_mode" in lan|proxy|localhost) ;; *) current_mode=lan ;; esac
  lan_state=OFF; proxy_state=OFF; local_state=OFF
  case "$current_mode" in
    lan) lan_state=ON ;;
    proxy) proxy_state=ON ;;
    localhost) local_state=ON ;;
  esac
  access_prompt=$'WEB ACCESS CONFIGURATION\n\nSelect how OPM will be accessed over the network.\nLAN HTTP is suitable for a trusted LAN or encrypted VPN.\nNever expose the HTTP listener directly to the Internet.'
  selected=$(opm_ui_capture --title ' OPM Installer | Web Access Configuration ' \
    --ok-button Next --cancel-button Back \
    --default-item "$current_mode" \
    --radiolist "$access_prompt" \
    19 "$OPM_UI_WIDTH" 3 \
    lan 'LAN / VPN HTTP (Default for new installs)' "$lan_state" \
    proxy 'Internal HTTPS Reverse Proxy' "$proxy_state" \
    localhost 'Localhost Only (SSH tunnel)' "$local_state") || return $?
  # Preserve the last entered address for the chosen mode when appropriate.
  local former_mode="${OPM_UI_MODE:-}" former_host="${OPM_UI_HOST:-}"
  local former_proxy="${OPM_UI_PROXY_IP:-}"
  local answer detected
  case "$selected" in
    lan)
      detected=$(python3 - "$OPM_UI_SOURCE" <<'PYIP'
import sys
sys.path.insert(0, sys.argv[1])
from OPMWebAccess import detect_lan_ip
print(detect_lan_ip())
PYIP
) || return 1
      [[ "$former_mode" == lan && -n "$former_host" ]] && detected="$former_host"
      while :; do
        answer=$(opm_ui_input ' LAN/VPN HTTP | Network address ' \
          'Confirm the private IPv4 address of this OPM LXC/VM:\n\nOpen OPM from http://IP:8765' "$detected") || return 1
        if python3 - "$OPM_UI_SOURCE" "$answer" <<'PYVALID'
import sys
sys.path.insert(0, sys.argv[1])
from OPMWebAccess import settings_for
settings_for('lan', sys.argv[2])
PYVALID
        then OPM_UI_HOST="$answer"; OPM_UI_PROXY_IP=''; OPM_UI_MODE=lan; break; fi
        opm_ui_message ' Invalid LAN address ' 'Enter a private non-loopback IPv4 address belonging to this host.'
      done
      ;;
    proxy)
      while :; do
        local default_host='' default_proxy=''
        if [[ "$former_mode" == proxy ]]; then
          default_host="$former_host"; default_proxy="$former_proxy"
        fi
        answer=$(opm_ui_input ' HTTPS proxy | Hostname ' \
          'Enter the private HTTPS hostname without https:// or a port.\n\nExample: opm.example.internal' "$default_host") || return 1
        local entered_proxy
        entered_proxy=$(opm_ui_input ' HTTPS proxy | Trusted source ' \
          'Enter your reverse proxy private source IPv4 address.\n\nOnly this proxy can forward HTTPS headers.' "$default_proxy") || return 1
        if python3 - "$OPM_UI_SOURCE" "$answer" "$entered_proxy" <<'PYVALID'
import sys
sys.path.insert(0, sys.argv[1])
from OPMWebAccess import settings_for
settings_for('proxy', sys.argv[2], sys.argv[3])
PYVALID
        then
          OPM_UI_HOST="$answer"; OPM_UI_PROXY_IP="$entered_proxy"
          OPM_UI_MODE=proxy; break
        fi
        opm_ui_message ' Invalid reverse proxy settings ' 'Enter a valid hostname and a private IPv4 address for your proxy.'
      done
      ;;
    localhost)
      OPM_UI_HOST=''; OPM_UI_PROXY_IP=''; OPM_UI_MODE=localhost ;;
    *) return 1 ;;
  esac
  return 0
}

opm_ui_get_timezone() {
  local zone answer
  zone="${1:-Etc/UTC}"
  while :; do
    answer=$(opm_ui_input ' Step 2 of 4 : Timezone ' \
      'Select the timezone used by the OPM dashboard.\n\nUse an IANA timezone such as America/Toronto or Etc/UTC.' "$zone") || return 1
    if [[ "$answer" != /* && "$answer" != *..* && -f "/usr/share/zoneinfo/$answer" ]]; then
      OPM_UI_TIMEZONE="$answer"; return 0
    fi
    opm_ui_message ' Invalid timezone ' 'Select an existing IANA timezone (e.g. America/Toronto).'
  done
}

opm_ui_get_administrator() {
  local account password confirm_password
  account=$(opm_ui_input ' Step 3 of 4 : Administrator ' \
    'Choose the first OPM web administrator username.\n\nThe local Linux service account will always be named opm.' "${OPM_UI_ADMIN:-admin}") || return 1
  if [[ ! "$account" =~ ^[A-Za-z][A-Za-z0-9_.@+-]{2,63}$ ]]; then
    opm_ui_message ' Invalid username ' 'Use 3-64 characters, starting with a letter; letters, digits, . _ @ + - are allowed.'
    return 2
  fi
  password=$(opm_ui_capture --title ' Step 3 of 4 : Administrator password ' \
    --ok-button Continue --cancel-button Back \
    --passwordbox $'Create a strong administrator password (minimum 12 characters).\n\nThe password is never saved to installer logs.' \
    13 "$OPM_UI_WIDTH") || return 1
  if [[ ${#password} -lt 12 ]]; then
    opm_ui_message ' Password too short ' 'Choose a password with at least 12 characters.'
    return 2
  fi
  confirm_password=$(opm_ui_capture --title ' Step 3 of 4 : Confirm password ' \
    --ok-button Continue --cancel-button Back \
    --passwordbox 'Re-enter the administrator password:' 12 "$OPM_UI_WIDTH") || return 1
  if [[ "$password" != "$confirm_password" ]]; then
    opm_ui_message ' Password mismatch ' 'The two passwords did not match. Please try again.'
    return 2
  fi
  OPM_UI_ADMIN="$account"
  OPM_UI_ADMIN_PASSWORD="$password"
  unset confirm_password password
  return 0
}

opm_ui_review_install() {
  local access
  case "$OPM_UI_MODE" in
    lan) access="LAN/VPN HTTP  http://${OPM_UI_HOST}:8765" ;;
    proxy) access="HTTPS reverse proxy  https://${OPM_UI_HOST}" ;;
    localhost) access='Localhost only  http://127.0.0.1:8765' ;;
  esac
  local summary
  printf -v summary 'INSTALLATION SUMMARY\n\nReview your configuration before installation:\n\n  Installation path : /home/opm/onyx_patch_manager\n  Service account   : opm (created automatically)\n  Web access mode   : %s\n  Timezone          : %s\n  Admin username    : %s\n\nOPM installs Python dependencies and its services.\nAfter successful installation, the installer will exit.\n\nProceed with installation?' "$access" "$OPM_UI_TIMEZONE" "$OPM_UI_ADMIN"
  opm_ui_box --title ' OPM Installer | Installation Summary / Review ' \
    --yes-button Install --no-button Back --yesno "$summary" 20 "$OPM_UI_WIDTH"
}

# First-run wizard finishes before any package changes or account creation.
# Only the confirmed configuration is applied later by the core installer.
opm_ui_first_install() {
  OPM_UI_SOURCE="$1"
  local guessed="$2" rc
  OPM_UI_MODE='lan'; OPM_UI_HOST=''; OPM_UI_PROXY_IP=''
  OPM_UI_TIMEZONE="$guessed"; OPM_UI_ADMIN=''; OPM_UI_ADMIN_PASSWORD=''
  OPM_UI_WIZARD_STEP='Welcome'
  opm_ui_welcome || return $?
  OPM_UI_WIZARD_STEP='System Requirements'
  opm_ui_system_check || return $?
  while :; do
    OPM_UI_WIZARD_STEP='Web Access'
    opm_ui_select_access || return $?
    OPM_UI_WIZARD_STEP='Timezone'
    opm_ui_get_timezone "$OPM_UI_TIMEZONE" || continue
    OPM_UI_WIZARD_STEP='Administrator'
    while :; do
      if opm_ui_get_administrator; then break; else
        rc=$?; [[ $rc == 2 ]] && continue
        # Back from administrator returns to web access.
        break
      fi
    done
    [[ -n "$OPM_UI_ADMIN" ]] || continue
    OPM_UI_WIZARD_STEP='Review'
    if opm_ui_review_install; then return 0; fi
    OPM_UI_ADMIN=''
    unset OPM_UI_ADMIN_PASSWORD
  done
}

# The caller owns OPM_UI_STAGES, the manifest of every installation stage.
# NOTE: Whiptail/newt FIXES the gauge textbox height from its INITIAL text.
# If the initial --gauge argument has only two lines, later XXX updates with
# fifteen lines stay clipped. Build the complete checklist BEFORE spawning it.
opm_ui_gauge_stage_line() {
  local index="$1" completed="$2" current="$3" name="$4"
  if (( index < completed )); then
    printf '✓  %s' "$name"
  elif (( index == completed )) && [[ "$name" == "$current" ]]; then
    # Native newt gauges cannot reliably apply per-line foreground colours or
    # bold. The white gauge text plus arrow and uppercase stage name distinguish
    # the active step; checked lines and empty squares preserve completed and
    # upcoming stages. The green progress bar remains the completion cue.
    printf '▶  %s' "${name^^}"
  else
    printf '□  %s' "$name"
  fi
}

opm_ui_gauge_text() {
  local complete="${1:-0}" message="${2:-}" text line index left right
  [[ "$complete" =~ ^[0-9]+$ ]] || complete=0
  text="${OPM_UI_ACTION_LABEL:-INSTALLING ONYX PATCH MANAGER}"
  if declare -p OPM_UI_STAGES >/dev/null 2>&1 && (( ${#OPM_UI_STAGES[@]} > 0 )); then
    local count=${#OPM_UI_STAGES[@]} col_width split
    (( complete > count )) && complete=$count
    # Initial window drawing must include an active step even before the
    # installer calls start_step for the first time.
    if [[ -z "$message" ]] && (( complete < count )); then
      message="${OPM_UI_STAGES[complete]}"
    fi
    if (( OPM_UI_TERM_ROWS < 24 )); then
      col_width=$(( (OPM_UI_WIDTH - 12) / 2 ))
      (( col_width < 10 )) && col_width=10
      split=$(( (count + 1) / 2 ))
      for (( index=0; index<split; index++ )); do
        left=$(opm_ui_gauge_stage_line "$index" "$complete" "$message" "${OPM_UI_STAGES[index]}")
        if (( index + split < count )); then
          right=$(opm_ui_gauge_stage_line "$((index+split))" "$complete" "$message" "${OPM_UI_STAGES[index+split]}")
          printf -v line '%-*.*s  %-*.*s' "$col_width" "$col_width" "$left" "$col_width" "$col_width" "$right"
        else
          printf -v line '%.*s' "$col_width" "$left"
        fi
        text+=$'\n'"$line"
      done
    else
      for (( index=0; index<count; index++ )); do
        line=$(opm_ui_gauge_stage_line "$index" "$complete" "$message" "${OPM_UI_STAGES[index]}")
        text+=$'\n'"$line"
      done
    fi
  else
    # Standalone callers without a stage manifest still get the original
    # percentage-based progress display.
    if (( complete >= ${TOTAL_STEPS:-15} )); then
      text+=$'\n✓  Operation complete'
    else
      text+=$'\n▶  '"${message:-Preparing operation...}"
    fi
  fi
  printf '%s' "$text"
}

opm_ui_gauge_start() {
  [[ "$OPM_UI_ACTIVE" == 1 ]] || return 0
  OPM_UI_GAUGE_HEIGHT=$((OPM_UI_TERM_ROWS - 2))
  (( OPM_UI_GAUGE_HEIGHT > 23 )) && OPM_UI_GAUGE_HEIGHT=23
  (( OPM_UI_GAUGE_HEIGHT < 14 )) && OPM_UI_GAUGE_HEIGHT=14
  OPM_UI_GAUGE_DIR=$(mktemp -d /tmp/opm-gauge.XXXXXXXX) || return 1
  chmod 0700 "$OPM_UI_GAUGE_DIR"
  mkfifo -m 0600 "$OPM_UI_GAUGE_DIR/events"
  local initial_text
  initial_text=$(opm_ui_gauge_text "${COMPLETED:-0}" "${CURRENT_STEP:-}")
  # Force UTF-8 for native checkmarks, arrows and empty squares even if an SSH
  # login defaults to LANG=C. Debian 13 provides the C.UTF-8 locale.
  local initial_percent=0
  if [[ "${COMPLETED:-0}" =~ ^[0-9]+$ && "${TOTAL_STEPS:-0}" =~ ^[0-9]+$ ]] &&
     (( ${TOTAL_STEPS:-0} > 0 )); then
    initial_percent=$((COMPLETED * 100 / TOTAL_STEPS))
  fi
  LC_ALL=C.UTF-8 whiptail --fb --backtitle "$OPM_UI_BACKTITLE" \
    --title ' OPM Installer | Progress ' \
    --gauge "$initial_text" \
    "$OPM_UI_GAUGE_HEIGHT" "$OPM_UI_WIDTH" "$initial_percent" < "$OPM_UI_GAUGE_DIR/events" &
  OPM_UI_GAUGE_PID=$!
  exec {OPM_UI_GAUGE_FD}>"$OPM_UI_GAUGE_DIR/events"
  if [[ -n "${CURRENT_STEP:-}" && "${COMPLETED:-0}" =~ ^[0-9]+$ ]]; then
    opm_ui_gauge_update "$((COMPLETED * 100 / ${TOTAL_STEPS:-15}))" "$CURRENT_STEP" "$COMPLETED"
  fi
}

opm_ui_gauge_update() {
  [[ -n "$OPM_UI_GAUGE_FD" ]] || return 0
  local percent="$1" message="$2" complete="${3:-0}" text
  text=$(opm_ui_gauge_text "$complete" "$message")
  # The XXX frame replaces the text content without changing its height. The
  # initial text always has exactly as many rows as subsequent progress text.
  printf 'XXX\n%s\n%s\nXXX\n' "$percent" "$text" >&"$OPM_UI_GAUGE_FD" || true
}

opm_ui_gauge_stop() {
  [[ -n "$OPM_UI_GAUGE_FD" ]] || return 0
  exec {OPM_UI_GAUGE_FD}>&-
  if [[ -n "$OPM_UI_GAUGE_PID" ]]; then
    wait "$OPM_UI_GAUGE_PID" 2>/dev/null || true
  fi
  rm -rf -- "$OPM_UI_GAUGE_DIR"
  OPM_UI_GAUGE_FD=''; OPM_UI_GAUGE_DIR=''; OPM_UI_GAUGE_PID=''
}

opm_ui_show_text() {
  local title="$1" content="$2" tmp
  tmp=$(mktemp /tmp/opm-tui-output.XXXXXXXX) || return 1
  chmod 0600 "$tmp"
  printf '%s\n' "$content" > "$tmp"
  opm_ui_box --title "$title" --textbox "$tmp" 20 "$OPM_UI_WIDTH" || true
  rm -f -- "$tmp"
}
