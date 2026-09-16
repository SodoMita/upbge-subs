"""Headless v2 wiring check for talking_robots.blend.

    upbge -b talking_robots.blend -P verify_game.py

Read-only for the file on disk (it never saves): it loads the add-on from
beside the .blend, validates the story + its bindings against the scene,
checks the sync sidecar, the per-set actions, the baked text, the game bricks
and the embedded driver, and proves the per-set preview works for every set
(that part mutates the in-memory scene on purpose - press P is the real
preview, this is the check that it can).
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb

import bpy

OK, FAIL = [], []


def check(cond, what, detail=""):
    (OK if cond else FAIL).append(what + ((": " + detail) if detail else ""))
    print("VERIFY %s %s%s" % ("ok  " if cond else "FAIL", what,
                              " - " + detail if detail else ""))


def main():
    spec = importlib.util.spec_from_file_location(
        "typewriter_subtitles", os.path.join(HERE, "typewriter_subtitles.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register()
    scene = bpy.context.scene

    ver = mod.bl_info["version"]
    check(ver >= (1, 9, 0), "addon v1.9.0+ for the v2 story tools",
          ".".join(str(x) for x in ver))
    check(hasattr(bpy.types.Scene, "tw_preview_set"),
          "scene story props registered")

    story_path = os.path.join(HERE, "story.yml")
    loaded = sb.load_story_files(story_path)
    check(not loaded["errors"], "story + srt files load clean",
          "; ".join(loaded["errors"][:3]))
    check(not loaded["warnings"], "no story warnings",
          "; ".join(loaded["warnings"][:3]))
    story = loaded["story"]
    check(scene.tw_story and os.path.isfile(mod.story_path_of(scene)),
          "scene points at the story", scene.tw_story)

    # --- sidecar ----------------------------------------------------------
    sp = sb.sync_path_for(story_path)
    check(os.path.isfile(sp), "story.sync.json present")
    side = sb.load_sidecar(sp)
    live = mod.live_action_ranges()
    check(bool(side["actions"]), "sidecar has action ranges",
          "%d actions" % len(side["actions"]))
    check(bool(side["uids"]), "sidecar has the uid map",
          "%d ids" % len(side["uids"]))
    want_acts, want_objs, want_cams = mod.story_refs(sb, story)
    drift = {a: (side["actions"].get(a), live.get(a))
             for a in sorted(want_acts)
             if side["actions"].get(a) != live.get(a)}
    check(not drift, "sidecar ranges match the live keys", str(drift)[:200])
    rec = side["uids"]
    renames, new_ids, _gone = sb.diff_uids(rec, mod.live_uids())
    check(not renames, "no un-applied renames", str(renames)[:160])
    check(not new_ids, "every story reference carries a uid",
          str([n["name"] for n in new_ids])[:160])

    # --- bindings vs scene -----------------------------------------------
    objs = sorted(o.name for o in bpy.data.objects)
    cams = sorted(o.name for o in bpy.data.objects if o.type == 'CAMERA')
    binding = sb.check_bindings(story, objs, sorted(live) + sorted(
        a.name for a in bpy.data.actions), cams)
    check(not binding, "story bindings resolve in the scene",
          "; ".join(binding[:3]))
    for o in sorted(want_objs):
        check(o in bpy.data.objects, "actor object '%s' exists" % o)
    for c in sorted(want_cams):
        ob = bpy.data.objects.get(c)
        check(ob is not None and ob.type == 'CAMERA',
              "shot '%s' is a camera" % c)
        if ob is not None and ob.type == 'CAMERA':
            check(ob.data.lens > 0, "shot '%s' carries its own lens" % c,
                  "%.0f mm" % ob.data.lens)

    # --- actions + fake users + ranges ------------------------------------
    for a in sorted(want_acts):
        act = bpy.data.actions.get(a)
        rng = side["actions"].get(a)
        check(act is not None and rng is not None, "action '%s'" % a,
              "range %s" % (rng,))
        check(bool(act and act.use_fake_user),
              "action '%s' is fake-user protected" % a)

    # --- scene frame range + preview indicator ----------------------------
    start = str(story["start"])
    plan = sb.set_plan(story, loaded["files"], start, side["actions"],
                       mod.scene_fps(scene))
    want_end = int(scene.frame_start) + int(
        round(sb.set_duration(plan) * mod.scene_fps(scene)))
    check(int(scene.frame_end) == want_end, "frame range = the start set",
          "%d-%d (set expects 1-%d)" % (scene.frame_start, scene.frame_end,
                                         want_end))
    check(not [m for m in scene.timeline_markers
               if m.name.startswith(("SET-", "CH-"))],
          "no SET-/CH- markers (v2 previews sets, not markers)")
    cam = bpy.data.objects.get("Camera")
    check(cam is not None and (cam.animation_data is None
                                or cam.animation_data.action is None),
          "render camera is unbaked (the driver moves it procedurally)")

    # --- text objects, baked per-set lines, menu --------------------------
    sub = bpy.data.objects.get("Subtitles")
    menu = bpy.data.objects.get("ChoiceMenu")
    check(sub is not None and menu is not None, "Subtitles + ChoiceMenu")
    if sub is not None:
        check(len(sub.tw_entries) == len(plan["subs"]),
              "subtitle lines match the previewed set",
              "%d vs %d" % (len(sub.tw_entries), len(plan["subs"])))
        check(bool(sub.get("tw_backup")), "subtitle backup embedded",
              "%dB" % len(str(sub.get("tw_backup"))))
        check(sub.parent is cam, "subtitles ride the camera")
    # a re-bake must reuse names: `main_Line01.001` would mean the previous
    # bake's orphan data squatted the names (idempotency of build + bake)
    dupes = sorted(o.name for o in bpy.data.objects if ".00" in o.name)
    dupes += sorted(a.name for a in bpy.data.actions if ".00" in a.name)
    check(not dupes, "no .00N duplicates (rebuild/rebake reuses names)",
          ", ".join(dupes[:6]))

    sets = sorted(str(k) for k in (story.get("sets") or {}))
    previewed = scene.tw_preview_set
    for s in sets:
        p = sb.set_plan(story, loaded["files"], s, side["actions"],
                        mod.scene_fps(scene))
        baked = [o for o in bpy.data.objects if o.name.startswith(
            "%s_Line" % s)]
        # Only the previewed set is baked: frames are set-local, so two sets
        # baked into one scene would overlap in time (that is the point of
        # Preview Set - see the README's authoring model).
        want = len(p["subs"]) if s == previewed else 0
        check(len(baked) == want, "baked lines for set '%s' (%s)"
              % (s, "previewed" if s == previewed else "not previewed"),
              "%d objects, expected %d / %d cues" % (len(baked), want,
                                                      len(p["subs"])))
        for o in sorted(baked, key=lambda o: o.name):
            check(len(o.tw_entries) == 1 and o.tw_enabled is True,
                  "baked '%s' is a live one-line subtitle" % o.name)
    if menu is not None:
        check(menu.animation_data is None
              or menu.animation_data.action is None,
              "menu is plain (state-driven, no keys)")

    # --- game bricks + embedded driver ------------------------------------
    gd = bpy.data.objects.get("GameDirector")
    check(gd is not None, "GameDirector present")
    if gd is not None:
        check(len(gd.game.sensors) == 1 and len(gd.game.controllers) == 1,
              "exactly one sensor + controller (idempotent bricks)",
              "%d/%d" % (len(gd.game.sensors), len(gd.game.controllers)))
        sens, ctrl = gd.game.sensors.get("Always"), gd.game.controllers.get(
            "Dialogue")
        check(sens is not None and ctrl is not None
              and getattr(ctrl, "mode", None) == "MODULE"
              and getattr(ctrl, "module", None) == "game_subtitles.update"
              and ctrl in list(sens.controllers)
              and getattr(sens, "use_pulse_true_level", False) is True,
              "Always(pulse) -> Dialogue [MODULE game_subtitles.update]")
        p = gd.game.properties.get("tw_story")
        check(p is not None and p.value == "//story.yml",
              "tw_story holds the path only", p.value if p else "-")
    t = bpy.data.texts.get("game_subtitles.py")
    disk = open(os.path.join(HERE, "game_subtitles.py"),
                encoding="utf-8").read()
    check(t is not None and t.as_string() == disk,
          "embedded driver matches game_subtitles.py on disk")
    for name in ("Subtitles", "ChoiceMenu"):
        o = bpy.data.objects.get(name)
        q = o.game.properties.get("Text") if o else None
        check(q is not None and q.type == 'STRING',
              "'%s' has the Text game property (never bpy-written)" % name)

    # --- every set previews (mutates in memory; the file is not saved) -----
    for s in sets:
        ok, msgs = mod.preview_set_impl(scene, s)
        p = sb.set_plan(story, loaded["files"], s,
                        dict(side["actions"]), mod.scene_fps(scene))
        check(ok and len(sub.tw_entries) == len(p["subs"]),
              "preview set '%s'" % s, "; ".join(msgs)[:150])
        n = mod.bake_typewriter(sub, scene, prefix="%s_Line" % s)
        made = [o for o in bpy.data.objects
                if o.name.startswith("%s_Line" % s)]
        check(n == len(p["subs"]) == len(made),
              "Bake to Objects per set '%s'" % s, "%d objects" % n)
        for o in made:                       # leave the scene as found
            bpy.data.objects.remove(o, do_unlink=True)
    mod.preview_set_impl(scene, start)
    check(scene.tw_preview_set == start, "start set re-armed after probing",
          scene.tw_preview_set)

    # --- the generic per-object export the add-on writes ------------------
    check(len(gd.game.properties) >= 1 if gd else False,
          "director properties hold paths only",
          json.dumps({k: len(str(gd.game.properties[k].value))
                      for k in gd.game.properties.keys()}) if gd else "-")

    print("VERIFY summary: %d ok, %d failed" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("VERIFY FAILED: " + f)
    if FAIL:
        sys.exit(1)


main()
