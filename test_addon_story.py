"""Headless test for the add-on's story-v2 editor tools (runs inside Blender).

    upbge -b --factory-startup -P test_addon_story.py

Works on a TEMP COPY of the project so the repo's story.yml / story.sync.json
are never touched. Covers: Refresh Sync (sidecar + schema), Validate,
Preview Set for every set (frame range / action binding / cues / camera snap /
menu), the rename watcher (detect -> targeted YAML rewrite -> sidecar),
per-set Bake, and the fake-user purge guard.
"""
import bpy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw      # noqa: E402
import story as sb                     # noqa: E402

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("  ok   %s" % label, flush=True)
    else:
        print("  FAIL %s %s" % (label, extra), flush=True)
        FAILS.append(label)


def eq(got, want, label):
    check(got == want, label, "(got %r, want %r)" % (got, want))


# ---------------------------------------------------------------- fixture ---
TMP = tempfile.mkdtemp(prefix="twstory")
for fn in ("story.py", "story.yml", "dialogue.srt", "cuby.srt", "sphero.srt"):
    shutil.copy(os.path.join(HERE, fn), os.path.join(TMP, fn))
STORY = os.path.join(TMP, "story.yml")
SYNC = os.path.join(TMP, "story.sync.json")
SCHEMA = os.path.join(TMP, "story.schema.json")

if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
eq(".".join(str(x) for x in tw.bl_info["version"]), "1.9.0", "add-on is v1.9.0")

scene = bpy.context.scene
scene.render.fps = 24
scene.frame_start = 1
scene.frame_end = 250
scene.tw_story_path = STORY

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)

# expected per-set action ranges (the HANDOFF pins these exactly)
WANT_RANGES = {
    "main__cuby": [1, 361], "main__sphero": [1, 361],
    "main__cubymouth": [1, 361], "main__spheromouth": [1, 361],
    "main__cubyarm": [1, 361], "main__spheroarm": [1, 361],
    "cuby__cuby": [1, 91], "cuby__cubymouth": [1, 91], "cuby__cubyarm": [1, 91],
    "sphero__sphero": [1, 133], "sphero__spheromouth": [1, 133],
    "sphero__spheroarm": [1, 133],
}
ACTORS = {
    "CubyRoot": ["main__cuby", "cuby__cuby"],
    "SpheroRoot": ["main__sphero", "sphero__sphero"],
    "CubyMouth": ["main__cubymouth", "cuby__cubymouth"],
    "SpheroMouth": ["main__spheromouth", "sphero__spheromouth"],
    "CubyArmR": ["main__cubyarm", "cuby__cubyarm"],
    "SpheroArmR": ["main__spheroarm", "sphero__spheroarm"],
}
SHOTS = {"Wide": (0.0, -7.3, 3.35), "WideEnd": (0.0, -6.7, 3.05),
         "Cuby": (-1.6, -4.2, 2.2), "Sphero": (1.6, -4.2, 2.2)}
SHOT_LENS = {"Wide": 50, "WideEnd": 50, "Cuby": 55, "Sphero": 45}


def link(o):
    scene.collection.objects.link(o)
    return o


def new_action(obj, name, f0, f1):
    """Author a fresh local-timed action on obj (Blender 5 slot-correct)."""
    ad = obj.animation_data
    if ad is None:
        ad = obj.animation_data_create()
    ad.action = None                       # force a brand-new action + slot
    obj.location = (0.0, 0.0, 0.0)
    obj.keyframe_insert("location", frame=f0)
    obj.location = (0.0, 0.0, 1.0)
    obj.keyframe_insert("location", frame=f1)
    act = ad.action
    act.name = name
    return act


print("=== fixture scene ===", flush=True)
for name, acts in ACTORS.items():
    o = link(bpy.data.objects.new(name, None))
    o.empty_display_type = 'PLAIN_AXES'
    for a in acts:
        new_action(o, a, *WANT_RANGES[a])
for name, loc in SHOTS.items():
    cam = bpy.data.cameras.new(name)
    cam.lens = SHOT_LENS[name]
    o = link(bpy.data.objects.new(name, cam))
    o.location = loc
    o.rotation_euler = (1.1, 0.0, 0.0)
main_cam = link(bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera")))
main_cam.location = (9.9, 9.9, 9.9)
main_cam.data.lens = 35
main_cam["_tw_role"] = "camera"
scene.camera = main_cam

sub = link(bpy.data.objects.new("Subtitles", bpy.data.curves.new(
    "Subtitles", 'FONT')))
sub["_tw_role"] = "subs"
sub.tw_enabled = True
menu = link(bpy.data.objects.new("ChoiceMenu", bpy.data.curves.new(
    "ChoiceMenu", 'FONT')))
menu["_tw_role"] = "menu"
eq(len(bpy.data.actions), 12, "12 set actions authored")

# ------------------------------------------------------------ Refresh Sync --
print("=== Refresh Sync ===", flush=True)
ok, msgs, info = tw.refresh_sync_impl(scene)
check(ok, "refresh_sync_impl ok", str(msgs))
check(os.path.isfile(SYNC), "story.sync.json written")
check(os.path.isfile(SCHEMA), "story.schema.json written")
side = json.load(open(SYNC, encoding="utf-8"))
eq(side["actions"], WANT_RANGES, "sidecar action ranges match the pinned ones")
_refs = tw.story_refs(sb, sb.load_story_files(STORY))
eq(len(side["uids"]),
   len(_refs["actions"]) + len(_refs["objects"]) + len(_refs["cameras"]),
   "uids cover every referenced id (12 actions + 6 actors + 3 shots)")
eq(sorted(_refs["cameras"]), ["Cuby", "Sphero", "Wide"],
   "refs: cameras named by story.yml/[CAM]")
check(all(u.startswith("tw-") for u in side["uids"]), "uids look like stamps")
eq(side["uids"][tw._uid_of(bpy.data.objects["CubyRoot"])]["name"], "CubyRoot",
   "uid record names the object")
eq(side["uids"][tw._uid_of(bpy.data.actions["main__cuby"])]["type"], "ACTION",
   "action uids are typed ACTION")
check(all(a.use_fake_user for a in bpy.data.actions),
      "fake-user guard on every set action")
schema = json.load(open(SCHEMA, encoding="utf-8"))
eq(schema["properties"]["start"]["enum"], ["cuby", "main", "sphero"],
   "schema start enum")
eq(sorted(schema["properties"]["sets"]["patternProperties"]["^.+$"]
          ["properties"]["anims"]["items"]["anyOf"][1]["properties"]
          ["camera"]["enum"]),
   sorted(list(SHOTS) + ["Camera"]),
   "schema camera enum lists every camera in the scene")
ok2, msgs2, _i2 = tw.refresh_sync_impl(scene)
check(any("unchanged" in m for m in msgs2), "second refresh is a no-op",
      str(msgs2))

# ---------------------------------------------------------------- Validate --
print("=== Validate ===", flush=True)
errs, warns, binds, summary = tw.validate_impl(scene)
eq(errs, [], "no story errors")
eq(binds, [], "no binding errors")
eq(warns, [], "no warnings on a synced project")
check("3 set(s)" in summary and "2 choice(s)" in summary, "summary counts",
      summary)

# ------------------------------------------------------------- Preview Set --
print("=== Preview Set: main ===", flush=True)
ok, msg, info = tw.preview_set_impl(scene, "main")
check(ok, "preview main ok", msg)
eq((scene.frame_start, scene.frame_end), (1, 361), "main frame range 1-361")
eq(info["cues"], 8, "8 subtitle cues loaded")
eq(len([e for e in sub.tw_entries if e.text]), 8, "8 text entries")
eq(bpy.context.scene.objects["CubyRoot"].animation_data.action.name,
   "main__cuby", "CubyRoot plays main__cuby")
eq(bpy.context.scene.objects["SpheroArmR"].animation_data.action.name,
   "main__spheroarm", "SpheroArmR plays main__spheroarm")
slot = bpy.data.objects["CubyRoot"].animation_data.action_slot
check(slot is not None, "action_slot bound (else it silently won't play)",
      str(slot))
eq(main_cam.data.lens, 50.0, "camera lens snapped to Wide")
eq(tuple(round(v, 3) for v in main_cam.location), SHOTS["Wide"],
   "camera pose snapped to Wide")
eq(sub.tw_cps, 30.0, "cps taken from story.yml")
# v2: the menu has no keys, and the game only shows it once every blocking
# anim has finished (set_duration) - so a blocking set parks it at rest.
eq(menu.data.body, "", "menu parked: 'pick' appears at the end of the set")
eq(tuple(menu.scale), (0.0, 0.0, 0.0), "parked menu rests at scale 0")
check("menu parked" in msg and "choice 'pick'" in msg,
      "preview says the menu is parked and when it appears", msg)
eq(scene.tw_preview_set, "main", "tw_preview_set indicator")
eq(scene.tw_preview_range, "1-361", "tw_preview_range indicator")
# the set's own animation must actually evaluate (slot correctness proof)
scene.frame_set(361)
ev = bpy.data.objects["CubyRoot"].evaluated_get(
    bpy.context.evaluated_depsgraph_get())
eq(round(ev.location.z, 3), 1.0, "main__cuby evaluates at its last frame")

print("=== Preview Set: cuby (other sets must detach) ===", flush=True)
ok, msg, info = tw.preview_set_impl(scene, "cuby")
check(ok, "preview cuby ok", msg)
eq((scene.frame_start, scene.frame_end), (1, 91), "cuby frame range 1-91")
eq(info["cues"], 2, "2 subtitle cues")
eq(bpy.data.objects["CubyRoot"].animation_data.action.name, "cuby__cuby",
   "CubyRoot switched to cuby__cuby")
check(bpy.data.objects["SpheroRoot"].animation_data.action is None,
      "SpheroRoot detached (not in this set)")
eq(main_cam.data.lens, 55.0, "lens snapped to Cuby shot")
eq(tuple(menu.scale), (0.0, 0.0, 0.0), "cuby's menu parked too (blocking set)")
check("menu parked" in msg and "pick2" in msg,
      "cuby preview reports the parked pick2 menu", msg)

print("=== Preview Set: sphero (end: stop) ===", flush=True)
ok, msg, info = tw.preview_set_impl(scene, "sphero")
check(ok, "preview sphero ok", msg)
eq((scene.frame_start, scene.frame_end), (1, 133), "sphero frame range 1-133")
eq(info["cues"], 3, "3 subtitle cues")
eq(menu.data.body, "", "menu emptied for a stop ending")
eq(tuple(menu.scale), (0.0, 0.0, 0.0), "menu hidden by scale (never hide_render)")
check(not menu.hide_render, "menu is NOT hide_render (UPBGE would skip it)")

print("=== Preview Set: error paths ===", flush=True)
ok, msg, _i = tw.preview_set_impl(scene, "nosuchset")
check(not ok and "no set 'nosuchset'" in msg, "unknown set is rejected", msg)

# A set with no blocking anim at all gates on its choice at t=0, so THAT menu
# must be posed visible (with the real prompt/option text) for hand-placing.
print("=== Preview Set: choice gating at t=0 shows the menu ===", flush=True)
with open(STORY, encoding="utf-8") as fh:
    _orig_story = fh.read()          # read fully BEFORE any write (truncation)
_instant = _orig_story.replace(
    "choices:\n",
    "  instant:\n    anims:\n      - camera: Wide\n    end: choice pick\n"
    "choices:\n", 1)
check(_instant != _orig_story, "fixture: 'instant' set injected")
with open(STORY, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(_instant)
try:
    ok, msg, info = tw.preview_set_impl(scene, "instant")
    check(ok, "preview instant ok", msg)
    eq(info["plan"]["dur"], 0.0, "no blocking anim -> blocking end 0.0")
    eq((scene.frame_start, scene.frame_end), (1, 2),
       "a t=0 set still spans at least 2 frames")
    check("Who gets the last word?" in menu.data.body,
          "menu shows the prompt", repr(menu.data.body))
    check("> 1: Ask Cuby about cubes" in menu.data.body,
          "menu shows option 1 with the cursor")
    check("  2: Ask Sphero about spheres" in menu.data.body,
          "menu shows option 2")
    eq(tuple(menu.scale), (1.0, 1.0, 1.0),
       "menu visible when the choice gates at t=0")
    check("menu: choice 'pick'" in msg, "preview reports the visible menu", msg)
finally:
    with open(STORY, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_orig_story)
ok, msg, _i = tw.preview_set_impl(scene, "sphero")   # restore prior state
check(ok, "story.yml restored: sphero previews again", msg)
eq(menu.data.body, "", "menu back at rest after restoring the story")

# ------------------------------------------------------------ rename watch --
print("=== rename watcher: object ===", flush=True)
bpy.data.objects["CubyRoot"].name = "CubyBoss"
found = tw.rename_diff_quick(scene)
eq(len(found), 1, "one rename detected")
eq((found[0]["old"], found[0]["new"]), ("CubyRoot", "CubyBoss"),
   "detected CubyRoot -> CubyBoss")
n = tw.sync_pending_list(scene, found)
eq(n, 1, "pending list filled")
eq(len(scene.tw_renames), 1, "scene.tw_renames has 1 row")
# Refresh Sync must NOT promote the new name (story.yml still says CubyRoot)
tw.refresh_sync_impl(scene)
side = json.load(open(SYNC, encoding="utf-8"))
uid = tw._uid_of(bpy.data.objects["CubyBoss"])
eq(side["uids"][uid]["name"], "CubyRoot",
   "sidecar keeps the story-facing name while a rename is pending")
eq(list(side["actions"].keys()).count("main__cuby"), 1,
   "action ranges still keyed by story name")
yaml_before = open(STORY, encoding="utf-8").read()
count, msgs = tw.apply_renames_impl(scene)
eq(count, 2, "exactly 2 tokens rewritten (CubyRoot@ in main + cuby)")
yaml_after = open(STORY, encoding="utf-8").read()
check("CubyRoot@" not in yaml_after, "no stale CubyRoot@ tokens left")
eq(yaml_after.count("CubyBoss@"), 2, "two CubyBoss@ tokens")
eq(yaml_before.count("#"), yaml_after.count("#"),
   "comment count preserved (targeted rewrite, not a re-serialise)")
check("# yaml-language-server=$schema=./story.schema.json" in yaml_after,
      "header comment survived")
check("SpheroRoot@main__sphero" in yaml_after, "untouched refs are byte-identical")
eq(len(scene.tw_renames), 0, "pending list cleared by Apply")
side = json.load(open(SYNC, encoding="utf-8"))
eq(side["uids"][tw._uid_of(bpy.data.objects["CubyBoss"])]["name"], "CubyBoss",
   "sidecar promoted after Apply")
errs, warns, binds, summary = tw.validate_impl(scene)
eq(binds, [], "bindings clean after the rename rewrite")
eq([w for w in warns if "rename" in w], [], "no rename warning left")
ok, msg, info = tw.preview_set_impl(scene, "main")
check(ok, "preview still works after the rename", msg)
eq(bpy.data.objects["CubyBoss"].animation_data.action.name, "main__cuby",
   "renamed object still binds its action")

print("=== rename watcher: action ===", flush=True)
bpy.data.actions["main__cubymouth"].name = "main__jaw"
found = tw.rename_diff_quick(scene)
eq([(d["old"], d["new"], d["kind"]) for d in found],
   [("main__cubymouth", "main__jaw", "ACTION")], "action rename detected")
tw.sync_pending_list(scene, found)
count, msgs = tw.apply_renames_impl(scene)
check(count == 1, "one action token rewritten", str(msgs))
check("CubyMouth@main__jaw" in open(STORY, encoding="utf-8").read(),
      "story.yml now says CubyMouth@main__jaw")
errs, warns, binds, summary = tw.validate_impl(scene)
eq(binds, [], "bindings clean after the action rename")
side = json.load(open(SYNC, encoding="utf-8"))
eq(side["actions"]["main__jaw"], [1, 361],
   "range re-keyed to the new action name")
check("main__cubymouth" not in side["actions"], "old action key gone")

print("=== forget renames (hand-fixed story.yml) ===", flush=True)
bpy.data.objects["SpheroRoot"].name = "SpheroBall"
tw.sync_pending_list(scene, tw.rename_diff_quick(scene))
eq(len(scene.tw_renames), 1, "one pending rename")
txt = open(STORY, encoding="utf-8").read().replace("SpheroRoot@", "SpheroBall@")
open(STORY, "w", encoding="utf-8").write(txt)
tw.invalidate_story_cache()
n = tw.forget_renames_impl(scene)
eq(n, 1, "forget cleared the row")
eq(open(STORY, encoding="utf-8").read(), txt, "Forget left story.yml alone")
side = json.load(open(SYNC, encoding="utf-8"))
eq(side["uids"][tw._uid_of(bpy.data.objects["SpheroBall"])]["name"],
   "SpheroBall", "sidecar re-stamped from the scene")
errs, warns, binds, summary = tw.validate_impl(scene)
eq(binds, [], "bindings clean after Forget")

print("=== validator catches a broken reference ===", flush=True)
bad = open(STORY, encoding="utf-8").read().replace(
    "CubyMouth@main__jaw", "Ghost@main__jaw")
open(STORY, "w", encoding="utf-8").write(bad)
tw.invalidate_story_cache()
errs, warns, binds, summary = tw.validate_impl(scene)
check(any("Ghost" in b for b in binds), "binding error names the missing object",
      str(binds))
tw._fill_report(scene, errs, warns, binds, summary)
eq(len(scene.tw_report_bindings), len(binds), "report collection filled")
# NB: read into a variable FIRST - open(path, "w") truncates before the
# argument expression is evaluated, which would empty story.yml
_restored = open(STORY, encoding="utf-8").read().replace("Ghost@",
                                                          "CubyMouth@")
open(STORY, "w", encoding="utf-8").write(_restored)
tw.invalidate_story_cache()
errs, warns, binds, summary = tw.validate_impl(scene)
eq(errs, [], "story.yml restored intact (no parse errors)")
eq(binds, [], "bindings clean again")

# ------------------------------------------------------------------- bake ----
print("=== Bake Set to Objects ===", flush=True)
ok, msg, info = tw.preview_set_impl(scene, "main")
check(ok, "preview main before baking", msg)
bpy.context.view_layer.objects.active = sub
r = bpy.ops.tw.bake_set()
eq(r, {'FINISHED'}, "bake_set operator finished")
baked = sorted(o.name for o in scene.objects if o.get(tw._BAKE_TAG))
eq(len(baked), 9, "9 baked objects (8 cues + trailing clear)")
check(baked[0].startswith("main_Line"), "bake prefix is <set>_Line##",
      str(baked[:3]))
eq(baked[0], "main_Line01", "first baked object is main_Line01")
b0 = scene.objects["main_Line01"]
check(b0.type == 'FONT' and len(b0.tw_entries) == 1,
      "baked object is a live one-line typewriter")
_full = [e.text for e in sub.tw_entries if e.text][0]
eq(b0.tw_entries[0].text, _full, "baked object carries the full cue line")
scene.frame_set(1 + 12)          # mid-cue: the live typing must be partial
tw.update_object(b0, scene)
check(0 < len(b0.data.body) <= len(_full), "baked object types live",
      repr(b0.data.body))
tw.tw_save_pre()                 # the save handler restores FULL text
eq(b0.data.body, _full,
   "save_pre restores the full cue text (renders with no add-on)")
check(sub is not None and sub.name not in baked, "source object never removed")
ok, msg, info = tw.preview_set_impl(scene, "cuby")
check(ok, "preview cuby before re-baking", msg)
eq(scene.tw_preview_set, "cuby", "indicator follows the preview")
bpy.context.view_layer.objects.active = sub
eq(bpy.ops.tw.bake_set(), {'FINISHED'}, "second bake_set finished")
baked2 = sorted(o.name for o in scene.objects if o.get(tw._BAKE_TAG))
check(not any(b.startswith("main_Line") for b in baked2),
      "the previous set's bake was replaced (sets are local-timed)", str(baked2))
check(all(b.startswith("cuby_Line") for b in baked2), "only cuby_Line## left",
      str(baked2))
n, _m = tw.clear_preview_impl(scene)
eq(scene.tw_preview_set, "", "clear preview resets the indicator")

# ------------------------------------------------------------- operators ----
print("=== operators (registered + poll) ===", flush=True)
for op in ("preview_set", "jump_to_set", "clear_preview", "refresh_sync",
           "validate_story", "apply_renames", "forget_renames", "bake_set"):
    check(hasattr(bpy.ops.tw, op), "bpy.ops.tw.%s exists" % op)
check(not hasattr(bpy.types, "TW_OT_jump_to_marker"),
      "v1 marker operator class is gone (repurposed to tw.jump_to_set)")
eq(bpy.ops.tw.refresh_sync(), {'FINISHED'}, "refresh_sync operator")
eq(bpy.ops.tw.validate_story(), {'FINISHED'}, "validate_story operator")
eq(bpy.ops.tw.preview_set(set_name="sphero"), {'FINISHED'},
   "preview_set operator with an explicit set")
eq((scene.frame_start, scene.frame_end), (1, 133), "operator set the range")
eq(bpy.ops.tw.jump_to_set(set_name="main"), {'FINISHED'},
   "jump_to_set operator (repurposed)")
eq((scene.frame_start, scene.frame_end), (1, 361), "jump_to_set previewed main")

# ------------------------------------------------- save handler integration --
print("=== save_post refreshes the sidecars ===", flush=True)
os.remove(SYNC)
os.remove(SCHEMA)
blend = os.path.join(TMP, "t.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
check(os.path.isfile(SYNC), "save wrote story.sync.json")
check(os.path.isfile(SCHEMA), "save wrote story.schema.json")
side = json.load(open(SYNC, encoding="utf-8"))
check("main__cuby" in side["actions"], "saved sidecar has the ranges")

print("=== unregister/re-register is clean ===", flush=True)
tw.unregister()
check(not hasattr(bpy.types.Scene, "tw_preview_set"), "scene props removed")
check(not hasattr(bpy.types.Object, "tw_entries"), "object props removed")
tw.register()
check(hasattr(bpy.types.Scene, "tw_preview_set"), "scene props restored")
check(hasattr(bpy.types.Object, "tw_entries"), "object props restored")

shutil.rmtree(TMP, ignore_errors=True)
print("=== RESULT ===", flush=True)
if FAILS:
    print("ADDON STORY TEST FAILURES (%d): %s" % (len(FAILS), FAILS),
          flush=True)
else:
    print("ALL ADDON STORY TESTS PASSED", flush=True)
bpy.ops.wm.quit_blender()
sys.exit(1 if FAILS else 0)
