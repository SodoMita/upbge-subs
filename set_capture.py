"""Set the CAPTURE flag on GameDirector and save a capture variant.

Usage:
    upbge -b talking_robots.blend -P set_capture.py
    upbge -b newton/newton.blend    -P set_capture.py   # any scene with a GameDirector
(Saves <name>_capture.blend next to the opened file.)

The flag must be a *game* (SCA) property, not a custom (id) property:
in-game, UPBGE 0.50 does not expose the .blend's custom properties on
game objects at all (verified: every one raises KeyError in a probe
scene), while game properties are visible - the driver itself reads
tw_story that way. (A custom d["CAPTURE"] = 1 silently disappears in
the game, so capture mode never armed and the run could never
auto-quit.)
"""
import bpy
import os

HERE = os.path.dirname(os.path.abspath(__file__))
scene_file = bpy.data.filepath or os.path.join(HERE, "talking_robots.blend")
d = bpy.data.objects.get("GameDirector")
assert d is not None, "GameDirector not found"
prop = d.game.properties.get("CAPTURE")
if prop is None:
    saved = (d.hide_viewport, d.hide_render)
    d.hide_viewport = d.hide_render = False
    try:
        bpy.context.view_layer.objects.active = d
        bpy.ops.object.game_property_new(type='STRING', name='CAPTURE')
    finally:
        d.hide_viewport, d.hide_render = saved
    prop = d.game.properties.get("CAPTURE")
assert prop is not None and prop.type == 'STRING', "CAPTURE prop missing"
prop.value = "1"  # String game property: safe to write (Text props are not)
stem = os.path.splitext(os.path.basename(scene_file))[0]
out = os.path.join(os.path.dirname(scene_file) or HERE,
                   stem + "_capture.blend")
bpy.ops.wm.save_as_mainfile(filepath=out)
print("[CAPTURE] game property set ->", out)
