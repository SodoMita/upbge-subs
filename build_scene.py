#!/usr/bin/env python3
"""Build the Talking Robots scene: two low-effort primitive characters with
3D typewriter subtitles (Typewriter Subtitles addon) + UPBGE game logic.

v2 is animation-centric: story.yml wires named SETS of animations that all
play from t=0, so this script builds ONE local-timed starter action per
(set, actor) - main__cuby, cuby__cubymouth, sphero__spheroarm, ... - instead
of v1's single master timeline. The game driver (game_subtitles.py) plays
those actions and moves the camera procedurally between the staged shots, so
there is NO camera bake and NO SET-/CH- timeline markers any more, and the
choice menu is a plain object the game poses (Preview Set poses it too).

ADDITIVE / IDEMPOTENT by design: everything is create-if-missing. A second
run never deletes an action, a keyframe, an object or a material slot, so
hand-tweaked animation survives a rebuild and "build twice" is a supported
operation (verify_game.py checks exactly that).

The build finishes by previewing the story's START set (frame range, actions,
subtitle cues, opening shot, menu), baking that set's subtitles to
`<set>_Line##` objects so the file plays and renders with no add-on at all,
and writing story.sync.json + story.schema.json.

Run with a UI (logic bricks segfault in `blender -b`):
    xvfb-run -a upbge --factory-startup -P build_scene.py   (quits itself)
Saves talking_robots.blend next to this script.
"""
import bpy
import math
import os
import random
import subprocess
import sys
from mathutils import Euler, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw
import story as sb

FPS = 24
FRAME_START = 1
FRAME_END = None  # computed from the start set in main()
CPS = 30.0

# Staging rig: the shots story.yml (`camera: Shot`) and the SRT `[CAM Shot]`
# lines name. These are scene data, not story data, so they live here - each
# staged camera carries its OWN lens and the game follows it live (v1 kept
# them in story.py and baked a Camera_anim; v2 never bakes the camera).
WIDE_A = ((0.0, -7.3, 3.35), (0.0, 0.0, 1.05))
WIDE_B = ((0.0, -6.7, 3.05), (0.0, 0.0, 1.05))
SHOT_POSES = {"Wide": WIDE_A, "WideEnd": WIDE_B,
              "Cuby": ((-1.55, -4.35, 2.05), (-1.25, 0.0, 1.05)),
              "Sphero": ((1.55, -4.35, 2.05), (1.25, 0.0, 1.05))}
SHOT_LENS = {"Wide": 50, "WideEnd": 50, "Cuby": 55, "Sphero": 45}

# Per-actor look: rest pose + how it acts. Rest poses are re-applied on every
# build so a rebuild is deterministic even if an action left the object mid
# gesture (the actions themselves are never touched).
ROOT_BASE = {                     # name -> (rest location, base z, phase)
    "CubyRoot": ((-1.25, 0.0, 0.0), 0.0, 0.0),
    "SpheroRoot": ((1.25, 0.0, 0.12), 0.12, 2.1),
}
MOUTH_REST = {"CubyMouth": (0.34, 0.05, 0.035),
              "SpheroMouth": (0.34, 0.05, 0.035)}
MOUTH_OPEN = (0.30, 0.05, 0.16)
ARM_REST_RY = {"CubyArmR": -0.3, "SpheroArmR": -0.5}
SPEAKER_OF = {"CubyRoot": "CUBY", "CubyMouth": "CUBY", "CubyArmR": "CUBY",
              "SpheroRoot": "SPHERO", "SpheroMouth": "SPHERO",
              "SpheroArmR": "SPHERO"}
FLAP = {"CUBY": (7.0, 0.0), "SPHERO": (6.3, 1.3)}      # Hz, phase
EYES = {"CUBY": ("CubyEyeL", "CubyEyeR"),
        "SPHERO": ("SpheroEyeL", "SpheroEyeR")}
BLINK_PERIOD = 84
BLINK_FRAMES = 3
# The ranges story.sync.json must end up with (the game divides them by fps
# to know how long a blocking action runs). Asserted after the build.
WANT_RANGES = {"main": 361, "cuby": 91, "sphero": 133}

TAU = 2.0 * math.pi


def log(msg):
    print("[BUILD] " + msg, flush=True)


# --------------------------------------------------------------------------
# small helpers (all idempotent: a rebuild reuses what is already there)
# --------------------------------------------------------------------------

def make_mat(name, color, roughness=0.65, emission_color=None,
             emission_strength=0.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
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


def use_mat(obj, mat):
    """Assign a material without stacking slots on a rebuild."""
    slots = obj.data.materials
    for i, m in enumerate(slots):
        if m is not None and m.name == mat.name:
            return i
    slots.append(mat)
    return len(slots) - 1


def link(obj):
    scene = bpy.context.scene
    if obj.name not in scene.collection.objects:
        scene.collection.objects.link(obj)
    return obj


def ensure_empty(name, size=0.4):
    o = bpy.data.objects.get(name)
    if o is not None:
        return o
    o = link(bpy.data.objects.new(name, None))
    o.empty_display_type = 'PLAIN_AXES'
    o.empty_display_size = size
    return o


def prim(op, name, **kwargs):
    """Run a primitive-add op and rename the result - or reuse the object a
    previous build already made (never a second one with a .001 suffix)."""
    o = bpy.data.objects.get(name)
    if o is not None:
        return o
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
    o = bpy.data.objects.get(name)
    if o is not None:
        return o
    bpy.ops.object.text_add(location=(0, 0, 0))
    o = bpy.context.active_object
    o.name = name
    o.data.body = body
    o.data.align_x = 'CENTER'
    o.data.align_y = 'CENTER'
    o.data.size = size
    return o


def ensure_camera(name, lens):
    """A camera object with its own lens (the staged shots carry their framing
    AND their optics - the game follows both live)."""
    o = bpy.data.objects.get(name)
    if o is None:
        cam = bpy.data.cameras.get(name)
        if cam is None:
            cam = bpy.data.cameras.new(name)
        o = link(bpy.data.objects.new(name, cam))
    try:
        o.data.lens = lens
    except Exception:
        pass
    return o


def ensure_action(obj, name, poser):
    """Create the local-timed action `name` on obj, or leave it alone.

    NEVER deletes or rewrites keys: once an action exists its keys belong to
    the user (that is the whole point of "tweak keys, press P, no rebuild"),
    so a rebuild only fills in what is missing. Returns (action, created).
    """
    act = bpy.data.actions.get(name)
    if act is not None:
        return act, False
    ad = obj.animation_data
    if ad is None:
        ad = obj.animation_data_create()
    ad.action = None          # forces a brand-new action (and its own slot)
    poser(obj)
    act = ad.action
    if act is None:
        raise RuntimeError("keying %s produced no action" % name)
    act.name = name
    act.use_fake_user = True   # only assigned while previewed -> purge bait
    return act, True


def apply_rest_poses():
    """Put every actor back on its authored rest pose (no keys involved)."""
    for name, (loc, _bz, _ph) in ROOT_BASE.items():
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        o.location = loc
        o.scale = (1.0, 1.0, 1.0)
    for name, sc in MOUTH_REST.items():
        o = bpy.data.objects.get(name)
        if o is not None:
            o.scale = sc
    for name, ry in ARM_REST_RY.items():
        o = bpy.data.objects.get(name)
        if o is not None:
            o.rotation_euler = (0.0, ry, 0.0)
    for names in EYES.values():
        for name in names:
            o = bpy.data.objects.get(name)
            if o is not None:
                o.scale = (1.0, 1.0, 1.0)


# --------------------------------------------------------------------------
# scene setup
# ------------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# lights + cameras (v2: staged rig only, NO camera bake)
# --------------------------------------------------------------------------

def build_staged_cams():
    """One real camera object per shot: the editor's framing rig.

    v2 never bakes them into a Camera_anim - the game interpolates between
    them procedurally (smoothstep + slerp, lens followed live) and Preview Set
    snaps the render camera onto a set's opening shot. Each keeps its own lens.
    """
    staged = {}
    for name, (loc, tgt) in SHOT_POSES.items():
        c = ensure_camera(name, SHOT_LENS[name])
        c.location = loc
        look_at(c, tgt)
        staged[name] = c
    log("staged %d shot cameras (%s)"
        % (len(staged), ", ".join("%s %dmm" % (n, SHOT_LENS[n])
                                  for n in sorted(staged))))
    return staged


def build_lights_camera():
    """Key/fill suns + the single render camera (subtitles and the menu are
    parented to it, which is why there is only one)."""
    sun = bpy.data.objects.get("KeySun")
    if sun is None:
        bpy.ops.object.light_add(type='SUN', location=(4, -3, 6))
        sun = bpy.context.active_object
        sun.name = "KeySun"
    look_at(sun, (0, 0, 1))
    try:
        sun.data.energy = 4.0
        sun.data.angle = 0.15
    except Exception:
        pass
    fill = bpy.data.objects.get("FillSun")
    if fill is None:
        bpy.ops.object.light_add(type='SUN', location=(-5, 2, 4))
        fill = bpy.context.active_object
        fill.name = "FillSun"
    look_at(fill, (0, 0, 1))
    try:
        fill.data.energy = 0.8
        fill.data.use_shadow = False
    except Exception:
        pass
    cam = ensure_camera("Camera", SHOT_LENS["Wide"])
    try:
        cam.data.clip_start = 0.1
        cam.data.clip_end = 100.0
    except Exception:
        pass
    bpy.context.scene.camera = cam
    cam["_tw_role"] = "camera"
    build_staged_cams()
    bpy.context.view_layer.update()
    # No keys on the render camera: v2's camera is procedural in game and
    # snapped per set in the editor. If a v1 Camera_anim is still assigned it
    # would override both, so report it instead of silently deleting keys.
    ad = cam.animation_data
    if ad is not None and ad.action is not None:
        log("NOTE: '%s' still drives the render camera (v1 bake). v2 moves it "
            "procedurally - unassign it if you want Preview Set to frame."
            % ad.action.name)
    log("lights + render camera done (no camera bake)")
    return cam


def build_set(mats):
    # ground far below
    g = prim(bpy.ops.mesh.primitive_plane_add, "Ground",
             size=40, location=(0, 0, -0.2))
    use_mat(g, mats['ground'])
    # round talk-show stage
    st = prim(bpy.ops.mesh.primitive_cylinder_add, "Stage",
              radius=3.0, depth=0.2, location=(0, 0, -0.099),
              vertices=48)
    use_mat(st, mats['stage'])
    # light beams (fake volumetrics: emission cones)
    for nm, x, tilt in (("BeamL", -1.25, 0.06), ("BeamR", 1.25, -0.06)):
        cone = prim(bpy.ops.mesh.primitive_cone_add, nm,
                    radius1=0.65, radius2=0.18, depth=6.0,
                    location=(x, 1.8, 3.2))
        cone.rotation_euler = (0.0, tilt, 0.0)
        use_mat(cone, mats['beam'])
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
        use_mat(rock, mats['rock'])
    log("set done")


def build_cuby(mats):
    root = ensure_empty("CubyRoot")
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.4
    root.location = (-1.25, 0, 0)
    body = prim(bpy.ops.mesh.primitive_cube_add, "CubyBody",
                size=1.1, location=(0, 0, 0))
    body.parent = root
    body.location = (0, 0, 0.95)
    use_mat(body, mats['cuby'])
    for i, sx in enumerate((-0.28, 0.28)):
        leg = prim(bpy.ops.mesh.primitive_cylinder_add, f"CubyLeg{i}",
                   radius=0.12, depth=0.45, location=(0, 0, 0))
        leg.parent = root
        leg.location = (sx, 0, 0.225)
        use_mat(leg, mats['dark'])
    arms = {}
    for nm, sx, ry in (("CubyArmL", 0.68, 0.3), ("CubyArmR", -0.68, -0.3)):
        arm = prim(bpy.ops.mesh.primitive_cylinder_add, nm,
                   radius=0.09, depth=0.65, location=(0, 0, 0))
        arm.parent = root
        arm.location = (sx, 0, 0.85)
        arm.rotation_euler = (0, ry, 0)
        use_mat(arm, mats['cuby_dark'])
        arms[nm] = arm
    eyes = []
    for nm, sx in (("CubyEyeL", 0.23), ("CubyEyeR", -0.23)):
        white = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm,
                     radius=0.16, segments=20, ring_count=12,
                     location=(0, 0, 0))
        white.parent = root
        white.location = (sx, -0.50, 1.18)
        use_mat(white, mats['white'])
        smooth(white)
        pup = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm + "Pupil",
                   radius=0.07, segments=12, ring_count=8,
                   location=(0, 0, 0))
        pup.parent = white
        pup.location = (0, -0.13, 0.01)
        use_mat(pup, mats['black'])
        smooth(pup)
        eyes.append(white)
    mouth = prim(bpy.ops.mesh.primitive_cube_add, "CubyMouth",
                 size=1.0, location=(0, 0, 0))
    mouth.parent = root
    mouth.location = (0, -0.545, 0.80)
    mouth.scale = (0.34, 0.05, 0.035)
    use_mat(mouth, mats['black'])
    ant = prim(bpy.ops.mesh.primitive_cylinder_add, "CubyAntenna",
               radius=0.03, depth=0.5, location=(0, 0, 0))
    ant.parent = root
    ant.location = (0.3, 0.2, 1.7)
    use_mat(ant, mats['dark'])
    tip = prim(bpy.ops.mesh.primitive_uv_sphere_add, "CubyTip",
               radius=0.08, location=(0, 0, 0))
    tip.parent = root
    tip.location = (0.3, 0.2, 1.98)
    use_mat(tip, mats['red_glow'])
    smooth(tip)
    tag = add_text("CubyTag", "CUBY", size=0.20)
    tag.parent = root
    tag.location = (0, -0.60, 1.02)
    tag.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    use_mat(tag, mats['tag_blue'])
    log("cuby built")
    return {"root": root, "mouth": mouth, "arm_r": arms["CubyArmR"],
            "arm_r_ry": -0.3, "eyes": eyes}


def build_sphero(mats):
    root = ensure_empty("SpheroRoot")
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.4
    root.location = (1.25, 0, 0.12)  # hover base height
    body = prim(bpy.ops.mesh.primitive_uv_sphere_add, "SpheroBody",
                radius=0.78, segments=32, ring_count=16,
                location=(0, 0, 0))
    body.parent = root
    body.location = (0, 0, 0.95)
    use_mat(body, mats['sphero'])
    smooth(body)
    arms = {}
    for nm, sx, ry in (("SpheroArmL", 0.82, 0.5),
                       ("SpheroArmR", -0.82, -0.5)):
        arm = prim(bpy.ops.mesh.primitive_cylinder_add, nm,
                   radius=0.09, depth=0.65, location=(0, 0, 0))
        arm.parent = root
        arm.location = (sx, 0, 0.85)
        arm.rotation_euler = (0, ry, 0)
        use_mat(arm, mats['sphero_dark'])
        arms[nm] = arm
    eyes = []
    for nm, sx in (("SpheroEyeL", 0.25), ("SpheroEyeR", -0.25)):
        white = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm,
                     radius=0.17, segments=20, ring_count=12,
                     location=(0, 0, 0))
        white.parent = root
        white.location = (sx, -0.62, 1.18)
        use_mat(white, mats['white'])
        smooth(white)
        pup = prim(bpy.ops.mesh.primitive_uv_sphere_add, nm + "Pupil",
                   radius=0.075, segments=12, ring_count=8,
                   location=(0, 0, 0))
        pup.parent = white
        pup.location = (0, -0.14, 0.01)
        use_mat(pup, mats['black'])
        smooth(pup)
        eyes.append(white)
    mouth = prim(bpy.ops.mesh.primitive_cube_add, "SpheroMouth",
                 size=1.0, location=(0, 0, 0))
    mouth.parent = root
    mouth.location = (0, -0.72, 0.68)
    mouth.scale = (0.34, 0.05, 0.035)
    use_mat(mouth, mats['black'])
    hat = prim(bpy.ops.mesh.primitive_cone_add, "SpheroHat",
               radius1=0.28, radius2=0.02, depth=0.55,
               location=(0, 0, 0))
    hat.parent = root
    hat.location = (0.15, 0, 1.95)
    use_mat(hat, mats['hat'])
    smooth(hat)
    pom = prim(bpy.ops.mesh.primitive_uv_sphere_add, "SpheroPom",
               radius=0.09, location=(0, 0, 0))
    pom.parent = root
    pom.location = (0.15, 0, 2.26)
    use_mat(pom, mats['pom'])
    smooth(pom)
    ring = prim(bpy.ops.mesh.primitive_torus_add, "SpheroRing",
                major_radius=0.5, minor_radius=0.07, location=(0, 0, 0))
    ring.parent = root
    ring.location = (0, 0, 0.10)
    use_mat(ring, mats['cyan_glow'])
    smooth(ring)
    tag = add_text("SPHERO", "SPHERO", size=0.17)
    tag.parent = root
    tag.location = (0, -0.86, 0.92)
    tag.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    use_mat(tag, mats['tag_orange'])
    log("sphero built")
    return {"root": root, "mouth": mouth, "arm_r": arms["SpheroArmR"],
            "arm_r_ry": -0.5, "eyes": eyes}


# --------------------------------------------------------------------------
# subtitles + choice menu (v2: content arrives via Preview Set, not a master
# timeline import)
# --------------------------------------------------------------------------

def build_subtitles(scene, cam, mats, cps):
    """The subtitle text object: parented to the render camera, tagged, empty.

    v1 imported every cue of dialogue.srt onto one master timeline and baked
    them. v2 sets are local-timed, so the cues of the set being previewed are
    loaded by the add-on (Preview Set) and baked per set as `<set>_Line##`.
    """
    sub = add_text("Subtitles", "", size=0.10)
    try:
        sub.data.name = "Subtitles"
    except Exception:
        pass
    sub.data.align_x = 'CENTER'
    sub.data.align_y = 'CENTER'
    try:
        sub.data.extrude = 0.008
        sub.data.bevel_depth = 0.0015
        sub.data.resolution_u = 3
    except Exception:
        pass
    sub.parent = cam
    sub.matrix_parent_inverse.identity()   # in-place, returns None
    sub.location = (0, -0.44, -3.0)
    sub.rotation_euler = (0, 0, 0)
    sub.scale = (1, 1, 1)
    use_mat(sub, mats['subtitle'])
    sub["_tw_role"] = "subs"
    sub.tw_enabled = True
    sub.tw_cps = cps
    sub.tw_reveal = 'LINEAR'
    sub.tw_source_path = os.path.join(HERE, "dialogue.srt")
    log("subtitle object ready (cps=%s, content comes from Preview Set)" % cps)
    return sub


def build_menu(scene, cam, mats):
    """The choice menu: a PLAIN text object (v2).

    No scale keys any more - the game poses it from story state and Preview
    Set poses it for the timeline. It rests at scale 0 and is never
    hide_render'd: UPBGE skips hide_render objects at game conversion.
    """
    menu = add_text("ChoiceMenu", "", size=0.06)
    menu.parent = cam
    menu.matrix_parent_inverse.identity()   # in-place, returns None
    menu.location = (0, 0.30, -3.0)
    menu.rotation_euler = (0, 0, 0)
    use_mat(menu, mats['menu'])
    menu["_tw_role"] = "menu"
    menu.scale = (0.0, 0.0, 0.0)
    menu.tw_enabled = False      # never let the typing handler touch it
    ad = menu.animation_data
    if ad is not None and ad.action is not None:
        log("NOTE: unassigning the v1 menu action '%s' (the menu is plain in "
            "v2 - keys are not deleted)" % ad.action.name)
        ad.action = None
    log("choice menu done (plain object, scale-0 rest)")
    return menu


# --------------------------------------------------------------------------
# per-set local-timed animation
# --------------------------------------------------------------------------

def cue_state(plan, t):
    """(speakers, seconds since the cue started) at set-local time t."""
    idx = -1
    for i, (st, _en, _tx) in enumerate(plan["subs"]):
        if st <= t + 1e-9:
            idx = i
    if idx < 0:
        return [], 0.0
    st, _en, text = plan["subs"][idx]
    return sb.speakers_of(text), max(0.0, t - st)


def key_root(obj, name, plan, f0, f1):
    """Idle bob + a talk pop on the cue attack (v1's math, set-local time)."""
    rest, base_z, phase = ROOT_BASE[name]

    def poser(o):
        for f in range(f0, f1 + 1):
            t = (f - f0) / FPS
            sp, since_s = cue_state(plan, t)
            talking = SPEAKER_OF[name] in sp
            since = int(round(since_s * FPS))
            bob = 0.05 * math.sin(TAU * 1.2 * t + phase)
            if talking:
                bob += 0.03 * math.sin(TAU * 3.0 * t)
                if since <= 12:
                    bob += math.sin(math.pi * since / 12.0) * 0.16
            o.location = (rest[0], rest[1], base_z + bob)
            pop = 1.0
            if talking and since <= 8:
                pop += math.sin(math.pi * since / 8.0) * 0.06
            o.scale = (pop, pop, pop)
            o.keyframe_insert("location", frame=f)
            o.keyframe_insert("scale", frame=f)
    return poser


def key_mouth(obj, name, plan, f0, f1):
    """Flap while this robot talks (square wave off a sine, as in v1)."""
    hz, phase = FLAP[SPEAKER_OF[name]]
    rest = MOUTH_REST[name]

    def poser(o):
        for f in range(f0, f1 + 1):
            t = (f - f0) / FPS
            sp, _since = cue_state(plan, t)
            talking = SPEAKER_OF[name] in sp
            flap = 0.5 + 0.5 * math.sin(TAU * hz * t + phase)
            o.scale = MOUTH_OPEN if (talking and flap > 0.45) else rest
            o.keyframe_insert("scale", frame=f)
    return poser


def key_arm(obj, name, plan, f0, f1):
    """Wave the right arm while talking."""
    ry = ARM_REST_RY[name]

    def poser(o):
        for f in range(f0, f1 + 1):
            t = (f - f0) / FPS
            sp, _since = cue_state(plan, t)
            talking = SPEAKER_OF[name] in sp
            swing = 0.7 * math.sin(TAU * 3.0 * t) if talking else 0.0
            o.rotation_euler = (swing, ry, 0.0)
            o.keyframe_insert("rotation_euler", frame=f)
    return poser


def key_blink(obj, name, phase_off, f0, f1):
    """Periodic blink - ambience, so it is NOT a story anim (see build_blinks)."""
    def poser(o):
        for f in range(f0, f1 + 1):
            blinking = ((f + phase_off) % BLINK_PERIOD) < BLINK_FRAMES
            o.scale = (1.0, 1.0, 0.12 if blinking else 1.0)
            o.keyframe_insert("scale", frame=f)
    return poser


POSE_FOR = {}
for _n in ROOT_BASE:
    POSE_FOR[_n] = key_root
for _n in MOUTH_REST:
    POSE_FOR[_n] = key_mouth
for _n in ARM_REST_RY:
    POSE_FOR[_n] = key_arm


def set_frame_span(story, files, set_name):
    """(f0, f1) a set occupies: its local timeline, so it always starts at 1.

    Derived from the story (blocking subs + action ranges), never hardcoded:
    main -> 1..361, cuby -> 1..91, sphero -> 1..133.
    """
    plan = sb.set_plan(story, files, set_name, {}, FPS)
    return FRAME_START, FRAME_START + int(round(sb.set_duration(plan) * FPS))


def build_set_actions(story, files, scene):
    """One local-timed starter action per (set, actor) named in story.yml.

    The action list comes FROM story.yml (not from a hardcoded table), so
    adding `- action: CubyHat@main__cubyhat` to a set makes the next build
    create it. Existing actions are left untouched.
    """
    created, kept, unknown = [], [], []
    for set_name in sorted(story.get("sets") or {}):
        f0, f1 = set_frame_span(story, files, set_name)
        plan = sb.set_plan(story, files, set_name, {}, FPS)
        for (obj_name, act_name, at, _wait, _dur) in plan["actions"]:
            obj = scene.objects.get(obj_name)
            if obj is None:
                unknown.append("%s (object missing)" % obj_name)
                continue
            maker = POSE_FOR.get(obj_name)
            if maker is None:
                unknown.append("%s (no pose rule - author it by hand)"
                               % obj_name)
                continue
            act, new = ensure_action(
                obj, act_name, maker(obj, obj_name, plan, f0, f1))
            (created if new else kept).append("%s [%d,%d]"
                                              % (act_name, f0, f1))
        log("set '%s': frames %d-%d" % (set_name, f0, f1))
    log("set actions: %d created, %d kept as-is" % (len(created), len(kept)))
    for u in unknown:
        log("  WARNING: " + u)
    return created, kept, unknown


def build_blinks(story, files):
    """One always-assigned blink action per eye.

    story.yml lists root/mouth/arm per set; blinking is ambience that should
    run in every set, so each eye keeps a single long periodic action of its
    own instead of a per-set one. It plays on the timeline because it stays
    assigned; the game driver only starts actions story.yml names, so this is
    timeline-only charm - exactly as in v1.
    """
    longest = FRAME_START
    for set_name in (story.get("sets") or {}):
        _f0, f1 = set_frame_span(story, files, set_name)
        longest = max(longest, f1)
    made = 0
    for speaker, names in sorted(EYES.items()):
        for i, name in enumerate(names):
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            _act, new = ensure_action(
                obj, "%s_blink" % name,
                key_blink(obj, name, i * 7 + (0 if speaker == "CUBY" else 40),
                          FRAME_START, longest))
            made += 1 if new else 0
    log("blink actions: %d created (span 1-%d, not story anims)"
        % (made, longest))


# --------------------------------------------------------------------------
# UPBGE game logic (idempotent bricks)
# --------------------------------------------------------------------------

GAME_SENSOR = 'Always'
GAME_CTRL = 'Dialogue'
GAME_MODULE = 'game_subtitles.update'
GAME_TEXT = 'game_subtitles.py'
STORY_PROP = 'tw_story'
STORY_VALUE = '//story.yml'
# Game property strings are length-capped in UPBGE, so only PATHS live in
# them - never JSON. And the `Text` property is CREATE-ONLY from bpy: writing
# `prop.value` on a Text game property corrupts UPBGE 0.50 state and segfaults
# later (bisected over ~10 probes). The game driver rewrites those values
# every tick through the KX path, which is safe.
TEXT_PROP = 'Text'
CREATE_ONLY_PROPS = (("Subtitles", TEXT_PROP), ("ChoiceMenu", TEXT_PROP))


def _ensure_brick(op_add, kind, name, obj, **kwargs):
    """Create a logic brick only if it is missing (a rebuild must not stack
    duplicate sensors/controllers)."""
    try:
        coll = obj.game.sensors if kind == 'sensor' else obj.game.controllers
        if coll.get(name) is not None:
            return coll.get(name), False
    except Exception:
        return None, False
    prev = bpy.context.view_layer.objects.active
    try:
        try:
            op_add(object=obj.name, name=name, **kwargs)
        except TypeError:
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            op_add(name=name, **kwargs)
    finally:
        try:
            bpy.context.view_layer.objects.active = prev
        except Exception:
            pass
    try:
        coll = obj.game.sensors if kind == 'sensor' else obj.game.controllers
        return coll.get(name), True
    except Exception:
        return None, True


def _ensure_string_prop(obj, name, value=None):
    """Create a STRING game property; only set `value` when explicitly given
    (never for the create-only Text properties - see the warning above)."""
    try:
        props = obj.game.properties
    except Exception:
        return False
    if props.get(name) is not None:
        if value is not None:
            try:
                props[name].value = value
            except Exception:
                return False
        return True
    prev = bpy.context.view_layer.objects.active
    saved = (obj.hide_viewport, obj.hide_render)
    try:
        # the op refuses hidden objects (the menu rests at scale 0 / hidden)
        obj.hide_viewport = False
        obj.hide_render = False
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.game_property_new(type='STRING', name=name)
        except TypeError:
            before = set(p.name for p in props)
            bpy.ops.object.game_property_new()
            fresh = [p for p in props if p.name not in before]
            if not fresh:
                return False
            try:
                fresh[0].name = name
                fresh[0].type = 'STRING'
            except Exception:
                pass
    except Exception:
        import traceback
        traceback.print_exc()
        return False
    finally:
        obj.hide_viewport, obj.hide_render = saved
        try:
            bpy.context.view_layer.objects.active = prev
        except Exception:
            pass
    if value is not None and props.get(name) is not None:
        try:
            props[name].value = value
        except Exception:
            return False
    return props.get(name) is not None


def build_game():
    """GameDirector: Always(pulse) -> Python(MODULE game_subtitles.update)."""
    try:
        d = bpy.data.objects.get("GameDirector")
        if d is None:
            bpy.ops.object.empty_add(location=(0, 0, 0))
            d = bpy.context.active_object
            d.name = "GameDirector"
            log("game: GameDirector created")
        sens, s_new = _ensure_brick(bpy.ops.logic.sensor_add, 'sensor',
                                    GAME_SENSOR, d, type='ALWAYS')
        if sens is None:
            log("GAME SETUP FAILED: no Always sensor")
            return False
        try:
            if hasattr(sens, 'use_pulse_true_level'):
                sens.use_pulse_true_level = True
        except Exception:
            pass
        ctrl, c_new = _ensure_brick(bpy.ops.logic.controller_add, 'controller',
                                    GAME_CTRL, d, type='PYTHON')
        if ctrl is None:
            log("GAME SETUP FAILED: no Python controller")
            return False
        ctrl.mode = 'MODULE'
        ctrl.module = GAME_MODULE
        try:
            sens.link(ctrl)
        except Exception as ex:
            log("game: sensor link note: %s" % ex)
        # the driver is embedded as a text block so MODULE resolves in-game
        with open(os.path.join(HERE, 'game_subtitles.py'),
                  encoding='utf-8') as fh:
            code = fh.read()
        txt = bpy.data.texts.get(GAME_TEXT)
        if txt is None:
            txt = bpy.data.texts.new(GAME_TEXT)
        if txt.as_string() != code:
            txt.from_string(code)
            log("game: embedded %s refreshed" % GAME_TEXT)
        if not _ensure_string_prop(d, STORY_PROP, STORY_VALUE):
            log("GAME SETUP FAILED: no %s property" % STORY_PROP)
            return False
        for oname, pname in CREATE_ONLY_PROPS:
            o = bpy.data.objects.get(oname)
            if o is None:
                log("game: WARNING %s is missing (no %s property)"
                    % (oname, pname))
                continue
            if not _ensure_string_prop(o, pname, None):   # never write Text
                log("game: WARNING could not create %s.%s" % (oname, pname))
        log("game: bricks ready (sensor %s, controller %s, %s=%s, "
            "create-only Text props on Subtitles/ChoiceMenu)"
            % ("created" if s_new else "reused",
               "created" if c_new else "reused", STORY_PROP, STORY_VALUE))
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        log("GAME SETUP FAILED (timeline animation still works)")
        return False


def embed_readme():
    txt = bpy.data.texts.get('README')
    if txt is None:
        txt = bpy.data.texts.new('README')
    body = (
        "TALKING ROBOTS - 3D typewriter subtitles demo (story sets, v2)\n"
        "==============================================================\n\n"
        "1) Install the addon: Edit > Preferences > Add-ons > Install...\n"
        "   pick typewriter_subtitles.py (v1.9.0), enable it.\n\n"
        "2) TIMELINE MODE: press Spacebar. The file opens on the START set\n"
        "   (main, frames 1-361) with its subtitles baked to main_Line##\n"
        "   objects, so it plays and renders with no addon at all. To work\n"
        "   on another set: select Subtitles > Sidebar (N) > Story Sets >\n"
        "   'Preview Set...' - that assigns the set's actions, loads its\n"
        "   .srt cues, sets the frame range, snaps the camera to the\n"
        "   opening shot and poses the choice menu.\n\n"
        "3) GAME MODE: press P. game_subtitles.py reads story.yml + the\n"
        "   .srt files live, plays each set's actions from t=0, moves the\n"
        "   camera between the staged shots and types the lines. Choose\n"
        "   with Up/Down+Enter (or 1/2), R restarts, ESC quits.\n"
        "   game_debug.log (next to the .blend) records sets and choices.\n\n"
        "4) EDIT: tweak any key, retime any cue, rewire any set - then\n"
        "   press P. No rebuild. After renaming an object or action the\n"
        "   Story panel lists the pending rename; Apply rewrites story.yml.\n"
        "   'Refresh Sync' rewrites story.sync.json + story.schema.json\n"
        "   (also done automatically on save).\n\n"
        "Files next to this .blend: story.yml (sets/anims/choices),\n"
        "story.py (parser), story.sync.json (action ranges + rename map),\n"
        "story.schema.json (editor completion), dialogue.srt / cuby.srt /\n"
        "sphero.srt (per-set subtitle text + [CAM] shots),\n"
        "game_subtitles.py (game driver), typewriter_subtitles.py (addon).\n")
    if txt.as_string() != body:
        txt.from_string(body)


def purge_orphans():
    for coll in (bpy.data.materials, bpy.data.meshes, bpy.data.curves,
                 bpy.data.lights, bpy.data.cameras, bpy.data.worlds):
        for x in list(coll):
            try:
                if x.users == 0:
                    coll.remove(x)
            except Exception:
                pass


# --------------------------------------------------------------------------

def check_ranges(scene):
    """Assert the build produced exactly the action ranges the story expects.

    The game divides a blocking action's range by fps to know when a set ends,
    so a wrong range is a wrong story - fail the build rather than ship it.
    """
    sp = os.path.join(HERE, "story.yml")
    side = sb.load_sidecar(sb.sync_path_for(sp))
    bad = []
    for set_name, want_f1 in sorted(WANT_RANGES.items()):
        for name, rng in sorted(side["actions"].items()):
            if not name.startswith(set_name + "__"):
                continue
            if list(rng) != [FRAME_START, want_f1]:
                bad.append("%s = %s (want [1, %d])" % (name, rng, want_f1))
    if bad:
        raise RuntimeError("action ranges are wrong:\n  " + "\n  ".join(bad))
    log("action ranges verified: %s"
        % ", ".join("%s__* [1,%d]" % (s, f)
                    for s, f in sorted(WANT_RANGES.items())))
    return side


def main():
    global FRAME_END
    if not hasattr(bpy.types.Object, "tw_entries"):
        tw.register()
    log("addon active: v%s"
        % ".".join(str(x) for x in tw.bl_info.get("version", ())))
    story_path = os.path.join(HERE, "story.yml")
    loaded = sb.load_story_files(story_path)
    if loaded["errors"]:
        raise RuntimeError("story errors:\n- " + "\n- ".join(loaded["errors"]))
    for w in loaded["warnings"]:
        log("story warning: " + w)
    story, files = loaded["story"], loaded["files"]
    start = story.get("start")
    # ADDITIVE: a rebuild tops up the existing file instead of starting from a
    # blank factory scene, so hand-tweaked keys and stamped uids survive.
    # TW_FRESH=1 forces a from-scratch regeneration instead.
    #
    # The file has to be loaded as a command-line ARGUMENT, not with
    # bpy.ops.wm.open_mainfile(): on UPBGE 0.50 that call inside a UI session
    # segfaults at teardown (probed - open + quit with nothing else in between
    # exits 139, and game_property_new after it hangs), while the same file
    # passed as an argument builds, saves and quits cleanly. So re-run.
    blend = os.path.abspath(os.path.join(HERE, "talking_robots.blend"))
    loaded_ok = (os.path.abspath(bpy.data.filepath or "") == blend)
    if (os.path.isfile(blend) and not loaded_ok
            and not os.environ.get("TW_FRESH")
            and not os.environ.get("TW_BUILD_CHILD")):
        log("additive build: re-running with %s loaded"
            % os.path.basename(blend))
        env = dict(os.environ, TW_BUILD_CHILD="1")
        # bpy.app.binary_path, NOT sys.executable: in UPBGE the latter is the
        # bundled python3.11, which would try to run the .blend as a script
        exe = bpy.app.binary_path or sys.argv[0]
        rc = subprocess.call([exe, blend, "--python",
                              os.path.abspath(__file__)], env=env, cwd=HERE)
        log("additive build finished (exit %d)" % rc)
        if rc != 0:
            os._exit(rc)        # nothing was built here; propagate the failure
        bpy.ops.wm.quit_blender()
        return
    if os.path.isfile(blend) and not loaded_ok:
        log("rebuilding from scratch (%s will be overwritten)"
            % ("TW_FRESH set" if os.environ.get("TW_FRESH")
               else "no existing file"))
    if start not in (story.get("sets") or {}):
        raise RuntimeError("story 'start' names no set: %r" % (start,))
    scene = bpy.context.scene
    # the file opens on the start set; Preview Set at the end sets it exactly
    _f0, FRAME_END = set_frame_span(story, files, start)
    log("story: %d sets (%s), %d choices, start '%s', frames %d-%d"
        % (len(story["sets"]), ", ".join(sorted(story["sets"])),
           len(story.get("choices") or {}), start, FRAME_START, FRAME_END))

    random.seed(7)
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
    cam = build_lights_camera()
    build_set(mats)
    build_cuby(mats)
    build_sphero(mats)
    sub = build_subtitles(scene, cam, mats, story.get("cps", CPS))
    build_menu(scene, cam, mats)
    apply_rest_poses()
    bpy.context.view_layer.update()
    log("step: per-set actions")
    build_set_actions(story, files, scene)
    build_blinks(story, files)
    apply_rest_poses()          # keying left the objects mid-gesture
    log("step: game")
    game_ok = build_game()
    log("step: readme")
    embed_readme()
    log("step: purge")
    purge_orphans()
    log("step: auto-preview the start set")
    baked_here = [o for o in scene.objects if o.get(tw._BAKE_TAG) == start]
    if (scene.tw_preview_set == start and len(sub.tw_entries)
            and len(baked_here) == len(sub.tw_entries)):
        # Already sitting on the start set with a matching bake: leave the
        # frame range, the assigned actions, the loaded cues AND the baked
        # reveal keys alone. Re-previewing would rebuild SubtitlesAction and
        # silently discard hand-retimed subtitle timing.
        log("already previewing set '%s' (%d cue(s), %d baked object(s)) - "
            "kept as-is" % (start, len(sub.tw_entries), len(baked_here)))
    else:
        ok, msg, info = tw.preview_set_impl(scene, start)
        log("preview: %s" % msg)
        if not ok:
            raise RuntimeError("auto-preview failed: %s" % msg)
        # bakes of OTHER sets would double up on screen: the shipped file
        # shows the start set's subtitles only (bakes are derived data)
        for o in [o for o in list(scene.objects)
                  if o.get(tw._BAKE_TAG) not in (None, start)]:
            log("removing stale bake '%s' (set '%s')"
                % (o.name, o.get(tw._BAKE_TAG)))
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.curves.remove(data)   # else an orphan steals the name
        old_bake = [o for o in scene.objects if o.get(tw._BAKE_TAG) == start]
        if old_bake and len(old_bake) == len(sub.tw_entries):
            # a rebuild must not throw away a bake the user re-timed by hand
            log("bake for set '%s' already present (%d object(s)) - kept as-is"
                % (start, len(old_bake)))
        else:
            n_baked = tw.bake_typewriter(sub, scene, prefix="%s_Line" % start,
                                         tag=start)
            if n_baked <= 0:
                raise RuntimeError("baking set '%s' produced no objects"
                                   % start)
            log("baked %d subtitle object(s) as %s_Line##" % (n_baked, start))
    log("step: refresh sync (story.sync.json + story.schema.json)")
    ok, msgs, _info = tw.refresh_sync_impl(scene)
    for m in msgs:
        log("sync: " + m)
    if not ok:
        raise RuntimeError("Refresh Sync failed: %s" % "; ".join(msgs))
    check_ranges(scene)
    errs, warns, binds, summary = tw.validate_impl(scene)
    log("validate: " + summary)
    for e in errs + binds:
        log("  ERROR: " + e)
    for w in warns:
        log("  warning: " + w)
    if errs or binds:
        raise RuntimeError("the built scene does not validate:\n- "
                           + "\n- ".join(errs + binds))
    scene.frame_set(FRAME_START)
    bpy.context.view_layer.update()
    log("step: save")
    out = os.path.join(HERE, "talking_robots.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    log("SAVED %s (game=%s, sets=%d, actions=%d, objects=%d)"
        % (out, "OK" if game_ok else "FAILED", len(story["sets"]),
           len(bpy.data.actions), len(scene.objects)))
    try:
        if not bpy.app.background:
            bpy.ops.wm.quit_blender()
    except Exception:
        pass


if __name__ == "__main__":
    # Blender executes a -P script as __main__, so the build still runs; but
    # resave_game.py / verify_game.py import this module to reuse the brick
    # helpers, and importing must NOT rebuild the scene.
    main()
