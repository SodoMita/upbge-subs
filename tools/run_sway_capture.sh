#!/bin/sh
# run_sway_capture.sh - bring up a FULL headless display stack and run a UPBGE
# capture blend inside it. No GPU, no pre-existing display, no /run/user.
#
# This is the self-contained sibling of tools/run_live_newton.sh (which
# assumes a display already exists and inherits DISPLAY). Here sway IS the
# display:
#
#   * sway with WLR_BACKENDS=headless + WLR_RENDERERS=pixman - the compositor
#     itself renders with the pixman software renderer;
#   * Xwayland :1 - the engine's GHOST is X11-only in UPBGE 0.50 (it tries
#     back-end(s) ['X11'] and nothing else), so the game window runs as an
#     X11 client inside sway;
#   * llvmpipe for the game's GL (LIBGL_ALWAYS_SOFTWARE=1);
#   * PulseAudio null sink - ONLY when the engine binary has not been
#     patched with tools/patch_upbge_audio3d.py (WITH_PULSE=1). Unpatched
#     0.50 segfaults in LA_Launcher::InitEngine -> AUD_Device_setSpeedOfSound
#     on a NULL 3D-device when no audio server is present; the patched
#     binary makes the server irrelevant. Either workaround is verified.
#
# Usage:
#   sh tools/run_sway_capture.sh <blend> [budget-seconds]
# Env:
#   UPBGE=/opt/upbge-0.50-linux-x64   engine dir (must contain blenderplayer)
#   WITH_PULSE=1                      also start a PulseAudio null sink
#   PLAYER_ARGS="-w 640 360"          extra blenderplayer args (window size)
#
# Requires: sway wlr-randr xwayland pulseaudio libgl1-mesa-dri
#           x11-xserver-utils          (install_env.sh installs all of it)
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BLEND="${1:?usage: run_sway_capture.sh <blend> [budget-seconds]}"
BUDGET="${2:-420}"
UPBGE="${UPBGE:-/opt/upbge-0.50-linux-x64}"
PLAYER_ARGS="${PLAYER_ARGS:--w 640 360}"

case "$BLEND" in /*) ;; *) BLEND="$HERE/$BLEND" ;; esac

RUN="$(mktemp -d /tmp/upbge-sway.XXXXXX)"
export XDG_RUNTIME_DIR="$RUN"
export WAYLAND_DISPLAY=wayland-1
chmod 700 "$RUN"

# 1. sway: headless backend + pixman renderer
WLR_BACKENDS=headless WLR_RENDERERS=pixman \
  sway -d >"$RUN/sway.log" 2>&1 &
SWAY_PID=$!
for _ in $(seq 1 30); do
  [ -S "$RUN/wayland-1" ] && break
  sleep 1
done
if [ ! -S "$RUN/wayland-1" ]; then
  echo "ERROR: sway did not start; log: $RUN/sway.log" >&2
  exit 1
fi
echo "[sway] headless + pixman up (pid $SWAY_PID, $WAYLAND_DISPLAY)"

# 2. Xwayland :1 (the engine's X11 GHOST lands here)
XAUTHORITY="$RUN/xauth"
: > "$XAUTHORITY"; chmod 600 "$XAUTHORITY"
XAUTHORITY="$XAUTHORITY" Xwayland :1 -rootless >"$RUN/xwayland.log" 2>&1 &
XW_PID=$!
export DISPLAY=:1 XAUTHORITY
for _ in $(seq 1 30); do
  xset q >/dev/null 2>&1 && break
  sleep 1
done
echo "[xwayland] :1 up (pid $XW_PID)"

# 3. optional PulseAudio null sink (unpatched binaries)
PA_PID=""
if [ "${WITH_PULSE:-0}" = "1" ] && ! pactl info >/dev/null 2>&1; then
  echo "load-module module-null-sink sink_name=headless" >"$RUN/pa.conf"
  pulseaudio -D --exit-idle-time=-1 -F "$RUN/pa.conf" >"$RUN/pa.log" 2>&1 &
  PA_PID=$!
  for _ in $(seq 1 15); do
    pactl info >/dev/null 2>&1 && break
    sleep 1
  done
  echo "[pulse] null sink up (pid $PA_PID)"
fi

# 4. the game - prefer the dedicated player (no UI, clean exit); fall back
#    to the editor + autostart_game.py
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
cd "$HERE"
rm -f game_debug.log
rm -rf capture
if [ -x "$UPBGE/blenderplayer" ]; then
  timeout "$BUDGET" "$UPBGE/blenderplayer" $PLAYER_ARGS "$BLEND" \
    >"$RUN/game.log" 2>&1
else
  timeout "$BUDGET" "$UPBGE/blender" "$BLEND" -P "$HERE/autostart_game.py" \
    >"$RUN/game.log" 2>&1
fi
RC=$?
echo "[game] exit=$RC  (124 = budget expired; the engine sometimes hangs on teardown - the evidence below is what matters)"

echo "=== game_debug.log (tail) ==="
tail -n 25 "$HERE/game_debug.log" 2>/dev/null
echo "=== captures ==="
ls "$HERE/capture" 2>/dev/null | wc -l
ls -t "$HERE/capture" 2>/dev/null | head -5
echo "=== run dir kept for inspection: $RUN (sway.log xwayland.log game.log) ==="

# cleanup: teardown can hang, so SIGKILL what we started
kill -9 "$XW_PID" "$SWAY_PID" 2>/dev/null
[ -n "$PA_PID" ] && kill "$PA_PID" 2>/dev/null
exit $RC
