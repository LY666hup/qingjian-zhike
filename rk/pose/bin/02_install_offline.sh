#!/bin/sh
# Step 2 - install into an independent venv.  Never touches the system Python, never touches the YOLO env.
# Offline by construction: --no-index --find-links wheels, and --no-deps so pip cannot pull a musllinux build.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
VENV="$ROOT/venv"
WHEELS="$ROOT/wheels"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
LOG="$LOGS/step2_install.log"
: > "$LOG"

say() { echo "$@" | tee -a "$LOG"; }
fail() { say "INSTALL FAIL: $1 :: $2"; exit 1; }

say "INSTALL start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "  root   : $ROOT"
say "  wheels : $WHEELS"
say "  venv   : $VENV"

# ---- refuse to run against anything but the board interpreter this module was built for
PY=$(command -v python3 || true)
[ -n "$PY" ] || fail "no_python3" "python3 not on PATH"
PYV=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
[ "$PYV" = "3.11" ] || fail "python_version" "need 3.11, got $PYV ($PY)"
say "INSTALL ok: python $PYV at $PY"

# ---- step 1 must have passed; do not install on a mismatched set
"$PY" "$HERE/01_check_env.py" >>"$LOG" 2>&1 || fail "env_check" "bin/01_check_env.py failed, see $LOG"

# ---- independent venv without inheriting the system/YOLO site-packages
if [ -d "$VENV" ]; then
  say "INSTALL note: reusing existing venv $VENV"
else
  "$PY" -m venv "$VENV" >>"$LOG" 2>&1 || fail "venv_create" "python3 -m venv failed (missing python3-venv?)"
fi
VPY="$VENV/bin/python"
[ -x "$VPY" ] || fail "venv_python" "$VPY missing"

"$VPY" -m pip --version >>"$LOG" 2>&1 || "$VPY" -m ensurepip >>"$LOG" 2>&1 || fail "pip" "no pip in venv"

# ---- pinned, offline, in dependency order
PKGS="numpy==1.26.4 protobuf==4.25.3 flatbuffers==24.3.25 absl-py==2.1.0 attrs==23.2.0 \
opencv-python-headless==4.10.0.84 mediapipe==0.10.14"
say "INSTALL packages: $PKGS"
# shellcheck disable=SC2086
"$VPY" -m pip install --no-index --find-links "$WHEELS" --no-deps --disable-pip-version-check $PKGS \
  >>"$LOG" 2>&1 || fail "pip_install" "see $LOG (a musllinux wheel or a missing dependency shows up here)"

# ---- what landed, with sha256 of the wheels that were actually used
say "INSTALL installed:"
"$VPY" -m pip list --format=freeze 2>/dev/null | tee -a "$LOG" | sed 's/^/  /'

"$VPY" "$HERE/03_verify_import.py" >>"$LOG" 2>&1 || fail "verify_import" "import check failed, see $LOG"

say "INSTALL OK: venv=$VENV"
say "  activate: . $VENV/bin/activate"
say "  log: $LOG"