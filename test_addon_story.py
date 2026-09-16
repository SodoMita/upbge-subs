"""Story-tool tests for the add-on (run inside Blender, no display needed):
    blender -b --factory-startup -P test_addon_story.py

Covers the v2 editor features end to end on a throwaway fixture project:
Refresh Sync (sidecar + schema + fake users), the per-set preview (cues,
actions, camera snap, menu, frame range), the rename watch (pending list,
Apply, auto-rewrite) and per-set Bake to Objects.
"""
import bpy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw                      # noqa: E402

if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
if not hasattr(bpy.types.Scene, "tw_story"):
    raise SystemExit("FAIL: scene story props not registered")
print("[ADDONSTORY] addon v%s" % ".".join(str(x) for x in
                                           tw.bl_info["version"]))

FIX = tempfile.mkdtemp(prefix="twfix")
FPS = 24


def log(m):
    print("[ADDONSTORY] %s" % m, flush=True)


def write(name, text):
    with open(os.path.join(FIX, name), "w", encoding="utf-8") as fh:
        fh.write(text)


write("main.srt", "1\n00:00:00,000 --> 00:00:01,000\nACTOR: first line\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\nsecond line here\n\n"
      "3\n00:00:02,500 --> 00:00:03,500\n[CAM Close]\npick something?\n")
write("cuby.srt", "1\n00:00:00,000 --> 00:00:01,000\n[CAM Close]\n"
      "done and dusted\n")
STORY = """start: main
cps: 30
sets:
  main:
    anims:
      - subs: main.srt#1-2
      - camera: Wide
      - action: Actor@main__act
    end: choice pick
  cuby:
    anims:
      - subs: cuby.srt#1-1
      - camera: Close
    end: stop
choices:
  pick:
    prompt: main.srt#3
    options:
      - [1, "Go to cuby", cuby]
"""
write("story.yml", STORY)
# story.py must sit next to the story (the add-on loads the parser from there)
shutil.copy(os.path.join(HERE, "story.py"), os.path.join(FIX, "story.py"))

scene = bpy.context.scene
scene.render.fps = FPS
scene.frame_start = 1
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)

actor = bpy.data.objects.new("Actor", bpy.data.meshes.new("Actor"))
scene.collection.objects.link(actor)
act = bpy.data.actions.new("main__act")
slot = act.slots.new(id_type='OBJECT', name="Actor")
ad = actor.animation_data_create()
ad.action = act
ad.action_slot = slot
for f, x in ((1, 0.0), (49, 2.0)):
    actor.location = (x, 0, 0)
    actor.keyframe_insert("location", frame=f)
for cname, loc, lens in (("Wide", (0, -6, 2), 50.0),
                         ("Close", (1.5, -2, 1), 65.0)):
    bpy.ops.object.camera_add(location=loc)
    c = bpy.context.active_object
    c.name = cname
    c.data.lens = lens
bpy.context.view_layer.update()
scene.camera = bpy.data.objects["Wide"]
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(cam)
cam.location = (0, 0, 0)
cam.rotation_euler = (1.5708, 0, 0)
scene.camera = cam
bpy.ops.object.text_add(location=(0, 0, 0))
sub = bpy.context.active_object
sub.name = "Subtitles"
bpy.ops.object.text_add(location=(0, 0, 0))
menu = bpy.context.active_object
menu.name = "ChoiceMenu"
menu.data.body = ""
scene.tw_story = os.path.join(FIX, "story.yml")

# --- 1) validation -------------------------------------------------------
rep = tw.story_check_impl(scene)
assert rep["ok"], rep
assert rep["errors"] == [] and rep["bindings"] == [], rep
assert rep["sets"] == ["cuby", "main"] and rep["start"] == "main", rep
log("check clean: %s" % rep["sets"])
assert bpy.ops.tw.check_story() == {'FINISHED'}

# --- 2) Refresh Sync: sidecar + schema + fake users ----------------------
rep = tw.refresh_sync_impl(scene)
assert rep["errors"] == [], rep
assert sorted(os.path.basename(p) for p in rep["written"]) == \
    ["story.schema.json", "story.sync.json"], rep["written"]
side = json.load(open(os.path.join(FIX, "story.sync.json")))
assert side["actions"] == {"main__act": [1, 49]}, side
assert rep["fake_users"] == 1 and act.use_fake_user is True, rep
# stamping is aimed at what the story references (2 cams, 1 object, 1 action)
types = sorted(v["type"] for v in side["uids"].values())
assert types == ["action", "camera", "camera", "object"], types
sch = json.load(open(os.path.join(FIX, "story.schema.json")))
assert sch["properties"]["start"]["enum"] == ["cuby", "main"], sch
log("sync ok: ranges=%s uids=%d" % (side["actions"], len(side["uids"])))

# --- 3) preview a set: cues, actions, camera, menu, frame range ----------
ok, msgs = tw.preview_set_impl(scene, "main")
assert ok, msgs
assert [e.frame for e in sorted(sub.tw_entries, key=lambda e: e.frame)] \
    == [1, 25], [(e.frame, e.text) for e in sub.tw_entries]
assert sub.tw_entries[0].text == "ACTOR: first line"
assert int(scene.frame_end) == 49, [m for m in msgs]
assert scene.tw_preview_set == "main"
assert actor.animation_data.action is act
# the menu is state-driven: armed for the set, but only revealed at its tail
assert str(menu.get("_tw_menu_choice", "")) == "> 1: Go to cuby", \
    repr(menu.get("_tw_menu_choice"))
assert menu.data.body == "", "hidden mid-set: " + repr(menu.data.body)
assert tuple(cam.location) == tuple(bpy.data.objects["Wide"].location)
assert cam.data.lens == 50.0
scene.frame_set(25)                       # cue 2 starts here: 0 chars yet
bpy.context.view_layer.update()
assert sub.data.body == "", repr(sub.data.body)
scene.frame_set(27)                       # mid-line: a growing prefix
bpy.context.view_layer.update()
partial = sub.data.body
assert partial and "second line here".startswith(partial) \
    and partial != "second line here", repr(partial)
assert float(sub.tw_cps) == 30.0, sub.tw_cps    # from story cps
scene.frame_set(40)                       # 16 chars at 30cps: done by f38
bpy.context.view_layer.update()
assert sub.data.body == "second line here", repr(sub.data.body)
log("preview main: %d lines, end=%d, live typing f27=%r f40=%r"
    % (len(sub.tw_entries), scene.frame_end, partial, sub.data.body))

ok, msgs = tw.preview_set_impl(scene, "cuby")
assert ok, msgs
assert len(sub.tw_entries) == 1 and int(scene.frame_end) == 25, msgs
assert str(menu.get("_tw_menu_choice", "")) == "", "set ends in stop"
assert menu.data.body == "", repr(menu.data.body)
assert cam.data.lens == 65.0, cam.data.lens       # snapped to Close
log("preview cuby: end=%d lens=%s" % (scene.frame_end, cam.data.lens))

# --- 4) per-set bake (prefix <set>_Line##) --------------------------------
ok, _msgs = tw.preview_set_impl(scene, "main")
bpy.context.view_layer.objects.active = sub
sub.select_set(True)
assert bpy.ops.tw.bake_typewriter.poll()
assert bpy.ops.tw.bake_typewriter() == {'FINISHED'}
baked = sorted(o.name for o in bpy.data.objects
               if o.name.startswith("main_Line"))
assert baked == ["main_Line01", "main_Line02"], baked
b1 = bpy.data.objects["main_Line01"]
assert b1.tw_enabled and len(b1.tw_entries) == 1 and \
    b1.tw_entries[0].text == "ACTOR: first line", b1.name
assert sub.tw_enabled is False
log("bake per set: %s" % baked)

# --- 5) rename watch: pending list + Apply --------------------------------
bpy.data.objects["Actor"].name = "Renamed"
ren = tw.name_watch_scan(scene, force=True)
assert len(ren) == 1 and ren[0]["old"] == "Actor" and ren[0]["new"] == \
    "Renamed", ren
assert len(scene.tw_pending_renames) == 1, list(scene.tw_pending_renames)
item = scene.tw_pending_renames[0]
assert (item.kind, item.old, item.new) == ("object", "Actor", "Renamed")
assert bpy.ops.tw.apply_renames.poll()
assert bpy.ops.tw.apply_renames() == {'FINISHED'}
now = open(os.path.join(FIX, "story.yml")).read()
assert "action: Renamed@main__act" in now, now
assert "Go to cuby" in now, "labels must survive the rewrite"
assert STORY.replace("Actor@", "Renamed@") == now, (now, STORY)
assert len(scene.tw_pending_renames) == 0
rep = tw.story_check_impl(scene)
assert rep["ok"] and rep["bindings"] == [], rep
side = json.load(open(os.path.join(FIX, "story.sync.json")))
assert any(v["name"] == "Renamed" for v in side["uids"].values()), side
log("rename applied + story.yml rewritten (labels intact)")

# --- 6) auto-rewrite toggle ------------------------------------------------
scene.tw_autorewrite = True
bpy.data.objects["Renamed"].name = "Again"
tw.name_watch_scan(scene, force=True)
assert len(scene.tw_pending_renames) == 0
assert "action: Again@main__act" in open(os.path.join(FIX, "story.yml")).read()
log("auto-rewrite ok")

# --- 7) camera rename also rewrites [CAM] lines in the .srt ---------------
bpy.data.objects["Close"].name = "CloseUp"
tw.name_watch_scan(scene, force=True)
yml = open(os.path.join(FIX, "story.yml")).read()
srt = open(os.path.join(FIX, "cuby.srt")).read()
assert "camera: CloseUp" in yml and "camera: Close\n" not in yml, yml
assert "[CAM CloseUp]" in srt, srt
assert "camera: CloseUp" in yml
log("camera rename -> story.yml + [CAM] in cuby.srt")

# --- 8) a broken story is reported, never rewritten -----------------------
scene.tw_autorewrite = False
write("story.yml", yml + "\n  bogus: [\n")
rep = tw.story_check_impl(scene)
assert rep["errors"], rep
ok, msgs = tw.preview_set_impl(scene, "main")
assert not ok and "fix them first" in msgs[0], msgs
applied, written = tw.rewrite_story_refs(scene, [{"type": "object",
                                                  "old": "Again",
                                                  "new": "Nope"}])
assert applied == [] and written == [], (applied, written)
log("broken story: preview refused, rewrite refused")

# --- 9) panel plumbing that only a UI would otherwise exercise ------------
write("story.yml", yml)              # undo section 8's deliberate breakage
rep = tw.story_check_impl(scene)
assert rep["ok"], rep
props = ["tw_story", "tw_preview_set", "tw_autorewrite",
         "tw_pending_renames", "tw_rename_index"]
assert all(hasattr(bpy.types.Scene, p) for p in props), props
items = tw._preview_set_items(None, bpy.context)
assert [i[1] for i in items] == ["cuby", "main (start)"], items
assert tw._SET_PICK["s1"] == "main" and tw._SET_PICK["s0"] == "cuby", \
    tw._SET_PICK
assert sub.tw_enabled is False, "the bake above turned live typing off"
assert bpy.ops.tw.preview_set(set_id="s0") == {'FINISHED'}     # preview cuby
assert scene.tw_preview_set == "cuby"
assert sub.tw_enabled is True, "preview re-enables typing for an unbaked set"
assert len(sub.tw_entries) == 1, len(sub.tw_entries)   # cuby has one cue
_b = bpy.data.objects["main_Line01"]
_b.hide_viewport = _b.hide_render = False          # pretend it is visible
ok, msgs = tw.preview_set_impl(scene, "cuby")
assert _b.hide_render and _b.hide_viewport, "other set's bake must hide"
assert not any(o.hide_render for o in bpy.data.objects
               if o.name.startswith("cuby_Line")), "own bake stays visible"
ok, msgs = tw.preview_set_impl(scene, "main")      # main IS baked
assert sub.tw_enabled is False, "baked set: no double text"
assert not _b.hide_render, "previewed set's bake is shown again"
assert sub.data.body == "", "baked set: the plate stays silent: %r" \
    % sub.data.body
sub.data.body = "STALE"                       # simulate a dirty plate
tw.tw_save_pre()
assert sub.data.body == "", "save_pre must clear a superseded plate"
sub.tw_enabled = True
tw._update_body_impl(sub, scene)              # live typing works again
sub.tw_enabled = False
assert len(sub.tw_entries) == 2, len(sub.tw_entries)   # back to main's cues
log("bake visibility: per set, no stacked text")


# --- 9b) the choice menu is state-driven, not always-on -------------------
ok, _m = tw.preview_set_impl(scene, "main")
assert ok
scene.frame_set(10)
tw._menu_tick(scene)
assert menu.data.body == "" and abs(float(menu.scale[0])) < 1e-6, (
    menu.data.body, tuple(menu.scale))          # hidden mid-set
scene.frame_set(int(scene.frame_end))
bpy.context.view_layer.update()
tw._menu_tick(scene)
assert menu.data.body == "> 1: Go to cuby", repr(menu.data.body)
assert float(menu.scale[0]) > 0.5, tuple(menu.scale)   # revealed at the tail
ok, _m = tw.preview_set_impl(scene, "cuby")       # ends in `stop`: no menu
scene.frame_set(int(scene.frame_end))
tw._menu_tick(scene)
assert menu.data.body == "" and float(menu.scale[0]) < 1e-6, \
    repr(menu.data.body)
log("menu: a set ending in stop never reveals it")
log("menu: hidden mid-set, revealed at the choice tail")

class _FakeLayout:                          # records calls, never draws
    def __init__(self, log=None):
        self.log = log if log is not None else []

    def __getattr__(self, name):
        def _f(*a, **kw):
            self.log.append((name, a, kw))
            return _FakeLayout(self.log)
        return _f

    def __bool__(self):
        return True


fl = _FakeLayout()
tw._wrap_label(fl, "set 'main': subs start 3.0s in (leading silence)", 'INFO')
tw._wrap_label(fl, "x" * 130, 'ERROR')                  # one long word
tw._wrap_label(fl, "", 'INFO')
tw._story_box(fl, bpy.context)                            # the real draw path
drawn = [n for (n, _a, _k) in fl.log]
assert "prop" in drawn and "operator_enum" in drawn and "label" in drawn, drawn
ops = {a[0] for (n, a, _k) in fl.log if n == "operator" and a}
assert {"tw.check_story", "tw.refresh_sync"} <= ops, ops
assert "tw.apply_renames" not in ops, "no renames pending -> no Apply button"
log("panel: enum items %s, wrap+story box drew %d calls"
    % ([i[0] for i in items], len(fl.log)))

# --- 10) the save-time sidecar refresh must not hijack another scene ------
# (saving a scratch .blend that sits in the same folder as someone's story
# used to rewrite that story's sidecar with the scratch scene's actions)
sy = os.path.join(FIX, "story.sync.json")
before = open(sy).read()
_lib, _loaded = tw.load_story(scene)          # names the story uses NOW
_refs, _objs, _cams = tw.story_refs(_lib, _loaded["story"])
renamed = {}
for oname in sorted(_objs):                   # (renamed by sections 5-7)
    o = bpy.data.objects.get(oname)
    if o is not None:
        renamed[oname] = "scratch_" + oname      # story name -> scratch name
        o.name = renamed[oname]
assert renamed, "the story must reference at least one object"
tw.write_sync_files(scene)
assert open(sy).read() == before, "a scene without the story must not write"
log("save guard: scratch scene left story.sync.json alone")
for oldname, newname in renamed.items():
    bpy.data.objects[newname].name = oldname
bpy.context.view_layer.objects.active = actor
actor.location = (9.0, 0, 0)
actor.keyframe_insert("location", frame=80)   # widens main__act to [1, 80]
tw.write_sync_files(scene)
after = json.load(open(sy))["actions"]["main__act"]
assert after == [1, 80], after
log("save guard: the owning scene still refreshes it (%s)" % (after,))
actor.keyframe_clear() if hasattr(actor, "keyframe_clear") else None

# --- 11) the preview drives the render camera exactly like the game -------
# A second, self-contained project: one set, two shots, a cut at 2.5 s.
FIX2 = tempfile.mkdtemp(prefix="twfix2")


def w2(name, text):
    with open(os.path.join(FIX2, name), "w", encoding="utf-8") as fh:
        fh.write(text)


# the earlier sections renamed the story's objects (that is what the rename
# watch is for), so pick the live cameras by type and author the cut with those
cams = [o for o in scene.objects
        if o.type == 'CAMERA' and o is not scene.camera]
assert len(cams) >= 2, [o.name for o in scene.objects if o.type == 'CAMERA']
wide = min(cams, key=lambda o: o.location.y)     # the one further back
close = max(cams, key=lambda o: o.location.y)    # the tighter one
w2("cut.srt", "1\n00:00:00,000 --> 00:00:01,000\nfirst line\n\n"
              "2\n00:00:02,500 --> 00:00:03,500\n[CAM %s]\nafter the cut\n"
              % close.name)
w2("story.yml", "start: cam\ncps: 30\nsets:\n  cam:\n    anims:\n"
                "      - subs: cut.srt#1-2\n      - camera: %s\n"
                "    end: stop\n"
                % wide.name)
shutil.copy(os.path.join(HERE, "story.py"), os.path.join(FIX2, "story.py"))
story1 = scene.tw_story
scene.tw_story = os.path.join(FIX2, "story.yml")
scene.frame_start = 1
scene.tw_preview_camera = True
cam = scene.camera
assert cam.parent is None and wide.parent is None, "fixture assumes no parents"


def pose():
    return (tuple(round(v, 4) for v in cam.matrix_basis.translation),
            round(float(cam.data.lens), 3))


def of(o):
    return (tuple(round(v, 4) for v in o.matrix_world.translation),
            round(float(o.data.lens), 3))


WP, CL = of(wide), of(close)


def blend(k):
    """The eased pose at smoothstep(k), computed without the add-on."""
    kk = k * k * (3.0 - 2.0 * k)
    return (tuple(round(WP[0][i] + (CL[0][i] - WP[0][i]) * kk, 4)
                  for i in range(3)),
            round(WP[1] + (CL[1] - WP[1]) * kk, 3))


def goto(t):
    """Jump like the user does and read the pose the handler produced."""
    scene.frame_set(1 + int(round(t * FPS)))
    bpy.context.view_layer.update()
    return pose()


ok, msgs = tw.preview_set_impl(scene, "cam")
assert ok, msgs
assert int(scene.frame_end) == 85, msgs            # 3.5 s of cues at 24 fps
snap = pose()
assert snap == WP, snap        # snapped to the set's opening shot
assert blend(0.0) == WP and blend(1.0) == CL, (blend(0.0), blend(1.0))
assert goto(0.0) == WP and goto(1.0) == WP, pose()       # still holding Wide
assert goto(2.75) == blend(0.5), (pose(), blend(0.5))    # eased half-way
assert tw.preview_camera_tick(scene) is False, \
    "no write when the camera is already posed"          # idempotent tick
assert goto(3.1) == CL, (pose(), CL)                      # exactly Close
log("camera follow: Wide %s -> eased -> Close %s" % (WP, CL))
# deterministic: the same t always gives the same pose (scrub + render proof)
seen = {}
for t in (0.0, 1.0, 2.75, 3.1):
    seen[t] = goto(t)
for t in (3.1, 2.75, 1.0, 0.0, 2.75, 0.0, 3.1):
    assert goto(t) == seen[t], (t, pose(), seen[t])
log("follow is a pure function of t: 7 replays, 0 differences")
# it really does write when the pose is wrong (no frame change involved)
cam.matrix_basis.translation = (0.0, 0.0, 0.0)
assert tw.preview_camera_tick(scene) is True, "a wrong pose must be fixed"
assert pose() == seen[3.1], (pose(), seen[3.1])   # the frame still shows 3.1
# outside the previewed range the camera is left alone
scene.frame_set(int(scene.frame_end) + 4)
bpy.context.view_layer.update()
assert tw.preview_camera_tick(scene) is False and pose() == seen[3.1], pose()
scene.frame_set(1)
goto(2.75)
# hand-authored camera keys always win (same rule the menu uses)
keep = bpy.data.actions.new("CamHand")
kslot = keep.slots.new(id_type='OBJECT', name=cam.name)
ad = cam.animation_data or cam.animation_data_create()
ad.action = keep
ad.action_slot = kslot
cam.location = (9.0, 9.0, 9.0)
cam.keyframe_insert("location", frame=1)
cam.keyframe_insert("location", frame=85)
bpy.context.view_layer.update()
assert tw.preview_camera_tick(scene) is False, "follow ran over hand keys"
ad.action = None
bpy.data.actions.remove(keep)
# the checkbox hands the camera back
cam.matrix_basis.translation = (0.0, 0.0, 0.0)
scene.tw_preview_camera = False
held = pose()
for t in (0.0, 2.75, 3.1):                          # lens included: nothing at
    assert goto(t) == held, (t, pose(), held)       # all is written when off
scene.tw_preview_camera = True
assert goto(2.75) == blend(0.5), (pose(), blend(0.5))
# the plan is cached (per-frame cost matters) and invalidated on a story edit
p1 = tw.preview_plan(scene)
assert p1 is tw.preview_plan(scene) is not None
os.utime(os.path.join(FIX2, "story.yml"), (os.path.getmtime(
    os.path.join(FIX2, "story.yml")) + 3,) * 2)
assert tw.preview_plan(scene) is not p1, "plan cache ignored the story edit"
# Leave Preview: back to the start set, follow off, nothing deleted
ok, msgs = tw.clear_preview_impl(scene)
assert ok, msgs
assert scene.tw_preview_set == "cam" and scene.tw_preview_camera is False, msgs
cam.matrix_basis.translation = (1.0, 2.0, 3.0)
tw.preview_plan(scene, reload=True)
goto(2.75)
assert pose()[0] == (1.0, 2.0, 3.0), "Leave Preview still drove the camera"
assert len(bpy.data.objects["Subtitles"].tw_entries) == 2, "cues still loaded"
assert hasattr(bpy.ops.tw, "clear_preview"), "the operator is registered"
scene.tw_story = story1
shutil.rmtree(FIX2, ignore_errors=True)
log("preview camera + Leave Preview behave")

# --- 12) slot resolution for shared/renamed objects (Blender 5 slotted) ---
shared = bpy.data.actions.new("shared")
shared.slots.new(id_type='MESH', name="MeshSlot")      # wrong host, first
so = shared.slots.new(id_type='OBJECT', name=actor.name)
ad2 = actor.animation_data
old_action = ad2.action
ad2.action = shared
got = tw._slot_for(shared, actor)
# RNA collections hand out a fresh wrapper per access, so compare with ==
assert got == so and got.identifier == so.identifier, (got, so)
actor.name = "ActorRenamed"
assert tw._slot_for(shared, actor) == so, "a rename must not orphan the slot"
assert len(shared.slots) == 2, [s.identifier for s in shared.slots]
other = bpy.data.objects.new("Other", bpy.data.meshes.new("Other"))
scene.collection.objects.link(other)
ad3 = other.animation_data_create()
ad3.action = shared
assert str(tw._slot_for(shared, other).identifier).startswith("OB"), \
    "never bind a mesh slot to an object"
fresh = bpy.data.actions.new("fresh")
ad3.action = fresh                      # assign the action first, as Blender 5
made = tw._slot_for(fresh, other)       # wants (the slot must belong to it)
assert made is not None and len(fresh.slots) == 1 \
    and made.identifier == "OBOther", [s.identifier for s in fresh.slots]
assert tw._slot_for(fresh, other) == made and len(fresh.slots) == 1, \
    "a second call reuses its slot (no pile-up across rebuilds)"
ad2.action = old_action
actor.name = "Actor"
bpy.data.objects.remove(other, do_unlink=True)
for a in (shared, fresh):
    bpy.data.actions.remove(a)
log("slots: named slot wins, renames keep their slot, foreign hosts skip")

# --- 13) the handler wiring actually calls the follow --------------------
src = open(os.path.join(HERE, "typewriter_subtitles.py"),
           encoding="utf-8").read()
import ast as _ast
_tree = _ast.parse(src)
_wired = set()
for _n in _ast.walk(_tree):
    if isinstance(_n, _ast.FunctionDef) and _n.name in (
            "tw_frame_change", "tw_depsgraph_update", "tw_load_post_story"):
        for _m in _ast.walk(_n):
            if isinstance(_m, _ast.Call) and getattr(_m.func, "id", "") == \
                    "preview_camera_tick":
                _wired.add(_n.name)
assert _wired == {"tw_frame_change", "tw_load_post_story"}, _wired
# deliberately NOT in the depsgraph handler: editing an unrelated object must
# never snap the camera back (that would feel like a stuck view).
for _n in _ast.walk(_tree):
    if isinstance(_n, _ast.FunctionDef) and _n.name == "tw_depsgraph_update":
        assert not any(isinstance(m, _ast.Call)
                       and getattr(m.func, "id", "") == "preview_camera_tick"
                       for m in _ast.walk(_n)), "follow wired twice"
log("camera follow wired into %s" % ", ".join(sorted(_wired)))

for coll in (bpy.data.actions, bpy.data.meshes, bpy.data.curves,
             bpy.data.cameras):
    for x in list(coll):
        if x.users == 0:
            coll.remove(x)
shutil.rmtree(FIX, ignore_errors=True)
log("fixture cleaned")
print("ALL ADDON STORY TESTS PASSED")
