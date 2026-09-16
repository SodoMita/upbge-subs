#!/bin/sh
# run_live_newton.sh - live smoke test of the Newton example in the real
# UPBGE player, software GL (sway/XWayland + llvmpipe or any GL you have).
#
#   sh tools/run_live_newton.sh            # run + assert on game_debug.log
#   REBUILD=1 sh tools/run_live_newton.sh  # also refresh newton_capture.blend
#
# What it proves: the story driver runs the whole Newton tour in the live
# engine (init ok, intro -> law1 -> law2 -> law3 -> outro -> stop), every
# playAction/camera/actor call resolves (no unavailable/_fail lines) and,
# with CAPTURE_WATCH set, game_debug.log carries the rigid-body positions.
# Physics *correctness* is asserted deterministically by
# tools/verify_newton_physics.py (headless Bullet bake); on GPU-less boxes
# the player's first rendered frame takes seconds and the engine's catch-up
# bursts make in-game body trajectories unreliable - see HANDOFF.md.
set -e
cd "$(dirname "$0")/.."
U="${UPBGE:?set UPBGE=/path/to/upbge-0.50-linux-x64}"
export DISPLAY="${DISPLAY:-:0}" XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export LIBGL_ALWAYS_SOFTWARE=1

[ -n "$REBUILD" ] && "$U/blender" -b newton_laws.blend \
    -P tools/make_newton_capture.py >/dev/null

run_once() {
    rm -f game_debug.log
    rm -rf capture
    timeout "${LIVE_TIMEOUT:-420}" "$U/blenderplayer" -w 320 180 \
        newton_capture.blend >live_newton.log 2>&1 || true
    grep -q "enter set 'outro'" game_debug.log 2>/dev/null
}

# GPU-less boxes are flaky (the software rasterizer can die mid-run);
# one retry keeps the smoke test honest instead of lucky.
attempt=1
until run_once; do
    echo "attempt $attempt: story incomplete, retrying..."
    attempt=$((attempt + 1))
    [ "$attempt" -gt 2 ] && break
done

fail=0
grep -q "init ok" game_debug.log || { echo "FAIL: no init ok"; fail=1; }
for s in intro law1 law2 law3 outro; do
    grep -q "enter set '$s'" game_debug.log ||
        { echo "FAIL: set $s never entered"; fail=1; }
done
grep -q "finished (stop)" game_debug.log ||
    echo "WARN: story did not reach stop within the run window"
if grep -qE "actor unavailable|_fail" game_debug.log; then
    echo "FAIL: unresolved actors/actions:"; grep -E "actor unavailable|_fail" game_debug.log; fail=1
fi
echo "sets entered:   $(grep -c 'enter set' game_debug.log)"
echo "watch lines:    $(grep -c 'watch' game_debug.log)"
echo "captures:       $(ls capture 2>/dev/null | wc -l)"
echo "last watch:     $(grep 'watch' game_debug.log | tail -1)"
[ "$fail" = 0 ] && echo "LIVE NEWTON SMOKE: PASS" || echo "LIVE NEWTON SMOKE: FAIL"
exit $fail
