"""Refresh the demo's game setup for v2 + self-test the generic driver path.

Run WITH a display (logic/property ops need one; the add-on's save handler
also refreshes story.sync.json on the way out):
    xvfb-run -a upbge talking_robots.blend -P resave_game.py   (quits itself)

Reads the story the way the game does, so a story that cannot load never gets
saved into the .blend. Never deletes keys or actions, and never writes a
`Text` game property (see the add-on's notes: bpy writes to Text props
corrupt UPBGE 0.50 state and segfault on a later scene op).
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
ver = tw.bl_info.get("version", ())
log("addon v%s" % ".".join(str(x) for x in ver))
if ver < (1, 9, 0):
    raise SystemExit("resave needs addon 1.9.0+ (story preview/sync tools)")
scene = bpy.context.scene
if not hasattr(bpy.types.Scene, "tw_preview_set"):
    raise SystemExit("scene story props missing - register the addon first")
scene.tw_story = "//story.yml"

# 1. refresh the demo's internal game script (the .blend must ship the driver)
code = open(os.path.join(HERE, "game_subtitles.py"), encoding="utf-8").read()
txt = bpy.data.texts.get("game_subtitles.py")
if txt is None:
    txt = bpy.data.texts.new("game_subtitles.py")
txt.from_string(code)
log("internal game_subtitles.py refreshed (%d bytes)" % len(code))

# 2. story: validate, then bind the scene to it (path only - game property
#    strings are length-capped, and the driver reads the files live)
loaded = sb.load_story_files(os.path.join(HERE, "story.yml"))
if loaded["errors"]:
    raise SystemExit("story errors:\n- " + "\n- ".join(loaded["errors"]))
for w in loaded["warnings"]:
    log("story warning: " + w)
story = loaded["story"]
objs = sorted(o.name for o in bpy.data.objects)
cams = sorted(o.name for o in bpy.data.objects if o.type == 'CAMERA')
binding = sb.check_bindings(story, objs, [a.name for a in bpy.data.actions],
                            cams)
if binding:
    raise SystemExit("story does not match the scene:\n- "
                     + "\n- ".join(binding))
log("story ok: %d sets, %d choices, %d cues in %d files"
    % (len(story["sets"]), len(story.get("choices") or {}),
       sum(len(f["cues"]) for f in loaded["files"].values()),
       len(loaded["files"])))

gd = bpy.data.objects.get("GameDirector")
if gd is None:
    raise SystemExit("no GameDirector in the scene (run build_scene.py)")
prop = gd.game.properties.get("tw_story")
if prop is None:
    raise SystemExit("GameDirector has no tw_story property (rebuild?)")
prop.value = "//story.yml"          # String prop: safe (Text props are not)
log("tw_story -> %s" % prop.value)

# 3. game props on the text objects (create only, never assign values)
prev_active = bpy.context.view_layer.objects.active
try:
    for oname in ("Subtitles", "ChoiceMenu"):
        o = bpy.data.objects.get(oname)
        assert o is not None, oname
        if o.game.properties.get("Text") is None:
            hv, hr = o.hide_viewport, o.hide_render
            o.hide_viewport = o.hide_render = False
            try:
                bpy.context.view_layer.objects.active = o
                r = bpy.ops.object.game_property_new(type='STRING',
                                                      name='Text')
            finally:
                o.hide_viewport, o.hide_render = hv, hr
            log("game_property_new %s.Text: %s" % (oname, r))
        q = o.game.properties.get("Text")
        assert q is not None and q.type == 'STRING', oname
    log("Text game properties ready (values left alone - see the notes)")
finally:
    try:
        bpy.context.view_layer.objects.active = prev_active
    except Exception:
        pass

# 4. per-set actions: fake users + the sidecar the game needs
rep = tw.refresh_sync_impl(scene)
log("refresh sync: %s (%d ranges, %d new uids)"
    % (", ".join(rep.get("written", [])), rep.get("ranges", 0),
       rep.get("uids_new", 0)))
if rep.get("errors"):
    raise SystemExit("sync errors:\n- " + "\n- ".join(rep["errors"]))
missing = rep.get("missing_ranges") or []
if missing:
    raise SystemExit("story actions without keys: %s" % ", ".join(missing))
sub = bpy.data.objects.get("Subtitles")
start = str(story["start"])
if sub is not None:
    side = sb.load_sidecar(sb.sync_path_for(os.path.join(HERE, "story.yml")))
    plan = sb.set_plan(story, loaded["files"], start, side["actions"],
                       tw.scene_fps(scene))
    if len(sub.tw_entries) != len(plan["subs"]):
        log("NOTE: '%s' carries %d lines, the '%s' set has %d - press "
            "Preview %s in the Subtitles panel"
            % (sub.name, len(sub.tw_entries), start, len(plan["subs"]),
               start))
    else:
        log("subtitle lines match the '%s' set (%d)" % (start, len(plan["subs"])))

# 5. SELF-TEST the generic (non-story) driver path the addon exports
bpy.ops.object.text_add(location=(0, 0, 0))
tmp = bpy.context.active_object
tmp.name = "TwGameSelfTest"
tw._create_entry(tmp, 1, "Hello game")
tw._create_entry(tmp, 30, "Second line")
bpy.context.view_layer.objects.active = tmp
assert bpy.ops.tw.setup_game_logic.poll(), "setup poll failed"
assert bpy.ops.tw.setup_game_logic() == {'FINISHED'}
data = json.loads(tmp.game.properties["tw_game_data"].value)
assert len(data["cues"]) == 2 and data["cues"][0][0] == 0.0, data
assert abs(data["cues"][1][0] - 29.0 / 24.0) < 1e-3, data
assert tmp.game.sensors.get("TwGameAlways") is not None
ctrl = tmp.game.controllers.get("TwGameDriver")
assert ctrl is not None and ctrl.mode == 'MODULE' \
    and ctrl.module == 'tw_game.update', ctrl
assert bpy.data.texts.get("tw_game.py") is not None
tmp.tw_entries[1].text = "Second v2"
assert tw._write_game_data(tmp, scene)
assert "Second v2" in tmp.game.properties["tw_game_data"].value
assert bpy.ops.tw.refresh_game_data() == {'FINISHED'}
log("self-test: generic Add Game Logic path ok (export + refresh)")
bpy.data.objects.remove(tmp, do_unlink=True)
tdrv = bpy.data.texts.get("tw_game.py")
if tdrv is not None:
    bpy.data.texts.remove(tdrv)

# 6. demo bricks + save
sens = gd.game.sensors["Always"]
linked = [c.name for c in sens.controllers]
assert "Dialogue" in linked, linked
assert sens.controllers["Dialogue"].module == "game_subtitles.update"
assert getattr(sens, "use_pulse_true_level", False) is True
log("demo bricks ok: Always(pulse) -> %s" % linked)
scene.frame_set(min(30, int(scene.frame_end)))
bpy.context.view_layer.update()
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE,
                                                   "talking_robots.blend"))
log("saved talking_robots.blend")
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
