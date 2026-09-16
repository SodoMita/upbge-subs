#!/usr/bin/env python3
"""Build/refresh the Talking Robots demo scene (v2, animation-centric).

The scene is ONE Blender scene whose frame range belongs to one story set at
a time (set-local timing = "Option B" authoring): every set's motion lives in
its own actions, the story's `action: Obj@Act` references name them, and the
add-on's Preview Set swaps the context. The game (UPBGE, press P) plays the
whole branching story from the same files with no rebuild and no bake.

Idempotent by design: it creates what is missing and never deletes actions,
keyframes or baked text, so running it again on an edited scene only fills
the holes. Re-running it on the factory startup file builds the demo from
scratch.

Run with a UI (logic bricks need a display):
    xvfb-run -a upbge --factory-startup -P build_scene.py   (quits itself)
Saves talking_robots.blend next to this script.
"""
import bpy
import math
import os
import random
import sys
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw
import story as sb

FPS = 24
FRAME_START = 1
FRAME_END = None  # = the start set's length (set-local), set in main()
CPS = 30.0

# Staged shot cameras: the framing rig. Each carries its own lens; the game
# follows them live (no bake), so moving one re-aims the shot for good.
SHOTS = {
    "Wide":   ((-0.15, -6.4, 2.35), (0, 0, 1.05), 50.0),
    "Cuby":   ((-1.9, -3.1, 1.9), (-1.25, 0, 1.05), 55.0),
    "Sphero": ((1.9, -3.1, 1.9), (1.25, 0, 1.15), 45.0),
}

# Rest poses the per-set actions start and end on (the actors' own defaults).
RIG_REST = {
    "CubyRoot":   {"kind": "root", "who": "CUBY",
                   "loc": (-1.25, 0.0, 0.0), "phase": 0.0},
    "SpheroRoot": {"kind": "root", "who": "SPHERO",
                   "loc": (1.25, 0.0, 0.12), "phase": 2.1},
    "CubyMouth":   {"kind": "mouth", "who": "CUBY",
                    "scale": (0.34, 0.05, 0.035)},
    "SpheroMouth": {"kind": "mouth", "who": "SPHERO",
                    "scale": (0.34, 0.05, 0.035)},
    "CubyArmR":   {"kind": "arm", "who": "CUBY", "ry": -0.3},
    "SpheroArmR": {"kind": "arm", "who": "SPHERO", "ry": -0.5},
}


def log(msg):
    print("[BUILD] " + msg, flush=True)


def make_mats():
    """Create-if-missing materials (a re-run must not duplicate them)."""
    spec = {
        'cuby': ("CubyBlue", (0.16, 0.42, 0.90), 0.55, None, 0.0),
        'cuby_dark': ("CubyDark", (0.10, 0.28, 0.62), 0.6, None, 0.0),
        'sphero': ("SpheroOrange", (0.95, 0.48, 0.12), 0.5, None, 0.0),
        'sphero_dark': ("SpheroDark", (0.70, 0.33, 0.08), 0.6, None, 0.0),
        'white': ("EyeWhite", (0.95, 0.95, 0.97), 0.35, None, 0.0),
        'black': ("Black", (0.01, 0.01, 0.015), 0.5, None, 0.0),
        'dark': ("DarkMetal", (0.03, 0.03, 0.04), 0.4, None, 0.0),
        'red_glow': ("RedGlow", (0.05, 0.0, 0.0), 0.5, (1.0, 0.1, 0.1), 3.0),
        'cyan_glow': ("CyanGlow", (0.0, 0.05, 0.06), 0.5, (0.2, 0.9, 1.0),
                      2.5),
        'hat': ("HatPurple", (0.55, 0.2, 0.8), 0.6, None, 0.0),
        'pom': ("PomYellow", (1.0, 0.85, 0.2), 0.6, None, 0.0),
        'ground': ("Ground", (0.05, 0.055, 0.08), 0.95, None, 0.0),
        'stage': ("Stage", (0.16, 0.14, 0.22), 0.8, None, 0.0),
        'beam': ("Beam", (0.05, 0.045, 0.03), 0.9, (1.0, 0.85, 0.55), 0.8),
        'rock': ("Rock", (0.25, 0.26, 0.30), 0.95, None, 0.0),
        'subtitle': ("SubtitleMat", (1.0, 1.0, 1.0), 0.5, (1.0, 1.0, 1.0),
                     0.35),
        'menu': ("MenuMat", (0.75, 1.0, 1.0), 0.5, (0.5, 1.0, 1.0), 0.8),
        'tag_blue': ("TagBlue", (0.7, 0.85, 1.0), 0.5, (0.3, 0.55, 1.0), 0.8),
        'tag_orange': ("TagOrange", (1.0, 0.85, 0.6), 0.5,
                       (1.0, 0.55, 0.15), 0.8),
    }
    out = {}
    for key, (name, col, rough, em, strength) in spec.items():
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = make_mat(name, col, rough, em, strength)
        out[key] = mat
    return out


def clear_factory_scene():
    """Only for a first build: drop Blender's startup Cube/Camera/Light.

    Never called on a scene that already carries the demo (any object with a
    `tw_backup`/story marker), so re-runs cannot destroy work.
    """
    storyish = any(o.name in ("GameDirector", "Subtitles", "CubyRoot")
                   for o in bpy.data.objects)
    if storyish:
        log("scene already holds the demo: additive refresh only")
        return False
    for o in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            pass
    log("factory startup objects cleared")
    return True

# --------------------------------------------------------------------------
# small helpers (unchanged from v1: primitives, materials, text)
# --------------------------------------------------------------------------

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


def setup_scene(story, start, plan, fps):
    """Scene settings + set-local frame range (no master timeline any more)."""
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
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    scene.frame_start = FRAME_START
    scene.frame_end = FRAME_END
    scene.render.filepath = os.path.join(HERE, "render", "frame_")
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.012, 0.018, 0.045, 1.0)
        bg.inputs[1].default_value = 1.0
    log("scene: %d fps, frames %d-%d (the '%s' set)"
        % (fps, FRAME_START, FRAME_END, start))


def build_lights():
    for name, loc, energy, shadow, tilt in (
            ("KeySun", (4, -3, 6), 4.0, True, None),
            ("FillSun", (-5, 2, 4), 0.8, False, None)):
        if bpy.data.objects.get(name) is not None:
            continue
        bpy.ops.object.light_add(type='SUN', location=loc)
        s = bpy.context.active_object
        s.name = name
        look_at(s, (0, 0, 1))
        try:
            s.data.energy = energy
            s.data.use_shadow = shadow
        except Exception:
            pass
    log("lights ready")


# --------------------------------------------------------------------------
# cameras: staged shots carry the framing + lens, the render cam is plain
# --------------------------------------------------------------------------

def build_cameras():
    """Staged shot cameras (each with its own lens) + the render camera.

    v2 bakes NOTHING onto the render camera: the game driver moves it live
    between the staged shots (smoothstep + slerp, lens followed), and the
    add-on's Preview Set snaps it to the set's opening shot. So re-aiming a
    shot is a matter of moving a camera in the viewport and pressing P.
    """
    made = []
    for name, (loc, tgt, lens) in SHOTS.items():
        cam = bpy.data.objects.get(name)
        if cam is None:
            bpy.ops.object.camera_add(location=loc)
            cam = bpy.context.active_object
            cam.name = name
            made.append(name)
        cam.data.lens = lens
        look_at(cam, tgt)
    main = bpy.data.objects.get("Camera")
    if main is None:
        bpy.ops.object.camera_add(location=SHOTS["Wide"][1])
        main = bpy.context.active_object
        main.name = "Camera"
        look_at(main, (0, 0, 1.0))
    bpy.context.scene.camera = main
    bpy.context.view_layer.update()
    log("cameras: %s (+ render Camera)%s"
        % (", ".join(sorted(SHOTS)), "" if not made else " - created"))
    return main


# --------------------------------------------------------------------------
# per-set starter animation (the set-local half of Option B authoring)
# --------------------------------------------------------------------------

def _slot_of(action, obj):
    for s in list(getattr(action, "slots", []) or []):
        if s.identifier == "OB" + obj.name:
            return s
    slots = list(getattr(action, "slots", []) or [])
    if slots:
        return slots[0]
    return action.slots.new(id_type='OBJECT', name=obj.name)


def _set_spans(plan, fps):
    """[(f0, f1, speakers)] from the set's cues (set-local frames)."""
    out = []
    for (start, end, text) in plan["subs"]:
        out.append((FRAME_START + int(round(start * fps)),
                    FRAME_START + int(round(end * fps)),
                    set(sb.speakers_of(text))))
    return out


def _talking(spans, f, who):
    """Half-open spans, so the set's last frame is already at rest."""
    for (s, e, sp) in spans:
        if s <= f < e and who in sp:
            return True, f - s
    return False, 0


def animate_actor(obj, kind, spans, fps, f_end, rest):
    """Key one actor over [FRAME_START, f_end] in set-local time.

    Rest pose on the first and last frame of every set, so a set can be
    entered or left mid-story without a snap (the driver starts each action
    at its own frame 1 and the camera holds while the next set fades in).
    """
    TAU = 2.0 * math.pi
    for f in range(FRAME_START, f_end + 1):
        t = (f - FRAME_START) / fps
        first = f == FRAME_START
        last = f == f_end
        if kind == "root":
            who = rest["who"]
            talk, since = (False, 0) if (first or last) else _talking(
                spans, f, who)
            bob = 0.05 * math.sin(TAU * 1.2 * t + rest["phase"])
            if talk:
                bob += 0.03 * math.sin(TAU * 3.0 * t)
                if 0 < since <= 12:
                    bob += math.sin(math.pi * since / 12.0) * 0.16
            obj.location = (rest["loc"][0], rest["loc"][1],
                            rest["loc"][2] + bob)
            pop = 1.0 + (math.sin(math.pi * since / 8.0) * 0.06
                         if (talk and 0 < since <= 8) else 0.0)
            obj.scale = (pop, pop, pop)
            obj.keyframe_insert("location", frame=f)
            obj.keyframe_insert("scale", frame=f)
        elif kind == "mouth":
            who = rest["who"]
            talk, since = (False, 0) if (first or last) else _talking(
                spans, f, who)
            flap = 0.5 + 0.5 * math.sin(TAU * (7.0 if who == "CUBY"
                                               else 6.3) * t
                                        + (0.0 if who == "CUBY" else 1.3))
            open_now = talk and flap > 0.45
            obj.scale = ((0.30, 0.05, 0.16) if open_now
                         else rest["scale"])
            obj.keyframe_insert("scale", frame=f)
        elif kind == "arm":
            who = rest["who"]
            talk, _since = (False, 0) if (first or last) else _talking(
                spans, f, who)
            swing = 0.7 * math.sin(TAU * 3.0 * t) if talk else 0.0
            obj.rotation_euler = (swing, rest["ry"], 0.0)
            obj.keyframe_insert("rotation_euler", frame=f)
    return f_end - FRAME_START + 1


def build_set_actions(story, plans, rigs):
    """Create (never overwrite) one action per story `action:` reference.

    Names come from story.yml, so a set's bundle is literally the list of
    actions it plays; ranges land in story.sync.json via Refresh Sync.
    """
    made, kept, missing = [], [], []
    fps = FPS
    for name, s in sorted((story.get("sets") or {}).items()):
        plan = plans[name]
        spans = _set_spans(plan, fps)
        f_end = FRAME_START + int(round(sb.set_duration(plan) * fps))
        for (oname, aname, _at, _wait, _dur) in plan["actions"]:
            action = bpy.data.actions.get(aname)
            obj = bpy.data.objects.get(oname)
            if obj is None:
                missing.append("%s (object for '%s')" % (oname, aname))
                continue
            if action is not None and _action_has_keys(action):
                kept.append(aname)
                continue
            if action is None:
                action = bpy.data.actions.new(aname)
            ad = obj.animation_data or obj.animation_data_create()
            ad.action = action
            slot = _slot_of(action, obj)
            if slot is not None:
                ad.action_slot = slot
            kind = _actor_kind(oname)
            n = animate_actor(obj, kind, spans, fps, f_end,
                              rigs[oname]["rest"])
            action.use_fake_user = True
            made.append("%s [%d..%d] %d keys" % (aname, FRAME_START, f_end,
                                                 n))
    log("actions: %d made, %d kept%s" % (len(made), len(kept),
                                         "" if not missing else
                                         ", %d missing" % len(missing)))
    for m in made:
        log("   + " + m)
    for m in kept:
        log("   = %s (keys kept)" % m)
    for m in missing:
        log("   ! " + m)
    return made, kept, missing


def _action_has_keys(action):
    for fc in tw._action_fcurves(action):
        if len(fc.keyframe_points):
            return True
    return False


def _actor_kind(obj_name):
    low = obj_name.lower()
    if low.endswith("root"):
        return "root"
    if "mouth" in low:
        return "mouth"
    return "arm"

# --------------------------------------------------------------------------
# subtitle + menu objects (v2: plain, driven by the preview / the game)
# --------------------------------------------------------------------------

def build_text_objects(cam, mats, story):
    """`Subtitles` (typed by the preview and the game) + `ChoiceMenu`.

    No cues are imported here: the frame range and the lines belong to the
    previewed set, and main() previews the start set at the end (which also
    bakes one text object per cue, so renders need no add-on).
    """
    cps = float(story.get("cps", CPS) or CPS)
    sub = bpy.data.objects.get("Subtitles")
    if sub is None:
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
    for attr, val in (("extrude", 0.008), ("bevel_depth", 0.0015),
                      ("resolution_u", 3)):
        try:
            setattr(sub.data, attr, val)
        except Exception:
            pass
    if not sub.data.materials:
        sub.data.materials.append(mats['subtitle'])
    sub.parent = cam
    sub.location = (0, -0.44, -3.0)
    sub.tw_enabled = True
    sub.tw_cps = cps
    sub.tw_reveal = 'LINEAR'
    menu = bpy.data.objects.get("ChoiceMenu")
    if menu is None:
        menu = add_text("ChoiceMenu", "", size=0.06)
    menu.parent = cam
    menu.location = (0, 0.30, -3.0)
    if not menu.data.materials:
        menu.data.materials.append(mats['menu'])
    # NO scale/visibility keys: the menu is a plain object the driver shows
    # by writing its text (and hides by writing ""), and never hide_render
    # (UPBGE would skip the object at game conversion entirely).
    menu.scale = (1.0, 1.0, 1.0)
    menu.data.body = ""
    log("text objects ready (Subtitles, ChoiceMenu)")
    return sub, menu


# --------------------------------------------------------------------------
# UPBGE logic bricks (create-if-missing, so re-running is safe)
# --------------------------------------------------------------------------

def _prop(obj, name):
    return obj.game.properties.get(name)


def build_game():
    try:
        d = bpy.data.objects.get("GameDirector")
        if d is None:
            bpy.ops.object.empty_add(location=(0, 0, 0))
            d = bpy.context.active_object
            d.name = "GameDirector"
        sens = d.game.sensors.get("Always")
        if sens is None:
            try:
                bpy.ops.logic.sensor_add(type='ALWAYS', name='Always',
                                         object='GameDirector')
            except TypeError:
                bpy.context.view_layer.objects.active = d
                d.select_set(True)
                bpy.ops.logic.sensor_add(type='ALWAYS', name='Always')
            sens = d.game.sensors['Always']
        if hasattr(sens, 'use_pulse_true_level'):
            sens.use_pulse_true_level = True
        ctrl = d.game.controllers.get("Dialogue")
        if ctrl is None:
            try:
                bpy.ops.logic.controller_add(type='PYTHON', name='Dialogue',
                                             object='GameDirector')
            except TypeError:
                bpy.context.view_layer.objects.active = d
                d.select_set(True)
                bpy.ops.logic.controller_add(type='PYTHON',
                                             name='Dialogue')
            ctrl = d.game.controllers['Dialogue']
        ctrl.mode = 'MODULE'
        ctrl.module = 'game_subtitles.update'
        if ctrl not in list(sens.controllers):
            try:
                sens.link(ctrl)
            except Exception as ex:
                log("sensor link note: %s" % ex)
        code = open(os.path.join(HERE, 'game_subtitles.py'),
                    encoding='utf-8').read()
        txt = bpy.data.texts.get('game_subtitles.py')
        if txt is None:
            txt = bpy.data.texts.new('game_subtitles.py')
            txt.from_string(code)
        else:
            txt.from_string(code)
        want = [("Subtitles", "Text"), ("ChoiceMenu", "Text"),
                ("GameDirector", "tw_story")]
        prev = bpy.context.view_layer.objects.active
        try:
            for (oname, pname) in want:
                o = bpy.data.objects.get(oname)
                if o is None or _prop(o, pname) is not None:
                    continue
                saved = (o.hide_viewport, o.hide_render)
                o.hide_viewport = o.hide_render = False
                try:
                    bpy.context.view_layer.objects.active = o
                    bpy.ops.object.game_property_new(type='STRING',
                                                      name=pname)
                finally:
                    o.hide_viewport, o.hide_render = saved
                if pname == "tw_story":
                    # NB: only the path lives in a property (they are
                    # length-capped) and only when freshly created - a Text
                    # game property written from bpy corrupts UPBGE 0.50.
                    _prop(o, pname).value = "//story.yml"
        finally:
            try:
                bpy.context.view_layer.objects.active = prev
            except Exception:
                pass
        log("game bricks: Always(pulse) -> Dialogue [MODULE "
            "game_subtitles.update], script embedded, props ready "
            "(sensors=%d controllers=%d)"
            % (len(d.game.sensors), len(d.game.controllers)))
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        log("GAME SETUP FAILED (the timeline preview still works)")
        return False


def embed_readme():
    txt = bpy.data.texts.get('README')
    if txt is None:
        txt = bpy.data.texts.new('README')
    txt.from_string(
        "TALKING ROBOTS - 3D typewriter subtitles + story sets (v2)\n"
        "===========================================================\n"
        "1) Install the add-on: Edit > Preferences > Add-ons > Install...\n"
        "   pick typewriter_subtitles.py, enable 'Typewriter Subtitles'.\n"
        "2) TIMELINE: the scene is aimed at one story set (set-local\n"
        "   frames 1-N). Sidebar (N) > Subtitles > Story sets >\n"
        "   Preview <set> switches the context: that set's cues become the\n"
        "   subtitle lines, its actions go on the actors, its opening shot\n"
        "   frames the camera, its choice fills the menu. Spacebar plays it.\n"
        "3) GAME (press P): the driver reads story.yml + the .srt files\n"
        "   live, plays every set's action bundle from t=0, moves the\n"
        "   camera between the staged shots, and types the lines.\n"
        "   Up/Down + Enter (or 1-9) choose, R restarts, ESC quits.\n"
        "4) After editing keys/lines/cues: Story sets > Refresh Sync (also\n"
        "   runs on save) so story.sync.json keeps the action ranges, then\n"
        "   Bake to Objects per set if you render without the add-on.\n"
        "5) game_debug.log next to the .blend proves the logic ran.\n"
        )

# --------------------------------------------------------------------------
# characters (low effort, maximum charm) - geometry helpers above
# --------------------------------------------------------------------------


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
    if not hasattr(bpy.types.Scene, "tw_preview_set"):
        raise RuntimeError("add-on is too old for the v2 scene "
                           "(need Typewriter Subtitles 1.9.0+)")
    log("add-on v" + ".".join(str(x) for x in tw.bl_info["version"]))
    story_path = os.path.join(HERE, "story.yml")
    loaded = sb.load_story_files(story_path)
    if loaded["errors"]:
        raise RuntimeError("story errors:\n- " + "\n- ".join(loaded["errors"]))
    for w in loaded["warnings"]:
        log("story warning: " + w)
    story, files = loaded["story"], loaded["files"]
    start = str(story["start"])
    side = sb.load_sidecar(sb.sync_path_for(story_path))
    ranges = dict(side["actions"])
    ranges.update(tw.live_action_ranges())
    plans = {n: sb.set_plan(story, files, n, ranges, FPS)
             for n in story["sets"]}
    FRAME_END = FRAME_START + int(round(sb.set_duration(plans[start]) * FPS))
    log("story: %d sets (start '%s'), %d cues in %d files, frames %d-%d"
        % (len(plans), start,
           sum(len(p["subs"]) for p in plans.values()), len(files),
           FRAME_START, FRAME_END))
    random.seed(7)
    fresh = clear_factory_scene()
    setup_scene(story, start, plans[start], FPS)
    mats = make_mats()
    build_lights()
    cam = build_cameras()
    if bpy.data.objects.get("CubyRoot") is None:
        build_set(mats)
        build_cuby(mats)
        build_sphero(mats)
    else:
        log("robots present: geometry kept as-is")
    rigs = {}
    for name, spec in RIG_REST.items():
        o = bpy.data.objects.get(name)
        if o is None:
            raise RuntimeError("story actor '%s' is not in the scene" % name)
        if fresh:                      # rest pose only on a first build
            if spec["kind"] == "root":
                o.location = spec["loc"]
                o.scale = (1.0, 1.0, 1.0)
            elif spec["kind"] == "mouth":
                o.scale = spec["scale"]
            else:
                o.rotation_euler = (0.0, spec["ry"], 0.0)
        rigs[name] = {"rest": spec}
    made, kept, missing = build_set_actions(story, plans, rigs)
    if missing:
        raise RuntimeError("story references missing actors: %s"
                           % ", ".join(missing))
    sub, menu = build_text_objects(cam, mats, story)
    game_ok = build_game()
    embed_readme()
    scene = bpy.context.scene
    ok, msgs = tw.preview_set_impl(scene, start)
    for m in msgs:
        log("preview '%s': %s" % (start, m))
    if not ok:
        raise RuntimeError("previewing the start set failed")
    n = tw.bake_typewriter(sub, scene, prefix="%s_Line" % start)
    log("baked %d per-cue text object(s) as %s_Line##" % (n, start))
    rep = tw.refresh_sync_impl(scene)
    log("refresh sync: %s (%d ranges, %d fake-user action(s))"
        % (", ".join(rep["written"]), rep.get("ranges", 0),
           rep.get("fake_users", 0)))
    final = sb.load_sidecar(sb.sync_path_for(story_path))["actions"]
    for name, plan in sorted(plans.items()):
        want = FRAME_START + int(round(sb.set_duration(plan) * FPS))
        for (_o, a, _at, _w, _d) in plan["actions"]:
            got = final.get(a)
            if got != [FRAME_START, want]:
                raise RuntimeError("sync range for '%s' is %r, expected "
                                   "[%d, %d]" % (a, got, FRAME_START, want))
    log("sync ranges verified against the plans")
    check = tw.story_check_impl(scene)
    if check["errors"] or check["bindings"]:
        raise RuntimeError("scene/story check failed: %s"
                           % "; ".join(check["errors"]
                                       + check["bindings"]))
    for w in check["warnings"]:
        log("warning: " + w)
    for coll in (bpy.data.actions,):      # story actions must survive purge
        for a in coll:
            if a.name in {n for _s, p in plans.items()
                          for (_o, n, _a, _w, _d) in p["actions"]}:
                a.use_fake_user = True
    if fresh:
        purge_orphans()
    scene.frame_set(min(30, FRAME_END))
    bpy.context.view_layer.update()
    out = os.path.join(HERE, "talking_robots.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    log("SAVED %s (sets=%d actions_made=%d actions_kept=%d game=%s)"
        % (out, len(plans), len(made), len(kept), "OK" if game_ok else "FAIL"))
    try:
        if not bpy.app.background:
            bpy.ops.wm.quit_blender()
    except Exception:
        pass


main()
