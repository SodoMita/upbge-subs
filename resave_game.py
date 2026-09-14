"""Refresh demo game setup + self-test the generic game driver path.

Run WITH a UI (logic/property ops need one):
    xvfb-run -a upbge talking_robots.blend -P resave_game.py   (quits itself)
"""
import bpy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw
import story as sb


def log(m):
    print("[GAME] %s" % m, flush=True)


if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
    log("addon registered")
log("addon v%s" % (".".join(str(x) for x in tw.bl_info.get("version", ())),))

# 1. refresh the demo's internal game script
code = open(os.path.join(HERE, "game_subtitles.py"), encoding="utf-8").read()
txt = bpy.data.texts.get("game_subtitles.py")
if txt is None:
    txt = bpy.data.texts.new("game_subtitles.py")
txt.from_string(code)
log("internal game_subtitles.py refreshed (%d bytes)" % len(code))

# 1b. validate the story files (the game reads them live; the property
# holds only the path - property strings are length-capped)
_loaded = sb.load_story_files(os.path.join(HERE, "story.yml"))
if _loaded["errors"]:
    raise SystemExit("story errors:\n- " + "\n- ".join(_loaded["errors"]))
for _w in _loaded["warnings"]:
    log("story warning: " + _w)
_gd = bpy.data.objects.get("GameDirector")
_bp = _gd.game.properties.get("tw_story") if _gd is not None else None
if _bp is None:
    raise SystemExit("GameDirector has no tw_story property (rebuild?)")
_bp.value = "//story.yml"
_story = _loaded["story"]
log("tw_story -> //story.yml (%d sets, %d choices, %d cues)"
    % (len(_story["sets"]), len(_story.get("choices", {})),
       len(_loaded["cues"])))

# 2. ensure String game property "Text" on the subtitle + menu objects
sub = bpy.data.objects.get("Subtitles")
assert sub is not None
prev_active = bpy.context.view_layer.objects.active
try:
    for oname in ("Subtitles", "ChoiceMenu"):
        o = bpy.data.objects.get(oname)
        assert o is not None, oname
        if o.game.properties.get("Text") is None:
            hv, hr = o.hide_viewport, o.hide_render
            o.hide_viewport = False
            o.hide_render = False
            try:
                bpy.context.view_layer.objects.active = o
                r = bpy.ops.object.game_property_new(type='STRING', name='Text')
            finally:
                o.hide_viewport = hv
                o.hide_render = hr
            log("game_property_new %s: %s" % (oname, r))
        q = o.game.properties.get("Text")
        assert q is not None and q.type == 'STRING', oname
        # NB: never ASSIGN q.value here - bpy writes to the "Text" game
        # property corrupt UPBGE 0.50 state (segfault on a later scene op).
        # The game driver overwrites it every tick anyway, so the stored
        # value is irrelevant.
    log("Text game properties ready")
finally:
    try:
        bpy.context.view_layer.objects.active = prev_active
    except Exception:
        pass

# 3. SELF-TEST: generic driver setup on a throwaway object
bpy.ops.object.text_add(location=(0, 0, 0))
tmp = bpy.context.active_object
tmp.name = "TwGameSelfTest"
e1 = tw._create_entry(tmp, 1, "Hello game")
e2 = tw._create_entry(tmp, 30, "Second line")
bpy.context.view_layer.objects.active = tmp
assert bpy.ops.tw.setup_game_logic.poll(), "setup poll failed"
r = bpy.ops.tw.setup_game_logic()
assert r == {'FINISHED'}, r
prop = tmp.game.properties.get("tw_game_data")
data = json.loads(prop.value)
assert len(data["cues"]) == 2 and data["cues"][0][0] == 0.0, data
assert abs(data["cues"][1][0] - 29.0 / 24.0) < 1e-3, data  # (30-1)/24 s
assert tmp.game.sensors.get("TwGameAlways") is not None
assert tmp.game.controllers.get("TwGameDriver") is not None
ctrl = tmp.game.controllers["TwGameDriver"]
assert ctrl.mode == 'MODULE' and ctrl.module == 'tw_game.update', \
    (ctrl.mode, ctrl.module)
assert any(c.name == "TwGameDriver"
           for c in tmp.game.sensors["TwGameAlways"].controllers)
assert bpy.data.texts.get("tw_game.py") is not None
log("self-test: setup ok, cues=%s" % (data["cues"],))
# edit -> the shared export path must refresh the data
e2.text = "Second v2"
assert tw._write_game_data(tmp, bpy.context.scene)
assert "Second v2" in tmp.game.properties["tw_game_data"].value
assert bpy.ops.tw.refresh_game_data.poll()
assert bpy.ops.tw.refresh_game_data() == {'FINISHED'}
log("self-test: refresh ok")
bpy.data.objects.remove(tmp, do_unlink=True)
tdrv = bpy.data.texts.get("tw_game.py")
if tdrv is not None:
    bpy.data.texts.remove(tdrv)
log("self-test: cleaned up")

# 4. demo bricks sanity + save
d = bpy.data.objects.get("GameDirector")
sens = d.game.sensors["Always"]
linked = [c.name for c in sens.controllers]
assert "Dialogue" in linked, linked
ctrl = d.game.controllers["Dialogue"]
assert ctrl.module == "game_subtitles.update", ctrl.module
log("demo bricks ok: Always -> %s" % linked)
scene = bpy.context.scene
scene.frame_set(30)
bpy.context.view_layer.update()
log("frame reset to 30 (baked subtitle objects)")
log("readme embedded at build time (untouched)")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "talking_robots.blend"))
log("saved")
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
