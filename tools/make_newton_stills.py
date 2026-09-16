#!/usr/bin/env python3
"""make_newton_stills.py - README poster stills for the Newton example.

Bakes the same rigid-body world as tools/verify_newton_physics.py and
renders three workbench stills of the moments that show each law:

  docs/newton_law1.png  f230 - the pusher has left; the puck glides on
                        frictionless ice at constant velocity,
  docs/newton_law2.png  f190 - one hammer-block stroke: the 0.5 kg ball
                        is already far ahead of the 5 kg one,
  docs/newton_law3.png  f140 - the pad has pushed back: the dropped
                        ball is airborne again above the pad.

Usage:
    upbge -b newton_laws.blend -P tools/make_newton_stills.py
Workbench + baked physics: seconds per still, no GPU, no game logic.
"""
import bpy
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = (("Law1", 230, "newton_law1.png"),
         ("Law2", 250, "newton_law2.png"),
         ("Law3", 140, "newton_law3.png"))
# per-shot camera overrides so the evidence is in frame
AIM = {"Law3": ((5.0, -4.6, 1.7), (5.0, 0.0, 0.7))}


def bake():
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 288
    if sc.rigidbody_world is None:
        bpy.ops.rigidbody.world_add()
    rbw = sc.rigidbody_world
    rbw.point_cache.frame_start = 1
    rbw.point_cache.frame_end = 288

    def add(name, mass=None, friction=0.5, kinematic=False):
        o = bpy.data.objects[name]
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.rigidbody.object_add()
        rb = o.rigid_body
        rb.type = 'PASSIVE' if (kinematic or mass is None) else 'ACTIVE'
        rb.kinematic = kinematic
        rb.collision_shape = 'CONVEX_HULL'
        if mass is not None:
            rb.mass = mass
        rb.friction = friction
        rb.use_deactivation = False

    add("Floor", friction=0.6)
    add("IcePad", friction=0.0)
    add("BouncePad", kinematic=True, friction=0.4)
    add("IcePusher", kinematic=True, friction=0.6)
    add("Hammer", kinematic=True, friction=0.4)
    add("HammerBlock", mass=2.0, friction=0.3)
    add("TrapDoor", kinematic=True, friction=0.6)
    add("Puck", mass=1.0, friction=0.0)
    add("BallLight", mass=0.5, friction=0.1)
    add("BallHeavy", mass=5.0, friction=0.1)
    add("DropBall", mass=1.0, friction=0.3)
    with bpy.context.temp_override(scene=sc):
        bpy.ops.ptcache.bake_all(bake=True)


def main():
    bake()
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.render.resolution_x = 640
    sc.render.resolution_y = 360
    docs = os.path.join(HERE, "docs")
    os.makedirs(docs, exist_ok=True)
    from mathutils import Vector
    for cam, frame, name in SHOTS:
        c = bpy.data.objects[cam]
        if cam in AIM:
            loc, tgt = AIM[cam]
            c.location = loc
            d = Vector(tgt) - c.location
            c.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
        sc.camera = c
        sc.frame_set(frame)
        sc.render.filepath = os.path.join(docs, name)
        bpy.ops.render.render(write_still=True)
        print("[NEWTON-STILL] %s @ f%d -> docs/%s" % (cam, frame, name))


main()
