#!/usr/bin/env python3
"""Build the Talking Robots scene: two low-effort primitive characters with
3D typewriter subtitles (Typewriter Subtitles addon) + UPBGE game logic.

Story sets/actors/choices come from story.yml, subtitle text + the [CAM]
shot plan from dialogue.srt (see story.py for the formats).

Run with a UI (logic bricks need one):
    xvfb-run -a upbge --factory-startup -P build_scene.py   (quits itself)
Saves talking_robots.blend next to this script.
"""
import bpy
import math
import os
import random
import sys
from mathutils import Euler, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw
import story as sb

FPS = 24
FRAME_START = 1
FRAME_END = None  # computed from dialogue.srt in main()
CPS = 30.0


def log(msg):
    print("[BUILD] " + msg, flush=True)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def clear_scene():
    for o in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            pass
    log("scene cleared")


def make_mat(name, color, roughness=0.65, emission_color=None,
             emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        inp = bsdf.inputs
        if "Base Color" in inp:
            inp["Base Color"].default_value = (*color, 1.0)
        if "Roughness" in inp:
            inp["Roughness"].default_value = roughness
        if emission_color is not None:
            if "Emission Color" in inp:
                inp["Emission Color"].default_value = (*emission_color, 1.0)
                if "Emission Strength" in inp:
                    inp["Emission Strength"].default_value = emission_strength
            elif "Emission" in inp:
                inp["Emission"].default_value = (*emission_color, 1.0)
    return mat


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def prim(op, name, **kwargs):
    """Run a primitive-add op, rename result. Location baked into kwargs."""
    op(**kwargs)
    o = bpy.context.active_object
    o.name = name
    return o


def look_at(obj, target):
    d = Vector(target) - obj.location
    if d.length > 1e-6:
        obj.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


def smooth(obj):
    try:
        for poly in obj.data.polygons:
            poly.use_smooth = True
    except Exception:
        pass


def add_text(name, body, size=0.5):
    bpy.ops.object.text_add(location=(0, 0, 0))
    o = bpy.context.active_object
    o.name = name
    o.data.body = body
    o.data.align_x = 'CENTER'
    o.data.align_y = 'CENTER'
    o.data.size = size
    return o


def _key_cam_pose2(cam, frame, loc, eul, lens, interp, interp_map):
    cam.location = tuple(loc)
    cam.rotation_euler = eul
    cam.data.lens = lens
    cam.keyframe_insert("location", frame=frame)
    cam.keyframe_insert("rotation_euler", frame=frame)
    cam.data.keyframe_insert("lens", frame=frame)
    interp_map[int(frame)] = interp


def build_staged_cams():
    """Real camera objects per shot (Wide/WideEnd/Cuby/Sphero): the editor
    framing rig. Starter timeline keys are baked from them at build; the
    game plays the baked actions (tweak keys + press P, no rebuild)."""
    staged = {}
    for name, (loc, tgt) in sb.SHOT_POSES.items():
        bpy.ops.object.camera_add(location=loc)
        c = bpy.context.active_object
        c.name = name
        c.data.lens = sb.SHOT_LENS[name]
        look_at(c, tgt)
        staged[name] = c
    log("staged %d shot cameras" % len(staged))
    return staged


# --------------------------------------------------------------------------
# scene setup
# --------------------------------------------------------------------------

def setup_scene():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    try:
        scene.cycles.device = 'CPU'
    except Exception:
        pass
    scene.cycles.samples = 12
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    scene.frame_start = FRAME_START
    scene.frame_end = FRAME_END
    scene.render.filepath = os.path.join(HERE, "render", "frame_")
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    # dark stage backdrop
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.012, 0.018, 0.045, 1.0)
        bg.inputs[1].default_value = 1.0
    log("scene settings done")


def build_lights_camera(cues, cams):
    # key sun
    bpy.ops.object.light_add(type='SUN', location=(4, -3, 6))
    sun = bpy.context.active_object
    sun.name = "KeySun"
    look_at(sun, (0, 0, 1))
    try:
        sun.data.energy = 4.0
    except Exception:
        pass
    try:
        sun.data.angle = 0.15
    except Exception:
        pass
    # cool fill sun (no shadow)
    bpy.ops.object.light_add(type='SUN', location=(-5, 2, 4))
    fill = bpy.context.active_object
    fill.name = "FillSun"
    look_at(fill, (0, 0, 1))
    try:
        fill.data.energy = 0.8
    except Exception:
        pass
    try:
        fill.data.use_shadow = False
    except Exception:
        pass
    # the single render camera; [CAM] shots become keys copied from the
    # staged cameras (cuts between them, push-in drift inside Wide spans)
    bpy.ops.object.camera_add(location=sb.WIDE_A[0])
    cam = bpy.context.active_object
    cam.name = "Camera"
    cam.data.lens = 50
    try:
        cam.data.clip_start = 0.1
        cam.data.clip_end = 100.0
    except Exception:
        pass
    bpy.context.scene.camera = cam
    staged = build_staged_cams()
    bpy.context.view_layer.update()
    la = staged["Wide"].matrix_world.to_translation()
    ea = staged["Wide"].matrix_world.to_euler()
    lb = staged["WideEnd"].matrix_world.to_translation()
    eb = staged["WideEnd"].matrix_world.to_euler()

    def wide_pose(f):
        k = (f - FRAME_START) / max(FRAME_END - FRAME_START, 1)
        loc = [la[i] + (lb[i] - la[i]) * k for i in range(3)]
        return loc, Euler([ea[i] + (eb[i] - ea[i]) * k for i in range(3)],
                          'XYZ')

    interp_map = {}
    spans = sb.cam_spans(cams, FPS, FRAME_START, FRAME_END)
    for (f0, f1, shot) in spans:
        lens = sb.SHOT_LENS[shot]
        if shot == "Wide":
            loc, eul = wide_pose(f0)
            _key_cam_pose2(cam, f0, loc, eul, lens, 'BEZIER', interp_map)
            if f1 > f0:
                loc, eul = wide_pose(f1)
                _key_cam_pose2(cam, f1, loc, eul, lens, 'CONSTANT', interp_map)
        else:
            m = staged[shot].matrix_world
            _key_cam_pose2(cam, f0, m.to_translation(), m.to_euler(), lens,
                            'CONSTANT', interp_map)
    ad = cam.animation_data
    if ad is not None and ad.action is not None:
        for fc in tw._action_fcurves(ad.action):
            if fc.data_path in ("location", "rotation_euler"):
                for k in fc.keyframe_points:
                    want = interp_map.get(int(round(k.co.x)))
                    if want is not None:
                        k.interpolation = want
                fc.update()
    dad = cam.data.animation_data
    if dad is not None and dad.action is not None:
        for fc in tw._action_fcurves(dad.action):
            if fc.data_path == "lens":
                for k in fc.keyframe_points:
                    want = interp_map.get(int(round(k.co.x)))
                    if want is not None:
                        k.interpolation = want
                fc.update()
    if cam.animation_data is not None and \
            cam.animation_data.action is not None:
        cam.animation_data.action.name = "Camera_anim"
    if cam.data.animation_data is not None and \
            cam.data.animation_data.action is not None and \
            cam.data.animation_data.action is not cam.animation_data.action:
        cam.data.animation_data.action.name = "Camera_lens_anim"
    log("lights + camera done (%d shot spans)" % len(spans))
    return cam


def build_set(mats):
    # ground far below
    g = prim(bpy.ops.mesh.primitive_plane_add, "Ground",
             size=40, location=(0, 0, -0.2))
    g.data.materials.append(mats['ground'])
    # round talk-show stage
    st = prim(bpy.ops.mesh.primitive_cylinder_add, "Stage",
              radius=3.0, depth=0.2, location=(0, 0, -0.099),
              vertices=48)
    st.data.materials.append(mats['stage'])
    # light beams (fake volumetrics: emission cones)
    for nm, x, tilt in (("BeamL", -1.25, 0.06), ("BeamR", 1.25, -0.06)):
        cone = prim(bpy.ops.mesh.primitive_cone_add, nm,
                    radius1=0.65, radius2=0.18, depth=6.0,
                    location=(x, 1.8, 3.2))
        cone.rotation_euler = (0.0, tilt, 0.0)
        cone.data.materials.append(mats['beam'])
        smooth(cone)
    # scattered low-poly rocks
    rng = random.Random(7)
    for i in range(6):
        ang = rng.uniform(-1.2, 1.2) + (math.pi if i % 2 else 0.0)
        rad = rng.uniform(4.5, 9.0)
        rock = prim(bpy.ops.mesh.primitive_ico_sphere_add, f"Rock{i}",
                    subdivisions=1, radius=rng.uniform(0.25, 0.7),
                    location=(math.sin(ang) * rad, math.cos(ang) * rad,
                              -0.2 + rng.uniform(0.0, 0.2)))
        rock.rotation_euler = (rng.uniform(0, 3), rng.uniform(0, 3),
                               rng.uniform(0, 3))
        rock.scale = (rng.uniform(0.7, 1.3), rng.uniform(0.7, 1.3),
                      rng.uniform(0.5, 1.0))
        rock.data.materials.append(mats['rock'])
    log("set done")


# --------------------------------------------------------------------------
# characters (low effort, maximum charm)
# --------------------------------------------------------------------------

def build_cuby(mats):
    root = link(bpy.data.objects.new("CubyRoot", None))
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.4
    root.location = (-1.25, 0, 0)
    body = prim(bpy.ops.mesh.primitive_cube_add, "CubyBody",
                size=1.1, location=(0, 0, 0))
    body.parent = root
    body.location = (0, 0, 0.95)
    body.data.materials.append(mats['cuby'])
    for i, sx in enumerate((-0.28, 0.28)):
        leg = prim(bpy.ops.mesh.primitive_cylinder_add, f"CubyLeg{i}",
                   radius=0.12, depth=0.45, location=(0, 0, 0))
        leg.parent = root
        leg.location = (sx, 0, 0.225)
        leg.data.materials.append(mats['dark'])
    arms = {}
    for nm, sx, ry in (("CubyArmL", 0.68, 0.3), ("CubyArmR", -0.68, -0.3)):
        arm = prim(bpy.ops.mesh.primitive_cylinder_add, nm,
                   radius=0.09, depth=0.65, location=(0, 0, 0))
        arm.parent = root
        arm.location = (sx, 0, 0.85)
        arm.rotation_euler = (0, ry, 0)
        arm.data.materials.append(mats['cuby_dark'])
        arms[nm] = arm
    eyes = []
    for nm, sx in (("CubyEyeL", 0.23), ("CubyEyeR", -0.23)):
        white = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm,
                     radius=0.16, segments=20, ring_count=12,
                     location=(0, 0, 0))
        white.parent = root
        white.location = (sx, -0.50, 1.18)
        white.data.materials.append(mats['white'])
        smooth(white)
        pup = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm + "Pupil",
                   radius=0.07, segments=12, ring_count=8,
                   location=(0, 0, 0))
        pup.parent = white
        pup.location = (0, -0.13, 0.01)
        pup.data.materials.append(mats['black'])
        smooth(pup)
        eyes.append(white)
    mouth = prim(bpy.ops.mesh.primitive_cube_add, "CubyMouth",
                 size=1.0, location=(0, 0, 0))
    mouth.parent = root
    mouth.location = (0, -0.545, 0.80)
    mouth.scale = (0.34, 0.05, 0.035)
    mouth.data.materials.append(mats['black'])
    ant = prim(bpy.ops.mesh.primitive_cylinder_add, "CubyAntenna",
               radius=0.03, depth=0.5, location=(0, 0, 0))
    ant.parent = root
    ant.location = (0.3, 0.2, 1.7)
    ant.data.materials.append(mats['dark'])
    tip = prim(bpy.ops.mesh.primitive_uv_sphere_add, "CubyTip",
               radius=0.08, location=(0, 0, 0))
    tip.parent = root
    tip.location = (0.3, 0.2, 1.98)
    tip.data.materials.append(mats['red_glow'])
    smooth(tip)
    tag = add_text("CubyTag", "CUBY", size=0.20)
    tag.parent = root
    tag.location = (0, -0.60, 1.02)
    tag.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    tag.data.materials.append(mats['tag_blue'])
    log("cuby built")
    return {"root": root, "mouth": mouth, "arm_r": arms["CubyArmR"],
            "arm_r_ry": -0.3, "eyes": eyes}


def build_sphero(mats):
    root = link(bpy.data.objects.new("SpheroRoot", None))
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.4
    root.location = (1.25, 0, 0.12)  # hover base height
    body = prim(bpy.ops.mesh.primitive_uv_sphere_add, "SpheroBody",
                radius=0.78, segments=32, ring_count=16,
                location=(0, 0, 0))
    body.parent = root
    body.location = (0, 0, 0.95)
    body.data.materials.append(mats['sphero'])
    smooth(body)
    arms = {}
    for nm, sx, ry in (("SpheroArmL", 0.82, 0.5),
                       ("SpheroArmR", -0.82, -0.5)):
        arm = prim(bpy.ops.mesh.primitive_cylinder_add, nm,
                   radius=0.09, depth=0.65, location=(0, 0, 0))
        arm.parent = root
        arm.location = (sx, 0, 0.85)
        arm.rotation_euler = (0, ry, 0)
        arm.data.materials.append(mats['sphero_dark'])
        arms[nm] = arm
    eyes = []
    for nm, sx in (("SpheroEyeL", 0.25), ("SpheroEyeR", -0.25)):
        white = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm,
                     radius=0.17, segments=20, ring_count=12,
                     location=(0, 0, 0))
        white.parent = root
        white.location = (sx, -0.62, 1.18)
        white.data.materials.append(mats['white'])
        smooth(white)
        pup = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm + "Pupil",
                   radius=0.075, segments=12, ring_count=8,
                   location=(0, 0, 0))
        pup.parent = white
        pup.location = (0, -0.14, 0.01)
        pup.data.materials.append(mats['black'])
        smooth(pup)
        eyes.append(white)
    mouth = prim(bpy.ops.mesh.primitive_cube_add, "SpheroMouth",
                 size=1.0, location=(0, 0, 0))
    mouth.parent = root
    mouth.location = (0, -0.72, 0.68)
    mouth.scale = (0.34, 0.05, 0.035)
    mouth.data.materials.append(mats['black'])
    hat = prim(bpy.ops.mesh.primitive_cone_add, "SpheroHat",
               radius1=0.28, radius2=0.02, depth=0.55,
               location=(0, 0, 0))
    hat.parent = root
    hat.location = (0.15, 0, 1.95)
    hat.data.materials.append(mats['hat'])
    smooth(hat)
    pom = prim(bpy.ops.mesh.primitive_uv_sphere_add, "SpheroPom",
               radius=0.09, location=(0, 0, 0))
    pom.parent = root
    pom.location = (0.15, 0, 2.26)
    pom.data.materials.append(mats['pom'])
    smooth(pom)
    ring = prim(bpy.ops.mesh.primitive_torus_add, "SpheroRing",
                major_radius=0.5, minor_radius=0.07, location=(0, 0, 0))
    ring.parent = root
    ring.location = (0, 0, 0.10)
    ring.data.materials.append(mats['cyan_glow'])
    smooth(ring)
    tag = add_text("SPHERO", "SPHERO", size=0.17)
    tag.parent = root
    tag.location = (0, -0.86, 0.92)
    tag.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    tag.data.materials.append(mats['tag_orange'])
    log("sphero built")
    return {"root": root, "mouth": mouth, "arm_r": arms["SpheroArmR"],
            "arm_r_ry": -0.5, "eyes": eyes}


# --------------------------------------------------------------------------
# subtitles via the addon (SRT import) + branches
# --------------------------------------------------------------------------

def build_subtitles(scene, cam, mats, cues, cps):
    bpy.ops.object.text_add(location=(0, 0, 0))
    sub = bpy.context.active_object
    sub.name = "Subtitles"
    try:
        sub.data.name = "Subtitles"
    except Exception:
        pass
    sub.data.align_x = 'CENTER'
    sub.data.align_y = 'CENTER'
    sub.data.size = 0.10
    try:
        sub.data.extrude = 0.008
    except Exception:
        pass
    try:
        sub.data.bevel_depth = 0.0015
    except Exception:
        pass
    try:
        sub.data.resolution_u = 3
    except Exception:
        pass
    sub.parent = cam
    sub.location = (0, -0.44, -3.0)
    sub.data.materials.append(mats['subtitle'])
    sub.tw_enabled = True
    sub.tw_cps = cps
    sub.tw_reveal = 'LINEAR'
    srt = os.path.join(HERE, "dialogue.srt")
    n = tw._apply_subtitle_file(sub, srt, scene, replace=True,
                                insert_clears=False)
    log(f"imported {n} subtitle cues from dialogue.srt")
    # the addon's flat import and the branch parser must agree cue by cue
    # (frames + order); speakers/branches come from the parser.
    entries = sorted(sub.tw_entries, key=lambda e: e.frame)
    disp = sb.ordered_cues(cues)
    if len(entries) != len(disp):
        raise RuntimeError("SRT/addon cue mismatch: %d entries vs %d parsed"
                           % (len(entries), len(disp)))
    cue_info = []
    for e, (num, start, _end, _text, speakers) in zip(entries, disp):
        want = scene.frame_start + round(start * FPS)
        if e.frame != want:
            raise RuntimeError("cue misaligned: entry f%d vs parsed f%d (%r)"
                               % (e.frame, want, _text[:24]))
        cue_info.append((e.frame, tuple(speakers)))
        log(f"  cue {num} f{e.frame}: {e.text.splitlines()[0][:30]!r}")
    n_baked = tw.bake_typewriter(sub, scene)
    if n_baked != len(sub.tw_entries):
        raise RuntimeError("subtitle bake wrote %d of %d objects"
                           % (n_baked, len(sub.tw_entries)))
    log("baked %d subtitle objects (live typing off)" % n_baked)
    return sub, cue_info


def build_menu(scene, cam, mats, story, cues):
    choices = story.get("choices", {})
    menu = add_text("ChoiceMenu", "", size=0.06)
    menu.parent = cam
    menu.location = (0, 0.30, -3.0)
    menu.data.materials.append(mats['menu'])
    if choices:
        menu.data.body = sb.menu_body(
            next(iter(choices.values()))["options"])
    # Hidden via scale keys, NOT hide_render/hide_viewport: UPBGE skips
    # hide_render objects in game conversion entirely ("not in the same
    # layer ... will not be converted"). The driver resets scale in-game.
    menu.scale = (0.0, 0.0, 0.0)
    menu.keyframe_insert("scale", frame=FRAME_START)
    for ch in choices.values():
        p = cues[ch["prompt_cue"]]
        f0 = scene.frame_start + round(p["start"] * FPS)
        f1 = scene.frame_start + round(p["end"] * FPS)
        for f, s in ((f0, 1.0), (f1, 0.0)):
            menu.scale = (s, s, s)
            menu.keyframe_insert("scale", frame=f)
    ad = menu.animation_data
    if ad is not None and ad.action is not None:
        ad.action.name = "ChoiceMenu_anim"
        for fc in tw._action_fcurves(ad.action):
            if fc.data_path == "scale":
                for k in fc.keyframe_points:
                    k.interpolation = 'CONSTANT'
                fc.update()
    log("choice menu done (%d choice(s))" % len(choices))
    return menu


def build_markers(scene, story, cues):
    marks = {}
    for name, f in sb.marker_frames(story, cues, FPS,
                                    FRAME_START).items():
        marks.setdefault(f, []).append(name)
    for f in sorted(marks):
        scene.timeline_markers.new("+".join(marks[f]), frame=f)
    log("markers done: %s"
        % ", ".join("%s@%d" % (n, f) for f in sorted(marks)
                    for n in marks[f]))


# --------------------------------------------------------------------------
# animation
# --------------------------------------------------------------------------

def set_mouth(mouth, is_open, f):
    if is_open:
        mouth.scale = (0.30, 0.05, 0.16)
    else:
        mouth.scale = (0.34, 0.05, 0.035)
    mouth.keyframe_insert("scale", frame=f)


def animate(scene, rig_c, rig_s, cue_info):
    TAU = 2.0 * math.pi

    def speakers_at(f):
        idx = 0
        for i, (s, _sp) in enumerate(cue_info):
            if s <= f:
                idx = i
        return idx, cue_info[idx][1]

    for f in range(FRAME_START, FRAME_END + 1):
        t = (f - 1) / FPS
        idx, sp = speakers_at(f)
        c_talk = "CUBY" in sp
        s_talk = "SPHERO" in sp
        since = f - cue_info[idx][0]
        # roots
        for rig, base_z, phase, talking in (
                (rig_c, 0.0, 0.0, c_talk),
                (rig_s, 0.12, 2.1, s_talk)):
            root = rig["root"]
            bob = 0.05 * math.sin(TAU * 1.2 * t + phase)
            if talking:
                bob += 0.03 * math.sin(TAU * 3.0 * t)
                if since <= 12:
                    bob += math.sin(math.pi * since / 12.0) * 0.16
            root.location = (root.location.x, root.location.y,
                             base_z + bob)
            pop = 1.0
            if talking and since <= 8:
                pop += math.sin(math.pi * since / 8.0) * 0.06
            root.scale = (pop, pop, pop)
            root.keyframe_insert("location", frame=f)
            root.keyframe_insert("scale", frame=f)
        # mouths
        flap_c = 0.5 + 0.5 * math.sin(TAU * 7.0 * t)
        flap_s = 0.5 + 0.5 * math.sin(TAU * 6.3 * t + 1.3)
        set_mouth(rig_c["mouth"], c_talk and flap_c > 0.45, f)
        set_mouth(rig_s["mouth"], s_talk and flap_s > 0.45, f)
        # waving arms
        for rig, talking in ((rig_c, c_talk), (rig_s, s_talk)):
            swing = 0.7 * math.sin(TAU * 3.0 * t) if talking else 0.0
            rig["arm_r"].rotation_euler = (swing, rig["arm_r_ry"], 0.0)
            rig["arm_r"].keyframe_insert("rotation_euler", frame=f)
        # blinking (offset per character)
        for eyes, off in ((rig_c["eyes"], 0), (rig_s["eyes"], 40)):
            blinking = ((f + off) % 84) < 3
            for w in eyes:
                w.scale = (1.0, 1.0, 0.12 if blinking else 1.0)
                w.keyframe_insert("scale", frame=f)
        if f % 60 == 0:
            log(f"animated frame {f}/{FRAME_END}")
    for o in (rig_c["root"], rig_s["root"], rig_c["mouth"],
              rig_s["mouth"], rig_c["arm_r"], rig_s["arm_r"],
              *rig_c["eyes"], *rig_s["eyes"]):
        ad = o.animation_data
        if ad is not None and ad.action is not None:
            ad.action.name = o.name + "_anim"
    log("animation done")


# --------------------------------------------------------------------------
# UPBGE game logic
# --------------------------------------------------------------------------

def build_game():
    try:
        log("game: add empty")
        bpy.ops.object.empty_add(location=(0, 0, 0))
        d = bpy.context.active_object
        d.name = "GameDirector"
        log("game: add sensor")
        try:
            bpy.ops.logic.sensor_add(type='ALWAYS', name='Always',
                                     object='GameDirector')
        except TypeError:
            bpy.context.view_layer.objects.active = d
            d.select_set(True)
            bpy.ops.logic.sensor_add(type='ALWAYS', name='Always')
        log("game: add controller")
        try:
            bpy.ops.logic.controller_add(type='PYTHON', name='Dialogue',
                                         object='GameDirector')
        except TypeError:
            bpy.context.view_layer.objects.active = d
            d.select_set(True)
            bpy.ops.logic.controller_add(type='PYTHON', name='Dialogue')
        log("game: configure bricks")
        sens = d.game.sensors['Always']
        if hasattr(sens, 'use_pulse_true_level'):
            sens.use_pulse_true_level = True
        ctrl = d.game.controllers['Dialogue']
        ctrl.mode = 'MODULE'
        ctrl.module = 'game_subtitles.update'
        try:
            sens.link(ctrl)
        except Exception as ex:
            log(f"sensor link note: {ex}")
        with open(os.path.join(HERE, 'game_subtitles.py'),
                  encoding='utf-8') as fh:
            code = fh.read()
        txt = bpy.data.texts.new('game_subtitles.py')
        txt.from_string(code)
        log("game: dialogue data property")
        prev = bpy.context.view_layer.objects.active
        try:
            targets = []
            for oname, pname in (("Subtitles", "Text"),
                                 ("ChoiceMenu", "Text"),
                                 ("GameDirector", "tw_story")):
                o = bpy.data.objects.get(oname)
                if o is not None and o.game.properties.get(pname) is None:
                    targets.append((o, pname))
            # the op refuses hidden objects (the menu hides at frame 1)
            saved_hide = [(o, o.hide_viewport, o.hide_render)
                          for (o, _p) in targets]
            try:
                for (o, _p) in targets:
                    o.hide_viewport = False
                    o.hide_render = False
                for (o, pname) in targets:
                    bpy.context.view_layer.objects.active = o
                    bpy.ops.object.game_property_new(type='STRING',
                                                     name=pname)
            finally:
                for (o, hv, hr) in saved_hide:
                    o.hide_viewport = hv
                    o.hide_render = hr
            gp = d.game.properties["tw_story"]
            gp.value = "//story.yml"
            log("game: tw_story points at %s" % gp.value)
        finally:
            try:
                bpy.context.view_layer.objects.active = prev
            except Exception:
                pass
        log("game logic bricks + embedded script installed "
            f"(sensors={len(d.game.sensors)}, controllers="
            f"{len(d.game.controllers)})")
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        log("GAME SETUP FAILED (timeline animation still works)")
        return False


def embed_readme():
    txt = bpy.data.texts.new('README')
    txt.from_string(
        "TALKING ROBOTS - 3D typewriter subtitles demo (story sets + actors)\n"
        "=================================================================\n\n"
        "1) Install the addon: Edit > Preferences > Add-ons > Install...\n"
        "   pick typewriter_subtitles.py, enable 'Typewriter Subtitles'.\n\n"
        "2) TIMELINE MODE: press Spacebar. Subtitles type live on the\n"
        "   baked per-cue text objects (no addon needed); the\n"
        "   camera plays the baked Camera_anim action. Markers\n"
        "   SET-main/SET-cuby/SET-sphero/CH-pick mark the story sets -\n"
        "   select 'Subtitles' > Sidebar (N) > Subtitles > 'Jump to\n"
        "   Set' to preview each one. Edit dialogue.srt externally,\n"
        "   then Import/Reload it and press Bake to Objects.\n\n"
        "3) GAME MODE: press P. The driver reads story.yml +\n"
        "   dialogue.srt live, plays each actor's action, types the\n"
        "   lines. Choose with Up/Down+Enter (or 1/2), R restarts,\n"
        "   ESC quits. game_debug.log (next to the .blend) records\n"
        "   sets, choices and actors - proof the logic ran.\n\n"
        "4) Render: F12 still / Ctrl+F12 animation (PNG sequence in\n"
        "   ./render/). Subtitles are baked keys, so renders need no addon.\n\n"
        "Files next to this .blend: story.yml (sets/actors/choices),\n"
        "dialogue.srt (subtitle text + [CAM] shot plan),\n"
        "game_subtitles.py (external copy of the embedded game script),\n"
        "story.py (story parser), typewriter_subtitles.py (the addon).\n"
        "Tweak keys, retime cues, rewire sets - then press P.\n")


def purge_orphans():
    for coll in (bpy.data.materials, bpy.data.meshes, bpy.data.curves,
                 bpy.data.lights, bpy.data.cameras, bpy.data.worlds):
        for x in list(coll):
            try:
                if x.users == 0:
                    coll.remove(x)
            except Exception:
                pass


def main():
    global FRAME_END
    if not hasattr(bpy.types.Object, "tw_entries"):
        tw.register()
    log("addon active: v%s"
        % ".".join(str(x) for x in tw.bl_info.get("version", ())))
    loaded = sb.load_story_files(os.path.join(HERE, "story.yml"))
    if loaded["errors"]:
        raise RuntimeError("story errors:\n- "
                           + "\n- ".join(loaded["errors"]))
    for w in loaded["warnings"]:
        log("story warning: " + w)
    story, cues, cams = loaded["story"], loaded["cues"], loaded["cams"]
    last_end = max(c["end"] for c in cues.values())
    FRAME_END = int(round(last_end * FPS)) + FPS
    rng_errs = sb.validate_story(story, cues, FRAME_START, FRAME_END)
    if rng_errs:
        raise RuntimeError("story errors:\n- " + "\n- ".join(rng_errs))
    log("story: %d cues, sets %s; frames %d-%d"
        % (len(cues), ", ".join(sorted(story["sets"])), FRAME_START,
           FRAME_END))
    random.seed(7)
    clear_scene()
    setup_scene()
    mats = {
        'cuby': make_mat("CubyBlue", (0.16, 0.42, 0.90), 0.55),
        'cuby_dark': make_mat("CubyDark", (0.10, 0.28, 0.62), 0.6),
        'sphero': make_mat("SpheroOrange", (0.95, 0.48, 0.12), 0.5),
        'sphero_dark': make_mat("SpheroDark", (0.70, 0.33, 0.08), 0.6),
        'white': make_mat("EyeWhite", (0.95, 0.95, 0.97), 0.35),
        'black': make_mat("Black", (0.01, 0.01, 0.015), 0.5),
        'dark': make_mat("DarkMetal", (0.03, 0.03, 0.04), 0.4),
        'red_glow': make_mat("RedGlow", (0.05, 0.0, 0.0), 0.5,
                             (1.0, 0.1, 0.1), 3.0),
        'cyan_glow': make_mat("CyanGlow", (0.0, 0.05, 0.06), 0.5,
                              (0.2, 0.9, 1.0), 2.5),
        'hat': make_mat("HatPurple", (0.55, 0.2, 0.8), 0.6),
        'pom': make_mat("PomYellow", (1.0, 0.85, 0.2), 0.6),
        'ground': make_mat("Ground", (0.05, 0.055, 0.08), 0.95),
        'stage': make_mat("Stage", (0.16, 0.14, 0.22), 0.8),
        'beam': make_mat("Beam", (0.05, 0.045, 0.03), 0.9,
                         (1.0, 0.85, 0.55), 0.8),
        'rock': make_mat("Rock", (0.25, 0.26, 0.30), 0.95),
        'subtitle': make_mat("SubtitleMat", (1.0, 1.0, 1.0), 0.5,
                             (1.0, 1.0, 1.0), 0.35),
        'menu': make_mat("MenuMat", (0.75, 1.0, 1.0), 0.5,
                         (0.5, 1.0, 1.0), 0.8),
        'tag_blue': make_mat("TagBlue", (0.7, 0.85, 1.0), 0.5,
                             (0.3, 0.55, 1.0), 0.8),
        'tag_orange': make_mat("TagOrange", (1.0, 0.85, 0.6), 0.5,
                               (1.0, 0.55, 0.15), 0.8),
    }
    scene = bpy.context.scene
    cam = build_lights_camera(cues, cams)
    build_set(mats)
    rig_c = build_cuby(mats)
    rig_s = build_sphero(mats)
    sub, cue_info = build_subtitles(scene, cam, mats, cues,
                                      story.get("cps", CPS))
    build_menu(scene, cam, mats, story, cues)
    build_markers(scene, story, cues)
    animate(scene, rig_c, rig_s, cue_info)
    log("step: frame reset")
    scene.frame_set(FRAME_START)
    bpy.context.view_layer.update()
    log("step: game")
    game_ok = build_game()
    log("step: readme")
    embed_readme()
    log("step: purge")
    purge_orphans()
    log("step: save")
    out = os.path.join(HERE, "talking_robots.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    log(f"SAVED {out} (game={'OK' if game_ok else 'FAILED'})")
    try:
        if not bpy.app.background:
            bpy.ops.wm.quit_blender()
    except Exception:
        pass


main()
