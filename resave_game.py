#!/usr/bin/env python3
"""Refresh the demo's game setup + self-test the generic driver path (v2).

v2 differences from v1:
- the story is a flow map of SETS, so "how many cues" became per-set plans
  (v1 read `loaded["cues"]`, which no longer exists);
- story.sync.json + story.schema.json must be present, current, and cover
  every action range the game divides by fps to know when a set ends;
- set actions must be fake-user guarded: an action is only assigned while its
  set is previewed, so between previews it has no real users and Recursive
  Purge would eat it, taking the animation with it;
- bricks and properties are create-if-missing and never rewritten, so this
  script is safe to re-run (it reuses build_scene.py's own helpers - that
  module's main() is __name__-guarded, so importing it does not rebuild).

Run WITH a UI and the .blend as a command-line ARGUMENT:
    xvfb-run -a upbge talking_robots.blend -P resave_game.py   (quits itself)
Both halves matter: logic-brick / game-property ops misbehave in `blender -b`,
and bpy.ops.wm.open_mainfile() inside a UI session crashes UPBGE 0.50 at
teardown (probed), so the file has to come in on the command line.

Gotcha respected throughout: the `Text` game property is CREATE-ONLY from bpy.
Writing `prop.value` on it corrupts UPBGE 0.50 state and segfaults on a later
scene op. Creating and reading it is fine, and the game driver rewrites those
values every tick through the KX path, which is safe.
"""
import bpy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw      # noqa: E402
import story as sb                     # noqa: E402
import build_scene as bs               # noqa: E402  (import-safe: no rebuild)

BLEND = os.path.join(HERE, "talking_robots.blend")
STORY = os.path.join(HERE, "story.yml")
DRIVER = "game_subtitles.py"
FAILS = []


def log(m):
    print("[GAME] %s" % m, flush=True)


def check(cond, label, extra=""):
    if cond:
        log("  ok   " + label)
    else:
        log("  FAIL %s%s" % (label, (" | " + str(extra)) if extra else ""))
        FAILS.append(label)
    return bool(cond)


# --------------------------------------------------------------------------
log("=== add-on + story ===")
if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
    log("add-on registered")
ver = ".".join(str(x) for x in tw.bl_info.get("version", ()))
check(ver == "1.9.0", "add-on is v1.9.0", ver)

loaded = sb.load_story_files(STORY)
if loaded["errors"]:
    raise SystemExit("story.yml has errors - fix them before resaving:\n- "
                     + "\n- ".join(loaded["errors"]))
for w in loaded["warnings"]:
    log("  story warning: " + w)
story, files = loaded["story"], loaded["files"]
scene = bpy.context.scene
fps = tw.scene_fps(scene)
sets = story.get("sets") or {}
start = story.get("start")
check(start in sets, "story 'start' names a set", start)

# per-set plans need the action ranges, which come from the sidecar - so they
# are built in the "sync sidecar" step below, not here.
log("story: %d set(s) (%s), %d choice(s), start '%s', %d srt file(s)"
    % (len(sets), ", ".join(sorted(sets)), len(story.get("choices") or {}),
       start, len(files)))

# --------------------------------------------------------------------------
log("=== embedded game driver ===")
code = open(os.path.join(HERE, DRIVER), encoding="utf-8").read()
txt = bpy.data.texts.get(DRIVER)
if txt is None:
    txt = bpy.data.texts.new(DRIVER)
    txt.from_string(code)
    log("  created the %s text block (%d bytes)" % (DRIVER, len(code)))
elif txt.as_string() != code:
    txt.from_string(code)
    log("  re-embedded %s (it had drifted from the file)" % DRIVER)
else:
    log("  %s already current (%d bytes)" % (DRIVER, len(code)))
check(bpy.data.texts.get(DRIVER) is not None, "%s is embedded" % DRIVER)

# --------------------------------------------------------------------------
log("=== bricks + properties (create-if-missing, never rewritten) ===")
gd = bpy.data.objects.get("GameDirector")
had_gd = gd is not None
check(bs.build_game(), "GameDirector bricks + properties ready")
gd = bpy.data.objects.get("GameDirector")
if check(gd is not None, "GameDirector exists"):
    sens = gd.game.sensors.get(bs.GAME_SENSOR)
    ctrl = gd.game.controllers.get(bs.GAME_CTRL)
    check(sens is not None, "Always sensor present")
    check(ctrl is not None, "Python controller present")
    if ctrl is not None:
        check(ctrl.mode == 'MODULE' and ctrl.module == bs.GAME_MODULE,
              "controller is MODULE %s" % bs.GAME_MODULE,
              (ctrl.mode, ctrl.module))
    if sens is not None and ctrl is not None:
        check(any(c.name == ctrl.name for c in sens.controllers),
              "Always -> Dialogue linked")
        try:
            check(bool(sens.use_pulse_true_level),
                  "Always pulses every tick (the driver is a tick loop)")
        except Exception:
            pass
    prop = gd.game.properties.get(bs.STORY_PROP)
    check(prop is not None and prop.type == 'STRING',
          "%s is a STRING property" % bs.STORY_PROP)
    if prop is not None:
        # only path-ish values fit: game property strings are length-capped,
        # so JSON never goes in here (the game reads the files live)
        check(prop.value == bs.STORY_VALUE,
              "%s == %s (a path, never JSON)" % (bs.STORY_PROP, bs.STORY_VALUE),
              repr(prop.value))
        check(len(prop.value) < 60, "property value is short (length cap)",
              len(prop.value))
    log("  (%s)" % ("reused the existing GameDirector" if had_gd
                    else "created GameDirector from scratch"))

for oname, pname in bs.CREATE_ONLY_PROPS:
    o = bpy.data.objects.get(oname)
    if not check(o is not None, "%s exists" % oname):
        continue
    p = o.game.properties.get(pname)
    check(p is not None and p.type == 'STRING',
          "%s.%s exists as STRING (created, never value-written)"
          % (oname, pname))
    check(o.type == 'FONT' and o.data is not None,
          "%s is a text object (the driver writes it via KX_FontObject)"
          % oname)

# --------------------------------------------------------------------------
log("=== sync sidecar + editor schema ===")
sync_path = sb.sync_path_for(STORY)
schema_path = sb.schema_path_for(STORY)
side = sb.load_sidecar(sync_path)
check(os.path.isfile(sync_path), "story.sync.json exists", sync_path)
check(os.path.isfile(schema_path), "story.schema.json exists (editor completion)")
check(bool(side.get("actions")), "sidecar carries action ranges")
check(bool(side.get("uids")), "sidecar carries the rename map (uids)")

refs = tw.story_refs(sb, loaded)
old_uids = side.get("uids") or {}
by_rec_obj, by_rec_act = tw._resolve_by_recorded(refs, old_uids)
live = tw.action_ranges(scene, refs, old_uids)
missing = sorted(set(refs["actions"]) - set(live))
drift = {a: (list(side["actions"].get(a, [])), list(live[a]))
         for a in sorted(live)
         if list(side.get("actions", {}).get(a, [])) != list(live[a])}
if missing or drift:
    log("  sidecar is stale (missing=%s drift=%s) - refreshing"
        % (missing, drift))
    ok, msgs, _info = tw.refresh_sync_impl(scene)
    for m in msgs:
        log("  " + m)
    check(ok, "Refresh Sync succeeded")
    side = sb.load_sidecar(sync_path)
    live = tw.action_ranges(scene, refs, side.get("uids") or {})
    drift = {a: (list(side["actions"].get(a, [])), list(live[a]))
             for a in sorted(live)
             if list(side.get("actions", {}).get(a, [])) != list(live[a])}
check(not missing, "every action story.yml names exists in the file", missing)
check(not drift, "sidecar ranges match the live actions", drift)
for set_name, want in sorted(bs.WANT_RANGES.items()):
    got = {n: list(r) for n, r in sorted(side.get("actions", {}).items())
           if n.startswith(set_name + "__")}
    check(bool(got) and all(r == [1, want] for r in got.values()),
          "set '%s' actions all span [1, %d]" % (set_name, want), got)

n_guard = tw.guard_fake_users(refs, side.get("uids") or {})
if n_guard:
    log("  guarded %d action(s) with a fake user" % n_guard)

errs, warns, binds, summary = tw.validate_impl(scene)
log("  validate: " + summary)
for e in errs:
    log("    ERROR: " + e)
for b in binds:
    log("    BINDING: " + b)
for w in warns:
    log("    warning: " + w)
check(not errs, "Validate reports no story errors", errs)
check(not binds, "Validate reports no binding errors", binds)

# --------------------------------------------------------------------------
log("=== per-set plans (what the game will play) ===")
ranges = dict(side.get("actions") or {})
plans = {n: sb.set_plan(story, files, n, ranges, fps) for n in sorted(sets)}
for name in sorted(plans):
    plan = plans[name]
    dur = sb.set_duration(plan)
    f1 = 1 + int(round(dur * fps))
    kind, target = plan["end"]
    log("  set '%s': %d action(s), %d cue(s), %d shot(s), %d audio(s), "
        "blocks %.2fs -> frames 1-%d, end: %s %s"
        % (name, len(plan["actions"]), len(plan["subs"]), len(plan["shots"]),
           len(plan["audios"]), dur, f1, kind, target or ""))
    for (oname, aname, at, wait, adur) in plan["actions"]:
        obj = tw.find_object(scene, oname, by_rec_obj)
        check(obj is not None, "%s: object '%s' found" % (name, oname))
        act = tw.find_action(aname, by_rec_act)
        if not check(act is not None, "%s: action '%s' found" % (name, aname)):
            continue
        check(act.use_fake_user, "'%s' is fake-user guarded" % aname)
        check(len(act.slots) > 0,
              "'%s' has an action slot (a slotless action silently does not "
              "evaluate)" % aname)
        got = [int(round(v)) for v in act.frame_range]
        check(got == list(ranges.get(aname, got)),
              "'%s' frame range %s matches the sidecar" % (aname, got),
              ranges.get(aname))
        check(adur is not None and adur > 0.0,
              "'%s' has a duration the game can block on" % aname, adur)
    seen = set()
    for (t, shot) in plan["shots"]:      # the opening shot + [CAM] cues can
        if shot in seen:                 # repeat; check each camera once
            continue
        seen.add(shot)
        check(tw.find_object(scene, shot, by_rec_obj) is not None,
              "%s: shot camera '%s' exists (t=%.1fs)" % (name, shot, t))
    for (path, t) in plan["audios"]:
        log("    audio '%s' at t=%.1fs (fire-and-forget, never blocks)"
            % (path, t))
    if kind == "choice":
        opts = ((story.get("choices") or {}).get(target) or {}).get("options")
        check(bool(opts), "choice '%s' has options" % target)
        for o in (opts or []):
            check(len(o) == 3 and o[2] in sets,
                  "option %s -> set '%s' exists" % (o[0], o[2]), o)
    elif kind == "goto":
        check(target in sets, "goto target '%s' exists" % target)

# --------------------------------------------------------------------------
log("=== baked subtitles (the file must play with no add-on) ===")
baked = [o for o in scene.objects if o.get(tw._BAKE_TAG) == start]
check(len(baked) > 0, "start set '%s' is baked to %s_Line## objects"
      % (start, start), len(baked))
sub = tw.role_object(scene, tw._ROLE_SUBS, ("Subtitles",))
check(sub is not None and sub.type == 'FONT', "subtitle object found by role")
if sub is not None:
    check(len(baked) == len(sub.tw_entries),
          "bake count matches the loaded cues (%d entries)"
          % len(sub.tw_entries), len(baked))
    check(sub.get("_tw_role") == "subs", "subtitle object is tagged by role")
foreign = sorted(o.name for o in scene.objects
                 if o.get(tw._BAKE_TAG) not in (None, start))
check(not foreign, "no stale bakes from other sets (they would double up)",
      foreign)
hidden = [o.name for o in baked if o.hide_render]
check(not hidden, "every baked line renders (hide_render would hide it)",
      hidden)
menu = tw.role_object(scene, tw._ROLE_MENU, ("ChoiceMenu",))
if check(menu is not None, "choice menu found by role"):
    check(not menu.hide_render,
          "menu is NOT hide_render (UPBGE skips those at conversion)")

# --------------------------------------------------------------------------
log("=== self-test: the add-on's GENERIC driver path (tw_game.py) ===")
bpy.ops.object.text_add(location=(0, 0, 0))
tmp = bpy.context.active_object
tmp.name = "TwGameSelfTest"
e1 = tw._create_entry(tmp, 1, "Hello game")
e2 = tw._create_entry(tmp, 30, "Second line")
prev_active = bpy.context.view_layer.objects.active
bpy.context.view_layer.objects.active = tmp
try:
    check(bool(bpy.ops.tw.setup_game_logic.poll()),
          "setup_game_logic poll passes on a tagged text object")
    r = bpy.ops.tw.setup_game_logic()
    check(r == {'FINISHED'}, "setup_game_logic ran", r)
    prop = tmp.game.properties.get("tw_game_data")
    data = json.loads(prop.value) if prop is not None else {}
    check(len(data.get("cues", [])) == 2 and data["cues"][0][0] == 0.0,
          "generic export: 2 cues, first at t=0", data.get("cues"))
    check(abs(data["cues"][1][0] - 29.0 / fps) < 1e-3,
          "generic export: cue 2 at (30-1)/fps seconds", data["cues"][1])
    check(tmp.game.sensors.get("TwGameAlways") is not None,
          "generic sensor TwGameAlways")
    ctrl = tmp.game.controllers.get("TwGameDriver")
    check(ctrl is not None and ctrl.mode == 'MODULE'
          and ctrl.module == "tw_game.update",
          "generic controller is MODULE tw_game.update",
          (ctrl.mode, ctrl.module) if ctrl is not None else None)
    if ctrl is not None:
        check(any(c.name == "TwGameDriver"
                  for c in tmp.game.sensors["TwGameAlways"].controllers),
              "generic sensor -> controller linked")
    check(bpy.data.texts.get("tw_game.py") is not None,
          "generic driver tw_game.py embedded")
    e2.text = "Second v2"
    check(tw._write_game_data(tmp, scene), "editing an entry re-exports")
    check("Second v2" in tmp.game.properties["tw_game_data"].value,
          "re-exported data carries the edit")
    check(bool(bpy.ops.tw.refresh_game_data.poll())
          and bpy.ops.tw.refresh_game_data() == {'FINISHED'},
          "refresh_game_data operator works")
finally:
    bpy.data.objects.remove(tmp, do_unlink=True)
    tdrv = bpy.data.texts.get("tw_game.py")
    if tdrv is not None:
        bpy.data.texts.remove(tdrv)
    try:
        bpy.context.view_layer.objects.active = prev_active
    except Exception:
        pass
log("  self-test object + driver text removed")
check(bpy.data.objects.get("TwGameSelfTest") is None,
      "no self-test leftovers in the scene")

# --------------------------------------------------------------------------
log("=== save ===")
before = sb.load_sidecar(sync_path)
bpy.ops.wm.save_as_mainfile(filepath=BLEND)
after = sb.load_sidecar(sync_path)
check(before == after,
      "save_post refreshed the sidecars and found them already in sync")
log("saved %s" % BLEND)
log("preview state left untouched: set '%s', frames %d-%d, frame %d"
    % (scene.tw_preview_set or "(none)", scene.frame_start, scene.frame_end,
       scene.frame_current))

if FAILS:
    log("RESULT: %d CHECK(S) FAILED" % len(FAILS))
    for f in FAILS:
        log("  - " + f)
    sys.stdout.flush()
    os._exit(1)        # everything is saved; Blender would exit 0 regardless
log("RESULT: ALL GAME CHECKS PASSED")
sys.stdout.flush()
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
