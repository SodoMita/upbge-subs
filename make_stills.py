#!/usr/bin/env python3
"""Render the README/HANDOFF proof stills for the v2 demo, and verify the ones
already committed. It never saves the .blend and it never rewrites a still that
already matches, so re-running it is a check, not a churn-fest.

    U=/path/to/upbge-0.50-linux-x64
    $U/blender -b talking_robots.blend -P make_stills.py     # all + verify
    MODE=camera $U/blender -b talking_robots.blend -P make_stills.py
    OVERWRITE=1 $U/blender -b talking_robots.blend -P make_stills.py  # accept

noaddon  v2_noaddon_0008.png  f8 with NO add-on in the session: the baked
                              cards render on their own, full line and all
addon    v2_addon_0008.png    the same frame with the add-on registered: still
                              typing (the pair is the typing proof)
sets     v2_<set>_0030.png    Preview Set on that set: its own cues, shot and
               _0200.png      frame range. The start set also gets f200 and
               _0361.png      f<frame_end> (its tail = the choice menu)
camera     v2_cam_hold.png    f<first real cut> with the preview camera follow
           v2_cam_follow.png  OFF / ON: the same frame, two framings - the
                              render camera moves with the story's [CAM] cues
                              exactly as it does when you press P

Matching is done on the PNG's IDAT payload only: Blender embeds a render
timestamp in a tEXt chunk, so whole-file hashes differ for identical pixels
while the pixel data does not.
"""
import bpy
import hashlib
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb                                    # noqa: E402

MODE = os.environ.get("MODE", "all")
OVERWRITE = bool(os.environ.get("OVERWRITE"))
OUT = os.path.join(HERE, "test")
SCRATCH = os.path.join(OUT, ".stills")
STORY = os.path.join(HERE, "story.yml")
DRIFT = []


def log(m):
    print("[STILL] %s" % m, flush=True)


def want(name):
    return MODE in ("all", name)


def pixels(path):
    """sha256 of the concatenated IDAT chunks (the pixels, no metadata)."""
    with open(path, "rb") as fh:
        d = fh.read()
    h, i = hashlib.sha256(), 8
    while i + 8 <= len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        if d[i + 4:i + 8] == b"IDAT":
            h.update(d[i + 8:i + 8 + ln])
        i += 12 + ln
    return h.hexdigest()


def render(name, frame, scene):
    """Render one still into test/<name>, keeping it if it already matches."""
    scene.frame_set(int(frame))
    bpy.context.view_layer.update()
    tmp = os.path.join(SCRATCH, name)
    dst = os.path.join(OUT, name)
    scene.render.filepath = tmp
    bpy.ops.render.render(write_still=True)
    if os.path.isfile(dst) and pixels(dst) == pixels(tmp):
        os.remove(tmp)
        log("%-22s f%-4d matches the committed still (pixels identical)"
            % (name, int(frame)))
        return
    if not os.path.isfile(dst):
        shutil.move(tmp, dst)
        log("%-22s f%-4d WROTE (new still)" % (name, int(frame)))
    elif OVERWRITE:
        shutil.move(tmp, dst)
        log("%-22s f%-4d REWROTE (OVERWRITE=1; the picture changed)"
            % (name, int(frame)))
    else:
        DRIFT.append(name)
        log("%-22s f%-4d DIFFERS from the committed still - left as %s "
            "(re-run with OVERWRITE=1 to accept)"
            % (name, int(frame), os.path.relpath(tmp, HERE)))


def main():
    os.makedirs(SCRATCH, exist_ok=True)
    scene = bpy.context.scene
    loaded = sb.load_story_files(STORY)
    if loaded["errors"]:
        raise SystemExit("[STILL] story does not load: %s"
                         % loaded["errors"][:3])
    story, fps = loaded["story"], float(scene.render.fps) or 24.0
    start = str(story["start"])
    sets = sorted(str(k) for k in story["sets"])
    side = sb.load_sidecar(sb.sync_path_for(STORY))
    plan = sb.set_plan(story, loaded["files"], start,
                       dict(side["actions"]), fps)
    f0 = int(scene.frame_start)
    shots = plan["shots"]
    # the first frame that cuts to a DIFFERENT shot (an opening `camera:` anim
    # and a t=0 [CAM] cue name the same rig, so skipping repeats is the point);
    # +0.6 s is past the game's CAM_BLEND, so the cut has landed
    j = next((i for i in range(1, len(shots)) if shots[i][1] != shots[0][1]),
             None)
    cut_t = (shots[j][0] + 0.6) if j else 1.0
    cut = max(f0, min(f0 + int(round(cut_t * fps)), int(scene.frame_end)))
    log("start set '%s' shots=%s -> first real cut '%s' at %.3fs = frame %d"
        % (start, shots, shots[j][1] if j else shots[0][1], cut_t, cut))

    if want("noaddon"):
        log("=== no add-on: the baked cards must play and render alone ===")
        log("add-on present in this session: %s"
            % hasattr(bpy.types.Object, "tw_entries"))
        render("v2_noaddon_0008.png", 8, scene)

    if not (want("addon") or want("sets") or want("camera")):
        return finish()
    if hasattr(bpy.types.Object, "tw_entries"):
        log("add-on already registered in this session")
    else:
        import typewriter_subtitles as tw
        tw.register()
    import typewriter_subtitles as tw                  # noqa: E402
    sub = bpy.data.objects.get("Subtitles")

    if want("addon"):
        log("=== with the add-on: the same frame, mid-type ===")
        scene.frame_set(8)
        bpy.context.view_layer.update()
        first = bpy.data.objects.get("main_Line01")
        log("live plate at f8: %r" % ((sub.data.body if sub else "") or ""))
        log("baked line '%s' at f8: %r" % (first.name, first.data.body)
            if first is not None else "no baked main_Line01 in this file")
        render("v2_addon_0008.png", 8, scene)

    if want("sets"):
        for s in sets:
            ok, msgs = tw.preview_set_impl(scene, s)
            if not ok:
                raise SystemExit("[STILL] preview '%s' failed: %s" % (s, msgs))
            log("preview '%s': %s" % (s, "; ".join(msgs)[:140]))
            render("v2_%s_0030.png" % s, min(30, int(scene.frame_end)), scene)
            if s == start:          # the start set is what the timeline shows
                render("v2_%s_0200.png" % s, min(200,
                                                 int(scene.frame_end)), scene)
                render("v2_%s_%04d.png" % (s, int(scene.frame_end)),
                       int(scene.frame_end), scene)

    if want("camera"):
        log("=== the preview drives the camera like the game does ===")
        ok, _m = tw.preview_set_impl(scene, start)
        if not ok:
            raise SystemExit("[STILL] preview of the start set failed")
        cam = scene.camera
        scene.tw_preview_camera = False
        render("v2_cam_hold.png", cut, scene)
        scene.tw_preview_camera = True
        scene.frame_set(f0)
        scene.frame_set(cut)
        bpy.context.view_layer.update()
        tw.preview_camera_tick(scene)          # idempotent: the handler did it
        render("v2_cam_follow.png", cut, scene)
        posed = (tuple(round(v, 4) for v in cam.matrix_basis.translation),
                 round(float(cam.data.lens), 3))
        log("follow ON at f%d: %s   (follow OFF held the opening shot)"
            % (cut, posed))
        if pixels(os.path.join(OUT, "v2_cam_hold.png")) == pixels(
                os.path.join(OUT, "v2_cam_follow.png")):
            raise SystemExit("[STILL] the follow did not change the picture")
        log("the two frames differ: the cut shows up in a render")
        scene.tw_preview_camera = False
    return finish()


def finish():
    """Drop the scratch dir and turn any drift into a failed run."""
    if os.path.isdir(SCRATCH):
        left = sorted(os.listdir(SCRATCH))
        if left and not DRIFT:
            log("kept for comparison: %s" % left)
        elif not left:
            shutil.rmtree(SCRATCH, ignore_errors=True)
    if DRIFT:
        raise SystemExit("[STILL] %d still(s) no longer match: %s"
                         % (len(DRIFT), ", ".join(DRIFT)))
    log("done - every rendered still matches the committed one")


main()
