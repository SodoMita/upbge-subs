#!/usr/bin/env python3
"""Make newton_capture.blend - the live-physics proof variant.

The shipped newton_laws.blend narrates at story pace (triggers fire at
4-7 s).  On a GPU-less box the software rasterizer manages only a handful
of rendered frames per run, so this variant, used ONLY by the live smoke
harness (tools/run_live_game.sh), compresses the demonstration:

  - triggers stroke at ~0.3 s instead of 4-7 s (same strokes, re-timed),
  - CAPTURE=1 (screenshots + auto-quit; the driver raises the engine's
    per-frame tick clamp so the story clock never lags physics time),
  - CAPTURE_TICRATE=10: on GPU-less boxes the player's catch-up bursts
    send run-away bodies to |z| in the kilometres and the rasterizer
    dies mid-law2; a 10 Hz logic clock keeps every trajectory bounded
    so the smoke run reaches the outro. Physics at 60 Hz (correct
    speeds) is what tools/verify_newton_physics.py bakes and asserts.
  - CAPTURE_WATCH lists the rigid bodies; the driver then logs their
    world positions every 10th tick into game_debug.log - that log is
    the empirical proof that Bullet performed the law (the puck keeps
    sliding after the pusher, the light ball outruns the heavy one, the
    dropped ball falls and bounces).

Usage:
    upbge -b newton_laws.blend -P tools/make_newton_capture.py
"""
import bpy
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (action, {old frame: new frame}) - hold, stroke start, stroke end, hold
RETIME = {
    "law1__pusher": {168: 7, 180: 19},
    "law2__hammer": {148: 7, 160: 19},
    "law3__door": {96: 7, 104: 15},
}
WATCH = "Puck,BallLight,BallHeavy,DropBall"


def retime(act_name, moves):
    act = bpy.data.actions.get(act_name)
    if act is None:
        print("[NEWTON-CAP] missing action", act_name)
        return 0
    n = 0
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in bag.fcurves:
                    for kp in fc.keyframe_points:
                        for old, new in moves.items():
                            if abs(kp.co.x - old) < 0.5:
                                kp.co.x = float(new)
                                n += 1
                    fc.update()
    print("[NEWTON-CAP] %s: retimed %d keys" % (act_name, n))
    return n


def main():
    total = 0
    for act_name, moves in RETIME.items():
        total += retime(act_name, moves)
    assert total >= 6, "retime moved too few keys (%d)" % total
    d = bpy.data.objects["GameDirector"]
    for prop, val in (("CAPTURE", 1), ("CAPTURE_WATCH", WATCH),
                      ("CAPTURE_STRIDE", 6), ("CAPTURE_TICRATE", 10)):
        if d.game.properties.get(prop) is None:
            prev = bpy.context.view_layer.objects.active
            bpy.context.view_layer.objects.active = d
            bpy.ops.object.game_property_new(
                type='STRING' if isinstance(val, str) else 'INT',
                name=prop)
            bpy.context.view_layer.objects.active = prev
        d.game.properties[prop].value = val
    out = os.path.join(HERE, "newton_capture.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("[NEWTON-CAP] ->", out)


main()
