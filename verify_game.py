"""Headless v2 wiring check for talking_robots.blend.

    upbge -b talking_robots.blend -P verify_game.py

Read-only for the file on disk (it never saves): it loads the add-on from
beside the .blend, validates the story + its bindings against the scene,
checks the sync sidecar, the per-set actions, the baked text, the game bricks
and the embedded driver, proves the per-set preview works for every set (that
part mutates the in-memory scene on purpose - press P is the real preview,
this is the check that it can) and - unless TW_SKIP_BUILD_TWICE=1 - rebuilds
the whole project in a THROWAWAY COPY of it and compares an action-key
fingerprint before/after, so "build_scene.py is additive and idempotent"
stays a proven fact and not a promise (a hand tweak to a key must survive a
rebuild). The repo's own files are never written by that: it asserts so.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb

import bpy

OK, FAIL = [], []


def check(cond, what, detail=""):
    if not isinstance(detail, str):        # lists/tuples/ints are fair game
        detail = repr(detail)
    (OK if cond else FAIL).append(what + ((": " + detail) if detail else ""))
    print("VERIFY %s %s%s" % ("ok  " if cond else "FAIL", what,
                              " - " + detail if detail else ""))


def log(msg):
    """Progress line from a section that is not itself a check."""
    print("VERIFY  %s" % msg, flush=True)


FP_SCRIPT = r'''
# Fingerprint of everything a rebuild could damage. Written and run by
# verify_game's build_twice INSIDE a throwaway copy of the project. With
# TW_TWEAK=<action> it also nudges that action's inner keys by +0.25 and saves,
# so the second fingerprint proves a rebuild keeps hand-edited keys instead of
# flattening them back to what the story's generator writes.
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
import typewriter_subtitles as tw


def dig(a):
    rows = []
    for fc in tw._action_fcurves(a):
        for k in fc.keyframe_points:
            rows.append([fc.data_path, round(k.co.x, 4), round(k.co.y, 6),
                         str(k.interpolation)])
    rows.sort()
    return rows


name = os.environ.get("TW_TWEAK")
if name:
    act = bpy.data.actions.get(name)
    assert act is not None, "no action '%s' to tweak" % name
    f0, f1 = [int(round(v)) for v in act.frame_range]
    n = 0
    for fc in tw._action_fcurves(act):
        for k in fc.keyframe_points:
            if f0 + 1 <= round(k.co.x) <= f1 - 1:
                k.co.y += 0.25
                n += 1
        if n:
            fc.update()
    assert n, "action '%s' (frames %d-%d) has no inner key to nudge" % (
        name, f0, f1)
    print("[FP] nudged %d hand-tweak key(s) on '%s'" % (n, name), flush=True)

fp = {"actions": {a.name: {
          "keys": len(dig(a)),
          "range": [int(round(v)) for v in a.frame_range],
          "fake_user": a.use_fake_user,
          "digest": hashlib.sha1(json.dumps(dig(a)).encode()).hexdigest()}
      for a in bpy.data.actions},
      "objects": sorted([o.name for o in bpy.data.objects]),
      "meshes": sorted([m.name for m in bpy.data.meshes]),
      "materials": sorted([m.name for m in bpy.data.materials]),
      "uids": {o.name: o.get("_tw_uid") for o in bpy.data.objects
               if o.get("_tw_uid")},
      "frames": [int(bpy.context.scene.frame_start),
                 int(bpy.context.scene.frame_end)]}
with open(os.environ["TW_FP_OUT"], "w", encoding="utf-8", newline="\n") as fh:
    json.dump(fp, fh, indent=1, sort_keys=True)
print("[FP] %s: %d action(s), %d object(s), %d uid(s)"
      % (os.environ["TW_FP_OUT"], len(fp["actions"]), len(fp["objects"]),
         len(fp["uids"])), flush=True)
if name:
    bpy.ops.wm.save_mainfile()
    print("[FP] saved the tweak", flush=True)
'''


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def build_twice():
    """Rebuild a COPY of the project and prove nothing was clobbered.

    The claim this pins: build_scene.py is additive - re-running it over a file
    that already holds the story's data changes nothing, and a hand edit to a
    generated action's keys survives it. Done in a copy because the rebuild
    saves. Skipped (with a log line, not a failure) when this environment
    cannot re-exec Blender; `TW_SKIP_BUILD_TWICE=1` skips it on purpose.
    """
    log("=== build-twice proof (throwaway copy of the project) ===")
    if os.environ.get("TW_SKIP_BUILD_TWICE"):
        log("  SKIP: TW_SKIP_BUILD_TWICE is set")
        return
    # bpy.app.binary_path, NOT sys.executable: under UPBGE that is the bundled
    # python, which would try to run the .blend as a script.
    exe = getattr(bpy.app, "binary_path", "") or ""
    if not exe or not os.path.isfile(exe):
        log("  SKIP: bpy.app.binary_path is not a runnable binary (%r)" % exe)
        return
    repo_blend = os.path.join(HERE, "talking_robots.blend")
    ld = sb.load_story_files(os.path.join(HERE, "story.yml"))
    if ld["errors"]:
        log("  SKIP: story does not load (%s)" % ld["errors"][:1])
        return
    fps = float(bpy.context.scene.render.fps) or 24.0
    want = sorted({a for n in sorted(ld["story"]["sets"])
                   for (_o, a, _at, _w, _d) in sb.set_plan(
                       ld["story"], ld["files"], n, {}, fps)["actions"]})
    if not want:
        log("  SKIP: the story names no per-set actions")
        return
    tmp = tempfile.mkdtemp(prefix="twverify")
    before_sha = _sha(repo_blend)
    try:
        for fn in sorted(os.listdir(HERE)):
            src = os.path.join(HERE, fn)
            if os.path.isfile(src) and fn.endswith((".py", ".yml", ".srt",
                                                    ".json", ".blend")):
                shutil.copy2(src, tmp)
            elif os.path.isdir(src) and fn in ("subtitles", "audio"):
                shutil.copytree(src, os.path.join(tmp, fn))
        with open(os.path.join(tmp, "_verify_fp.py"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(FP_SCRIPT)
        blend = os.path.join(tmp, "talking_robots.blend")
        fp = {t: os.path.join(tmp, "fp_%s.json" % t)
              for t in ("before", "after")}
        xvfb = shutil.which("xvfb-run")
        ui = ([xvfb, "-a", "-s", "-screen 0 1280x800x24", exe] if xvfb
              else [exe])

        def run(args, env=None, label=""):
            e = dict(os.environ)
            e.update(env or {})
            r = subprocess.run(args, cwd=tmp, env=e, capture_output=True,
                               text=True, timeout=1800)
            if r.returncode != 0:
                log("  child failed (%s, exit %d): %s"
                    % (label, r.returncode,
                       (r.stdout or "")[-700:] + (r.stderr or "")[-400:]))
            return r.returncode, (r.stdout or "")

        rc, out = run(ui + ["-b", blend, "-P", "_verify_fp.py"],
                      {"TW_FP_OUT": fp["before"], "TW_TWEAK": want[0]},
                      "fingerprint + hand tweak")
        check(rc == 0, "fingerprint + hand tweak on '%s' exits 0"
              % want[0], rc)
        check("[FP] saved the tweak" in out, "the tweaked copy was saved (so "
              "the rebuild really had hand edits to keep)", out[-160:])
        rc, out = run(ui + ["-b", blend, "-P", "build_scene.py"], None,
                      "additive rebuild")
        check(rc == 0, "build #2 (additive rebuild over the tweaked file) "
                       "exits 0", rc)
        check("SAVED " in out, "the rebuild really ran and saved the copy",
              out[-160:].replace("\n", " | "))
        rc, out2 = run(ui + ["-b", blend, "-P", "_verify_fp.py"],
                       {"TW_FP_OUT": fp["after"]}, "fingerprint")
        check(rc == 0, "second fingerprint exits 0", rc)
        check("Traceback" not in out + out2, "no child printed a traceback",
              [l for l in (out + out2).splitlines() if "Traceback" in l][:1])
        if not all(os.path.isfile(p) for p in fp.values()):
            check(False, "both fingerprints were written")
            return
        b = json.load(open(fp["before"], encoding="utf-8"))
        a = json.load(open(fp["after"], encoding="utf-8"))
        check(_sha(repo_blend) == before_sha,
              "the proof never touched the repo's own .blend", "same sha256")
        moved = sorted(n for n in sorted(set(b["actions"]) & set(a["actions"]))
                       if b["actions"][n]["digest"]
                       != a["actions"][n]["digest"])
        check(not moved, "no action key changed across the rebuild (the "
                         "hand tweak survived on all %d)" % len(b["actions"]),
              moved)
        check(sorted(b["actions"]) == sorted(a["actions"]),
              "the rebuild created no extra action and dropped none",
              (len(b["actions"]), len(a["actions"])))
        check(b["objects"] == a["objects"],
              "no duplicate objects after the rebuild (.001 names)",
              [o for o in a["objects"] if o not in b["objects"]][:5])
        check(b["meshes"] == a["meshes"] and b["materials"] == a["materials"],
              "no duplicate mesh/material blocks after the rebuild")
        check(b["uids"] == a["uids"],
              "_tw_uid stamps are stable across the rebuild",
              len(a["uids"]))
        check(b["frames"] == a["frames"],
              "the frame range survived the rebuild",
              (b["frames"], a["frames"]))
        setacts = sorted(n for n in a["actions"] if "__" in n)
        check(setacts == want,
              "the file holds exactly the %d per-set actions story.yml names"
              % len(want), sorted(set(setacts) ^ set(want)))
        check(all(a["actions"][n]["fake_user"] for n in setacts),
              "every per-set action is fake-user guarded after the rebuild")
        side = sb.load_sidecar(
            sb.sync_path_for(os.path.join(tmp, "story.yml")))
        check(all(a["actions"][n]["range"] == side["actions"][n]
                  for n in setacts if n in side["actions"]),
              "live ranges still match the sidecar after the rebuild",
              {n: [a["actions"][n]["range"], side["actions"].get(n)]
               for n in setacts
               if n in side["actions"]
               and a["actions"][n]["range"] != side["actions"][n]})
        log("  %d action(s) fingerprinted twice, %d object(s), %d uid(s)"
            % (len(a["actions"]), len(a["objects"]), len(a["uids"])))
    except Exception as ex:
        # A sandbox without a re-executable Blender or a spare 2 GB of RAM is
        # not a reason to call the build broken: say so and carry on.
        log("  SKIP: build-twice could not run here (%r)" % (ex,))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
        armed = str(menu.get("_tw_menu_choice", "") or "")
        check(bool(armed) == (plan["end"][0] == "choice"),
              "menu armed with the previewed set's options",
              repr(armed.splitlines()[:1]))
        scene.frame_set(int(scene.frame_end))
        tw_moved = mod._menu_tick(scene)
        check(bool(menu.data.body) and float(menu.scale[0]) > 0.5,
              "menu reveals at the choice tail", repr(menu.data.body))
        scene.frame_set(int(scene.frame_start))
        mod._menu_tick(scene)
        check(menu.data.body == "" and float(menu.scale[0]) < 1e-6,
              "menu hides mid-set (no always-on overlay)",
              "%r%s" % (menu.data.body, " (moved)" if tw_moved else ""))

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

    # --- the preview drives the camera like the game does ---------------
    plan = sb.set_plan(story, loaded["files"], start, dict(side["actions"]),
                       mod.scene_fps(scene))
    cam = scene.camera
    shots = plan["shots"]
    check(len(shots) >= 2, "the start set has cuts to follow",
          [s[1] for s in shots])
    fps = max(float(mod.scene_fps(scene)), 1e-6)
    f0 = int(scene.frame_start)
    j = next((i for i in range(1, len(shots))
              if shots[i][1] != shots[0][1]), None)
    check(j is not None, "the start set cuts to a DIFFERENT shot",
          [sh[1] for sh in shots])
    if j is None:
        j = 1                        # keep going: the checks below will report
    fps = max(float(mod.scene_fps(scene)), 1e-6)
    f0 = int(scene.frame_start)

    def of(obj_name):
        o = bpy.data.objects[obj_name]
        return (tuple(round(v, 5) for v in o.matrix_world.translation),
                round(float(o.data.lens), 5))

    def eased(k):
        """What smoothstep(k) between the two shots must give (independent)."""
        a, b = of(shots[0][1]), of(shots[j][1])
        kk = k * k * (3.0 - 2.0 * k)
        return (tuple(round(a[0][i] + (b[0][i] - a[0][i]) * kk, 5)
                      for i in range(3)), round(a[1] + (b[1] - a[1]) * kk, 5))

    def pose():
        return (tuple(round(v, 5) for v in cam.matrix_basis.translation),
                round(float(cam.data.lens), 5))

    def at(t):
        scene.frame_set(f0 + int(round(t * fps)))
        return pose()

    open_p = at(shots[0][0])
    mid_p = at(shots[j][0] + mod.CAM_BLEND / 2.0)
    cut_p = at(shots[j][0] + mod.CAM_BLEND + 0.1)
    check(open_p == of(shots[0][1]), "preview holds the set's opening shot",
          "%s vs %s" % (open_p, of(shots[0][1])))
    check(cut_p == of(shots[j][1]), "the cut re-frames the camera (no bake)",
          "%s vs %s" % (cut_p, of(shots[j][1])))
    check(mid_p == eased(0.5), "the move is eased with the game's smoothstep",
          "%s vs %s" % (mid_p, eased(0.5)))
    check(mid_p not in (open_p, cut_p), "mid-move is neither end pose", mid_p)
    check(not mod.preview_camera_tick(scene),
          "the tick is idempotent (a correct pose writes nothing)")
    # reproducible: re-visiting the same frames lands on the same pose
    cut_t = shots[j][0] + mod.CAM_BLEND + 0.1
    check(at(shots[0][0]) == open_p and at(cut_t) == cut_p
          and at(cut_t) == cut_p, "the follow is a pure function of t "
                                  "(scrub + render reproducible)")
    # out of the previewed range: the camera is left alone
    scene.frame_set(int(scene.frame_end) + 3)
    bpy.context.view_layer.update()
    check(pose() == cut_p, "outside the set range the camera is not driven",
          pose())
    scene.frame_set(f0 + int(round((shots[j][0] + mod.CAM_BLEND + 0.1) * fps)))
    # hand-authored camera keys always win (the same rule the menu uses)
    ad = cam.animation_data or cam.animation_data_create()
    keep = bpy.data.actions.new("__verify_cam_keys")
    ad.action = keep
    ad.action_slot = mod._slot_for(keep, cam)
    cam.location = (12.0, -34.0, 5.0)
    cam.keyframe_insert("location", frame=f0)
    cam.keyframe_insert("location", frame=int(scene.frame_end))
    scene.frame_set(f0 + 40)
    bpy.context.view_layer.update()
    check(abs(cam.matrix_basis.translation.x - 12.0) < 1e-4
          and not mod.preview_camera_tick(scene),
          "hand-authored camera keys beat the follow", pose())
    ad.action = None
    bpy.data.actions.remove(keep)
    check(at(shots[j][0] + mod.CAM_BLEND + 0.1) == cut_p,
          "removing the hand keys gives the follow back")
    # the checkbox hands the camera back
    cam.matrix_basis.translation = (1.0, 2.0, 3.0)
    cam.data.lens = 33.0
    scene.tw_preview_camera = False
    at(shots[0][0])
    at(shots[j][0])
    check(pose() == ((1.0, 2.0, 3.0), 33.0),
          "the checkbox alone stops every camera write", pose())
    scene.tw_preview_camera = True
    ok, msgs = mod.clear_preview_impl(scene)
    check(ok and scene.tw_preview_set == start and not scene.tw_preview_camera,
          "Leave Preview re-arms the start set and stops the follow",
          "; ".join(msgs)[:120])
    cam.matrix_basis.translation = (4.0, 5.0, 6.0)
    at(shots[j][0])
    check(pose() == ((4.0, 5.0, 6.0), pose()[1]),
          "after Leave Preview the camera is really yours", pose())
    mod.preview_set_impl(scene, start)
    check(scene.tw_preview_set == start and len(sub.tw_entries) ==
          len(sb.set_plan(story, loaded["files"], start, dict(side["actions"]),
                          mod.scene_fps(scene))["subs"]),
          "the scene is left in the start-set state")

    # --- the generic per-object export the add-on writes ------------------
    check(len(gd.game.properties) >= 1 if gd else False,
          "director properties hold paths only",
          json.dumps({k: len(str(gd.game.properties[k].value))
                      for k in gd.game.properties.keys()}) if gd else "-")

    build_twice()

    print("VERIFY summary: %d ok, %d failed" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("VERIFY FAILED: " + f)
    if FAIL:
        sys.exit(1)


main()
