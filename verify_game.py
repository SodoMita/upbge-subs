#!/usr/bin/env python3
"""Headless verification of the v2 demo blend.

    blender -b talking_robots.blend --python verify_game.py

Read-only: it never saves the blend it was given. It does switch the in-memory
preview between sets (that is how it proves every set is playable), and the
build-twice test runs in a THROWAWAY COPY of the project under a temp dir, so
the real talking_robots.blend is never rebuilt by this script.

What it verifies:
  1. add-on is v1.9.0 and story.yml loads with no errors AND no warnings;
  2. the sidecars exist and are current: story.sync.json action ranges match
     the live actions and the pinned per-set spans, uids cover every reference;
  3. bindings vs the scene: every object / action / camera / shot / choice
     target story.yml names resolves (through the uid map, so a rename that
     has not been applied yet still resolves);
  4. Preview Set works for EVERY set: frame range, action assignment (and
     detachment of other sets' actions), cue count, opening shot + lens;
  5. animation actually evaluates - a slotless action silently does nothing,
     so the proof is a depsgraph read, not "the action exists";
  6. v1 leftovers are gone: no timeline markers, no Camera_anim bake, no
     ChoiceMenu_anim, no SubLine## bakes, no tw_branches property;
  7. bricks: Always(pulse) -> Python MODULE game_subtitles.update, linked;
     tw_story is a short path (never JSON); Text props exist and are STRING;
  8. the start set is baked to <set>_Line## objects that render with no
     add-on at all (parented to the camera, one entry each, text matches);
  9. build-twice key survival: build a temp copy twice with a hand-nudged
     keyframe in between and require an identical action-key fingerprint.

Exits non-zero if any check fails (Blender itself would exit 0 regardless).
Set TW_SKIP_BUILD_TWICE=1 to skip step 9 (it needs xvfb-run + ~30s).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb                     # noqa: E402
import build_scene as bs               # noqa: E402  (import-safe: no rebuild)

import bpy                             # noqa: E402

BLEND = os.path.join(HERE, "talking_robots.blend")
STORY = os.path.join(HERE, "story.yml")
FAILS = []
CHECKS = [0]


def log(m):
    print("[VERIFY] %s" % m, flush=True)


def check(cond, label, extra=""):
    CHECKS[0] += 1
    if cond:
        log("  ok   " + label)
    else:
        log("  FAIL %s%s" % (label, (" | " + str(extra)) if extra else ""))
        FAILS.append(label)
    return bool(cond)


def load_addon():
    """Register the add-on from the file next to the blend (importlib, so a
    stale module in sys.modules can never mask the real one)."""
    spec = importlib.util.spec_from_file_location(
        "typewriter_subtitles",
        os.path.join(HERE, "typewriter_subtitles.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(bpy.types.Object, "tw_entries"):
        mod.register()
    return mod


def key_digest(tw, act):
    """Every keyframe of an action, sorted - Blender 5 layered walk, because
    `action.fcurves` is the empty legacy API and raises here."""
    rows = []
    for fc in tw._action_fcurves(act):
        for k in fc.keyframe_points:
            rows.append([fc.data_path, round(k.co.x, 4), round(k.co.y, 6),
                         round(k.handle_left.x, 4), round(k.handle_right.x, 4),
                         str(k.interpolation), fc.array_index])
    rows.sort()
    return rows


def main():
    tw = load_addon()
    scene = bpy.context.scene
    fps = tw.scene_fps(scene)

    # ------------------------------------------------------------- 1. story -
    log("=== add-on + story.yml ===")
    check(tw.bl_info["version"] == (1, 9, 0), "add-on is v1.9.0",
          tw.bl_info["version"])
    loaded = sb.load_story_files(STORY)
    check(loaded["errors"] == [], "story.yml has no errors", loaded["errors"])
    check(loaded["warnings"] == [], "story.yml has no warnings",
          loaded["warnings"])
    if loaded["errors"]:
        return finish(scene)
    story, files = loaded["story"], loaded["files"]
    sets = story.get("sets") or {}
    start = story.get("start")
    check(start in sets, "start names a set", start)
    check(sorted(sets) == ["cuby", "main", "sphero"],
          "the demo has the three documented sets", sorted(sets))
    check(len((story.get("choices") or {}).get("pick", {}).get("options", []))
          == 2, "choice 'pick' has 2 options")
    log("  %d set(s), %d choice(s), %d srt file(s), fps %s"
        % (len(sets), len(story.get("choices") or {}), len(files), fps))

    # ---------------------------------------------------------- 2. sidecars -
    log("=== sidecars (story.sync.json + story.schema.json) ===")
    sync_path = sb.sync_path_for(STORY)
    schema_path = sb.schema_path_for(STORY)
    check(os.path.isfile(sync_path), "story.sync.json exists", sync_path)
    check(os.path.isfile(schema_path), "story.schema.json exists", schema_path)
    side = sb.load_sidecar(sync_path)
    refs = tw.story_refs(sb, loaded)
    old_uids = side.get("uids") or {}
    by_rec_obj, by_rec_act = tw._resolve_by_recorded(refs, old_uids)
    live = tw.action_ranges(scene, refs, old_uids)
    check(bool(side.get("actions")), "sidecar carries action ranges")
    missing_act = sorted(set(refs["actions"]) - set(live))
    check(not missing_act, "every referenced action exists in the file",
          missing_act)
    drift = {a: (list(side["actions"].get(a, [])), list(live[a]))
             for a in sorted(live)
             if list(side.get("actions", {}).get(a, [])) != list(live[a])}
    check(not drift, "sidecar ranges match the live action ranges", drift)
    for set_name, want in sorted(bs.WANT_RANGES.items()):
        got = {n: list(r) for n, r in sorted(side.get("actions", {}).items())
               if n.startswith(set_name + "__")}
        check(bool(got) and all(r == [1, want] for r in got.values()),
              "set '%s' actions span [1, %d]" % (set_name, want), got)
    need = set(refs["objects"]) | set(refs["cameras"]) | set(refs["actions"])
    have = {r["name"] for r in side.get("uids", {}).values()
            if isinstance(r, dict)}
    check(need <= have, "every story reference has a uid (rename tracking)",
          sorted(need - have))
    schema = json.load(open(schema_path, encoding="utf-8"))
    start_enum = (schema.get("properties", {}).get("start", {})
                  .get("enum"))
    check(sorted(start_enum or []) == sorted(sets),
          "schema 'start' enum lists the sets", start_enum)
    errs, warns, binds, summary = tw.validate_impl(scene)
    log("  validate: " + summary)
    for w in warns:
        log("    warning: " + w)
    check(not errs, "Validate: no story errors", errs)
    check(not binds, "Validate: no binding errors", binds)
    check(not warns, "Validate: no warnings", warns)

    # --------------------------------------------------- 3+4. preview every set
    log("=== Preview Set works for every set (bindings vs the scene) ===")
    cam = tw.role_object(scene, tw._ROLE_CAMERA, ("Camera",)) or scene.camera
    sub = tw.role_object(scene, tw._ROLE_SUBS, ("Subtitles",))
    menu = tw.role_object(scene, tw._ROLE_MENU, ("ChoiceMenu",))
    check(cam is not None, "render camera found by role")
    check(sub is not None and sub.type == 'FONT', "subtitle object by role")
    check(menu is not None and menu.type == 'FONT', "menu object by role")
    plans = {}
    for name in sorted(sets):
        ok, msg, info = tw.preview_set_impl(scene, name)
        plan = info.get("plan") or sb.set_plan(story, files, name,
                                               dict(side.get("actions") or {}),
                                               fps)
        plans[name] = plan
        want_f1 = 1 + int(round(sb.set_duration(plan) * fps))
        check(ok, "preview '%s' succeeds" % name, msg)
        check(info.get("missing") == [],
              "'%s' has no unresolved bindings" % name, info.get("missing"))
        check((scene.frame_start, scene.frame_end) == (1, want_f1),
              "'%s' frame range is set-local 1-%d" % (name, want_f1),
              (scene.frame_start, scene.frame_end))
        want_cues = len(plan["subs"])
        check(info.get("cues") == want_cues,
              "'%s' loaded %d subtitle cue(s)" % (name, want_cues),
              info.get("cues"))
        # only THIS set's actions may be assigned (the sets share a frame
        # range, so a leftover action from another set would play too)
        allowed = {a for (_o, a, _at, _w, _d) in plan["actions"]}
        stray = []
        for o in scene.objects:
            ad = getattr(o, "animation_data", None)
            if ad is None or ad.action is None:
                continue
            an = ad.action.name
            if "__" in an and an not in allowed:
                stray.append("%s@%s" % (o.name, an))
        check(not stray, "'%s' detached every other set's action" % name,
              stray)
        assigned = {a.split("@")[1] for a in info.get("assigned", [])}
        check(assigned == allowed,
              "'%s' assigned exactly its own %d action(s)" % (name, len(allowed)),
              sorted(allowed - assigned) or sorted(assigned - allowed))
        if plan["shots"] and cam is not None:
            shot = plan["shots"][0][1]
            staged = tw.find_object(scene, shot, by_rec_obj)
            check(staged is not None, "'%s' opening shot '%s' exists"
                  % (name, shot))
            if staged is not None:
                check(tuple(round(v, 3) for v in cam.location)
                      == tuple(round(v, 3) for v in staged.location),
                      "'%s' camera snapped to the opening shot" % name,
                      (tuple(cam.location), tuple(staged.location)))
                check(abs(cam.data.lens - staged.data.lens) < 1e-6,
                      "'%s' camera took the shot's lens (%smm)"
                      % (name, staged.data.lens), cam.data.lens)
        check(scene.tw_preview_set == name, "'%s' preview indicator set" % name)
        # The slot-binding proof. Cross-object action assignment leaves
        # action_slot None and then SILENTLY does not evaluate, so "the action
        # exists" proves nothing: read an animated value through the evaluated
        # depsgraph and require it to equal what the action keys say.
        for (oname, aname, _at, _w, _d) in plan["actions"]:
            obj = tw.find_object(scene, oname, by_rec_obj)
            act = tw.find_action(aname, by_rec_act)
            if obj is None or act is None:
                continue
            check(obj.animation_data is not None
                  and obj.animation_data.action_slot is not None,
                  "'%s' action_slot is bound on %s" % (aname, oname))
            groups = {}
            for r in key_digest(tw, act):
                if "." not in r[0]:
                    groups.setdefault((r[0], r[6]), []).append(r)
            pick = None
            for key in sorted(groups):
                rs = sorted(groups[key], key=lambda r: r[1])
                dev = max(rs, key=lambda r: abs(r[2] - rs[0][2]))
                if abs(dev[2] - rs[0][2]) > 1e-4:
                    pick = (key[0], key[1], dev, rs[0][2])
                    break
            if not check(pick is not None,
                         "'%s' animates something (vs its first key)" % aname):
                continue
            path, axis, dev, base = pick
            scene.frame_set(int(round(dev[1])))
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            got = getattr(obj.evaluated_get(dg), path)[axis]
            check(abs(got - dev[2]) < 1e-3,
                  "'%s' drives %s.%s[%d] through the depsgraph at f%d "
                  "(%.4f, action says %.4f, first key %.4f)"
                  % (aname, oname, path, axis, int(round(dev[1])), got,
                     dev[2], base), got)
    log("  previewed %d set(s): %s"
        % (len(plans), ", ".join("%s 1-%d" % (n, 1 + int(round(
            sb.set_duration(p) * fps))) for n, p in sorted(plans.items()))))

    # Restore the file's own preview state before the remaining sections: the
    # bake check compares against the START set's cues, and this script never
    # saves, so the in-memory scene must not be left on some other set.
    ok, msg, _i = tw.preview_set_impl(scene, start)
    check(ok, "preview restored to the start set '%s'" % start, msg)

    # --------------------------------------------------- 5. no v1 leftovers --
    log("=== v1 leftovers are gone ===")
    marks = sorted(m.name for m in scene.timeline_markers)
    check(not marks, "no timeline markers (v2 has no SET-/CH- markers)", marks)
    cam_act = (cam.animation_data.action
               if cam is not None and cam.animation_data is not None else None)
    check(cam_act is None,
          "render camera has no baked action (v1 baked Camera_anim)",
          cam_act.name if cam_act is not None else None)
    cam_dact = (cam.data.animation_data.action
                if cam is not None and cam.data.animation_data is not None
                else None)
    check(cam_dact is None, "camera data has no baked lens action",
          cam_dact.name if cam_dact is not None else None)
    menu_act = (menu.animation_data.action
                if menu is not None and menu.animation_data is not None
                else None)
    check(menu_act is None, "choice menu has no action (plain object in v2)",
          menu_act.name if menu_act is not None else None)
    legacy = sorted(o.name for o in scene.objects
                    if o.name.startswith("SubLine"))
    check(not legacy, "no v1 SubLine## bakes", legacy)
    gone = [a.name for a in bpy.data.actions
            if a.name in ("Camera_anim", "ChoiceMenu_anim")]
    check(not gone, "no v1 Camera_anim / ChoiceMenu_anim actions", gone)
    gd = bpy.data.objects.get("GameDirector")
    check(gd is not None, "GameDirector exists")
    if gd is not None:
        check(gd.game.properties.get("tw_branches") is None,
              "no v1 tw_branches property (v2 reads story.yml live)")

    # ------------------------------------------------------------- 6. bricks --
    log("=== game bricks + properties ===")
    if gd is not None:
        sens = gd.game.sensors.get(bs.GAME_SENSOR)
        ctrl = gd.game.controllers.get(bs.GAME_CTRL)
        check(sens is not None, "Always sensor present")
        check(ctrl is not None, "Python controller present")
        if sens is not None:
            check(getattr(sens, "use_pulse_true_level", False) is True,
                  "Always pulses every tick")
        if ctrl is not None:
            check(ctrl.mode == 'MODULE', "controller mode is MODULE", ctrl.mode)
            check(ctrl.module == bs.GAME_MODULE,
                  "controller module is %s" % bs.GAME_MODULE, ctrl.module)
        if sens is not None and ctrl is not None:
            check(ctrl in list(sens.controllers),
                  "Always -> Dialogue linked")
        sp = gd.game.properties.get(bs.STORY_PROP)
        check(sp is not None and sp.type == 'STRING'
              and sp.value == bs.STORY_VALUE,
              "%s is the STRING path %s" % (bs.STORY_PROP, bs.STORY_VALUE),
              (sp.type, sp.value) if sp is not None else None)
        if sp is not None:
            check(len(sp.value) < 60,
                  "property value is short (UPBGE caps property strings, so "
                  "only paths live here - never JSON)", len(sp.value))
    check(bs.GAME_TEXT in bpy.data.texts,
          "%s is embedded as a text block (MODULE needs it in-game)"
          % bs.GAME_TEXT)
    for oname, pname in bs.CREATE_ONLY_PROPS:
        o = bpy.data.objects.get(oname)
        p = o.game.properties.get(pname) if o is not None else None
        check(p is not None and p.type == 'STRING',
              "%s.%s is a STRING property" % (oname, pname))
    if cam is not None:
        for oname in ("Subtitles", "ChoiceMenu"):
            o = bpy.data.objects.get(oname)
            check(o is not None and o.parent == cam,
                  "%s is parented to the render camera" % oname)

    # -------------------------------------------------------------- 7. bakes --
    log("=== the start set is baked (renders with no add-on) ===")
    baked = sorted((o for o in scene.objects if o.get(tw._BAKE_TAG) == start),
                   key=lambda o: o.name)
    plan0 = plans.get(start) or sb.set_plan(story, files, start,
                                            dict(side.get("actions") or {}),
                                            fps)
    check(len(baked) > 0, "set '%s' has %s_Line## objects" % (start, start),
          len(baked))
    check(len(baked) == len(sub.tw_entries) if sub is not None else False,
          "bake count matches the previewed cue count", len(baked))
    for i, o in enumerate(baked):
        bad = []
        if o.parent != cam:
            bad.append("not parented to the camera")
        if len(o.modifiers):
            bad.append("%d modifier(s)" % len(o.modifiers))
        if o.hide_render:
            bad.append("hide_render")
        if len(o.tw_entries) != 1:
            bad.append("%d entries" % len(o.tw_entries))
        elif i < len(plan0["subs"]) and \
                o.tw_entries[0].text != plan0["subs"][i][2]:
            bad.append("text != cue %d" % (i + 1))
        if o.animation_data is None or o.animation_data.action is None:
            bad.append("no reveal action")
        check(not bad, "%s is a clean baked line" % o.name, bad)
    log("  baked: %s" % ", ".join(o.name for o in baked))

    # ------------------------------------------- 8. build-twice key survival --
    build_twice(tw, scene)

    return finish(scene)


def finish(scene):
    log("=== summary ===")
    log("scene: %d object(s), %d action(s), %d material(s), frames %d-%d, "
        "preview set '%s'"
        % (len(scene.objects), len(bpy.data.actions), len(bpy.data.materials),
           scene.frame_start, scene.frame_end, scene.tw_preview_set or "-"))
    if FAILS:
        log("RESULT: %d of %d CHECK(S) FAILED" % (len(FAILS), CHECKS[0]))
        for f in FAILS:
            log("  - " + f)
        sys.stdout.flush()
        os._exit(1)          # read-only: nothing to save, and Blender would
    log("RESULT: ALL %d CHECKS PASSED" % CHECKS[0])   # exit 0 regardless
    sys.stdout.flush()
    return 0


# --------------------------------------------------------------------------
# build-twice key survival (runs in a throwaway copy of the project)
# --------------------------------------------------------------------------

FP_SCRIPT = r'''
import bpy, hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(bpy.data.filepath))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw

def dig(a):
    rows = []
    for fc in tw._action_fcurves(a):
        for k in fc.keyframe_points:
            rows.append([fc.data_path, round(k.co.x, 4), round(k.co.y, 6),
                         round(k.handle_left.x, 4), round(k.handle_right.x, 4),
                         str(k.interpolation)])
    rows.sort()
    return rows

if os.environ.get("TW_TWEAK"):
    act = bpy.data.actions["main__cuby"]
    n = 0
    for fc in tw._action_fcurves(act):
        if fc.data_path == "location":
            for k in fc.keyframe_points:
                if round(k.co.x) == 100:
                    k.co.y += 0.25
                    n += 1
    assert n, "no frame-100 location key to nudge"
    for fc in tw._action_fcurves(act):
        fc.update()
    print("[FP] nudged %d hand-tweak key(s) at frame 100" % n, flush=True)

fp = {"actions": {a.name: {
          "keys": len(dig(a)),
          "range": [int(round(v)) for v in a.frame_range],
          "fake_user": a.use_fake_user,
          "digest": hashlib.sha1(json.dumps(dig(a)).encode()).hexdigest()}
      for a in bpy.data.actions},
      "objects": sorted(o.name for o in bpy.data.objects),
      "materials": sorted(m.name for m in bpy.data.materials),
      "uids": {o.name: o.get("_tw_uid") for o in bpy.data.objects
               if o.get("_tw_uid")}}
with open(os.environ["TW_FP_OUT"], "w", encoding="utf-8") as fh:
    json.dump(fp, fh, indent=1, sort_keys=True)
print("[FP] %s: %d action(s), %d object(s)"
      % (os.environ["TW_FP_OUT"], len(fp["actions"]), len(fp["objects"])),
      flush=True)
if os.environ.get("TW_TWEAK"):
    bpy.ops.wm.save_mainfile()
    print("[FP] saved the tweak", flush=True)
'''


def build_twice(tw, scene):
    log("=== build-twice key survival (throwaway copy) ===")
    if os.environ.get("TW_SKIP_BUILD_TWICE"):
        log("  SKIP: TW_SKIP_BUILD_TWICE is set")
        return
    exe = bpy.app.binary_path
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        log("  SKIP: xvfb-run not found (logic bricks need a display)")
        return
    tmp = tempfile.mkdtemp(prefix="twverify")
    try:
        for fn in sorted(os.listdir(HERE)):
            src = os.path.join(HERE, fn)
            if os.path.isfile(src) and (
                    fn.endswith((".py", ".yml", ".srt", ".json", ".blend"))):
                shutil.copy2(src, tmp)
            elif os.path.isdir(src) and fn in ("subtitles", "audio"):
                shutil.copytree(src, os.path.join(tmp, fn))
        blend = os.path.join(tmp, "talking_robots.blend")
        fp_py = os.path.join(tmp, "_verify_fp.py")
        with open(fp_py, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(FP_SCRIPT)
        before, after = (os.path.join(tmp, "fp_%s.json" % t)
                         for t in ("before", "after"))

        def run(argv, env=None, label=""):
            e = dict(os.environ)
            e.update(env or {})
            r = subprocess.run(argv, cwd=tmp, env=e, capture_output=True,
                               text=True, timeout=900)
            if r.returncode != 0:
                log("  child failed (%s, exit %d): %s"
                    % (label, r.returncode,
                       (r.stdout or "")[-600:] + (r.stderr or "")[-600:]))
            return r.returncode

        ui = [xvfb, "-a", "-s", "-screen 0 1280x800x24", exe]
        rc = run(ui + ["--factory-startup", "-P", "build_scene.py"],
                 {"TW_FRESH": "1"}, "build #1 from scratch")
        check(rc == 0, "build #1 (from scratch) exits 0", rc)
        rc = run([exe, "-b", blend, "-P", "_verify_fp.py"],
                 {"TW_FP_OUT": before, "TW_TWEAK": "1"}, "fingerprint+tweak")
        check(rc == 0, "fingerprint + hand tweak exits 0", rc)
        rc = run(ui + ["--factory-startup", "-P", "build_scene.py"],
                 None, "build #2 additive")
        check(rc == 0, "build #2 (additive rebuild) exits 0", rc)
        rc = run([exe, "-b", blend, "-P", "_verify_fp.py"],
                 {"TW_FP_OUT": after}, "fingerprint")
        check(rc == 0, "second fingerprint exits 0", rc)
        if not (os.path.isfile(before) and os.path.isfile(after)):
            check(False, "both fingerprints were written")
            return
        b = json.load(open(before, encoding="utf-8"))
        a = json.load(open(after, encoding="utf-8"))
        changed = sorted(n for n in set(b["actions"]) & set(a["actions"])
                         if b["actions"][n]["digest"]
                         != a["actions"][n]["digest"])
        check(not changed,
              "no action key changed across the rebuild (the hand tweak "
              "survived)", changed)
        check(sorted(b["actions"]) == sorted(a["actions"]),
              "the rebuild created no extra actions and dropped none",
              (len(b["actions"]), len(a["actions"])))
        check(b["objects"] == a["objects"],
              "no duplicate objects after the rebuild (.001 names)",
              [o for o in a["objects"] if o not in b["objects"]][:5])
        check(b["materials"] == a["materials"],
              "no duplicate materials after the rebuild")
        check(b["uids"] == a["uids"],
              "_tw_uid stamps are stable across the rebuild")
        want_acts = sorted(sb.load_sidecar(
            sb.sync_path_for(os.path.join(tmp, "story.yml")))["actions"])
        setacts = sorted(n for n in b["actions"] if "__" in n)
        check(setacts == want_acts,
              "the file holds exactly the %d per-set actions story.yml names"
              % len(want_acts), sorted(set(setacts) ^ set(want_acts)))
        check(all(a["actions"][n]["fake_user"] for n in setacts),
              "every per-set action is fake-user guarded after the rebuild")
        rng = {n: a["actions"][n]["range"] for n in setacts}
        check(all(rng[n] == [1, bs.WANT_RANGES[n.split("__")[0]]]
                  for n in setacts),
              "ranges still match the story after the rebuild", rng)
        log("  %d action(s) fingerprinted twice, %d object(s), %d uid(s)"
            % (len(a["actions"]), len(a["objects"]), len(a["uids"])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


main()
