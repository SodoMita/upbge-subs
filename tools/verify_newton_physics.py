#!/usr/bin/env python3
"""verify_newton_physics.py - deterministic proof that the Newton rig
performs the three laws, without a display or a rasterizer.

newton_laws.blend carries the demo as UPBGE game physics (obj.game.*).
This script mirrors that rig into a Blender rigid-body world (same
Bullet library, same masses/frictions/restitutions, same kinematic
trigger actions) and bakes it headless in background mode, then asserts
from the baked trajectories:

  law1  the pushed puck keeps a constant velocity on the frictionless
        ice (net force zero after the pusher leaves),
  law2  the same hammer stroke accelerates the 0.5 kg ball far more
        than the 5 kg one (a = F/m),
  law3  the dropped ball falls onto the pad and bounces back up
        (the pad pushes back).

Usage:
    upbge -b newton_laws.blend -P tools/verify_newton_physics.py
Exit code 0 = all three laws observed.
"""
import bpy
import sys

F_START, F_END = 1, 288          # 12 s @ 24 fps, set-local like the game
PUSH_END = 180                   # trigger strokes end (set-local frames)


def vel(track, f0, f1):
    """mean speed over frames f0..f1 from a {frame: Vector} track."""
    ds = 0.0
    n = 0
    for f in range(f0, f1):
        if f in track and f + 1 in track:
            ds += (track[f + 1] - track[f]).length
            n += 1
    return (ds * 24.0 / n) if n else 0.0


def main():
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = F_START, F_END
    if sc.rigidbody_world is None:
        bpy.ops.rigidbody.world_add()
    rbw = sc.rigidbody_world
    rbw.point_cache.frame_start, rbw.point_cache.frame_end = F_START, F_END

    def add(name, mass=None, friction=0.5, rest=0.0, kinematic=False):
        o = bpy.data.objects[name]
        bpy.context.view_layer.objects.active = o
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
        o.select_set(True)
        bpy.ops.rigidbody.object_add()
        rb = o.rigid_body
        rb.type = 'PASSIVE' if (kinematic or mass is None) else 'ACTIVE'
        rb.kinematic = kinematic
        rb.collision_shape = 'CONVEX_HULL'
        if mass is not None:
            rb.mass = mass
        rb.friction = friction
        rb.restitution = rest
        rb.use_deactivation = False
        if kinematic:
            rb.keyframe_insert("kinematic", frame=F_START)
        return rb

    add("Floor", friction=0.6)
    add("IcePad", friction=0.0)
    add("BouncePad", kinematic=True, friction=0.4)
    add("IcePusher", kinematic=True, friction=0.6)
    add("Hammer", kinematic=True, friction=0.4)
    add("HammerBlock", mass=2.0, friction=0.3)
    add("TrapDoor", kinematic=True, friction=0.6)
    add("Puck", mass=1.0, friction=0.0, rest=0.0)
    add("BallLight", mass=0.5, friction=0.1)
    add("BallHeavy", mass=5.0, friction=0.1)
    add("DropBall", mass=1.0, friction=0.3)

    with bpy.context.temp_override(scene=sc):
        bpy.ops.ptcache.bake_all(bake=True)

    tracks = {n: {} for n in ("Puck", "BallLight", "BallHeavy", "DropBall")}
    dg = bpy.context.evaluated_depsgraph_get()
    for f in range(F_START, F_END + 1):
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for n in tracks:
            tracks[n][f] = bpy.data.objects[n].evaluated_get(
                dg).matrix_world.translation.copy()

    ok = True

    # law 1: pushed, then constant velocity (frictionless ice)
    v_push = vel(tracks["Puck"], PUSH_END + 6, PUSH_END + 30)  # f186-210
    v_late = vel(tracks["Puck"], PUSH_END + 60, F_END - 2)  # f240-286
    l1 = v_push > 0.15 and v_late > 0.6 * v_push
    ok &= l1
    print("[NEWTON-PHYS] law1 inertia: push v=%.2f m/s, later v=%.2f m/s "
          "(keeps gliding: %s)" % (v_push, v_late, l1))

    # law 2: same stroke, 10x mass -> much less acceleration
    IMP = 162  # hammer block lands ~f162
    v_l = vel(tracks["BallLight"], IMP + 4, IMP + 28)
    v_h = vel(tracks["BallHeavy"], IMP + 4, IMP + 28)
    l2 = v_l > 2.0 * max(v_h, 1e-6)
    ok &= l2
    print("[NEWTON-PHYS] law2 F=ma: light(0.5kg) v=%.2f m/s vs "
          "heavy(5kg) v=%.2f m/s (light >> heavy: %s)" % (v_l, v_h, l2))

    # law 3: falls, hits the pad, bounces back up
    zs = [tracks["DropBall"][f].z for f in range(F_START, F_END + 1)]
    z0 = zs[0]
    i_land = next(i for i, z in enumerate(zs) if z < 0.6)   # first contact
    zmin = min(zs[max(0, i_land - 2):i_land + 8])
    peak = max(zs[i_land:i_land + 40])
    l3 = (z0 - zmin) > 1.0 and (peak - zmin) > 0.3
    ok &= l3
    print("[NEWTON-PHYS] law3 reaction: drop z %.2f -> %.2f (fell %.2f m), "
          "pad flick rebounds to %.2f (pushed back: %s)"
          % (z0, zmin, z0 - zmin, peak, l3))

    print("[NEWTON-PHYS] %s" % ("ALL THREE LAWS OBSERVED" if ok
                                else "LAWS NOT OBSERVED"))
    sys.exit(0 if ok else 1)


main()
