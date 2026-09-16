#!/usr/bin/env python3
"""Make a logic-only capture blend for GPU-less live game smoke runs.

Strips every mesh/light from ``talking_robots.blend`` (robots, stage) but
keeps exactly what the story driver needs to run a whole story live:

- ``GameDirector`` (bricks + ``tw_story`` + ``CAPTURE``),
- ``Camera`` + the staged shot cameras (the procedural camera moves),
- ``Subtitles`` / ``ChoiceMenu`` text objects (live typing proof),
- every per-set action datablock is NOT needed (actors are gone; the
  driver latches "actor unavailable" and the set still advances - that
  latch is itself the proof the playAction path was exercised).

EEVEE is reduced to 1 sample / no shadows / no raytracing so llvmpipe
frames stay cheap.  The result renders an almost empty scene, which keeps
the software rasterizer out of the memory/fence regime that stalls the
full scene on a 2-core box, while ``game_debug.log`` records the complete
live story flow: init, ticks, set enters, choices, stop.

Usage (needs a display for logic bricks, like build_scene.py):
    xvfb-run -a upbge talking_robots.blend -P tools/make_logic_blend.py
Writes talking_robots_logic.blend next to the repo root blend.
"""
import bpy
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Cameras/text/empties survive; meshes/lights go.
KILL_TYPES = {"MESH", "LIGHT"}


def main():
    killed = 0
    for obj in list(bpy.data.objects):
        if obj.type in KILL_TYPES:
            bpy.data.objects.remove(obj, do_unlink=True)
            killed += 1
    sc = bpy.context.scene
    ee = sc.eevee
    for attr, val in (("taa_render_samples", 1), ("taa_samples", 1),
                      ("use_shadows", False), ("use_raytracing", False)):
        if hasattr(ee, attr):
            setattr(ee, attr, val)
    d = bpy.data.objects.get("GameDirector")
    assert d is not None, "GameDirector missing"
    d["CAPTURE"] = 1
    out = os.path.join(HERE, "talking_robots_logic.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("[LOGIC-BLEND] killed %d objects -> %s" % (killed, out))
    bpy.ops.wm.quit_blender()


main()
