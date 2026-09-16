"""Talking Robots - UPBGE game-mode story player (animation sets, v2).

Setup (already done in talking_robots.blend):
  GameDirector (Empty): Always sensor [TRUE pulse] -> Python controller
  (Module: "game_subtitles.update"). Also works in Script mode.
  Story data comes from FILES next to the .blend: the "tw_story" String
  game property holds the story path (default "//story.yml"); story.py
  (parser, same folder) and story.sync.json (action ranges, written by
  the add-on's Refresh Sync) load from beside it. Paths only - property
  strings are length-capped, so no JSON lives in them.

  A set = animations played together from t=0: subs (typewriter, blocks
  on cue-span end), action Obj@Act (blocks until done), camera (procedural
  moves between staged shot objects, never blocks), audio (never blocks).
  The set's end (stop | goto | choice) fires when blocking anims finish.
  All motion lives on objects (tweak keys + press P); staged cameras are
  followed live, so reframing needs no bake either.

Controls:
  Up/Down + Enter/Space = move in / confirm a dialogue choice
  1-9                   = pick a choice option directly
  R                     = restart story, ESC = quit game.

Diagnostics: writes game_debug.log next to the .blend (init info, set
jumps, choices, missing actors/shots, heartbeats, tracebacks). The tick
counter also lives on GameDirector as the "tick" property.
Headless capture: set a game property CAPTURE on GameDirector (any value)
  -> screenshots every 2nd tick into //capture/, choices auto-confirm
  option 1, auto-quit at story end (30 s backstop).
  CAPTURE_WATCH = "Name,Name2" (same object) adds a game_debug.log line
  every 10th tick with those objects' world positions - the proof that
  Bullet moved the bodies a story set triggered (physics has no keys).
  CAPTURE_STRIDE = N screenshots every Nth tick instead of every 2nd
  (long stories at 60 Hz would burn the capture budget otherwise).
"""

import bge
import os

try:
    import aud
except Exception:
    aud = None
try:
    import mathutils
except Exception:
    mathutils = None

STORY_PROP = "tw_story"
STORY_DEFAULT = "//story.yml"
CAM_OBJ = "Camera"
SUB_OBJ = "Subtitles"
MENU_OBJ = "ChoiceMenu"
LOG_PATH = "//game_debug.log"
DEAD_MESSAGE = "No dialogue data (see game_debug.log)"
CAP_TICKS = 1800       # capture-mode backstop: auto-quit after this long
CHOICE_AUTO_TICKS = 30  # capture mode confirms option 1 after this many ticks
STOP_HOLD = 2.0        # seconds the last line lingers before the replay hint
CPS_FALLBACK = 30.0
CAM_BLEND = 0.5        # seconds a camera move eases over


def _log(msg):
    try:
        with open(bge.logic.expandPath(LOG_PATH), "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def _obj(scene, name):
    try:
        return scene.objects[name]
    except Exception:
        pass
    try:
        for o in scene.objects:
            try:
                if o.name == name:
                    return o
            except Exception:
                pass
    except Exception:
        pass
    return None


def _set_text(font, body):
    ok = False
    try:
        font.text = body
        ok = True
    except Exception:
        pass
    try:
        font["Text"] = body
        ok = True
    except Exception:
        pass
    return ok


def _just_pressed(keyboard, events_mod, key_name):
    """Edge-triggered key check: keyboard.inputs on new UPBGE (.events is
    deprecated there), keyboard.events on classic BGE."""
    try:
        key = getattr(events_mod, key_name)
    except Exception:
        return False
    try:
        inputs = getattr(keyboard, "inputs", None)
    except Exception:
        inputs = None
    if inputs is not None:
        try:
            ev = inputs[key]
        except Exception:
            ev = None
        if ev is not None:
            try:
                return bool(ev.activated)
            except Exception:
                pass
    try:
        return keyboard.events[key] == bge.logic.KX_INPUT_JUST_ACTIVATED
    except Exception:
        return False


# --------------------------------------------------------------------------
# pure rotation helpers (camera easing; no mathutils needed)
# --------------------------------------------------------------------------

def _mat3_to_quat(m):
    import math
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        return [(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s,
                (m[1][0] - m[0][1]) / s, 0.25 * s]
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        return [0.25 * s, (m[0][1] + m[1][0]) / s,
                (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s]
    if m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        return [(m[0][1] + m[1][0]) / s, 0.25 * s,
                (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s]
    s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
    return [(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s,
            (m[1][0] - m[0][1]) / s]


def _quat_to_mat3(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w),
             2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z),
             2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w),
             1 - 2 * (x * x + y * y)]]


def _slerp(q0, q1, t):
    import math
    dot = sum(a * b for a, b in zip(q0, q1))
    if dot < 0.0:
        q1 = [-v for v in q1]
        dot = -dot
    if dot > 0.9995:
        out = [a + (b - a) * t for a, b in zip(q0, q1)]
        n = math.sqrt(sum(v * v for v in out)) or 1.0
        return [v / n for v in out]
    th = math.acos(max(-1.0, min(1.0, dot)))
    s = math.sin(th) or 1.0
    a = math.sin((1.0 - t) * th) / s
    b = math.sin(t * th) / s
    return [q0[i] * a + q1[i] * b for i in range(4)]


def _as_lists(ori):
    try:
        return [[float(ori[r][c]) for c in range(3)] for r in range(3)]
    except Exception:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _as_pos(pos):
    try:
        return [float(pos[0]), float(pos[1]), float(pos[2])]
    except Exception:
        return [0.0, 0.0, 0.0]


# --------------------------------------------------------------------------
# story loading (story.py lives next to the .blend, loaded from disk)
# --------------------------------------------------------------------------

def _load_story_lib(story_path):
    import importlib.util
    lib_path = os.path.join(os.path.dirname(story_path), "story.py")
    if not os.path.isfile(lib_path):
        raise ValueError("story parser %s is missing (ships with the story)"
                         % lib_path)
    spec = importlib.util.spec_from_file_location("tw_story_lib", lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_all(story_path):
    """(story, plans, choices, audio_paths, cps) or raises ValueError."""
    lib = _load_story_lib(story_path)
    loaded = lib.load_story_files(story_path)
    if loaded["errors"]:
        raise ValueError("; ".join(loaded["errors"]))
    story, files = loaded["story"], loaded["files"]
    sidecar = lib.load_sidecar(lib.sync_path_for(story_path))
    plans = {}
    for name in story["sets"]:
        plan = lib.set_plan(story, files, name, sidecar["actions"])
        for (o, a, _at, _w, dur) in plan["actions"]:
            if dur is None:
                raise ValueError(
                    "action '%s' has no frame range (run Refresh Sync "
                    "in Blender and re-save)" % a)
        plans[name] = plan
    choices = {}
    for cid, ch in (story.get("choices") or {}).items():
        choices[cid] = {"options": [tuple(o) for o in ch["options"]]}
    return story, plans, choices, dict(loaded["audio"]), float(
        story.get("cps", CPS_FALLBACK)), sidecar["actions"]


# --------------------------------------------------------------------------
# set lifecycle
# --------------------------------------------------------------------------

def _fail(own, name, why):
    """Log-once latch for things the game cannot play."""
    try:
        failed = own["tw_actor_fail"]
    except Exception:
        failed = {}
        own["tw_actor_fail"] = failed
    if name in failed:
        return
    failed[name] = why
    _log("game_subtitles: actor '%s' unavailable (%s)" % (name, why))


def _play_mode():
    try:
        return bge.logic.KX_ACTION_MODE_PLAY
    except Exception:
        return 0


def _start_action(own, scene, obj_name, act, idx, ranges):
    obj = _obj(scene, obj_name)
    if obj is None:
        _fail(own, obj_name, "missing object")
        return
    try:
        f0, f1 = ranges[act]
    except Exception:
        _fail(own, obj_name, "no range for '%s'" % act)
        return
    try:
        obj.playAction(act, f0, f1, 0, 0, 0, _play_mode())
    except Exception as ex:
        _fail(own, obj_name, "playAction: %r" % (ex,))
        return
    own["tw_started"]["a%d" % idx] = True


def _start_audio(own, path, idx):
    if aud is None:
        _fail(own, "audio", "no aud module")
        return
    try:
        try:
            dev = own["tw_auddev"]
        except Exception:
            dev = aud.device()
            own["tw_auddev"] = dev
        try:
            sounds = own["tw_audsnd"]
        except Exception:
            sounds = {}
            own["tw_audsnd"] = sounds
        if path not in sounds:
            sounds[path] = aud.Sound(path)
        dev.play(sounds[path])
    except Exception as ex:
        _fail(own, "audio", "play: %r" % (ex,))
        return
    own["tw_started"]["s%d" % idx] = True


def _enter_set(own, scene, set_id, tick, why, ranges):
    own["tw_set"] = set_id
    own["tw_t0"] = tick
    own["tw_state"] = "play"
    own["tw_ci"] = 0
    own["tw_started"] = {}
    plan = own["tw_sets"][set_id]
    for i, (o, a, at, _w, _d) in enumerate(plan["actions"]):
        if at <= 0.0:
            own["tw_started"]["a%d" % i] = True  # attempted; failures latch
            _start_action(own, scene, o, a, i, ranges)
    for i, (_f, at) in enumerate(plan["audios"]):
        if at <= 0.0:
            _start_audio(own, own["tw_audpaths"].get(_f, _f), i)
    try:  # re-aim: blend from the live pose at the next tick
        own["tw_cam"] = {"shot": "", "blend": 0.0, "fp": None, "fq": None,
                         "fl": 0.0}
    except Exception:
        pass
    menu = _obj(scene, MENU_OBJ)
    if menu is not None:
        _set_text(menu, "")
    _log("game_subtitles: enter set '%s' (%s)" % (set_id, why))


def _start_due(own, scene, t, ranges):
    plan = own["tw_sets"][own["tw_set"]]
    started = own["tw_started"]
    for i, (o, a, at, _w, _d) in enumerate(plan["actions"]):
        if at > 0.0 and t >= at and "a%d" % i not in started:
            started["a%d" % i] = True  # attempted once; failures latch
            _start_action(own, scene, o, a, i, ranges)
    for i, (f, at) in enumerate(plan["audios"]):
        if at > 0.0 and t >= at and "s%d" % i not in started:
            _start_audio(own, own["tw_audpaths"].get(f, f), i)


def _actions_done(own, scene):
    plan = own["tw_sets"][own["tw_set"]]
    started = own["tw_started"]
    for i, (o, _a, _at, wait, _d) in enumerate(plan["actions"]):
        if not wait:
            continue
        if o in own.get("tw_actor_fail", {}):
            continue  # failed actors never hang the set
        if not started.get("a%d" % i):
            return False  # delayed anim not due yet
        obj = _obj(scene, o)
        if obj is None:
            continue
        try:
            playing = obj.isPlayingAction(0)
        except Exception:
            continue  # no query API: started counts as done
        if playing:
            return False
    return True


def _shot_at(shots, t):
    cur = None
    for (st, shot) in shots:
        if st <= t:
            cur = shot
        else:
            break
    return cur


def _camera_tick(own, scene, t, dt):
    plan = own["tw_sets"][own["tw_set"]]
    shots = plan["shots"]
    if not shots:
        return  # no camera anim: hold whatever framing is live
    target = _shot_at(shots, t) or shots[0][1]
    cam = _obj(scene, CAM_OBJ)
    if cam is None:
        return
    try:
        st = own["tw_cam"]
    except Exception:
        st = None
    if not isinstance(st, dict):
        st = {"shot": "", "blend": 0.0, "fp": None, "fq": None, "fl": 0.0}
        own["tw_cam"] = st
    staged = _obj(scene, target)
    if staged is None:
        _fail(own, target, "missing shot object")
        return
    if st["shot"] != target:
        try:
            st["fp"] = _as_pos(cam.worldPosition)
            st["fq"] = _mat3_to_quat(_as_lists(cam.worldOrientation))
            st["fl"] = float(cam.lens)
        except Exception:
            st["fp"] = _as_pos(staged.worldPosition)
            st["fq"] = _mat3_to_quat(_as_lists(staged.worldOrientation))
            try:
                st["fl"] = float(staged.lens)
            except Exception:
                st["fl"] = 50.0
        st["shot"] = target
        st["blend"] = 0.0
    tp = _as_pos(staged.worldPosition)
    to = _as_lists(staged.worldOrientation)
    tq = _mat3_to_quat(to)
    try:
        tl = float(staged.lens)
    except Exception:
        tl = 50.0
    if st["blend"] >= 1.0 or st["fp"] is None:
        pos, mat, lens = tp, to, tl  # exact: no quat round-trip error
    else:
        st["blend"] = min(1.0, st["blend"] + dt / CAM_BLEND)
        k = st["blend"]
        k = k * k * (3.0 - 2.0 * k)  # smoothstep
        pos = [st["fp"][i] + (tp[i] - st["fp"][i]) * k for i in range(3)]
        mat = _quat_to_mat3(_slerp(st["fq"], tq, k))
        lens = st["fl"] + (tl - st["fl"]) * k
    try:
        cam.worldPosition = pos
    except Exception:
        pass
    try:
        cam.worldOrientation = mathutils.Matrix(mat) \
            if mathutils is not None else mat
    except Exception:
        pass
    try:
        cam.lens = lens
    except Exception:
        pass


def move_choice(own, delta):
    """Move the choice cursor; shared by keyboard now, clicks later."""
    try:
        n = len(own["tw_opts"])
    except Exception:
        return
    if n <= 0:
        return
    try:
        ci = int(own["tw_ci"])
    except Exception:
        ci = 0
    own["tw_ci"] = (ci + delta) % n


def confirm_choice(own, scene, tick, idx, ranges):
    """Jump to the set of option idx (keyboard AND future clicks)."""
    try:
        key, label, dest = own["tw_opts"][idx]
    except Exception:
        return False
    _log("game_subtitles: choice -> option %s '%s' -> set '%s'"
         % (key, label, dest))
    _enter_set(own, scene, dest, tick, "choice", ranges)
    return True


def _menu_body(opts, ci):
    lines = []
    for i, (key, label, _dest) in enumerate(opts):
        lines.append("%s %s: %s" % (">" if i == ci else " ", key, label))
    return "\n".join(lines)


def update(cont):
    try:
        _update(cont)
    except Exception:
        import traceback
        _log("game_subtitles FATAL:\n" + traceback.format_exc())


def _update(cont):
    own = cont.owner
    scene = own.scene
    keyboard = bge.logic.keyboard

    try:
        tick = own["tick"]
    except Exception:
        tick = 0
        own["tick"] = 0
        own["cap_n"] = 0
        own["tw_ci"] = 0
        own["tw_ctick"] = 0
        own["tw_stop"] = -1
        own["tw_frozen"] = 0.0
        own["tw_opts"] = []
        own["tw_prompt"] = ""
        own["tw_actor_fail"] = {}
        own["tw_started"] = {}
        own["tw_cam"] = None
        own["tw_ranges"] = {}
        try:
            own["tw_dt"] = 1.0 / float(bge.logic.getLogicTicRate())
        except Exception:
            own["tw_dt"] = 1.0 / 60.0
        try:  # capture-only: ~1 fps software rendering would trip the
            if own["CAPTURE"]:  # engine's 5-ticks-per-frame clamp and
                bge.logic.setMaxLogicFrame(256)   # desync story clock
                bge.logic.setMaxPhysicsFrame(256)  # from physics time
        except Exception:
            pass
        try:  # capture-only: slow logic rate = more story per rendered
            tr = float(own["CAPTURE_TICRATE"])  # frame on software GL
            if tr >= 1.0:  # (physics then runs 6x slower than story -
                bge.logic.setLogicTicRate(tr)  # only for non-physics
                own["tw_dt"] = 1.0 / tr  # demos!)
        except Exception:
            pass
        try:
            story_prop = own[STORY_PROP]
        except Exception:
            story_prop = STORY_DEFAULT
        if not isinstance(story_prop, str) or not story_prop.strip():
            story_prop = STORY_DEFAULT
        try:
            if story_prop.startswith("//"):
                story_path = bge.logic.expandPath(story_prop)
            else:
                story_path = story_prop
            story, plans, choices, audpaths, cps, ranges = \
                _load_all(story_path)
            ns = len(plans)
            nc = sum(len(p["subs"]) for p in plans.values())
            na = sum(len(p["actions"]) for p in plans.values())
            own["tw_sets"] = plans
            own["tw_choices"] = choices
            own["tw_audpaths"] = audpaths
            own["tw_start"] = story["start"]
            own["tw_cps"] = cps
            own["tw_ranges"] = ranges
            _enter_set(own, scene, story["start"], 0, "init", ranges)
            try:  # the game always plays through the main camera
                maincam = _obj(scene, CAM_OBJ)
                if maincam is not None:
                    scene.active_camera = maincam
            except Exception:
                pass
            try:  # baked timeline subtitle objects stay in the editor
                try:
                    objs = list(scene.objects.values())
                except Exception:
                    try:
                        objs = list(scene.objects)
                    except Exception:
                        objs = []
                for o in objs:
                    try:
                        if "_Line" in o.name or o.name.startswith("SubLine"):
                            o.visible = False
                    except Exception:
                        pass
            except Exception:
                pass
            _log("game_subtitles: init ok: %d sets %d cues %d actions "
                 "dt=%.4f" % (ns, nc, na, own["tw_dt"]))
        except Exception as ex:
            own["tw_dead"] = "bad story data: %r" % (ex,)
            own["tw_state"] = "play"
            own["tw_set"] = ""
            own["tw_t0"] = 0
            own["tw_sets"] = {}
            own["tw_choices"] = {}
            own["tw_audpaths"] = {}
            own["tw_start"] = ""
            own["tw_cps"] = CPS_FALLBACK
            _log("game_subtitles: INIT FAILED: %s" % own["tw_dead"])

    try:
        dead = own["tw_dead"]
    except Exception:
        dead = None

    if _just_pressed(keyboard, bge.events, "ESCKEY"):
        try:
            bge.logic.endGame()
        except Exception:
            pass
        return

    try:
        own["CAPTURE"]
        capturing = True
    except Exception:
        capturing = False

    if dead:
        sub = _obj(scene, SUB_OBJ)
        if sub is not None:
            _set_text(sub, DEAD_MESSAGE)
        return

    if _just_pressed(keyboard, bge.events, "RKEY"):
        _enter_set(own, scene, own["tw_start"], tick, "restart",
                   own["tw_ranges"])

    tick += 1
    own["tick"] = tick
    try:
        dt = float(own["tw_dt"])
    except Exception:
        dt = 1.0 / 60.0
    try:
        cps = max(float(own["tw_cps"]), 0.01)
    except Exception:
        cps = CPS_FALLBACK

    state = own["tw_state"]
    set_id = own["tw_set"]
    S = own["tw_sets"][set_id]
    t = (tick - own["tw_t0"]) * dt

    # ---- per-tick play (delayed anims fire, camera follows) ----
    if state == "play":
        _start_due(own, scene, t, own["tw_ranges"])
        _camera_tick(own, scene, t, dt)

    # ---- transitions (may switch set/state; rendering refetches after) ----
    if state == "play" and t >= S["dur"] and _actions_done(own, scene):
        kind, target = S["end"]
        if kind == "choice":
            own["tw_state"] = "choice"
            own["tw_ctick"] = tick
            own["tw_frozen"] = t
            own["tw_ci"] = 0
            own["tw_opts"] = list(own["tw_choices"][target]["options"])
            own["tw_prompt"] = S["prompt"]
            _log("game_subtitles: choice '%s' (%d options)"
                 % (target, len(own["tw_opts"])))
        elif kind == "goto":
            _log("game_subtitles: goto set '%s'" % target)
            _enter_set(own, scene, target, tick, "goto", own["tw_ranges"])
        else:
            own["tw_state"] = "stop"
            own["tw_stop"] = tick
            own["tw_frozen"] = t
            _log("game_subtitles: set '%s' finished (stop)" % set_id)
    elif state == "choice":
        if _just_pressed(keyboard, bge.events, "UPARROWKEY"):
            move_choice(own, -1)
        if _just_pressed(keyboard, bge.events, "DOWNARROWKEY"):
            move_choice(own, 1)
        direct = None
        for n in ("ONEKEY", "TWOKEY", "THREEKEY", "FOURKEY", "FIVEKEY",
                  "SIXKEY", "SEVENKEY", "EIGHTKEY", "NINEKEY"):
            if _just_pressed(keyboard, bge.events, n):
                direct = n
                break
        if direct is not None:
            digit = {"ONEKEY": "1", "TWOKEY": "2", "THREEKEY": "3",
                     "FOURKEY": "4", "FIVEKEY": "5", "SIXKEY": "6",
                     "SEVENKEY": "7", "EIGHTKEY": "8", "NINEKEY": "9"}[direct]
            for i, (key, _label, _dest) in enumerate(own["tw_opts"]):
                if str(key) == digit:
                    confirm_choice(own, scene, tick, i, own["tw_ranges"])
                    break
        elif (_just_pressed(keyboard, bge.events, "ENTERKEY")
              or _just_pressed(keyboard, bge.events, "RETKEY")
              or _just_pressed(keyboard, bge.events, "SPACEKEY")):
            try:
                ci = int(own["tw_ci"])
            except Exception:
                ci = 0
            confirm_choice(own, scene, tick, ci, own["tw_ranges"])
        elif capturing and (tick - own["tw_ctick"]) >= CHOICE_AUTO_TICKS:
            _log("game_subtitles: capture auto-picks option 1")
            confirm_choice(own, scene, tick, 0, own["tw_ranges"])

    # ---- render (refetch: the set may just have changed) ----
    state = own["tw_state"]
    set_id = own["tw_set"]
    S = own["tw_sets"][set_id]
    if state == "play":
        t = (tick - own["tw_t0"]) * dt
    else:
        t = own["tw_frozen"]
    cues = S["subs"]

    if state == "choice":
        body = own["tw_prompt"]
        menu = _obj(scene, MENU_OBJ)
        if menu is not None:
            try:
                ci = int(own["tw_ci"])
            except Exception:
                ci = 0
            _set_text(menu, _menu_body(own["tw_opts"], ci))
    elif state == "stop":
        if (tick - own["tw_stop"]) * dt > STOP_HOLD:
            body = "R = replay!   ESC = quit"
        else:
            body = cues[-1][2] if cues else ""
    else:
        idx = -1
        for i, (st, _en, _tx) in enumerate(cues):
            if st <= t:
                idx = i
        if idx < 0:
            body = ""
        else:
            st, _en, tx = cues[idx]
            body = tx[:min(len(tx), int((t - st) * cps))]

    sub = _obj(scene, SUB_OBJ)
    if sub is not None:
        if not _set_text(sub, body):
            _log("game_subtitles: cannot write subtitle text")
    try:
        menu0 = _obj(scene, MENU_OBJ)
        if menu0 is not None:
            menu0.worldScale = [1.0, 1.0, 1.0]
    except Exception:
        pass

    if tick <= 5:
        _log("game_subtitles: tick %d t=%.3f set=%s state=%s"
             % (tick, t, set_id, state))
    if tick % 600 == 0:
        _log("game_subtitles: heartbeat tick=%d t=%.1f set=%s state=%s"
             % (tick, t, set_id, state))

    # ---- headless capture mode ----
    stride = 2
    try:
        stride = max(2, int(own["CAPTURE_STRIDE"]))
    except Exception:
        pass
    if capturing and tick % stride == 0:
        try:
            import os as _os
            capdir = bge.logic.expandPath("//capture")
            _os.makedirs(capdir, exist_ok=True)
            own["cap_n"] += 1
            bge.render.makeScreenshot(
                _os.path.join(capdir, "game_%04d.png" % own["cap_n"]))
        except Exception as ex:
            print("capture failed:", ex)
    if capturing and tick % 10 == 0:
        try:
            watch = own["CAPTURE_WATCH"]
        except Exception:
            watch = ""
        if watch:
            parts = []
            for nm in str(watch).split(","):
                nm = nm.strip()
                o = _obj(scene, nm)
                if o is not None:
                    p = o.worldPosition
                    parts.append("%s=(%.2f,%.2f,%.2f)"
                                 % (nm, p[0], p[1], p[2]))
            if parts:
                _log("game_subtitles: watch t=%.2f %s"
                     % (t, " ".join(parts)))
    if capturing and state == "stop" and \
            (tick - own["tw_stop"]) * dt > STOP_HOLD:
        try:
            bge.logic.endGame()
        except Exception:
            pass
    if capturing and tick >= CAP_TICKS:
        _log("game_subtitles: capture budget hit, quitting")
        try:
            bge.logic.endGame()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        update(bge.logic.getCurrentController())
    except Exception as ex:
        _log("game_subtitles: script-mode start failed: %r" % (ex,))
