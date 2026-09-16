#!/usr/bin/env python3
"""Build newton_laws.blend - the Newton-mechanics example story.

A second, self-contained demo scene for the same engine (story.yml
vocabulary, game_subtitles.py driver, Typewriter Subtitles add-on):
Cuby and Sphero host a physics lab where the three laws are *performed*
by Bullet rigid bodies, triggered by kinematic rig actions:

  law1  inertia        IcePusher sweeps once; the frictionless puck
                       keeps sliding at constant velocity afterwards.
  law2  F = m a        one Hammer stroke pushes a 0.5 kg and a 5 kg
                       ball with the same stroke - the light one flies.
  law3  action/reaction TrapDoor releases a ball onto an elastic pad;
                       the pad pushes back exactly as hard - it bounces.

The moving bodies carry NO action: physics plays them. The tour is a
linear goto chain (a rig is a one-shot performance); R restarts.

Run with a UI (logic bricks need a display), like build_scene.py:
    xvfb-run -a upbge --factory-startup -P build_newton.py
Writes newton_laws.blend + newton.sync.json + newton.schema.json next
to this script. Idempotent: re-running rebuilds rigs/actions it owns
(names prefixed per set) and never touches anything else.
"""
import bpy
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb

FPS = 24
FRAME_START = 1
FRAME_END = 192          # the intro set, set-local (8 s)
SUB_DIST = 0.45
SUB_LOCAL = (0.0, -0.066, -SUB_DIST)
SUB_SIZE = 0.015

SHOTS = {
    "NewtonWide": ((0.0, -10.5, 2.4), (0.0, -2.2, 0.7), 40.0),
    "Law1": ((-5.6, -3.8, 1.1), (-5.6, -0.4, 0.2), 45.0),
    "Law2": ((0.3, -3.4, 1.2), (0.3, -0.3, 0.35), 45.0),
    "Law3": ((5.0, -4.2, 2.3), (5.0, 0.0, 1.3), 45.0),
}

# trigger timings, seconds (set-local); frames derived below
PUSHER_T = 7.0
HAMMER_T = 6.2
DOOR_T = 4.0
TRIG_LEN = 12.0          # trigger actions run 12 s (they block their set)


def log(msg):
    print("[NEWTON] " + msg, flush=True)


def make_mat(name, color, rough=0.6, emit=None):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    b = mat.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = (*color, 1.0)
        b.inputs["Roughness"].default_value = rough
        if emit:
            b.inputs["Emission Color"].default_value = (*emit, 1.0)
            b.inputs["Emission Strength"].default_value = 2.0
    return mat


def link(obj):
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    return obj


def prim(op, name, mat, **kw):
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    getattr(bpy.ops.mesh, op)(**kw)
    o = bpy.context.active_object
    o.name = name
    o.data.materials.clear()
    o.data.materials.append(mat)
    return o


def look_at(obj, target):
    from mathutils import Vector
    d = Vector(target) - obj.location
    obj.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


def phys(o, ptype, mass=1.0, friction=0.5, elasticity=0.2, bounds=None,
         ccd=0.0):
    g = o.game
    g.physics_type = ptype
    g.mass = mass
    g.friction = friction
    g.elasticity = elasticity
    if bounds:
        g.use_collision_bounds = True
        g.collision_bounds_type = bounds
    if ccd:  # continuous collision: slow software frames take big
        g.use_ccd_rigid_body = True  # physics steps; CCD stops tunneling
        g.ccd_swept_sphere_radius = ccd
        g.ccd_motion_threshold = 0.0


def clear_factory_scene():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.actions,
                 bpy.data.cameras, bpy.data.curves):
        for b in list(coll):
            if b.users == 0:
                coll.remove(b)


# --------------------------------------------------------------------------
# actions (set-local frames; Blender 5 layered actions + slot binding)
# --------------------------------------------------------------------------

def _new_action(name, obj):
    act = bpy.data.actions.get(name)
    if act:
        bpy.data.actions.remove(act)
    act = bpy.data.actions.new(name)
    ad = obj.animation_data_create()
    ad.action = act
    slot = None
    try:
        suitable = [s for s in act.slots
                    if s.identifier == "OB" + obj.name]
        if suitable:
            slot = suitable[0]
    except Exception:
        slot = None
    if slot is None:
        slot = act.slots.new(id_type='OBJECT', name=obj.name)
    ad.action_slot = slot
    return act


def kf(obj, path, frame):
    obj.keyframe_insert(path, frame=frame)


def set_interp(action, interp):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in bag.fcurves:
                    for kp in fc.keyframe_points:
                        kp.interpolation = interp


def build_trigger_actions():
    """Kinematic one-shot rigs: hold, stroke, hold (set-local)."""
    made = []
    f3 = int(TRIG_LEN * FPS)

    pusher = bpy.data.objects["IcePusher"]
    act = _new_action("law1__pusher", pusher)
    f1, f2 = int(PUSHER_T * FPS), int(PUSHER_T * FPS) + 12
    for f, x in ((1, -6.6), (f1, -6.6), (f2, -6.3), (f3, -6.3)):
        pusher.location = (x, 0.0, 0.35)
        kf(pusher, "location", f)
    set_interp(act, 'LINEAR')
    made.append(act)

    shelf = bpy.data.objects["Hammer"]
    act = _new_action("law2__hammer", shelf)
    f1, f2 = int(HAMMER_T * FPS), int(HAMMER_T * FPS) + 12
    for f, y in ((1, -0.5), (f1, -0.5), (f2, 0.9), (f3, 0.9)):
        shelf.location = (0.3, y, 1.02)
        kf(shelf, "location", f)
    set_interp(act, 'LINEAR')
    made.append(act)
    pad = bpy.data.objects["BouncePad"]
    act = _new_action("law3__padflick", pad)
    for f, z in ((1, 0.04), (118, 0.04), (122, 0.45), (128, 0.45),
                 (180, 0.04), (288, 0.04)):
        pad.location = (5.0, 0.0, z)
        kf(pad, "location", f)
    set_interp(act, 'LINEAR')
    made.append(act)

    door = bpy.data.objects["TrapDoor"]
    act = _new_action("law3__door", door)
    f1, f2 = int(DOOR_T * FPS), int(DOOR_T * FPS) + 8
    for f, x in ((1, 5.0), (f1, 5.0), (f2, 6.8), (f3, 6.8)):
        door.location = (x, 0.0, 2.2)
        kf(door, "location", f)
    set_interp(act, 'LINEAR')
    made.append(act)
    return made


def build_robot_actions(story, files):
    """Root bob for intro/outro + per-set mouth chatter."""
    made = []
    rigs = {
        "CubyRoot": ((-1.25, -3.4, 0.55), 0.0),
        "SpheroRoot": ((1.25, -3.4, 0.62), 2.1),
    }
    for name, (loc, phase) in rigs.items():
        obj = bpy.data.objects[name]
        act = _new_action("newton__" + name.split("Root")[0].lower(), obj)
        f = 1
        while f <= FRAME_END:
            t = (f - 1) / FPS
            obj.location = (loc[0], loc[1],
                            loc[2] + 0.03 * math.sin(
                                2 * math.pi * t / 2.4 + phase))
            obj.rotation_euler = (0, 0, 0.06 * math.sin(
                2 * math.pi * t / 3.6 + phase))
            kf(obj, "location", f)
            kf(obj, "rotation_euler", f)
            f += 4
        set_interp(act, 'LINEAR')
        made.append(act)
    for set_name in ("law1", "law2", "law3"):
        cues = files["newton_%s.srt" % set_name]["cues"]
        spans = {"CUBY": [], "SPHERO": []}
        for idx in sorted(cues):
            cue = cues[idx]
            st, en, tx = cue["start"], cue["end"], cue["text"]
            for who in spans:
                if tx.startswith(who + ":"):
                    spans[who].append((st, en))
        for who, objn in (("CUBY", "CubyMouth"), ("SPHERO", "SpheroMouth")):
            obj = bpy.data.objects[objn]
            act = _new_action("%s__%s" % (set_name, objn.lower()), obj)
            f_end = int(TRIG_LEN * FPS)
            openf = set()
            for st, en in spans[who]:
                f = int(st * FPS)
                while f <= int(en * FPS):
                    if (f // 3) % 2 == 0:
                        openf.add(f)
                    f += 1
            base = tuple(obj.scale)
            f = 1
            while f <= f_end:
                z = 0.09 if f in openf else 0.035
                obj.scale = (base[0], base[1], z)
                kf(obj, "scale", f)
                f += 1
            set_interp(act, 'CONSTANT')
            made.append(act)
    return made


def action_ranges(names):
    out = {}
    for n in names:
        act = bpy.data.actions.get(n)
        if not act:
            continue
        lo, hi = None, None
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for fc in bag.fcurves:
                        for kp in fc.keyframe_points:
                            f = kp.co.x
                            lo = f if lo is None else min(lo, f)
                            hi = f if hi is None else max(hi, f)
        if lo is not None:
            out[n] = [int(lo), int(hi)]
    return out


# --------------------------------------------------------------------------
# scene content
# --------------------------------------------------------------------------

def build_stage(mats):
    prim("primitive_plane_add", "Floor", mats["floor"], size=30,
         location=(0, 0, 0))
    phys(bpy.data.objects["Floor"], 'STATIC', friction=0.6)
    # station 1: ice rink + puck + pusher
    prim("primitive_cube_add", "IcePad", mats["ice"],
         location=(-4.0, 0.0, 0.02), scale=(14.0, 3.0, 0.02))
    phys(bpy.data.objects["IcePad"], 'STATIC', friction=0.0)
    prim("primitive_cylinder_add", "Puck", mats["puck"],
         location=(-6.0, 0.0, 0.17), radius=0.28, depth=0.22)
    phys(bpy.data.objects["Puck"], 'DYNAMIC', mass=1.0, friction=0.0,
         elasticity=0.0, ccd=0.2)
    bpy.data.objects["Puck"].game.use_sleep = False
    prim("primitive_cube_add", "IcePusher", mats["rig"],
         location=(-6.6, 0.0, 0.35), scale=(0.06, 0.9, 0.3))
    phys(bpy.data.objects["IcePusher"], 'STATIC', friction=0.6)
    # station 2: hammer + two masses
    prim("primitive_cube_add", "Hammer", mats["rig"],
         location=(0.3, -0.5, 1.02), scale=(1.0, 0.5, 0.03))
    phys(bpy.data.objects["Hammer"], 'STATIC', friction=0.4)
    prim("primitive_cube_add", "HammerBlock", mats["rig"],
         location=(0.3, -0.5, 1.15), scale=(0.9, 0.35, 0.08))
    phys(bpy.data.objects["HammerBlock"], 'DYNAMIC', mass=2.0,
         friction=0.3, elasticity=0.1)
    bpy.data.objects["HammerBlock"].game.use_sleep = False
    prim("primitive_uv_sphere_add", "BallLight", mats["light"],
         location=(-0.35, -0.5, 0.24), radius=0.22)
    phys(bpy.data.objects["BallLight"], 'DYNAMIC', mass=0.5,
         friction=0.1, elasticity=0.2, ccd=0.15)
    bpy.data.objects["BallLight"].game.use_sleep = False
    prim("primitive_uv_sphere_add", "BallHeavy", mats["heavy"],
         location=(0.95, -0.5, 0.32), radius=0.3)
    phys(bpy.data.objects["BallHeavy"], 'DYNAMIC', mass=5.0,
         friction=0.1, elasticity=0.2, ccd=0.2)
    bpy.data.objects["BallHeavy"].game.use_sleep = False
    bpy.data.objects["BallHeavy"].game.use_sleep = False
    # station 3: trap door + drop ball + elastic pad
    prim("primitive_cube_add", "TrapDoor", mats["rig"],
         location=(5.0, 0.0, 2.2), scale=(0.55, 0.55, 0.03))
    phys(bpy.data.objects["TrapDoor"], 'STATIC', friction=0.6)
    prim("primitive_uv_sphere_add", "DropBall", mats["puck"],
         location=(5.0, 0.0, 2.52), radius=0.25)
    phys(bpy.data.objects["DropBall"], 'DYNAMIC', mass=1.0,
         friction=0.3, elasticity=0.75, ccd=0.2)
    bpy.data.objects["DropBall"].game.use_sleep = False
    prim("primitive_cube_add", "BouncePad", mats["pad"],
         location=(5.0, 0.0, 0.04), scale=(0.9, 0.9, 0.04))
    phys(bpy.data.objects["BouncePad"], 'STATIC', friction=0.4,
         elasticity=0.75)


def build_robots(mats):
    cuby = prim("primitive_cube_add", "CubyRoot", mats["cuby"],
                location=(-1.25, -3.4, 0.55), scale=(0.5, 0.5, 0.55))
    phys(cuby, 'NO_COLLISION')
    eye = prim("primitive_uv_sphere_add", "CubyEyeL", mats["white"],
               location=(-1.42, -3.85, 0.75), radius=0.09)
    eye.parent = cuby
    eye = prim("primitive_uv_sphere_add", "CubyEyeR", mats["white"],
               location=(-1.08, -3.85, 0.75), radius=0.09)
    eye.parent = cuby
    mouth = prim("primitive_cube_add", "CubyMouth", mats["cuby_dark"],
                 location=(-1.25, -3.92, 0.42), scale=(0.34, 0.05, 0.035))
    mouth.parent = cuby
    phys(mouth, 'NO_COLLISION')
    sph = prim("primitive_uv_sphere_add", "SpheroRoot", mats["sphero"],
               location=(1.25, -3.4, 0.62), radius=0.55)
    phys(sph, 'NO_COLLISION')
    eye = prim("primitive_uv_sphere_add", "SpheroEyeL", mats["white"],
               location=(1.08, -3.85, 0.8), radius=0.09)
    eye.parent = sph
    eye = prim("primitive_uv_sphere_add", "SpheroEyeR", mats["white"],
               location=(1.42, -3.85, 0.8), radius=0.09)
    eye.parent = sph
    mouth = prim("primitive_cube_add", "SpheroMouth", mats["sphero_dark"],
                 location=(1.25, -3.9, 0.5), scale=(0.34, 0.05, 0.035))
    mouth.parent = sph
    phys(mouth, 'NO_COLLISION')


def build_cameras():
    for name, (loc, tgt, lens) in SHOTS.items():
        cam = bpy.data.objects.get(name)
        if cam is None:
            c = bpy.data.cameras.new(name)
            cam = bpy.data.objects.new(name, c)
            link(cam)
        cam.location = loc
        cam.data.lens = lens
        look_at(cam, tgt)
    main = bpy.data.objects.get("Camera")
    if main is None:
        c = bpy.data.cameras.new("Camera")
        main = bpy.data.objects.new("Camera", c)
        link(main)
    wide = bpy.data.objects["NewtonWide"]
    main.location = wide.location
    main.rotation_euler = wide.rotation_euler
    main.data.lens = wide.data.lens
    bpy.context.scene.camera = main


def build_text(mats):
    sub = bpy.data.objects.get("Subtitles")
    if sub is None:
        cu = bpy.data.curves.new("Subtitles", type='FONT')
        sub = bpy.data.objects.new("Subtitles", cu)
        link(sub)
    sub.data.body = ""
    sub.data.size = SUB_SIZE
    sub.data.align_x = 'CENTER'
    sub.data.align_y = 'CENTER'
    if not sub.data.materials:
        sub.data.materials.append(mats["sub"])
    cam = bpy.data.objects["Camera"]
    sub.parent = cam
    sub.matrix_parent_inverse.identity()
    sub.location = SUB_LOCAL
    phys(sub, 'NO_COLLISION')


def build_lights():
    if bpy.data.objects.get("Sun"):
        return
    li = bpy.data.lights.new("Sun", type='SUN')
    li.energy = 3.0
    lo = bpy.data.objects.new("Sun", li)
    link(lo)
    lo.location = (4, -6, 9)
    lo.rotation_euler = (math.radians(50), 0, math.radians(25))
    wi = bpy.data.lights.new("Fill", type='AREA')
    wi.energy = 400.0
    wo = bpy.data.objects.new("Fill", wi)
    link(wo)
    wo.location = (0, -7, 5)
    look_at(wo, (0, 0, 1))


def build_pad_bricks():
    pad = bpy.data.objects["BouncePad"]
    if pad.game.sensors.get("Hit") is None:
        bpy.context.view_layer.objects.active = pad
        bpy.ops.logic.sensor_add(type='COLLISION', name='Hit')
    sens = pad.game.sensors['Hit']
    if pad.game.controllers.get("Flick") is None:
        bpy.context.view_layer.objects.active = pad
        bpy.ops.logic.controller_add(type='LOGIC_AND', name='Flick')
    ctrl = pad.game.controllers['Flick']
    if ctrl not in list(sens.controllers):
        sens.link(ctrl)
    if pad.game.actuators.get("Flick") is None:
        bpy.context.view_layer.objects.active = pad
        bpy.ops.logic.actuator_add(type='ACTION', name='Flick')
    actr = pad.game.actuators['Flick']
    actr.action = bpy.data.actions['law3__padflick']
    actr.frame_start = 1
    actr.frame_end = 160
    if actr not in list(ctrl.actuators):
        actr.link(controller=ctrl)
    log("pad bricks ok: Collision -> Flick(action law3__padflick)")


def build_game():
    d = bpy.data.objects.get("GameDirector")
    if d is None:
        bpy.ops.object.empty_add(location=(0, 0, 0))
        d = bpy.context.active_object
        d.name = "GameDirector"
    phys(d, 'NO_COLLISION')
    if d.game.sensors.get("Always") is None:
        bpy.context.view_layer.objects.active = d
        bpy.ops.logic.sensor_add(type='ALWAYS', name='Always')
    sens = d.game.sensors['Always']
    if hasattr(sens, 'use_pulse_true_level'):
        sens.use_pulse_true_level = True
    if d.game.controllers.get("Dialogue") is None:
        bpy.context.view_layer.objects.active = d
        bpy.ops.logic.controller_add(type='PYTHON', name='Dialogue')
    ctrl = d.game.controllers['Dialogue']
    ctrl.mode = 'MODULE'
    ctrl.module = 'game_subtitles.update'
    if ctrl not in list(sens.controllers):
        sens.link(ctrl)
    code = open(os.path.join(HERE, 'game_subtitles.py'),
                encoding='utf-8').read()
    txt = bpy.data.texts.get('game_subtitles.py')
    if txt is None:
        txt = bpy.data.texts.new('game_subtitles.py')
    txt.from_string(code)
    if d.game.properties.get("tw_story") is None:
        prev = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = d
        bpy.ops.object.game_property_new(type='STRING', name='tw_story')
        bpy.context.view_layer.objects.active = prev
        d.game.properties["tw_story"].value = "//newton.yml"
    sub = bpy.data.objects["Subtitles"]
    if sub.game.properties.get("Text") is None:
        prev = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = sub
        bpy.ops.object.game_property_new(type='STRING', name='Text')
        bpy.context.view_layer.objects.active = prev
    log("bricks ok: Always -> Dialogue(module game_subtitles.update)")


def main():
    clear_factory_scene()
    sc = bpy.context.scene
    sc.render.fps = FPS
    # "Use Frame Rate": fixed-timestep mode - physics advances with the
    # logic clock (int(real_dt * ticrate) ticks per rendered frame)
    # instead of one real-time-sized step per frame.
    sc.game_settings.use_frame_rate = True
    sc.frame_start = FRAME_START
    sc.frame_end = FRAME_END
    mats = {
        "floor": make_mat("LabFloor", (0.22, 0.24, 0.28), 0.8),
        "ice": make_mat("Ice", (0.75, 0.88, 0.95), 0.12),
        "puck": make_mat("PuckBlue", (0.16, 0.42, 0.90), 0.4),
        "rig": make_mat("RigOrange", (0.85, 0.45, 0.10), 0.5),
        "light": make_mat("BallLight", (0.9, 0.75, 0.2), 0.35),
        "heavy": make_mat("BallHeavy", (0.35, 0.35, 0.4), 0.35),
        "pad": make_mat("PadGreen", (0.2, 0.7, 0.4), 0.5),
        "cuby": make_mat("CubyBlue", (0.16, 0.42, 0.90), 0.55),
        "cuby_dark": make_mat("CubyDark", (0.10, 0.28, 0.62), 0.6),
        "sphero": make_mat("SpheroOrange", (0.95, 0.48, 0.12), 0.5),
        "sphero_dark": make_mat("SpheroDark", (0.70, 0.33, 0.08), 0.6),
        "white": make_mat("EyeWhite", (0.95, 0.95, 0.97), 0.35),
        "sub": make_mat("SubWhite", (0.95, 0.95, 0.95), 0.4,
                        emit=(1.0, 1.0, 1.0)),
    }
    loaded = sb.load_story_files(os.path.join(HERE, "newton.yml"))
    if loaded["errors"]:
        raise SystemExit("story errors: %s" % loaded["errors"])
    story, files = loaded["story"], loaded["files"]
    build_stage(mats)
    build_robots(mats)
    build_cameras()
    build_text(mats)
    build_lights()
    acts = build_trigger_actions()
    acts += build_robot_actions(story, files)
    # rest transforms = hold pose: the builder's keying loops leave each
    # rig on its last key; without this the game loads the rig mid-stroke
    # and playAction(frame 1) teleports it into the bodies at set start.
    bpy.data.objects["IcePusher"].location = (-6.6, 0.0, 0.35)
    bpy.data.objects["Hammer"].location = (0.3, -0.5, 1.02)
    bpy.data.objects["TrapDoor"].location = (5.0, 0.0, 2.2)
    bpy.data.objects["BouncePad"].location = (5.0, 0.0, 0.04)
    sc.frame_set(FRAME_START)
    build_pad_bricks()
    build_game()
    names = [a.name for a in bpy.data.actions if a.users or a.use_fake_user]
    for a in bpy.data.actions:
        a.use_fake_user = True
    ranges = action_ranges([a.name for a in acts])
    side = os.path.join(HERE, "newton.sync.json")
    sb.save_sidecar(side, sb.sidecar_payload({}, ranges))
    log("sidecar: %d action ranges -> %s" % (len(ranges), side))
    try:
        schema = sb.build_schema(story,
                                 [o.name for o in bpy.data.objects],
                                 list(ranges),
                                 [s for s in SHOTS],
                                 sorted({k for k in files}),
                                 [])
        sp = sb.schema_path_for(os.path.join(HERE, "newton.yml"))
        import json
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(schema, fh, indent=2, sort_keys=True)
        log("schema -> %s" % sp)
    except Exception as ex:
        log("schema skipped: %r" % (ex,))
    out = os.path.join(HERE, "newton_laws.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out)
    log("saved %s (actions=%d objects=%d)" %
        (out, len(bpy.data.actions), len(bpy.data.objects)))
    bpy.ops.wm.quit_blender()


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    try:
        bpy.ops.wm.quit_blender()
    except Exception:
        pass
    raise
