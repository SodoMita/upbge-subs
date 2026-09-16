# SPDX-License-Identifier: GPL-2.0-or-later
#
# Typewriter Subtitles — 3D text subtitles driven by timeline keyframes.
#
# Each timeline keyframe stores one subtitle line. While the playhead moves
# away from a keyframe, the text is revealed character by character
# (typewriter effect). When the playhead reaches the *next* keyframe, the
# text is cleared and the next line starts appearing. Keys can be freely
# dragged, duplicated and deleted in the Timeline / Dope Sheet / Graph
# Editor — the subtitles follow.
#
# Works with the plain 3D Text object, so the subtitles are fully renderable
# 3D geometry (any font, extrude, bevel, materials...).

bl_info = {
    "name": "Typewriter Subtitles (3D Text Keyframes)",
    "author": "Arena Agent",
    "version": (1, 9, 0),
    "blender": (2, 83, 0),
    "location": "3D Viewport > Sidebar (N) > Subtitles;  Add > Text > Typewriter Subtitles;  File > Import/Export",
    "description": "3D text subtitles on timeline keyframes + animation-centric "
                   "story sets for UPBGE. Each key starts a character-by-character "
                   "(typewriter) reveal; the text is cleared when the next key "
                   "starts, and keys stay draggable in the Timeline. Link an SRT "
                   "or WebVTT file to reload it with one click (or automatically) "
                   "after editing it externally; lines are auto-backed-up on the "
                   "object, with one-click Rebuild / Recover. For stories: Preview "
                   "Set aims the scene at one set of story.yml (its cues, its "
                   "actions, its shot, its menu, its length), Refresh Sync writes "
                   "story.sync.json + the VSCode schema (also on save), Check "
                   "validates the story against the scene, and a rename watch keeps "
                   "story.yml's references in step. Bake to Objects freezes typing "
                   "per set (<set>_Line##) so renders need no add-on; Add Game "
                   "Logic exports lines for real-time play in UPBGE/BGE. "
                   "[CAM] cue lines steer the shot; they are understood, not shown.",
    "category": "Animation",
}

import bpy
import os
import re
import time
from mathutils import Matrix

# ---------------------------------------------------------------------------
# Constants / state
# ---------------------------------------------------------------------------

_KEY_ID = "tw_progress"          # custom float property that carries the keyframes
_KEY_PATH = '["tw_progress"]'    # RNA path of that property
_KEY_GROUP = "Typewriter Subtitles"
_BACKUP_ID = "tw_backup"         # JSON mirror of lines+settings (plain ID property)
_TW_GAME_TEXT = "tw_game.py"        # internal text block holding the game driver
_TW_GAME_DATA = "tw_game_data"      # cue export (String game property, JSON)
_TW_GAME_SENSOR = "TwGameAlways"    # auto-created logic bricks
_TW_GAME_CTRL = "TwGameDriver"
_TW_GAME_MODULE = "tw_game.update"

# Re-entrancy guard: our own property updates (body / frame writes) must not
# re-trigger the handlers or the keyframe callbacks.
_IN_HANDLER = False
_BODY_STATE = {}  # object name -> (frame, keyframe signature) cache for the depsgraph handler
_BACKUP_STATE = {}  # object name -> signature of the last written backup


# Embedded copy of tw_game.py (also shipped next to this add-on). "Add Game
# Logic" writes it into the .blend as an internal text block, so the game
# driver always matches the add-on version.
TW_GAME_DRIVER_SOURCE = """'''Generic UPBGE/BGE game-mode driver for Typewriter Subtitles.

Installed by the add-on (Sidebar > Subtitles > Add Game Logic), which also
exports this object's subtitle lines into the `tw_game_data` String game
property (JSON) and wires: Always sensor (TRUE pulse) -> Python controller
(Module: "tw_game.update").

Put the controller on the subtitle Font object itself (each subtitle object
then plays its own lines). R restarts the subtitles.

Diagnostics: failures are appended to tw_game_debug.log next to the .blend,
and the tick counter is kept in the owner's `tw_tick` property (watch it in
Show Debug Properties to prove the logic runs).

Works in both Module mode ("tw_game.update") and Script mode.
'''

import bge

DATA_PROP = "tw_game_data"
TICK_PROP = "tw_tick"
LOG_PATH = "//tw_game_debug.log"
DEAD_MESSAGE = "Subtitles unavailable (see tw_game_debug.log)"


def _log(msg):
    try:
        with open(bge.logic.expandPath(LOG_PATH), "a", encoding="utf-8") as fh:
            fh.write(msg + "\\n")
    except Exception:
        pass


def _scene_obj(scene, name):
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


def _has_prop(o, name):
    try:
        o[name]
        return True
    except Exception:
        return False


def _find_font(scene, owner):
    try:
        if _has_prop(owner, DATA_PROP):
            return owner
    except Exception:
        pass
    try:
        children = list(owner.childrenRecursive)
    except Exception:
        children = []
    for o in children:
        try:
            if _has_prop(o, DATA_PROP):
                return o
        except Exception:
            pass
    try:
        for o in scene.objects:
            try:
                if _has_prop(o, DATA_PROP):
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


def _reveal(text_len, elapsed_sec, cps, mode):
    if text_len <= 0:
        return 0
    if mode == 'CONSTANT':
        return text_len if elapsed_sec >= 0 else 0
    cps = max(float(cps), 0.01)
    if mode == 'SMOOTH':
        total = max(text_len / cps, 1e-6)
        p = min(max(elapsed_sec / total, 0.0), 1.0)
        p = p * p * (3.0 - 2.0 * p)
        return min(text_len, int(round(p * text_len)))
    if mode == 'EASE_OUT':
        total = max(text_len / cps, 1e-6)
        p = min(max(elapsed_sec / total, 0.0), 1.0)
        p = 1.0 - (1.0 - p) * (1.0 - p)
        return min(text_len, int(round(p * text_len)))
    return min(text_len, int(max(elapsed_sec, 0.0) * cps))


# Edge-triggered key check: keyboard.inputs on new UPBGE (.events is
# deprecated there), keyboard.events on classic BGE. NOTE: plain quotes
# only in this file - it is embedded into the add-on source.
def _just_pressed(keyboard, events_mod, key_name):
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


def update(cont):
    try:
        _update(cont)
    except Exception:
        import traceback
        _log("tw_game FATAL:\\n" + traceback.format_exc())


def _update(cont):
    import json
    own = cont.owner
    scene = own.scene

    try:
        tick = own[TICK_PROP]
    except Exception:
        tick = 0
        own[TICK_PROP] = 0
        try:
            own["tw_dt"] = 1.0 / float(bge.logic.getLogicTicRate())
        except Exception:
            own["tw_dt"] = 1.0 / 60.0
        font = _find_font(scene, own)
        if font is None:
            own["tw_dead"] = "no object carries '%s'" % DATA_PROP
            _log("tw_game: INIT FAILED: %s" % own["tw_dead"])
        else:
            try:
                data = json.loads(font[DATA_PROP])
                cues = sorted(((float(s), str(t))
                               for (s, t) in data["cues"]),
                              key=lambda c: c[0])
                own["tw_cues"] = cues
                own["tw_cps"] = float(data.get("cps", 15.0))
                own["tw_reveal"] = str(data.get("reveal", "LINEAR"))
                own["tw_font"] = font.name
                _log("tw_game: init ok: %d cues cps=%s reveal=%s dt=%.4f font=%s"
                     % (len(cues), own["tw_cps"], own["tw_reveal"],
                        own["tw_dt"], font.name))
            except Exception as ex:
                import traceback
                own["tw_dead"] = "bad game data: %r" % (ex,)
                try:
                    own["tw_font"] = font.name
                except Exception:
                    pass
                _log("tw_game: INIT FAILED: %r\\n%s"
                     % (ex, traceback.format_exc()))

    try:
        dead = own["tw_dead"]
    except Exception:
        dead = None
    if dead:
        try:
            font = _scene_obj(scene, str(own["tw_font"]))
        except Exception:
            font = None
        if font is not None:
            _set_text(font, DEAD_MESSAGE)
        return

    try:
        r_now = _just_pressed(bge.logic.keyboard, bge.events, "RKEY")
    except Exception:
        r_now = False
    if r_now:
        tick = 0
    tick += 1
    own[TICK_PROP] = tick

    try:
        cues = own["tw_cues"]
        cps = float(own["tw_cps"])
        reveal = str(own["tw_reveal"])
        font = _scene_obj(scene, str(own["tw_font"]))
    except Exception as ex:
        _log("tw_game: frame error: %r" % (ex,))
        return
    t = tick * float(own["tw_dt"])
    idx = -1
    for i, (st, _tx) in enumerate(cues):
        if st <= t:
            idx = i
    if idx < 0:
        body = ""
    else:
        st, tx = cues[idx]
        body = tx[:_reveal(len(tx), t - st, cps, reveal)]
    if font is not None:
        if not _set_text(font, body):
            _log("tw_game: cannot write text on '%s'" % font.name)
    if tick % 600 == 0:
        _log("tw_game: heartbeat tick=%d t=%.1f" % (tick, t))


if __name__ == "__main__":
    try:
        update(bge.logic.getCurrentController())
    except Exception as ex:
        _log("tw_game: script-mode start failed: %r" % (ex,))
"""


# ---------------------------------------------------------------------------
# Pure logic (kept bpy-free apart from nothing — easily unit-testable)
# ---------------------------------------------------------------------------

def compute_reveal_chars(text_len, elapsed_frames, fps, chars_per_sec, mode='LINEAR'):
    """Number of characters visible `elapsed_frames` after the key was hit."""
    if text_len <= 0:
        return 0
    if mode == 'CONSTANT':                     # classic subtitle: all at once
        return text_len if elapsed_frames >= 0 else 0
    if fps <= 0.0:
        fps = 24.0
    cps = max(float(chars_per_sec), 0.01)
    if mode == 'SMOOTH':                       # smoothstep over the typing duration
        total = max(1.0, (text_len / cps) * fps)
        p = min(max(elapsed_frames / total, 0.0), 1.0)
        p = p * p * (3.0 - 2.0 * p)
        return min(text_len, int(round(p * text_len)))
    if mode == 'EASE_OUT':                     # fast start, gentle finish
        total = max(1.0, (text_len / cps) * fps)
        p = min(max(elapsed_frames / total, 0.0), 1.0)
        p = 1.0 - (1.0 - p) * (1.0 - p)
        return min(text_len, int(round(p * text_len)))
    # 'LINEAR' — constant characters-per-second typing speed
    return min(text_len, int((max(elapsed_frames, 0) * cps) / fps))


def scene_fps(scene):
    try:
        fps = scene.render.fps / scene.render.fps_base
    except AttributeError:
        return 24.0
    return fps if fps > 0.0 else 24.0


# ---------------------------------------------------------------------------
# F-curve plumbing (compat: legacy actions <= 4.3 and slotted actions 4.4+/5.x)
# ---------------------------------------------------------------------------

def _action_fcurves(action):
    """Iterate all fcurves of an action on every Blender version."""
    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:                     # Blender <= 4.3 (legacy actions)
        yield from fcurves
        return
    try:                                        # Blender 4.4+ (slotted actions)
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    except AttributeError:
        pass


def _find_fcurve(obj):
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return None
    for fc in _action_fcurves(ad.action):
        if fc.data_path == _KEY_PATH:
            return fc
    return None


def _delete_fcurve(obj):
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return
    fc = _find_fcurve(obj)
    if fc is None:
        return
    fcurves = getattr(ad.action, "fcurves", None)
    if fcurves is not None:
        try:
            fcurves.remove(fc)
            return
        except Exception:
            pass
    try:
        for layer in ad.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    if fc in bag.fcurves[:]:
                        bag.fcurves.remove(fc)
    except Exception:
        pass


def _ensure_anim(obj):
    if obj.animation_data is None:
        obj.animation_data_create()
    if _KEY_ID not in obj.keys():
        obj[_KEY_ID] = 0.0


def _set_key(obj, uid, frame):
    """(Re-)insert the keyframe for subtitle entry `uid` at `frame`.

    Other entries' keys are never touched: if `frame` is already claimed by
    another subtitle key, the key skips ahead to the next free frame (sliding
    the Frame field across the timeline can no longer consume other keys).
    Returns the frame the key actually landed on."""
    _ensure_anim(obj)
    frame = int(frame)
    obj[_KEY_ID] = float(uid)
    fc = _find_fcurve(obj)
    if fc is None:
        # no fcurve at all -> no keys exist -> nothing can be claimed
        obj.keyframe_insert(_KEY_PATH, frame=frame, group=_KEY_GROUP)
        fc = _find_fcurve(obj)
        if fc is not None:
            for k in fc.keyframe_points:
                if int(round(k.co.y)) == uid:
                    k.interpolation = 'CONSTANT'
            fc.update()
        return frame
    claimed = set()
    ours = []
    for k in fc.keyframe_points:
        if int(round(k.co.y)) == uid:
            ours.append(k)
        else:
            claimed.add(int(round(k.co.x)))
    target = frame
    while target in claimed:
        target += 1
        if target - frame > 100000:   # paranoia, never expect this
            break
    if ours:
        ours[0].co = (float(target), float(uid))
        ours[0].interpolation = 'CONSTANT'
        for extra in ours[1:]:
            fc.keyframe_points.remove(extra)
    else:
        # `target` is free, so the insert cannot eat another entry's key
        obj.keyframe_insert(_KEY_PATH, frame=target, group=_KEY_GROUP)
        fc = _find_fcurve(obj) or fc
        for k in fc.keyframe_points:
            if int(round(k.co.y)) == uid:
                k.interpolation = 'CONSTANT'
    fc.update()
    return target


def _remove_key(obj, uid):
    fc = _find_fcurve(obj)
    if fc is None:
        return
    for k in list(fc.keyframe_points):
        if int(round(k.co.y)) == uid:
            fc.keyframe_points.remove(k)
    fc.update()


def keys_signature(obj):
    """Cheap fingerprint of the key positions, used to detect timeline edits."""
    fc = _find_fcurve(obj)
    if fc is None:
        return None
    return tuple((round(k.co.x, 2), round(k.co.y, 2)) for k in fc.keyframe_points)


def next_uid(obj):
    mx = obj.tw_uid_counter
    for e in obj.tw_entries:
        if e.uid > mx:
            mx = e.uid
    obj.tw_uid_counter = mx + 1
    return obj.tw_uid_counter


# ---------------------------------------------------------------------------
# Automatic backup (JSON mirror) + recovery
# ---------------------------------------------------------------------------
#
# Subtitle lines live in `tw_entries` (an RNA collection) and their timing in
# timeline keyframes. Both can be silently lost: deleting keys in the Timeline
# deletes the lines with them, and older Blender versions may drop the RNA
# collection when the file is saved while the add-on is disabled. To survive
# all of that, every edit also mirrors lines+settings into `obj["tw_backup"]`
# — a plain string ID property that Blender always preserves. The Subtitles
# panel shows the health of each object and offers one-click Rebuild/Recover.

def _backup_signature(obj):
    try:
        entries = tuple((e.uid, e.frame, e.text) for e in obj.tw_entries)
    except Exception:
        entries = ()
    try:
        cps = round(float(obj.tw_cps), 3)
        reveal = str(obj.tw_reveal)
    except Exception:
        cps, reveal = 15.0, 'LINEAR'
    return (entries, cps, reveal)


def _write_backup(obj, force=False):
    """Mirror lines+settings into the `tw_backup` ID property.

    Cheap: writes only when something actually changed. Returns True if the
    property was (re)written."""
    try:
        sig = _backup_signature(obj)
    except Exception:
        return False
    if not force and _BACKUP_STATE.get(obj.name) == sig:
        try:
            if obj.get(_BACKUP_ID):
                return False
        except Exception:
            pass
    import json
    payload = {"version": 1, "cps": sig[1], "reveal": sig[2],
               "entries": [[u, f, t] for (u, f, t) in sig[0]]}
    try:
        obj[_BACKUP_ID] = json.dumps(payload, ensure_ascii=False)
    except Exception:
        return False
    _BACKUP_STATE[obj.name] = sig
    try:  # keep the game-engine export in sync (no-op unless set up)
        _write_game_data(obj, bpy.context.scene)
    except Exception:
        pass
    return True


def _read_backup(obj):
    """(cps, reveal, [(uid, frame, text), ...]) or None if unusable."""
    import json
    try:
        raw = obj.get(_BACKUP_ID)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    try:
        items = [(int(u), int(f), str(t))
                 for (u, f, t) in data.get("entries", [])]
        return (float(data.get("cps", 15.0)),
                str(data.get("reveal", "LINEAR")), items)
    except Exception:
        return None


def _rebuild_keys_impl(obj):
    """Delete and re-create timeline keys from the current lines."""
    _delete_fcurve(obj)
    if _KEY_ID in obj.keys():
        try:
            del obj[_KEY_ID]
        except Exception:
            pass
    for e in sorted(obj.tw_entries, key=lambda e: (e.frame, e.uid)):
        landed = _set_key(obj, e.uid, e.frame)
        if landed != e.frame:
            e.frame = landed
    _sort_entries(obj)
    return len(obj.tw_entries)


def _recover_from_backup_impl(obj, scene):
    """Restore lines+keys+settings from the automatic backup.

    Returns the restored line count, or -1 when there is no backup."""
    data = _read_backup(obj)
    if data is None:
        return -1
    cps, reveal, items = data
    obj.tw_entries.clear()
    _delete_fcurve(obj)
    if _KEY_ID in obj.keys():
        try:
            del obj[_KEY_ID]
        except Exception:
            pass
    try:
        obj.tw_cps = cps
    except Exception:
        pass
    try:
        if reveal in ('LINEAR', 'SMOOTH', 'EASE_OUT', 'CONSTANT'):
            obj.tw_reveal = reveal
    except Exception:
        pass
    mx = 0
    for (uid, frame, text) in items:
        e = obj.tw_entries.add()
        e.uid = uid
        e.frame = int(frame)   # the frame callback inserts this line's key
        e.text = text
        if uid > mx:
            mx = uid
    if mx >= obj.tw_uid_counter:
        obj.tw_uid_counter = mx + 1
    for e in obj.tw_entries:   # paranoia: every line gets its key
        landed = _set_key(obj, e.uid, e.frame)
        if landed != e.frame:
            e.frame = landed
    obj.tw_active_index = 0
    _sort_entries(obj)
    _write_backup(obj, force=True)
    update_object(obj, scene)
    return len(items)

def _sort_entries(obj):
    """Keep the panel list ordered in time."""
    entries = obj.tw_entries
    if len(entries) < 2:
        return
    items = sorted(entries, key=lambda e: (e.frame, e.uid))
    already = True
    for a, b in zip(entries, items):
        if a.uid != b.uid:
            already = False
            break
    if already:
        return
    active_uid = None
    if 0 <= obj.tw_active_index < len(entries):
        active_uid = entries[obj.tw_active_index].uid
    cached = [(e.uid, e.frame, e.text) for e in items]
    entries.clear()
    for uid, frame, text in cached:
        e = entries.add()
        e.uid = uid
        e.frame = frame
        e.text = text
    for i, e in enumerate(entries):
        if e.uid == active_uid:
            obj.tw_active_index = i
            break


def _create_entry(obj, frame, text):
    """Add a subtitle entry (and its timeline keyframe via the update
    callback) in one step."""
    e = obj.tw_entries.add()
    e.uid = next_uid(obj)
    e.frame = int(frame)
    e.text = text
    return e


# ---------------------------------------------------------------------------
# SubRip (SRT) / WebVTT import & export
# ---------------------------------------------------------------------------

_TS_RE = re.compile(r'(?:(\d+):)?(\d+):(\d+)[,.](\d+)')

_TW_DIRECTIVES = ("BRANCH", "CHOICE", "OPT", "CAM", "GOTO", "END")


def _is_directive_line(line):
    """True for [BRANCH]/[CHOICE]/[OPT]/[CAM]/[GOTO]/[END] cue lines.

    NOTE: keep in sync with srt_branches.py (shipped with the demo
    project); the demo's test_srt_branches.py verifies parity.
    """
    s = line.strip()
    if len(s) < 3 or s[0] != "[" or s[-1] != "]":
        return False
    inner = s[1:-1].strip()
    if not inner:
        return False
    return inner.split(None, 1)[0] in _TW_DIRECTIVES


def _strip_directive_lines(text):
    """Remove branching/camera cue lines; plain [bracket] text is kept."""
    return "\n".join(ln for ln in text.split("\n")
                     if not _is_directive_line(ln)).strip()


def _ts_to_seconds(token):
    m = _TS_RE.search(token)
    if m is None:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    return (h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            + int(m.group(4)) / 1000.0)


def _seconds_to_ts(seconds, decimal_sep):
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000.0))
    return "%02d:%02d:%02d%s%03d" % (total_ms // 3600000,
                                     (total_ms // 60000) % 60,
                                     (total_ms // 1000) % 60,
                                     decimal_sep,
                                     total_ms % 1000)


def parse_subtitle_file(content):
    """Parse SRT or WebVTT text -> list of (start_sec, end_sec, text),
    sorted by start time."""
    cues = []
    for block in re.split(r'\n\s*\n', content.strip()):
        rows = [r for r in block.splitlines() if r.strip()]
        if not rows:
            continue
        if rows[0].strip().upper().startswith("WEBVTT"):
            rows = rows[1:]
            if not rows:
                continue
        tidx = None
        for j, r in enumerate(rows):
            if '-->' in r:
                tidx = j
                break
        if tidx is None:
            continue
        parts = rows[tidx].split('-->')
        t0 = _ts_to_seconds(parts[0])
        t1 = _ts_to_seconds(parts[1]) if len(parts) > 1 else None
        if t0 is None:
            continue
        if t1 is None or t1 <= t0:
            t1 = t0 + 1.0
        text = "\n".join(x.rstrip() for x in rows[tidx + 1:]).strip()
        # [BRANCH]/[CHOICE]/... lines are dialogue data, not subtitles
        text = _strip_directive_lines(text)
        if text:
            cues.append((t0, t1, text))
    cues.sort(key=lambda c: c[0])
    return cues


def collect_cues(obj, scene):
    """Subtitle entries -> (start_frame, end_frame, text) cues.
    A cue ends where the next entry begins (that's when the text is
    cleared); the last cue ends at the scene end (or +3 s)."""
    entries = sorted(obj.tw_entries, key=lambda e: (e.frame, e.uid))
    fps = scene_fps(scene)
    cues = []
    for i, e in enumerate(entries):
        if not e.text:
            continue
        end = None
        for nxt in entries[i + 1:]:
            end = nxt.frame
            break
        if end is None or end <= e.frame:
            end = max(int(scene.frame_end), e.frame + int(round(3.0 * fps)))
        cues.append((e.frame, end, e.text))
    return cues


def build_subtitle_file(cues, scene, webvtt=False):
    fps = scene_fps(scene)
    f0 = scene.frame_start
    sep = "." if webvtt else ","
    lines = ["WEBVTT", ""] if webvtt else []
    for i, (sf, ef, text) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append("%s --> %s" % (_seconds_to_ts((sf - f0) / fps, sep),
                                    _seconds_to_ts((ef - f0) / fps, sep)))
        lines.extend(text.split("\n"))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core: sync entries <-> keyframes, and drive the text body
# ---------------------------------------------------------------------------

def _sync_impl(obj):
    """Make entry frames follow their (possibly dragged/duplicated/deleted)
    timeline keys. Returns True if anything changed."""
    fc = _find_fcurve(obj)
    if fc is None:
        return False
    entries = obj.tw_entries
    points = sorted(fc.keyframe_points, key=lambda k: k.co.x)
    if not points:
        return False  # all keys deleted: entries stay (use "Clear" to reset)

    changed = False
    by_uid = {e.uid: e for e in entries}
    seen = set()        # uids encountered among the keys
    valid = set()       # uids of entries that have a key and must survive

    for k in points:
        k.interpolation = 'CONSTANT'
        uid = int(round(k.co.y))
        if uid in seen:
            # key was duplicated in the editor -> duplicate the subtitle line
            src = by_uid.get(uid)
            ne = entries.add()
            ne.uid = next_uid(obj)
            ne.text = src.text if src is not None else ""
            ne.frame = int(round(k.co.x))
            k.co = (float(ne.frame), float(ne.uid))
            by_uid[ne.uid] = ne
            valid.add(ne.uid)
            changed = True
            continue
        seen.add(uid)
        valid.add(uid)
        e = by_uid.get(uid)
        if e is None:
            # a key without an entry (e.g. pasted from another object)
            e = entries.add()
            e.uid = uid
            e.frame = int(round(k.co.x))
            e.text = ""
            by_uid[uid] = e
            changed = True
        else:
            nf = int(round(k.co.x))
            if nf != e.frame:
                e.frame = nf
                changed = True

    # entries whose key was deleted in the editor are removed
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].uid not in valid:
            entries.remove(i)
            changed = True

    if changed:
        if not (0 <= obj.tw_active_index < len(entries)):
            obj.tw_active_index = max(min(obj.tw_active_index, len(entries) - 1), 0)
        fc.update()
        _sort_entries(obj)
    return changed


def _update_body_impl(obj, scene):
    """Set the text body for the current scene frame (typewriter reveal)."""
    if obj.type != 'FONT':
        return
    entries = obj.tw_entries
    if not obj.tw_enabled or len(entries) == 0:
        return
    frame = int(scene.frame_current)
    active = None
    for e in entries:
        if e.frame <= frame and (active is None or e.frame >= active.frame):
            active = e
    if active is None:
        body = ""                                   # before the first key: empty
    else:
        n = compute_reveal_chars(len(active.text), frame - active.frame,
                                 scene_fps(scene), obj.tw_cps, obj.tw_reveal)
        body = active.text[:n]
    if obj.data.body != body:
        obj.data.body = body


def bake_typewriter(obj, scene, prefix="SubLine"):
    """Freeze the typewriter reveal into one text object per entry.

    Each baked object parents to obj's parent, copies its placement and
    text style, carries its cue as its own single subtitle line (typed
    live while the add-on runs) and scale keys (1 inside its span,
    0 outside - CONSTANT). Plain editable object animation: each cue's
    timing stays draggable in the Dope Sheet, and renders, scrubbing and
    the Graph Editor need no add-on and no code (without the add-on each
    cue shows as a clean full-line card). The saved file always keeps
    full cue text on baked objects - a save handler restores it, so live
    (partial) bodies never leak into renders without the add-on.
    Previous bakes (prefix*) are removed first; the source object's live
    typing switches off (tw_enabled False), so re-bake after edits.
    Returns the object count.
    """
    if obj is None or obj.type != 'FONT' or len(obj.tw_entries) == 0:
        return 0
    for o in [o for o in scene.objects if o.name.startswith(prefix)]:
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            pass
    # A re-bake must reuse the same names: the curve (and the per-object
    # scale action) a previous bake created stays behind as an orphan until
    # Blender purges it, and a squatting orphan would name this bake's
    # objects `...01.001`. Only zero-user blocks carrying *this* prefix are
    # dropped - the user's own data is never touched.
    for coll in (bpy.data.curves, bpy.data.meshes):
        for d in [d for d in coll if d.users == 0
                  and d.name.startswith(prefix)]:
            try:
                coll.remove(d)
            except Exception:
                pass
    for a in [a for a in bpy.data.actions if a.users == 0
              and not a.use_fake_user and a.name.startswith(prefix)]:
        try:
            bpy.data.actions.remove(a)
        except Exception:
            pass
    entries = sorted(obj.tw_entries, key=lambda e: e.frame)
    fps = scene_fps(scene)
    cps = max(float(obj.tw_cps), 0.01)
    f_end = int(scene.frame_end)
    n = 0
    for i, e in enumerate(entries):
        f0 = int(e.frame)
        f1 = int(entries[i + 1].frame) if i + 1 < len(entries) else f_end + 1
        curve = bpy.data.curves.new("%s%02d" % (prefix, i + 1), 'FONT')
        curve.body = e.text
        try:
            curve.align_x = obj.data.align_x
            curve.align_y = obj.data.align_y
            curve.size = obj.data.size
            for attr in ("extrude", "bevel_depth"):
                try:
                    setattr(curve, attr, getattr(obj.data, attr))
                except Exception:
                    pass
            for m in obj.data.materials:
                try:
                    curve.materials.append(m)
                except Exception:
                    pass
        except Exception:
            pass
        o = bpy.data.objects.new(curve.name, curve)
        scene.collection.objects.link(o)
        try:
            o.parent = obj.parent
            o.location = tuple(obj.location)
            o.rotation_euler = tuple(obj.rotation_euler)
        except Exception:
            pass
        # NB: no Build modifier - Build reveals text faces in tessellation
        # order (tail-first, face-proportional), not reading order, so it
        # scrambles the typing. The cue types live via its own subtitle
        # line instead (below); scale keys gate visibility on their own.
        o.scale = (0.0, 0.0, 0.0)
        try:
            f_start = int(scene.frame_start)
            o.scale = (1.0, 1.0, 1.0)
            o.keyframe_insert("scale", frame=f0)
            if f0 > f_start:
                # leading 0: CONSTANT extrapolation would otherwise hold
                # the opening 1.0 key before f0 (scale must read 0
                # everywhere outside the span)
                o.scale = (0.0, 0.0, 0.0)
                o.keyframe_insert("scale", frame=f0 - 1)
            if f1 <= f_end:
                o.scale = (0.0, 0.0, 0.0)
                o.keyframe_insert("scale", frame=f1)
            ad = o.animation_data
            if ad is not None and ad.action is not None:
                ad.action.name = "%s_anim" % o.name
                for fc in _action_fcurves(ad.action):
                    if fc.data_path == "scale":
                        for k in fc.keyframe_points:
                            k.interpolation = 'CONSTANT'
                        fc.update()
        except Exception:
            pass
        try:  # this cue is itself a live one-line typewriter object
            o.tw_enabled = True
            o.tw_cps = cps
            o.tw_reveal = obj.tw_reveal
            le = o.tw_entries.add()
            le.uid = next_uid(o)
            le.text = e.text
            le.frame = int(f0)   # frame callback inserts this line's key
            landed = _set_key(o, le.uid, int(f0))
            if landed != le.frame:
                le.frame = landed
            o.tw_active_index = 0
            _write_backup(o, force=True)
        except Exception:
            pass
        try:  # callbacks above may have typed a partial body; the file
            # must carry FULL text (renders without the add-on)
            o.data.body = e.text
        except Exception:
            pass
        n += 1
    obj.tw_enabled = False
    return n


def _process_object(obj, scene):
    sync_entries_with_keys(obj)
    _update_body_impl(obj, scene)
    _write_backup(obj)
    _BODY_STATE[obj.name] = (int(scene.frame_current), keys_signature(obj))


def sync_entries_with_keys(obj):
    global _IN_HANDLER
    if _IN_HANDLER:
        return _sync_impl(obj)
    _IN_HANDLER = True
    try:
        return _sync_impl(obj)
    finally:
        _IN_HANDLER = False


def update_object(obj, scene):
    global _IN_HANDLER
    if _IN_HANDLER:
        return _update_body_impl(obj, scene)
    _IN_HANDLER = True
    try:
        return _update_body_impl(obj, scene)
    finally:
        _IN_HANDLER = False


def _apply_subtitle_file(obj, filepath, scene, replace=True, insert_clears=True):
    """(Re)build subtitle entries and keyframes from an SRT/WebVTT file.
    Works both from operators and from handlers (key creation does not rely
    on the property update callbacks). Returns the number of text cues."""
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as fh:
        content = fh.read()
    cues = parse_subtitle_file(content)
    if not cues:
        return 0
    if replace:
        obj.tw_entries.clear()
        _delete_fcurve(obj)
        if _KEY_ID in obj.keys():
            del obj[_KEY_ID]
    fps = scene_fps(scene)
    f0 = scene.frame_start
    count = 0
    for i, (t0, t1, text) in enumerate(cues):
        e = _create_entry(obj, f0 + round(t0 * fps), text)
        count += 1
        if not insert_clears:
            continue
        endf = f0 + round(t1 * fps)
        if i + 1 < len(cues):
            nstart = f0 + round(cues[i + 1][0] * fps)
            if endf < nstart:
                _create_entry(obj, endf, "")   # hide until the next cue
        elif endf > e.frame:
            _create_entry(obj, endf, "")       # hide after the last cue
    for e in obj.tw_entries:
        landed = _set_key(obj, e.uid, e.frame)
        if landed != e.frame:              # frame claimed -> key skipped ahead
            e.frame = landed
    obj.tw_active_index = 0
    _sort_entries(obj)
    _write_backup(obj, force=True)
    update_object(obj, scene)
    _record_file_state(obj.name, filepath)
    return count


# ---------------------------------------------------------------------------
# Property update callbacks
# ---------------------------------------------------------------------------

def _entry_frame_updated(self, context):
    global _IN_HANDLER
    if _IN_HANDLER:
        return
    obj = self.id_data
    if not isinstance(obj, bpy.types.Object):
        return
    _IN_HANDLER = True
    try:
        landed = _set_key(obj, self.uid, self.frame)
        if landed != self.frame:
            # slid onto a frame claimed by another key: show where it landed
            # (callback is guarded by _IN_HANDLER, so no recursion)
            self.frame = landed
        _write_backup(obj)
        _update_body_impl(obj, context.scene)
    finally:
        _IN_HANDLER = False


def _entry_text_updated(self, context):
    global _IN_HANDLER
    if _IN_HANDLER:
        return
    obj = self.id_data
    if not isinstance(obj, bpy.types.Object):
        return
    update_object(obj, context.scene)
    _write_backup(obj)


def _obj_prop_updated(self, context):
    global _IN_HANDLER
    if _IN_HANDLER:
        return
    _IN_HANDLER = True
    try:
        _update_body_impl(self, context.scene)
        _write_backup(self)
    finally:
        _IN_HANDLER = False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class TW_SubtitleEntry(bpy.types.PropertyGroup):
    uid: bpy.props.IntProperty(default=-1)
    frame: bpy.props.IntProperty(
        name="Frame",
        description="Start frame of this subtitle. A matching timeline keyframe is kept "
                    "in sync; frames already claimed by another subtitle key are skipped",
        update=_entry_frame_updated,
    )
    text: bpy.props.StringProperty(
        name="Text",
        description="Subtitle text, revealed character by character starting at its frame",
        update=_entry_text_updated,
    )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class TW_OT_add_text_object(bpy.types.Operator):
    bl_idname = "tw.add_text_object"
    bl_label = "Add 3D Subtitle Text"
    bl_description = "Create a 3D text object set up for typewriter subtitles"
    bl_options = {'REGISTER', 'UNDO'}

    align_to_camera: bpy.props.BoolProperty(
        name="Align to Camera", default=True,
        description="Parent the text to the scene camera and frame it like a subtitle",
    )
    text: bpy.props.StringProperty(name="First Subtitle", default="Your subtitle here")

    def execute(self, context):
        scene = context.scene
        cam = scene.camera
        bpy.ops.object.text_add()
        obj = context.active_object
        if obj is None:
            self.report({'ERROR'}, "Could not create text object")
            return {'CANCELLED'}
        obj.name = "Subtitles"
        cu = obj.data
        cu.name = "Subtitles"
        cu.align_x = 'CENTER'
        cu.align_y = 'CENTER'
        cu.extrude = 0.005
        cu.resolution_u = 12

        if self.align_to_camera and cam is not None:
            obj.parent = cam
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.location = (0.0, -0.35, -2.0)
            cu.size = 0.22
        else:
            obj.location = scene.cursor_location
            cu.size = 0.5

        # simple always-visible material
        mat = bpy.data.materials.new("Subtitle Material")
        mat.use_nodes = True
        bsdf = None
        for n in mat.node_tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                bsdf = n
                break
        if bsdf is not None:
            try:
                bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
                bsdf.inputs["Emission Color"].default_value = (1.0, 1.0, 1.0, 1.0)
                bsdf.inputs["Emission Strength"].default_value = 0.25
            except Exception:
                try:
                    bsdf.inputs["Emission"].default_value = (1.0, 1.0, 1.0, 1.0)
                except Exception:
                    pass
        cu.materials.append(mat)

        _ensure_anim(obj)
        e = _create_entry(obj, scene.frame_current, self.text)
        obj.tw_active_index = len(obj.tw_entries) - 1
        update_object(obj, scene)

        # jump ahead so the freshly typed line is (almost) fully revealed
        scene.frame_current = e.frame + max(1, round(scene_fps(scene) * 0.8))
        self.report({'INFO'}, "Subtitle created — edit lines in the Subtitles panel")
        return {'FINISHED'}


class TW_OT_snap_to_camera(bpy.types.Operator):
    bl_idname = "tw.snap_to_camera"
    bl_label = "Snap to Camera"
    bl_description = "Parent this text object to the scene camera and place it like a subtitle"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'FONT'

    def execute(self, context):
        cam = context.scene.camera
        if cam is None:
            self.report({'ERROR'}, "Scene has no camera")
            return {'CANCELLED'}
        obj = context.active_object
        obj.parent = cam
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.location = (0.0, -0.35, -2.0)
        obj.rotation_euler = (0.0, 0.0, 0.0)
        obj.scale = (1.0, 1.0, 1.0)
        return {'FINISHED'}


def _insert_after_selected(obj, scene, text):
    """Insert a new subtitle right after the selected one, at
    selected.frame + 1. If that frame is claimed, the claiming entry and
    every entry before it shift one frame earlier (later entries keep their
    exact timing). Without a selection the subtitle starts at the current
    frame. Selects the new entry, updates the body and returns its frame."""
    global _IN_HANDLER
    _ensure_anim(obj)
    entries = obj.tw_entries
    if 0 <= obj.tw_active_index < len(entries):
        sel = entries[obj.tw_active_index]
        target = sel.frame + 1
        if any(e.frame == target for e in entries):
            # target frame claimed: shift the claiming entry and every
            # entry before it one frame earlier, then take the frame.
            # Ascending order -> each entry moves into the frame just
            # vacated by its predecessor, so nothing collides.
            doomed = sorted((e for e in entries if e.frame <= target),
                            key=lambda e: e.frame)
            _IN_HANDLER = True
            try:
                for e in doomed:
                    nf = e.frame - 1
                    _set_key(obj, e.uid, nf)
                    e.frame = nf
            finally:
                _IN_HANDLER = False
    else:
        target = scene.frame_current
    e = _create_entry(obj, target, text)
    new_uid = e.uid              # capture: _sort_entries rebuilds the collection
    landed = e.frame
    _sort_entries(obj)
    for i, it in enumerate(entries):
        if it.uid == new_uid:
            obj.tw_active_index = i
            break
    update_object(obj, scene)
    return landed


class TW_OT_add_entry(bpy.types.Operator):
    bl_idname = "tw.add_entry"
    bl_label = "Add Subtitle Key"
    bl_description = "Add a subtitle right after the selected one (at its frame + 1; " \
                     "occupied frames shift one frame earlier to keep the sequence). " \
                     "Without a selection it starts at the current frame"
    bl_options = {'REGISTER', 'UNDO'}

    text: bpy.props.StringProperty(name="Text", default="New subtitle")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'FONT'

    def execute(self, context):
        obj = context.active_object
        landed = _insert_after_selected(obj, context.scene, self.text)
        # jump straight into editing the new subtitle: the new entry is
        # selected (its Text field shows below the list) and the playhead
        # stands on its start frame, so typed characters appear immediately
        context.scene.frame_set(landed)
        self.report({'INFO'}, "Subtitle key added at frame %d" % landed)
        return {'FINISHED'}


class TW_OT_add_entry_dialog(bpy.types.Operator):
    bl_idname = "tw.add_entry_dialog"
    bl_label = "Add Subtitle (Type Text)"
    bl_description = "Type a subtitle and press Enter: it is inserted right after " \
                     "the selected one (at its frame + 1, shifting earlier lines if " \
                     "needed) and the playhead jumps to it"
    bl_options = {'REGISTER', 'UNDO'}

    text: bpy.props.StringProperty(
        name="Subtitle Text",
        description="Text of the new subtitle (multi-line allowed)",
        default="")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'FONT'

    def invoke(self, context, event):
        self.text = ""
        context.window_manager.invoke_props_dialog(self, title="New Subtitle")
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.active_object
        landed = _insert_after_selected(obj, context.scene, self.text)
        context.scene.frame_set(landed)
        self.report({'INFO'}, "Subtitle added at frame %d" % landed)
        return {'FINISHED'}


class TW_OT_remove_entry(bpy.types.Operator):
    bl_idname = "tw.remove_entry"
    bl_label = "Remove Subtitle Key"
    bl_description = "Remove the selected subtitle and its timeline keyframe"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'FONT'
                and 0 <= obj.tw_active_index < len(obj.tw_entries))

    def execute(self, context):
        obj = context.active_object
        idx = obj.tw_active_index
        e = obj.tw_entries[idx]
        _remove_key(obj, e.uid)
        obj.tw_entries.remove(idx)
        obj.tw_active_index = max(0, min(idx, len(obj.tw_entries) - 1))
        _write_backup(obj)
        if len(obj.tw_entries) == 0:
            obj.data.body = ""
        update_object(obj, context.scene)
        return {'FINISHED'}


class TW_OT_select_keys(bpy.types.Operator):
    bl_idname = "tw.select_keys"
    bl_label = "Select All Subtitle Keys"
    bl_description = "Select all timeline keyframes of this subtitle object"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'FONT' and _find_fcurve(obj) is not None

    def execute(self, context):
        obj = context.active_object
        fc = _find_fcurve(obj)
        count = 0
        for k in fc.keyframe_points:
            k.select_control_point = True
            k.select_left_handle = True
            k.select_right_handle = True
            count += 1
        fc.update()
        self.report({'INFO'}, "%d key(s) selected — drag them to retime subtitles" % count)
        return {'FINISHED'}


class TW_OT_clear_animation(bpy.types.Operator):
    bl_idname = "tw.clear_animation"
    bl_label = "Clear Subtitle Animation"
    bl_description = "Remove all subtitle entries and keyframes from this object"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'FONT'
                and (len(obj.tw_entries) or _find_fcurve(obj) is not None))

    def execute(self, context):
        obj = context.active_object
        obj.tw_entries.clear()
        _delete_fcurve(obj)
        if _KEY_ID in obj.keys():
            del obj[_KEY_ID]
        obj.data.body = ""
        obj.tw_active_index = 0
        _write_backup(obj, force=True)   # mirror the intentional empty state
        self.report({'INFO'}, "Subtitle animation cleared")
        return {'FINISHED'}


class TW_OT_export_subtitles(bpy.types.Operator):
    bl_idname = "tw.export_subtitles"
    bl_label = "Export Subtitles"
    bl_description = "Save this object's subtitle lines as an SRT (SubRip) or WebVTT file"
    filepath: bpy.props.StringProperty(name="File Path", subtype='FILE_PATH',
                                       default="subtitles.srt")
    filter_glob: bpy.props.StringProperty(default="*.srt;*.vtt", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'FONT' and len(obj.tw_entries)

    def invoke(self, context, event):
        obj = context.active_object
        if obj is not None and obj.tw_source_path:
            self.filepath = obj.tw_source_path   # default to the linked file
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.active_object
        scene = context.scene
        cues = collect_cues(obj, scene)
        if not cues:
            self.report({'WARNING'}, "No subtitle text to export")
            return {'CANCELLED'}
        if not self.filepath.lower().endswith((".srt", ".vtt")):
            self.filepath += ".srt"          # add a sensible extension
        webvtt = self.filepath.lower().endswith(".vtt")
        content = build_subtitle_file(cues, scene, webvtt)
        try:
            with open(self.filepath, 'w', encoding='utf-8') as fh:
                fh.write(content)
        except OSError as ex:
            self.report({'ERROR'}, "Could not write file: %s" % ex)
            return {'CANCELLED'}
        self.report({'INFO'}, "Exported %d cue(s) to %s" % (len(cues), self.filepath))
        return {'FINISHED'}


class TW_OT_import_subtitles(bpy.types.Operator):
    bl_idname = "tw.import_subtitles"
    bl_label = "Import Subtitles"
    bl_description = "Create subtitle entries and timeline keyframes from an SRT (SubRip) or WebVTT file"
    bl_options = {'REGISTER', 'UNDO'}
    filepath: bpy.props.StringProperty(name="File Path", subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.srt;*.vtt;*.txt", options={'HIDDEN'})
    replace: bpy.props.BoolProperty(
        name="Replace Existing", default=True,
        description="Remove this object's current subtitle lines first")
    insert_clears: bpy.props.BoolProperty(
        name="Insert Clears", default=True,
        description="Add empty lines at cue ends so the text hides between "
                    "cues, like in a video player")
    link: bpy.props.BoolProperty(
        name="Link to File", default=True,
        description="Remember this file on the object, so it can be reloaded "
                    "with one click after editing it externally")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'FONT'

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.active_object
        try:
            count = _apply_subtitle_file(obj, self.filepath, context.scene,
                                         replace=self.replace,
                                         insert_clears=self.insert_clears)
        except OSError as ex:
            self.report({'ERROR'}, "Could not read file: %s" % ex)
            return {'CANCELLED'}
        if count == 0:
            self.report({'WARNING'}, "No subtitle cues found in %s" % self.filepath)
            return {'CANCELLED'}
        if self.link:
            obj.tw_source_path = self.filepath
        self.report({'INFO'}, "Imported %d subtitle line(s)" % count)
        return {'FINISHED'}


class TW_OT_reload_subtitles(bpy.types.Operator):
    bl_idname = "tw.reload_subtitles"
    bl_label = "Reload Linked File"
    bl_description = "Re-apply the linked SRT/WebVTT file: subtitle lines and " \
                     "keyframes are rebuilt from it"
    bl_options = {'REGISTER', 'UNDO'}
    insert_clears: bpy.props.BoolProperty(
        name="Insert Clears", default=True,
        description="Add empty lines at cue ends so the text hides between "
                    "cues, like in a video player")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'FONT' and bool(obj.tw_source_path)

    def execute(self, context):
        obj = context.active_object
        try:
            count = _apply_subtitle_file(obj, obj.tw_source_path, context.scene,
                                         replace=True,
                                         insert_clears=self.insert_clears)
        except OSError as ex:
            self.report({'ERROR'},
                        "Could not read %s: %s" % (obj.tw_source_path, ex))
            return {'CANCELLED'}
        if count == 0:
            self.report({'WARNING'},
                        "No subtitle cues found in %s" % obj.tw_source_path)
            return {'CANCELLED'}
        self.report({'INFO'}, "Reloaded %d subtitle line(s) from file" % count)
        return {'FINISHED'}


def _do_reload_addon():
    """Swap the add-on to the code currently on disk (unregister -> reload
    module -> register). Never call while an operator method of this add-on
    is still on the Python stack: unregistering destroys those classes."""
    import importlib
    import sys
    mod = sys.modules.get(__name__)
    if mod is None:
        return
    try:
        mod.unregister()              # drop old classes/properties/handlers
    except Exception:
        pass
    importlib.reload(mod)             # load the new code from disk
    mod.register()
    print("Typewriter Subtitles reloaded: v%s"
          % ".".join(str(x) for x in mod.bl_info.get("version", ())))


class TW_OT_reload_addon(bpy.types.Operator):
    bl_idname = "tw.reload_addon"
    bl_label = "Reload Add-on (after update)"
    bl_description = "Reload this add-on's code from disk. After installing an " \
                     "update you do not need to uninstall/re-enable or restart — " \
                     "run this once and the new version is live"
    bl_options = {'REGISTER'}

    def execute(self, context):
        # Defer the swap to a timer: unregistering here would destroy this
        # very operator class while its execute() is still running (dangling
        # self -> crash). The timer fires after this call has fully unwound.
        try:
            bpy.app.timers.register(_do_reload_addon, first_interval=0.0)
        except Exception:
            pass
        self.report({'INFO'}, "Reloading Typewriter Subtitles from disk…")
        return {'FINISHED'}


class TW_OT_rebuild_keys(bpy.types.Operator):
    bl_idname = "tw.rebuild_keys"
    bl_label = "Rebuild Subtitle Keys"
    bl_description = ("Delete and re-create this object's timeline keyframes "
                      "from its subtitle lines (fixes lines that lost their keys)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'FONT'
                and len(obj.tw_entries) > 0)

    def execute(self, context):
        obj = context.active_object
        n = _rebuild_keys_impl(obj)
        update_object(obj, context.scene)
        _write_backup(obj, force=True)
        self.report({'INFO'}, "Rebuilt %d subtitle key(s)" % n)
        return {'FINISHED'}


class TW_OT_recover_backup(bpy.types.Operator):
    bl_idname = "tw.recover_backup"
    bl_label = "Recover Subtitles from Backup"
    bl_description = ("Restore subtitle lines, keys and settings from the "
                      "automatic backup stored on this object")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'FONT'
                and bool(obj.get(_BACKUP_ID)))

    def execute(self, context):
        obj = context.active_object
        n = _recover_from_backup_impl(obj, context.scene)
        if n < 0:
            self.report({'WARNING'},
                        "No usable backup on this object — "
                        "re-import the SRT file instead")
            return {'CANCELLED'}
        self.report({'INFO'},
                    "Recovered %d subtitle line(s) from backup" % n)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Game-engine export (UPBGE/BGE real-time play)
# ---------------------------------------------------------------------------
#
# Blender's frame handlers do not run inside the game engine, so timeline
# subtitles would stay frozen there. "Add Game Logic" bridges the gap: it
# exports the lines (as seconds, not frames) into a String game property and
# wires Always -> Python ("tw_game.update"). The driver (embedded above as
# TW_GAME_DRIVER_SOURCE, also shipped as tw_game.py) plays them back live.

def _game_data_json(obj, scene):
    """Canonical JSON export of this object's lines for the game driver."""
    import json
    fps = scene_fps(scene)
    f0 = scene.frame_start
    try:
        cps = float(obj.tw_cps)
    except Exception:
        cps = 15.0
    try:
        reveal = str(obj.tw_reveal)
    except Exception:
        reveal = 'LINEAR'
    cues = []
    for e in sorted(obj.tw_entries, key=lambda e: (e.frame, e.uid)):
        # empty lines included: they clear the text, like on the timeline
        cues.append([round((e.frame - f0) / fps, 4), e.text])
    return json.dumps({"version": 1, "cps": cps, "reveal": reveal,
                       "cues": cues}, ensure_ascii=False, sort_keys=True)


def _game_props(obj):
    """The object's game properties collection, or None."""
    try:
        game = obj.game
    except Exception:
        return None
    try:
        return game.properties
    except Exception:
        return None


def _game_data_status(obj, scene):
    """'current' | 'stale' | 'missing' | 'no-lines' | 'n/a'."""
    try:
        if len(obj.tw_entries) == 0:
            return 'no-lines'
    except Exception:
        return 'n/a'
    props = _game_props(obj)
    if props is None:
        return 'n/a'
    try:
        cur = props.get(_TW_GAME_DATA)
    except Exception:
        return 'missing'
    if cur is None:
        return 'missing'
    try:
        stored = cur.value
    except Exception:
        return 'missing'
    try:
        fresh = _game_data_json(obj, scene)
    except Exception:
        return 'n/a'
    return 'current' if stored == fresh else 'stale'


def _write_game_data(obj, scene):
    """Refresh the exported game data (only if it was set up)."""
    props = _game_props(obj)
    if props is None:
        return False
    try:
        prop = props.get(_TW_GAME_DATA)
    except Exception:
        return False
    if prop is None:
        return False
    try:
        prop.value = _game_data_json(obj, scene)
        return True
    except Exception:
        return False


def _ensure_game_text():
    txt = bpy.data.texts.get(_TW_GAME_TEXT)
    if txt is None:
        txt = bpy.data.texts.new(_TW_GAME_TEXT)
    if txt.as_string() != TW_GAME_DRIVER_SOURCE:
        txt.from_string(TW_GAME_DRIVER_SOURCE)
    return txt


def _ensure_game_bricks(obj):
    """Create Always+Python bricks (and link) unless already present."""
    try:
        sensors = obj.game.sensors
        controllers = obj.game.controllers
    except Exception as ex:
        return (False, "no game API (%s)" % ex)
    try:
        sens = sensors.get(_TW_GAME_SENSOR)
    except Exception:
        sens = None
    if sens is None:
        try:
            bpy.ops.logic.sensor_add(type='ALWAYS', name=_TW_GAME_SENSOR,
                                     object=obj.name)
        except TypeError:
            active = bpy.context.view_layer.objects.active
            try:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.logic.sensor_add(type='ALWAYS', name=_TW_GAME_SENSOR)
            finally:
                try:
                    bpy.context.view_layer.objects.active = active
                except Exception:
                    pass
        try:
            sens = sensors.get(_TW_GAME_SENSOR)
        except Exception:
            sens = None
        if sens is None:
            return (False, "could not create Always sensor")
    try:
        if hasattr(sens, 'use_pulse_true_level'):
            sens.use_pulse_true_level = True
    except Exception:
        pass
    try:
        ctrl = controllers.get(_TW_GAME_CTRL)
    except Exception:
        ctrl = None
    if ctrl is None:
        try:
            bpy.ops.logic.controller_add(type='PYTHON', name=_TW_GAME_CTRL,
                                         object=obj.name)
        except TypeError:
            active = bpy.context.view_layer.objects.active
            try:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.logic.controller_add(type='PYTHON', name=_TW_GAME_CTRL)
            finally:
                try:
                    bpy.context.view_layer.objects.active = active
                except Exception:
                    pass
        try:
            ctrl = controllers.get(_TW_GAME_CTRL)
        except Exception:
            ctrl = None
        if ctrl is None:
            return (False, "could not create Python controller")
    try:
        ctrl.mode = 'MODULE'
        ctrl.module = _TW_GAME_MODULE
    except Exception as ex:
        return (False, "could not configure controller (%s)" % ex)
    try:
        sens.link(ctrl)
    except Exception:
        pass
    return (True, "Always -> Python (%s)" % _TW_GAME_MODULE)


def _setup_game_logic_impl(obj, scene):
    if bpy.app.background:
        return (False, "run Blender with a UI to create logic bricks")
    _ensure_game_text()
    try:
        props = obj.game.properties
    except Exception as ex:
        return (False, "no game properties (%s)" % ex)
    if props.get(_TW_GAME_DATA) is None:
        active = bpy.context.view_layer.objects.active
        try:
            bpy.context.view_layer.objects.active = obj
            try:
                bpy.ops.object.game_property_new(type='STRING',
                                                 name=_TW_GAME_DATA)
            except TypeError:
                before = set(p.name for p in props)
                bpy.ops.object.game_property_new()
                fresh = [p for p in props if p.name not in before]
                if not fresh:
                    return (False, "property was not created")
                try:
                    fresh[0].name = _TW_GAME_DATA
                except Exception:
                    pass
                try:
                    fresh[0].type = 'STRING'
                except Exception:
                    pass
        except Exception as ex:
            return (False, "game_property_new failed (%s)" % ex)
        finally:
            try:
                bpy.context.view_layer.objects.active = active
            except Exception:
                pass
        if props.get(_TW_GAME_DATA) is None:
            return (False, "property was not created")
    if not _write_game_data(obj, scene):
        return (False, "could not write game data")
    return _ensure_game_bricks(obj)


class TW_OT_setup_game_logic(bpy.types.Operator):
    bl_idname = "tw.setup_game_logic"
    bl_label = "Add Game Logic"
    bl_description = ("Export this object's lines for the game engine and wire "
                      "Always -> Python (tw_game.update) so subtitles play when "
                      "you press P in UPBGE/BGE")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'FONT'
                and len(obj.tw_entries) > 0)

    def execute(self, context):
        obj = context.active_object
        ok, msg = _setup_game_logic_impl(obj, context.scene)
        if not ok:
            self.report({'ERROR'}, "Game logic setup failed: %s" % msg)
            return {'CANCELLED'}
        self.report({'INFO'}, "Game logic ready - press P to play (%s)" % msg)
        return {'FINISHED'}


class TW_OT_refresh_game_data(bpy.types.Operator):
    bl_idname = "tw.refresh_game_data"
    bl_label = "Refresh Game Data"
    bl_description = ("Re-export this object's lines to the game engine "
                      "(the export also refreshes automatically on edit)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'FONT' or len(obj.tw_entries) == 0:
            return False
        props = _game_props(obj)
        try:
            return props is not None and props.get(_TW_GAME_DATA) is not None
        except Exception:
            return False

    def execute(self, context):
        obj = context.active_object
        if _write_game_data(obj, context.scene):
            self.report({'INFO'}, "Game data refreshed")
            return {'FINISHED'}
        self.report({'WARNING'},
                    "No game data on this object - use Add Game Logic")
        return {'CANCELLED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class TW_OT_bake_typewriter(bpy.types.Operator):
    bl_idname = "tw.bake_typewriter"
    bl_label = "Bake Subtitles to Objects"
    bl_description = ("Freeze the typewriter reveal into one text object "
                      "per line (own subtitle key + scale keys; renders need "
                      "no add-on); live typing switches off until re-bake")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'FONT' \
            and len(obj.tw_entries) > 0

    def execute(self, context):
        obj = context.active_object
        sname = (getattr(context.scene, "tw_preview_set", "") or "").strip()
        n = bake_typewriter(obj, context.scene,
                            prefix=("%s_Line" % sname) if sname
                            else "SubLine")
        if n > 0:
            self.report({'INFO'}, "Baked %d subtitle objects (live typing off)"
                        % n)
            return {'FINISHED'}
        self.report({'WARNING'}, "Nothing baked (no lines?)")
        return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Story v2: set preview, sync sidecar, rename watch, validation
#
# All of it degrades gracefully: a project without story.py/story.yml next to
# the .blend simply gets "story not found" in the Story box, and the subtitle
# features keep working exactly as before.
# ---------------------------------------------------------------------------

_UID_PROP = "_tw_uid"          # invisible ID property = stable identity
_UID_SEQ = "tw_uid_seq"        # scene ID property: next uid number
_AUDIO_TAG = "TwAudio_"        # auto-managed timeline-audio strip prefix
_HERE = os.path.dirname(os.path.abspath(__file__))
_STORY_LIB_CACHE = {}          # story.py path -> (mtime, module)
_NAME_SNAPSHOT = {}            # uid -> (name, type) as of the last check
_CHECK_STATE = {"errors": [], "warnings": [], "bindings": [], "stamp": ""}


def _say(msg):
    """Console line for the story tools (`_log` belongs to the game driver)."""
    print("Typewriter Subtitles: " + str(msg), flush=True)


def story_path_of(scene):
    """Resolved story.yml path for editor tools ('' when unset/missing)."""
    try:
        raw = (scene.tw_story or "").strip()
    except Exception:
        raw = ""
    if not raw:
        raw = "//story.yml"
    try:
        path = bpy.path.abspath(raw)
    except Exception:
        path = raw
    if not path:
        path = os.path.join(_HERE, "story.yml")
    return path if os.path.isfile(path) else ""


def _load_story_lib(lib_path):
    """Import story.py from the story's folder (same trick as the driver)."""
    import importlib.util
    mtime = os.path.getmtime(lib_path)
    hit = _STORY_LIB_CACHE.get(lib_path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    spec = importlib.util.spec_from_file_location("tw_story_lib_addon",
                                                  lib_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _STORY_LIB_CACHE[lib_path] = (mtime, mod)
    return mod


def load_story(scene, quiet=False):
    """(lib, loaded) for the scene's story; (None, None) when unavailable.

    loaded is story.load_story_files(): {"errors","warnings","story","dir",
    "files","audio"}. Nothing here raises: validation output is for the panel.
    """
    path = story_path_of(scene)
    if not path:
        if not quiet:
            _say("story: no story.yml found next to the blend")
        return None, None
    try:
        lib = _load_story_lib(os.path.join(os.path.dirname(path), "story.py"))
    except Exception as ex:
        if not quiet:
            _say("story: parser story.py unreadable (%r)" % (ex,))
        return None, None
    try:
        return lib, lib.load_story_files(path)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        return lib, {"errors": ["story load failed: %r" % (ex,)],
                     "warnings": [], "story": {}, "dir": os.path.dirname(path),
                     "files": {}, "audio": {}}


def _scene_names():
    """(object names, action names, camera names) of the whole file."""
    return (sorted(o.name for o in bpy.data.objects),
            sorted(a.name for a in bpy.data.actions),
            sorted(o.name for o in bpy.data.objects if o.type == 'CAMERA'))


def _uid_of(block, stamp, seq):
    """Read (and optionally stamp) the invisible stable id of an ID."""
    try:
        uid = block.get(_UID_PROP)
    except Exception:
        return None, seq
    if isinstance(uid, str) and uid:
        return uid, seq
    if not stamp:
        return None, seq
    seq += 1
    uid = "u%d" % seq
    try:
        block[_UID_PROP] = uid
    except Exception:
        return None, seq
    return uid, seq


def live_uids(stamp=False, include=None):
    """{uid: {"name","type"}} over objects (cams as 'camera') + actions.

    The identity map the rename watch compares with the sidecar. `include`
    (a name set) aims the stamping at what the story references.
    """
    out, seq = {}, 0
    scene = bpy.context.scene
    if scene is not None:
        try:
            seq = int(scene.get(_UID_SEQ, 0))
        except Exception:
            seq = 0
    for o in bpy.data.objects:
        kind = "camera" if o.type == 'CAMERA' else "object"
        if include is not None and o.name not in include:
            continue
        uid, seq = _uid_of(o, stamp, seq)
        if uid:
            out[uid] = {"name": o.name, "type": kind}
    for a in bpy.data.actions:
        if include is not None and a.name not in include:
            continue
        uid, seq = _uid_of(a, stamp, seq)
        if uid:
            out[uid] = {"name": a.name, "type": "action"}
    if stamp and scene is not None:
        try:
            scene[_UID_SEQ] = seq
        except Exception:
            pass
    return out


def _key_range(action):
    """[f0, f1] over every keyframe of an action (Blender 5 layered API)."""
    lo = hi = None
    for fc in _action_fcurves(action):
        for k in fc.keyframe_points:
            lo = k.co.x if lo is None else min(lo, k.co.x)
            hi = k.co.x if hi is None else max(hi, k.co.x)
    if lo is None:
        return None
    return [int(round(lo)), int(round(hi))]


def live_action_ranges():
    """{action name: [f0, f1]} from the scene (Refresh Sync's source).

    Orphaned actions are skipped: a re-bake leaves the old `<obj>_anim`
    datablocks behind until Blender purges them, and the sidecar must not
    fill up with ranges nobody plays. Fake users count as kept-on-purpose
    (that is exactly what the story guard does).
    """
    out = {}
    for a in bpy.data.actions:
        if a.users == 0 and not a.use_fake_user:
            continue
        rng = _key_range(a)
        if rng is not None:
            out[a.name] = rng
    return out


def story_refs(lib, story):
    """(action names, object names, camera names) the story references."""
    acts, objs, cams = set(), set(), set()
    for _name, s in (story.get("sets", {}) or {}).items():
        if not isinstance(s, dict):
            continue
        for e in s.get("anims", []) or []:
            anim, err = (lib.normalize_anim(e) if isinstance(e, (str, dict))
                         else (None, "x"))
            if err or anim is None:
                continue
            if anim["type"] == "action":
                o, a, _ = lib.parse_action_ref(anim["value"])
                if o:
                    objs.add(o)
                if a:
                    acts.add(a)
            elif anim["type"] == "camera":
                cams.add(str(anim["value"]).strip())
    return acts, objs, cams


def fake_user_guard(lib, story):
    """Keep story actions alive across purge (nothing references them at
    save time: they only play when their set is previewed) -> count."""
    names, _objs, _cams = story_refs(lib, story)
    n = 0
    for a in bpy.data.actions:
        if a.name in names and not a.use_fake_user:
            a.use_fake_user = True
            n += 1
    return n


# ---------------------------------------------------------------------------
# per-set preview: the timeline plays ONE set at a time (set-local frames)
# ---------------------------------------------------------------------------

def set_entries_from_plan(obj, scene, plan, fps, frame_start):
    """Load one set's cues as this object's subtitle lines.

    Entries are rebuilt from scratch (set-local frames) so the object keeps
    every normal feature: live typing, draggable keys, the backup, SRT
    import/export. Returns the number of lines written.
    """
    frames = []
    for (start, _end, text) in plan["subs"]:
        f = int(frame_start) + int(round(start * fps))
        if frames and f <= frames[-1][0]:
            f = frames[-1][0] + 1        # two anims may collide on a frame
        frames.append((f, text))
    obj.tw_entries.clear()
    _delete_fcurve(obj)
    obj.tw_uid_counter = 0
    for f, text in frames:
        _create_entry(obj, f, text)
    _write_backup(obj, force=True)
    _update_body_impl(obj, scene)
    return len(frames)


def _slot_for(action, obj):
    """The action slot driving `obj` (created when the action has none)."""
    slots = list(getattr(action, "slots", []) or [])
    for s in slots:
        try:
            if s.identifier == "OB" + obj.name:
                return s
        except Exception:
            pass
    if slots:
        return slots[0]
    try:
        return action.slots.new(id_type='OBJECT', name=obj.name)
    except Exception:
        return None


def assign_set_actions(scene, plan):
    """Point every actor of the set at the set's action (+ its slot).

    Blender 5 wants the slot assigned AFTER the action (a slot of another
    action raises). Returns (assigned, missing) name lists.
    """
    assigned, missing = [], []
    for (oname, aname, _at, _wait, _dur) in plan["actions"]:
        obj = bpy.data.objects.get(oname)
        action = bpy.data.actions.get(aname)
        if obj is None or action is None:
            missing.append("%s@%s" % (oname, aname))
            continue
        ad = obj.animation_data or obj.animation_data_create()
        if ad.action is not action:
            try:
                ad.action = action
            except Exception:
                pass
        slot = _slot_for(action, obj)
        if slot is not None:
            try:
                ad.action_slot = slot
            except Exception:
                pass
        assigned.append(aname)
    return assigned, missing


def snap_opening_shot(scene, plan):
    """Copy the set's opening staged camera onto the render camera (+lens).

    The game moves the camera procedurally between the staged shots; the
    editor gets the opening framing so a preview (and a render) looks right
    without a camera bake.
    """
    cam = scene.camera
    if cam is None or not plan["shots"]:
        return ""
    shot = bpy.data.objects.get(plan["shots"][0][1])
    if shot is None or shot.type != 'CAMERA':
        return "shot '%s' is not a camera" % plan["shots"][0][1]
    try:
        bpy.context.view_layer.update()
        mat = shot.matrix_world.copy()
        if cam.parent is not None:
            mat = cam.parent.matrix_world.inverted() @ mat
        cam.matrix_basis = mat
        cam.data.lens = shot.data.lens
    except Exception as ex:
        return "camera snap failed: %r" % (ex,)
    return ""


def sync_speakers(scene, loaded, plan, fps, frame_start, set_name):
    """Rebuild the auto-managed timeline audio for this set.

    The game plays audio with `aud`; the editor plays the scene's sequencer,
    so each audio anim gets a sound strip at its offset (strips of other sets
    are left alone; only TwAudio_<set>_* are managed). Returns a message.
    """
    if not plan["audios"] and not any(
            s.name.startswith(_AUDIO_TAG + set_name)
            for s in getattr(getattr(scene, "sequence_editor", None),
                             "strips", ()) or ()):
        return ""
    try:
        ed = scene.sequence_editor or scene.sequence_editor_create()
    except Exception as ex:
        return "audio: no sequencer (%r)" % (ex,)
    tag = _AUDIO_TAG + set_name
    for s in [x for x in ed.strips if x.name.startswith(tag)]:
        try:
            ed.strips.remove(s)
        except Exception:
            pass
    if not plan["audios"]:
        return ""
    made = 0
    for i, (fname, at) in enumerate(plan["audios"]):
        path = (loaded.get("audio") or {}).get(fname, "")
        if not path or not os.path.isfile(path):
            continue
        try:
            strip = ed.strips.new_sound(name="%s%02d" % (tag, i + 1),
                                        filepath=path, channel=1,
                                        frame_start=int(frame_start)
                                        + int(round(at * fps)))
            made += 1
            del strip
        except Exception as ex:
            return "audio '%s' could not be previewed (%r)" % (fname, ex)
    try:
        scene.use_audio = made > 0
    except Exception:
        pass
    return "%d sound strip(s)" % made if made else ""


MENU_LEAD = 0.25         # seconds of tail where the menu is visible


def menu_preview(scene, plan, story):
    """Arm the choice menu for the previewed set ('' when it has no choice).

    The menu stays a plain, key-free object: the story's rule is that the
    *state* shows it (the game writes its text per tick), so the editor does
    the same - `_menu_tick` reveals it only over the tail of a set that ends
    in a choice. Nothing here writes animation data.
    """
    menu = bpy.data.objects.get("ChoiceMenu")
    if menu is None or menu.type != 'FONT':
        return "no ChoiceMenu text object"
    body = ""
    kind, target = plan["end"]
    if kind == "choice":
        ch = (story.get("choices", {}) or {}).get(target) or {}
        opts = [list(o) for o in ch.get("options", []) or []]
        body = menu_body_for(opts)
    try:
        menu["_tw_menu_choice"] = body
    except Exception as ex:
        return "menu arm failed: %r" % (ex,)
    _menu_tick(scene)
    return ""


def _menu_tick(scene):
    """Show ChoiceMenu only at the end of a set that ends in a choice.

    Skipped when the menu carries its own action (hand-authored keys always
    win) and in the game (handlers do not run there). Returns True when it
    changed something, so callers can avoid needless redraws.
    """
    if scene is None:
        return False
    menu = scene.objects.get("ChoiceMenu") if hasattr(scene, "objects") \
        else None
    if menu is None or menu.type != 'FONT':
        return False
    ad = menu.animation_data
    if ad is not None and ad.action is not None:
        return False
    body = str(menu.get("_tw_menu_choice", "") or "")
    try:
        lead = max(2, int(round(scene_fps(scene) * MENU_LEAD)))
        show = (bool(body)
                and int(scene.frame_current) >= int(scene.frame_end) - lead)
    except Exception:
        return False
    want = body if show else ""
    changed = False
    if menu.data.body != want:
        menu.data.body = want
        changed = True
    s = 1.0 if show else 0.0
    if max(abs(float(menu.scale[i]) - s) for i in range(3)) > 1e-6:
        menu.scale = (s, s, s)
        changed = True
    return changed


def menu_body_for(options):
    """Static menu text (cursor on option 1) - story.menu_body without
    importing the story lib (baked into the add-on on purpose)."""
    return "\n".join("%s %s: %s" % (">" if i == 0 else " ", o[0], o[1])
                     for i, o in enumerate(options))


# ---------------------------------------------------------------------------
# validation + Refresh Sync (story.sync.json / story.schema.json)
# ---------------------------------------------------------------------------

def story_check_impl(scene):
    """Validate the story files + the scene bindings -> report dict.

    Pure read. `_CHECK_STATE` mirrors the result for the panel, so the Story
    box can show it without re-reading files on every redraw.
    """
    lib, loaded = load_story(scene)
    rep = {"ok": False, "path": story_path_of(scene), "errors": [],
           "warnings": [], "bindings": [], "sets": [], "start": ""}
    if loaded is None:
        rep["errors"] = ["story.yml not found next to the blend "
                         "(set the Story File field)"]
        _CHECK_STATE.update(rep)
        return rep
    rep["errors"] = list(loaded["errors"])
    rep["warnings"] = list(loaded["warnings"])
    story = loaded["story"] or {}
    rep["sets"] = sorted(str(k) for k in (story.get("sets") or {}))
    rep["start"] = str(story.get("start") or "")
    if not rep["errors"]:
        objs, acts, cams = _scene_names()
        side = lib.load_sidecar(lib.sync_path_for(rep["path"]))
        rep["bindings"] = lib.check_bindings(story, objs,
                                             sorted(set(acts)
                                                    | set(side["actions"])),
                                             cams)
    rep["ok"] = not rep["errors"] and not rep["bindings"]
    rep["stamp"] = time.strftime("%H:%M:%S")
    _CHECK_STATE.update(rep)
    return rep


def refresh_sync_impl(scene, write_schema=True):
    """Refresh Sync: action ranges + uid map + schema, and fake users.

    The sidecar is what the game (and the timeline preview) needs to know how
    long each set action runs; the uid map is what the rename watch compares
    against. Written on save too, so it can never drift far.
    """
    lib, loaded = load_story(scene)
    rep = story_check_impl(scene)
    if lib is None:
        rep["written"] = []
        rep["note"] = "story.py not found next to story.yml"
        return rep
    path = rep["path"]
    side = lib.load_sidecar(lib.sync_path_for(path))
    acts, objs, cams = story_refs(lib, loaded["story"] if loaded else {})
    watch = set(acts) | set(objs) | set(cams)
    rep["fake_users"] = fake_user_guard(lib, (loaded or {}).get("story", {}))
    uids = dict(side["uids"])
    live = live_uids(stamp=True, include=watch)
    renames, new_ids, _missing = lib.diff_uids(uids, live)
    for uid, rec in live.items():
        uids[uid] = rec
    ranges = live_action_ranges()
    missing = sorted(a for a in acts if a not in ranges)
    payload = lib.sidecar_payload(uids, ranges)
    err = lib.save_sidecar(lib.sync_path_for(path), payload)
    rep["written"] = [] if err else [os.path.basename(
        lib.sync_path_for(path))]
    rep["ranges"] = len(ranges)
    rep["missing_ranges"] = missing
    rep["uids_new"] = len(new_ids)
    if err:
        rep["errors"] = rep["errors"] + [err]
    if missing:
        rep["warnings"] = rep["warnings"] + [
            "action '%s' has no keyframes (the game cannot time it)" % m
            for m in missing]
    if write_schema:
        cams = _scene_names()[2]
        try:
            audio = sorted(os.path.basename(p)
                           for p in ((loaded or {}).get("audio") or
                                     {}).values())
            sch = lib.build_schema((loaded or {}).get("story", {}),
                                   [o.name for o in bpy.data.objects],
                                   [a.name for a in bpy.data.actions],
                                   cams,
                                   sorted((loaded or {}).get("files", {})),
                                   audio)
            import json as _json
            with open(lib.schema_path_for(path), "w",
                      encoding="utf-8") as fh:
                _json.dump(sch, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            rep["written"].append(os.path.basename(
                lib.schema_path_for(path)))
        except Exception as ex:
            rep["warnings"].append("schema not written (%r)" % (ex,))
    rep["renames"] = renames
    _NAME_SNAPSHOT.clear()
    _NAME_SNAPSHOT.update({k: (v["name"], v["type"])
                           for k, v in live.items()})
    _CHECK_STATE.update({"stamp": time.strftime("%H:%M:%S")})
    return rep


# ---------------------------------------------------------------------------
# rename watch (stable uids -> targeted story.yml / [CAM] rewrite)
# ---------------------------------------------------------------------------

def _sidecar_uids(scene, lib=None):
    """The uid map recorded in story.sync.json ({} when unavailable)."""
    path = story_path_of(scene)
    if not path:
        return {}
    try:
        lib = lib or _load_story_lib(os.path.join(
            os.path.dirname(path), "story.py"))
        return lib.load_sidecar(lib.sync_path_for(path))["uids"]
    except Exception:
        return {}


def name_watch_scan(scene, force=False):
    """Detect renames of story-referenced blocks -> pending (or auto-fixed).

    Names are how the story refers to Blender data, so a rename breaks the
    binding. Each block carries an invisible `_tw_uid`, and story.sync.json
    records uid -> name; a mismatch is a rename. The depsgraph handler calls
    this (a cheap dict compare); with Scene.tw_autorewrite the reference is
    rewritten immediately, otherwise the panel offers Apply.
    """
    path = story_path_of(scene)
    if not path or (bpy.app.background and not force):
        return []        # headless runs stay deterministic (build/verify call
                         # this with force=True when they want the diff)
    try:
        lib = _load_story_lib(os.path.join(os.path.dirname(path), "story.py"))
    except Exception:
        return []
    live = live_uids(stamp=False)
    cur = {k: (v["name"], v["type"]) for k, v in live.items()}
    if not force and cur == _NAME_SNAPSHOT:
        return []
    _NAME_SNAPSHOT.clear()
    _NAME_SNAPSHOT.update(cur)
    renames, _new, _gone = lib.diff_uids(_sidecar_uids(scene, lib), live)
    renames = [r for r in renames if r["type"] in ("object", "action",
                                                   "camera")]
    if not renames:
        return []
    if scene.tw_autorewrite:
        rewrite_story_refs(scene, renames)
        return renames
    scene.tw_pending_renames.clear()
    for r in renames:
        it = scene.tw_pending_renames.add()
        it.uid, it.kind, it.old, it.new = (r["uid"], r["type"], r["old"],
                                           r["new"])
    _say("rename watch: %d pending (Sidebar > Subtitles > Apply Renames)"
         % len(renames))
    _redraw_panels()
    return renames


def rewrite_story_refs(scene, renames):
    """Rewrite references in story.yml (+ [CAM] lines of its .srt files).

    Only reference positions are touched (labels, set names and prose stay),
    files are replaced atomically, and a story with errors is never edited.
    Returns (applied notes, files written).
    """
    path = story_path_of(scene)
    if not path or not renames:
        return [], []
    lib, loaded = load_story(scene, quiet=True)
    if lib is None or loaded is None:
        return [], []
    if loaded["errors"]:
        return [], []          # never rewrite a story that does not parse
    targets = [(path, "yml")]
    for name in sorted(loaded.get("files", {})):
        p = loaded["files"][name].get("path")
        if p:
            targets.append((p, "srt"))
    applied, written = [], []
    for target, kind in targets:
        try:
            with open(target, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        new, ap = lib.rewrite_refs(text, renames, kind)
        if not ap or new == text:
            continue
        try:
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            os.replace(tmp, target)
        except OSError as ex:
            _say("rewrite failed for %s (%s)" % (target, ex))
            continue
        applied.extend("%s: %s" % (os.path.basename(target), a) for a in ap)
        if target not in written:
            written.append(target)
    if written:
        try:
            refresh_sync_impl(scene)     # re-record uid names + ranges
        except Exception:
            pass
    return applied, written


# ---------------------------------------------------------------------------
# the set preview (editor side of "press P is the full preview")
# ---------------------------------------------------------------------------

def preview_set_impl(scene, set_name, jump=True):
    """Switch the timeline context to one story set -> (ok, [messages]).

    Loads the set's cues as the subtitle lines, points every actor at the
    set's own action, snaps the render camera to the set's opening shot,
    fills the choice menu, swaps the auto-managed sound strips and sets the
    frame range - all set-local, so pressing Space plays just this set and
    pressing P plays the whole story. It only ever assigns; nothing here
    deletes an action or a keyframe.
    """
    msgs = []
    lib, loaded = load_story(scene, quiet=True)
    if lib is None or loaded is None:
        return False, ["story.yml/story.py not found next to the blend"]
    if loaded["errors"]:
        return False, ["story has errors - fix them first: "
                       + "; ".join(loaded["errors"][:3])]
    story, files = loaded["story"], loaded["files"]
    sets = story.get("sets", {}) or {}
    set_name = str(set_name or "").strip() or str(story.get("start") or "")
    if set_name not in sets:
        return False, ["no set '%s' (story has: %s)"
                       % (set_name, ", ".join(sorted(str(k) for k in sets)))]
    side = lib.load_sidecar(lib.sync_path_for(story_path_of(scene)))
    ranges = dict(side["actions"])
    ranges.update(live_action_ranges())
    fps = scene_fps(scene)
    plan = lib.set_plan(story, files, set_name, ranges, fps)
    f0 = int(scene.frame_start)
    want_end = f0 + int(round(lib.set_duration(plan) * fps))
    sub = _preview_target(scene)
    if sub is not None and sub.type == 'FONT':
        n = set_entries_from_plan(sub, scene, plan, fps, f0)
        msgs.append("%d subtitle line(s) on '%s'" % (n, sub.name))
        baked_here = [o for o in scene.objects
                      if o.name.startswith("%s_Line" % set_name)]
        if baked_here:
            # The set is baked, so its per-cue objects are the ones that show
            # (and type, while the add-on runs). Leaving the source enabled as
            # well would draw the same line twice, so this mirrors the bake:
            # baked set -> live typing off, unbaked set -> on.
            if sub.tw_enabled:
                sub.tw_enabled = False
            msgs.append("%d baked line(s) for this set carry the text - live "
                        "typing off (Bake to Objects refreshes them)"
                        % len(baked_here))
        elif not sub.tw_enabled:
            # Bake to Objects turns live typing off on its source; a preview
            # of an unbaked set must turn it back on or the cues you just
            # loaded would never show up.
            sub.tw_enabled = True
            msgs.append("live typing re-enabled on '%s' (a bake had it off)"
                        % sub.name)
        try:            # the story's cps is the authoritative typing speed
            want_cps = float(story.get("cps", 0) or 0)
            if want_cps > 0 and abs(float(sub.tw_cps) - want_cps) > 1e-3:
                sub.tw_cps = want_cps
                for o in scene.objects:      # baked lines of this set too
                    if o.type == 'FONT' and o.name.startswith(
                            "%s_Line" % set_name):
                        o.tw_cps = want_cps
                msgs.append("typing speed %g cps (from story cps)"
                            % want_cps)
        except Exception:
            pass
    else:
        msgs.append("no subtitle text object to load cues into (Add > Text > "
                    "Typewriter Subtitles)")
    assigned, missing = assign_set_actions(scene, plan)
    if assigned:
        msgs.append("actions: %s" % ", ".join(assigned))
    for m in missing:
        msgs.append("missing %s (add it or drop the anim)" % m)
    err = snap_opening_shot(scene, plan)
    if err:
        msgs.append(err)
    note = sync_speakers(scene, loaded, plan, fps, f0, set_name)
    if note:
        msgs.append("audio: " + note)
    note = menu_preview(scene, plan, story)
    if note:
        msgs.append("menu: " + note)
    guarded = fake_user_guard(lib, story)
    if guarded:
        msgs.append("fake user set on %d action(s)" % guarded)
    shown = sync_bake_visibility(scene, set_name, sorted(sets))
    if shown:
        msgs.append("%d baked line(s) of other sets hidden (frames overlap)"
                    % shown)
    try:
        if int(scene.frame_end) != want_end:
            scene.frame_end = want_end
            msgs.append("frame range %d-%d (set-local)" % (f0, want_end))
        scene.tw_preview_set = set_name
        if jump and sub is not None and len(sub.tw_entries):
            scene.frame_set(min(int(sub.tw_entries[0].frame), want_end))
        elif jump:
            scene.frame_set(f0)
    except Exception as ex:
        msgs.append("frame range skipped (%r)" % (ex,))
    try:
        story_check_impl(scene)
    except Exception:
        pass
    _redraw_panels()
    return True, msgs


def sync_bake_visibility(scene, set_name, set_names):
    """Keep only the previewed set's baked lines visible (viewport+render).

    Baked lines are set-local, so every set's bake occupies the same frames:
    showing two at once would stack their text. Only objects this feature
    created are touched - `<name>_Line##` where <name> is a set of this
    story - so hand-named text is never hidden. (Game-wise this is inert:
    the driver hides every `_Line` object anyway and types into the live one.)
    """
    n = 0
    for o in scene.objects:
        if o.type != 'FONT':
            continue
        base, sep, tail = o.name.partition("_Line")
        if not sep or base not in set_names or not tail.isdigit():
            continue
        want_hide = (base != set_name)
        if o.hide_render != want_hide or o.hide_viewport != want_hide:
            o.hide_render = want_hide
            o.hide_viewport = want_hide
            n += 1
    return n


def _preview_target(scene):
    """The text object the preview loads cues into.

    Order is deliberate: the object named `Subtitles` (the demo's, and the
    name the game driver types into) first, so previewing never lands on
    some other text object that happens to be selected; then the active 3D
    text (custom projects); then any text object that already has lines.
    """
    named = scene.objects.get("Subtitles") if scene is not None else None
    if named is None:
        named = bpy.data.objects.get("Subtitles")
    if named is not None and named.type == 'FONT':
        return named
    try:
        act = bpy.context.active_object
        if act is not None and act.type == 'FONT':
            return act
    except Exception:
        pass
    for o in scene.objects:
        if o.type == 'FONT' and len(o.tw_entries):
            return o
    return None


# ---------------------------------------------------------------------------
# panels/registration helpers
# ---------------------------------------------------------------------------

def write_sync_files(scene):
    """Rewrite story.sync.json (+ schema) from the live scene, read-only.

    Used by the save handler: unlike Refresh Sync it stamps nothing, so the
    file does not come back dirty right after saving.
    """
    path = story_path_of(scene)
    if not path:
        return
    try:
        lib = _load_story_lib(os.path.join(os.path.dirname(path), "story.py"))
    except Exception:
        return
    try:
        uids = dict(lib.load_sidecar(lib.sync_path_for(path))["uids"])
        for uid, rec in live_uids(stamp=False).items():
            uids[uid] = rec
        payload = lib.sidecar_payload(
            uids, live_action_ranges())
        err = lib.save_sidecar(lib.sync_path_for(path), payload)
        if err:
            _say(err)
        _CHECK_STATE["stamp"] = time.strftime("%H:%M:%S")
    except Exception as ex:
        _say("sync on save skipped (%r)" % (ex,))


def _redraw_panels():
    try:
        for area in bpy.context.screen.areas if bpy.context.screen else ():
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# story operators (preview / sync / rename / check)
# ---------------------------------------------------------------------------

_SET_PICK = {}     # enum identifier -> real set name (names can be odd)


def _preview_set_items(self, context):
    """One menu entry per story set (used by operator_enum + the popup)."""
    _SET_PICK.clear()
    lib, loaded = load_story(context.scene, quiet=True)
    if loaded is None or loaded["errors"]:
        return [("NONE", "No story (see Story box)", "")]
    story = loaded["story"] or {}
    start = str(story.get("start") or "")
    items = []
    for i, name in enumerate(sorted(str(k) for k in (story.get("sets") or {}))):
        ident = "s%d" % i
        _SET_PICK[ident] = name
        items.append((ident, name + (" (start)" if name == start else ""),
                      "Switch the timeline to set '%s'" % name))
    return items or [("NONE", "Story has no sets", "")]


class TW_OT_preview_set(bpy.types.Operator):
    bl_idname = "tw.preview_set"
    bl_label = "Preview Story Set"
    bl_description = ("Aim the timeline at one story set: its cues become the "
                      "subtitle lines, its actions go on the actors, its "
                      "opening shot frames the camera, its choice fills the "
                      "menu, its length sets the frame range. Nothing is "
                      "deleted - other sets keep their keys (press P for the "
                      "whole story)")
    bl_options = {'REGISTER', 'UNDO'}

    set_id: bpy.props.EnumProperty(name="Set", items=_preview_set_items)

    @classmethod
    def poll(cls, context):
        return bool(story_path_of(context.scene))

    def execute(self, context):
        name = _SET_PICK.get(self.set_id, self.set_id or "")
        ok, msgs = preview_set_impl(context.scene, name)
        for m in msgs:
            self.report({'INFO'} if ok else {'WARNING'}, m)
        if ok:
            _say("preview set '%s': %s" % (name, "; ".join(msgs) or "ok"))
        return {'FINISHED'} if ok else {'CANCELLED'}


class TW_OT_refresh_sync(bpy.types.Operator):
    bl_idname = "tw.refresh_sync"
    bl_label = "Refresh Sync"
    bl_description = ("Write story.sync.json (action frame ranges + the "
                      "rename watch's uid map) and story.schema.json (VSCode "
                      "completion) from the live scene; keeps story actions "
                      "from being purged. Also runs on save")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(story_path_of(context.scene))

    def execute(self, context):
        rep = refresh_sync_impl(context.scene)
        for e in rep.get("errors", []):
            self.report({'ERROR'}, e)
        for w in rep.get("warnings", [])[:6]:
            self.report({'WARNING'}, w)
        if rep.get("written"):
            self.report({'INFO'}, "wrote %s (%d ranges, %d uids)"
                        % (" + ".join(rep["written"]), rep.get("ranges", 0),
                           len(_sidecar_uids(context.scene))))
            return {'FINISHED'}
        self.report({'WARNING'}, rep.get("note") or "nothing written")
        return {'CANCELLED'}


class TW_OT_check_story(bpy.types.Operator):
    bl_idname = "tw.check_story"
    bl_label = "Check Story"
    bl_description = ("Validate story.yml + every .srt it references, and "
                       "cross-check the object/action/camera names against "
                       "this scene (results listed in the Story box)")

    @classmethod
    def poll(cls, context):
        return bool(story_path_of(context.scene))

    def execute(self, context):
        rep = story_check_impl(context.scene)
        for key, kind in (("errors", 'ERROR'), ("warnings", 'WARNING'),
                         ("bindings", 'ERROR')):
            for m in rep.get(key, []):
                self.report({kind}, "%s: %s" % (key, m))
        clean = not rep["errors"] and not rep["bindings"]
        _redraw_panels()
        if clean:
            self.report({'INFO'}, "story clean (%d sets)"
                        % len(rep.get("sets", [])))
            return {'FINISHED'}
        return {'CANCELLED'}


class TW_RenameItem(bpy.types.PropertyGroup):
    uid: bpy.props.StringProperty(name="Uid")
    kind: bpy.props.StringProperty(name="Kind")
    old: bpy.props.StringProperty(name="Was")
    new: bpy.props.StringProperty(name="Now")


def _wrap_label(layout, text, icon):
    """Plain wrapped text list (deliberately: no graphs, no fancy UI)."""
    line = ""
    for w in str(text).split():
        if line and len(line) + len(w) + 1 > 44:
            layout.label(text=line, icon=icon)
            line = w
        else:
            line = (line + " " + w).strip() or line
        if line == w and len(line) > 44:
            layout.label(text=line[:44], icon=icon)
            line = ""
    if line:
        layout.label(text=line, icon=icon)


def _story_box(layout, context):
    """Story (v2) box: set preview, sync + rename watch, validation lists."""
    scene = context.scene
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Story sets", icon='SEQUENCE')
    if scene.tw_preview_set:
        row.label(text="in: " + scene.tw_preview_set, icon='TIME')
    box.prop(scene, "tw_story", text="File")
    if not story_path_of(scene):
        box.label(text="No story.yml next to the blend", icon='INFO')
        return
    box.column(align=True).operator_enum("tw.preview_set", "set_id")
    row = box.row(align=True)
    row.operator("tw.refresh_sync", text="Refresh Sync", icon='FILE_REFRESH')
    row.operator("tw.check_story", text="Check", icon='CHECKMARK')
    if len(scene.tw_pending_renames):
        box.label(text="%d rename(s) break story refs:"
                  % len(scene.tw_pending_renames), icon='ERROR')
        row = box.row(align=True)
        row.template_list("TW_UL_renames", "", scene, "tw_pending_renames",
                          scene, "tw_rename_index", rows=2)
        box.operator("tw.apply_renames", text="Apply to Story",
                     icon='GREASEPENCIL')
    box.prop(scene, "tw_autorewrite", text="Auto-rewrite refs on rename")
    st = _CHECK_STATE
    n = 0
    for key, icon in (("errors", 'ERROR'), ("bindings", 'ERROR'),
                      ("warnings", 'INFO')):
        for m in st.get(key, []) or []:
            _wrap_label(box, "%s: %s" % (key[:-1], m), icon)
            n += 1
    if not n:
        box.label(text="story clean" + (" (checked %s)" % st["stamp"]
                                        if st.get("stamp") else ""),
                  icon='CHECKMARK')


class TW_UL_renames(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.label(text="%s: %s -> %s" % (item.kind, item.old,
                                                item.new),
                         icon='OUTLINER_DATA_' + ('OBJECT_DATA'
                                                  if item.kind == 'object' else
                                                  'ACTION' if item.kind ==
                                                  'action' else 'CAMERA_DATA'))
        else:
            layout.alignment = 'CENTER'
            layout.label(text="", icon='GREASEPENCIL')


class TW_OT_apply_renames(bpy.types.Operator):
    bl_idname = "tw.apply_renames"
    bl_label = "Apply Renames to Story"
    bl_description = ("Rewrite the renamed object/action/camera names into "
                      "story.yml (and [CAM] lines in the .srt files) - only "
                      "where they are used as references, never in labels")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.tw_pending_renames) > 0

    def execute(self, context):
        renames = [{"uid": it.uid, "type": it.kind, "old": it.old,
                    "new": it.new} for it in context.scene
                    .tw_pending_renames]
        applied, written = rewrite_story_refs(context.scene, renames)
        context.scene.tw_pending_renames.clear()
        _redraw_panels()
        if applied:
            self.report({'INFO'}, "rewrote %d reference(s) in %d file(s)"
                        % (len(applied), len(written)))
            for a in applied[:8]:
                _say("  " + a)
            return {'FINISHED'}
        self.report({'INFO'}, "no references matched (names were not used "
                              "in the story); uid map refreshed")
        return {'FINISHED'}


class TW_UL_entries(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.split(factor=0.22, align=True)
            row.prop(item, "frame", text="", emboss=False)
            row.prop(item, "text", text="", emboss=False)
        else:
            layout.alignment = 'CENTER'
            layout.label(text="", icon='DOT')


class VIEW3D_PT_tw_subtitles(bpy.types.Panel):
    bl_label = "Typewriter Subtitles"
    bl_idname = "VIEW3D_PT_tw_subtitles"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Subtitles"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        _story_box(layout, context)

        if obj is None or obj.type != 'FONT':
            layout.label(text="No 3D text object selected")
            layout.operator("tw.add_text_object", icon='FONT_DATA')
            return

        col = layout.column(align=True)
        col.prop(obj, "tw_enabled")
        col.prop(obj, "tw_cps")
        col.prop(obj, "tw_reveal", text="Reveal Curve")

        # health status + one-click recovery
        _fc = _find_fcurve(obj)
        _n_keys = len(_fc.keyframe_points) if _fc is not None else 0
        _n_lines = len(obj.tw_entries)
        _has_backup = bool(obj.get(_BACKUP_ID))
        _row = layout.row()
        if _n_lines == 0 and _n_keys == 0 and not _has_backup:
            _row.label(text="No subtitles on this object yet", icon='INFO')
        elif _n_lines and _n_lines == _n_keys:
            _row.label(text="%d lines, %d keys — synced" % (_n_lines, _n_keys),
                       icon='CHECKMARK')
        else:
            _row.label(text="%d lines, %d keys — needs repair" % (_n_lines, _n_keys),
                       icon='ERROR')
        if _n_lines:
            _fr = int(context.scene.frame_current)
            _act = None
            for _e in obj.tw_entries:
                if _e.frame <= _fr and (_act is None or _e.frame >= _act.frame):
                    _act = _e
            _row2 = layout.row()
            if _act is None:
                _row2.label(text="Frame %d: before first line" % _fr, icon='TIME')
            else:
                _shown = compute_reveal_chars(
                    len(_act.text), _fr - _act.frame, scene_fps(context.scene),
                    obj.tw_cps, obj.tw_reveal)
                _row2.label(text="Frame %d: showing %d/%d chars"
                            % (_fr, _shown, len(_act.text)), icon='TIME')
        _rec = layout.row(align=True)
        _rec.operator("tw.rebuild_keys", text="Rebuild Keys", icon='IPO_BEZIER')
        _rec.operator("tw.recover_backup", text="Recover Backup",
                      icon='RECOVER_LAST')

        layout.separator()
        row = layout.row()
        row.template_list("TW_UL_entries", "", obj, "tw_entries",
                          obj, "tw_active_index", rows=4)

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("tw.add_entry", icon='ADD')
        row.operator("tw.add_entry_dialog", text="", icon='TEXT')
        row = col.row(align=True)
        row.operator("tw.remove_entry", icon='REMOVE', text="Remove")
        row.operator("tw.select_keys", text="", icon='IPO_BEZIER')
        row.operator("tw.clear_animation", text="", icon='X')

        row = col.row(align=True)
        row.operator("tw.import_subtitles", text="Import", icon='IMPORT')
        row.operator("tw.export_subtitles", text="Export", icon='EXPORT')

        col.prop(obj, "tw_source_path", text="Linked File")
        row = col.row(align=True)
        row.operator("tw.reload_subtitles", text="Reload File", icon='FILE_REFRESH')
        row.prop(obj, "tw_auto_reload", text="Auto", icon='AUTO')
        col.operator("tw.bake_typewriter", text="Bake to Objects", icon='KEY_HLT')

        box = layout.box()
        _gst = _game_data_status(obj, context.scene)
        if _gst == 'current':
            box.label(text="Game engine: data current - press P", icon='CHECKMARK')
        elif _gst == 'stale':
            box.label(text="Game engine: data stale - refresh!", icon='ERROR')
        elif _gst == 'no-lines':
            box.label(text="Game engine: no lines yet", icon='INFO')
        elif _gst == 'n/a':
            box.label(text="Game engine: unavailable here", icon='INFO')
        else:
            box.label(text="Game engine: no game data", icon='INFO')
        _grow = box.row(align=True)
        _grow.operator("tw.setup_game_logic", text="Add Game Logic", icon='PLAY')
        _grow.operator("tw.refresh_game_data", text="Refresh", icon='FILE_REFRESH')

        layout.operator("tw.reload_addon", icon='FILE_REFRESH')

        if context.scene.camera is not None and obj.parent != context.scene.camera:
            layout.operator("tw.snap_to_camera", icon='CAMERA_DATA')

        if 0 <= obj.tw_active_index < len(obj.tw_entries):
            e = obj.tw_entries[obj.tw_active_index]
            layout.separator()
            layout.prop(e, "frame")
            layout.prop(e, "text")

        box = layout.box()
        box.scale_y = 0.75
        for line in ("Each key = one subtitle line, revealed char-by-char.",
                     "At the next key the text is cleared and the next",
                     "line starts appearing. Drag keys in the Timeline",
                     "to retime — the subtitles follow.",
                     "Ctrl+Shift+U: type a subtitle (works over panels too).",
                     "SRT/VTT linked files reload after external edits.",
                     "Lines are auto-backed-up; Rebuild/Recover fix mishaps.",
                     "Add Game Logic exports lines for UPBGE/BGE play.",
                     "Story box: Preview Set switches the timeline to a set.",
                     "Bake to Objects freezes typing into text + modifiers."):
            box.label(text=line)


def _add_menu_func(self, context):
    self.layout.separator()
    self.layout.operator("tw.add_text_object", text="Typewriter Subtitles",
                         icon='FONT_DATA')


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _modal_active():
    """True while a modal operator (e.g. dragging keyframes in the Graph
    Editor) is running. Writing to the fcurve that is being dragged can
    crash Blender, so handlers must not do it — key writes are deferred
    until the drag finishes instead."""
    try:
        wm = bpy.context.window_manager
    except Exception:
        return False
    if wm is None:
        return False
    for win in wm.windows:
        try:
            if len(win.modal_operators):
                return True
        except Exception:
            pass
    return False


_DEFER_SYNC_PENDING = False


def _deferred_sync_job():
    """bpy.app.timers job: run the full keys<->entries sync once no modal
    operator is active anymore."""
    global _DEFER_SYNC_PENDING, _IN_HANDLER
    if _modal_active():
        return 0.2        # still dragging — retry later
    _DEFER_SYNC_PENDING = False
    scene = bpy.context.scene
    if scene is None:
        return None
    _IN_HANDLER = True
    try:
        for name in list(_BODY_STATE.keys()):
            obj = bpy.data.objects.get(name)
            if obj is not None and obj.type == 'FONT' and len(obj.tw_entries):
                try:
                    _process_object(obj, scene)
                except Exception:
                    import traceback
                    traceback.print_exc()
    finally:
        _IN_HANDLER = False
    return None           # done — unregister the timer


def _schedule_deferred_sync():
    global _DEFER_SYNC_PENDING
    if _DEFER_SYNC_PENDING:
        return
    _DEFER_SYNC_PENDING = True
    try:
        bpy.app.timers.register(_deferred_sync_job, first_interval=0.0)
    except Exception:
        _DEFER_SYNC_PENDING = False


def _follow_keys_only(obj):
    """Drag-safe partial sync: move entry frames along with their keys.
    Reads keyframe positions but never writes to the fcurve."""
    fc = _find_fcurve(obj)
    if fc is None:
        return
    by_uid = {e.uid: e for e in obj.tw_entries}
    for k in fc.keyframe_points:
        e = by_uid.get(int(round(k.co.y)))
        if e is not None:
            nf = int(round(k.co.x))
            if nf != e.frame:
                e.frame = nf   # entry callback suppressed by _IN_HANDLER


_KEYMAPS = []       # stored (keymap, keymap item) for the add-subtitle hotkey
_FILE_STATE = {}    # object name -> (mtime_ns, size) of the linked subtitle file
_LAST_CHECK = {}    # object name -> last time the linked file was stat'ed


def _record_file_state(name, filepath):
    """Remember the current state of a linked subtitle file (baseline)."""
    try:
        st = os.stat(filepath)
        _FILE_STATE[name] = (st.st_mtime_ns, st.st_size)
    except OSError:
        _FILE_STATE.pop(name, None)


def _auto_reload_check(obj, scene):
    """If the linked subtitle file changed on disk, re-apply it.
    Debounced; never raises."""
    if not obj.tw_source_path or not obj.tw_auto_reload:
        return
    name = obj.name
    now = time.monotonic()
    if now - _LAST_CHECK.get(name, 0.0) < 0.5:
        return
    _LAST_CHECK[name] = now
    try:
        st = os.stat(obj.tw_source_path)
    except OSError:
        return
    state = (st.st_mtime_ns, st.st_size)
    if name not in _FILE_STATE or _FILE_STATE[name] == state:
        _FILE_STATE[name] = state        # first sighting: baseline only
        return
    try:
        count = _apply_subtitle_file(obj, obj.tw_source_path, scene)
    except Exception:
        import traceback
        traceback.print_exc()
        return
    if count:
        print("Typewriter Subtitles: auto-reloaded %d cue(s) from %s"
              % (count, obj.tw_source_path))


def _auto_reload_timer():
    """bpy.app.timers poller: watch linked subtitle files even while the
    playhead is not moving, so external edits are picked up without the
    user having to scrub first. Runs ~1x per second; very cheap (stat)."""
    if _modal_active():
        return 1.0            # never touch keys during a modal drag
    scene = bpy.context.scene
    if scene is None:
        return 1.0
    for obj in list(bpy.data.objects):
        try:
            if obj.type == 'FONT' and obj.tw_auto_reload and obj.tw_source_path:
                _auto_reload_check(obj, scene)
        except Exception:
            import traceback
            traceback.print_exc()
    return 1.0


@bpy.app.handlers.persistent
def tw_frame_change(scene, depsgraph=None):
    global _IN_HANDLER
    if _IN_HANDLER:
        return
    dragging = _modal_active()
    _IN_HANDLER = True
    try:
        _menu_tick(scene)
        for obj in scene.objects:
            if obj.type == 'FONT' and (len(obj.tw_entries) or obj.tw_auto_reload):
                try:
                    if not dragging and obj.tw_source_path and obj.tw_auto_reload:
                        _auto_reload_check(obj, scene)
                    if dragging:
                        _follow_keys_only(obj)
                        _update_body_impl(obj, scene)
                    else:
                        _process_object(obj, scene)
                except Exception:
                    import traceback
                    traceback.print_exc()
    finally:
        _IN_HANDLER = False
    if dragging:
        _schedule_deferred_sync()


@bpy.app.handlers.persistent
def tw_depsgraph_update(scene, depsgraph):
    """Fallback so dragging a key in the Dope Sheet updates subtitles even
    when the current frame does not change. While a modal drag is running,
    only the drag-safe partial sync is performed; the full sync (which may
    add/remove/re-uid keys) is deferred to a timer."""
    global _IN_HANDLER
    if _IN_HANDLER:
        return
    dragging = _modal_active()
    _IN_HANDLER = True
    try:
        _menu_tick(scene)
        for obj in scene.objects:
            if obj.type != 'FONT' or not (len(obj.tw_entries) or obj.tw_auto_reload):
                continue
            try:
                if not dragging and obj.tw_source_path and obj.tw_auto_reload:
                    _auto_reload_check(obj, scene)
                if dragging:
                    _follow_keys_only(obj)
                    _update_body_impl(obj, scene)
                    continue
                state = (int(scene.frame_current), keys_signature(obj))
                if _BODY_STATE.get(obj.name) == state:
                    continue
                _process_object(obj, scene)
            except Exception:
                import traceback
                traceback.print_exc()
    finally:
        _IN_HANDLER = False
    if dragging:
        _schedule_deferred_sync()
    if not dragging and scene is not None:
        try:
            name_watch_scan(scene)      # uid diff -> pending/auto-renames
        except Exception:
            pass


@bpy.app.handlers.persistent
def tw_load_post(*args):
    _BODY_STATE.clear()
    _BACKUP_STATE.clear()
    scene = bpy.context.scene
    if scene is None:
        return
    for obj in bpy.data.objects:
        if obj.type != 'FONT':
            continue
        if obj.tw_source_path:
            _record_file_state(obj.name, obj.tw_source_path)   # no reload on load
        # automatic repair: lines lost while the add-on was off are restored
        # from the backup; keys lost the same way are rebuilt from the lines
        try:
            n_entries = len(obj.tw_entries)
        except Exception:
            continue
        if n_entries == 0:
            try:
                data = _read_backup(obj)
            except Exception:
                data = None
            if data is not None and len(data[2]) > 0:
                try:
                    n = _recover_from_backup_impl(obj, scene)
                    print("Typewriter Subtitles: auto-recovered %d line(s) "
                          "on '%s' from backup" % (n, obj.name))
                    n_entries = max(n, 0)
                except Exception:
                    import traceback
                    traceback.print_exc()
        if n_entries > 0 and _find_fcurve(obj) is None:
            try:
                _rebuild_keys_impl(obj)
                print("Typewriter Subtitles: rebuilt %d missing key(s) "
                      "on '%s'" % (n_entries, obj.name))
            except Exception:
                import traceback
                traceback.print_exc()
        if len(obj.tw_entries):
            try:
                _update_body_impl(obj, scene)
            except Exception:
                pass


@bpy.app.handlers.persistent
def tw_load_post_story(*args):
    """Story-side load work (kept out of tw_load_post so the subtitle repair
    below can never be blocked by a story problem)."""
    scene = bpy.context.scene
    if scene is None or bpy.app.background:
        return
    _NAME_SNAPSHOT.clear()
    try:
        _menu_tick(scene)
        story_check_impl(scene)
        name_watch_scan(scene, force=True)
    except Exception:
        pass


@bpy.app.handlers.persistent
def tw_save_pre(*args):
    """Restore full cue text on baked one-line objects before saving.

    While the add-on runs, baked objects carry live (partial) bodies for
    the current frame; the saved file must hold FULL cue text so renders
    without the add-on show clean subtitles. Live state is frame-derived,
    so nothing needs stashing - tw_save_post recomputes it after the save.
    Never raises (must not endanger the save)."""
    for obj in bpy.data.objects:
        try:
            if obj.type != 'FONT' or not obj.tw_enabled:
                continue
            entries = obj.tw_entries
            if len(entries) != 1:
                continue
            if obj.data.body != entries[0].text:
                obj.data.body = entries[0].text
        except Exception:
            pass


@bpy.app.handlers.persistent
def tw_save_post(*args):
    """Refresh story.sync.json (+ schema) and recompute live bodies after a
    save - the sidecar can then never drift from the keys just written."""
    scene = bpy.context.scene
    if scene is None:
        return
    try:
        write_sync_files(scene)
    except Exception:
        pass
    for obj in bpy.data.objects:
        try:
            if obj.type == 'FONT' and len(obj.tw_entries):
                _update_body_impl(obj, scene)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    TW_SubtitleEntry,
    TW_OT_add_text_object,
    TW_OT_snap_to_camera,
    TW_OT_add_entry,
    TW_OT_add_entry_dialog,
    TW_OT_remove_entry,
    TW_OT_select_keys,
    TW_OT_clear_animation,
    TW_OT_rebuild_keys,
    TW_OT_recover_backup,
    TW_OT_setup_game_logic,
    TW_OT_refresh_game_data,
    TW_OT_bake_typewriter,
    TW_RenameItem,
    TW_UL_renames,
    TW_OT_preview_set,
    TW_OT_refresh_sync,
    TW_OT_check_story,
    TW_OT_apply_renames,
    TW_OT_import_subtitles,
    TW_OT_export_subtitles,
    TW_OT_reload_subtitles,
    TW_OT_reload_addon,
    TW_UL_entries,
    VIEW3D_PT_tw_subtitles,
)


def _import_menu_func(self, context):
    self.layout.operator("tw.import_subtitles",
                         text="Typewriter Subtitles (.srt/.vtt)", icon='FONT_DATA')


def _export_menu_func(self, context):
    self.layout.operator("tw.export_subtitles",
                         text="Typewriter Subtitles (.srt/.vtt)", icon='FONT_DATA')


def register():
    for c in classes:
        try:
            bpy.utils.register_class(c)
        except ValueError:
            pass                      # already registered (re-registration)
    for prop in ("tw_entries", "tw_active_index", "tw_enabled", "tw_cps",
                 "tw_reveal", "tw_source_path", "tw_auto_reload",
                 "tw_uid_counter"):
        if hasattr(bpy.types.Object, prop):
            try:
                delattr(bpy.types.Object, prop)
            except Exception:
                pass
    bpy.types.Object.tw_entries = bpy.props.CollectionProperty(type=TW_SubtitleEntry)
    bpy.types.Object.tw_active_index = bpy.props.IntProperty(default=0)
    bpy.types.Object.tw_enabled = bpy.props.BoolProperty(
        name="Enable Subtitles",
        description="Drive this text object's body from its subtitle keyframes",
        default=True, update=_obj_prop_updated)
    bpy.types.Object.tw_cps = bpy.props.FloatProperty(
        name="Characters / Second",
        description="Typing speed of the character-by-character reveal",
        default=15.0, min=0.1, soft_max=60.0, update=_obj_prop_updated)
    bpy.types.Object.tw_reveal = bpy.props.EnumProperty(
        name="Reveal Curve",
        description="How characters are distributed over time",
        items=[
            ('LINEAR', "Linear", "Constant typing speed"),
            ('SMOOTH', "Smooth", "Ease in and out of the typing duration"),
            ('EASE_OUT', "Ease Out", "Fast start, gentle finish"),
            ('CONSTANT', "Constant", "No typing — the full line appears at once "
             "at its key (classic subtitles; matches the Constant key "
             "interpolation shown in the Graph Editor)"),
        ],
        default='LINEAR', update=_obj_prop_updated)
    bpy.types.Object.tw_uid_counter = bpy.props.IntProperty(default=0)
    bpy.types.Object.tw_source_path = bpy.props.StringProperty(
        name="Subtitle File",
        description="SRT/WebVTT file linked to this object; edit it externally "
                    "and reload (or enable auto reload) instead of importing again",
        subtype='FILE_PATH')
    bpy.types.Object.tw_auto_reload = bpy.props.BoolProperty(
        name="Auto Reload",
        description="Watch the linked subtitle file and re-apply it automatically "
                    "when it changes on disk",
        default=False)

    for prop in ("tw_story", "tw_preview_set", "tw_autorewrite",
                 "tw_pending_renames", "tw_rename_index"):
        if hasattr(bpy.types.Scene, prop):
            try:
                delattr(bpy.types.Scene, prop)
            except Exception:
                pass
    bpy.types.Scene.tw_story = bpy.props.StringProperty(
        name="Story File",
        description="story.yml this project's sets live in (the game reads the "
                    "tw_story game property on GameDirector; this one is for the "
                    "editor tools)",
        default="//story.yml", subtype='FILE_PATH')
    bpy.types.Scene.tw_preview_set = bpy.props.StringProperty(
        name="Preview Set",
        description="Story set the timeline is currently aimed at (set by "
                    "Preview Story Set; also the Bake to Objects name prefix)",
        default="")
    bpy.types.Scene.tw_autorewrite = bpy.props.BoolProperty(
        name="Auto-rewrite refs on rename",
        description="Rewrite story.yml (+ [CAM] lines) the moment a referenced "
                    "object/action/camera is renamed; off = the panel offers "
                    "Apply first",
        default=False)
    bpy.types.Scene.tw_pending_renames = bpy.props.CollectionProperty(
        type=TW_RenameItem)
    bpy.types.Scene.tw_rename_index = bpy.props.IntProperty(default=0)

    bpy.app.handlers.frame_change_post.append(tw_frame_change)
    bpy.app.handlers.depsgraph_update_post.append(tw_depsgraph_update)
    bpy.app.handlers.load_post.append(tw_load_post)
    bpy.app.handlers.load_post.append(tw_load_post_story)
    bpy.app.handlers.save_pre.append(tw_save_pre)
    bpy.app.handlers.save_post.append(tw_save_post)
    bpy.types.VIEW3D_MT_add.append(_add_menu_func)
    bpy.types.TOPBAR_MT_file_import.append(_import_menu_func)
    bpy.types.TOPBAR_MT_file_export.append(_export_menu_func)
    try:
        if not bpy.app.timers.is_registered(_auto_reload_timer):
            bpy.app.timers.register(_auto_reload_timer, first_interval=1.0,
                                    persistent=True)
    except Exception:
        pass
    if not _KEYMAPS:                 # guard against double registration
        def _get_or_new_keymap(name, space_type, region_type):
            # KeyMaps.get() uses qualified keys, so match manually to avoid
            # creating duplicate keymaps on every (re)registration.
            for km in kc.keymaps:
                if (km.name == name and km.space_type == space_type
                        and km.region_type == region_type):
                    return km
            return kc.keymaps.new(name=name, space_type=space_type,
                                  region_type=region_type)

        try:
            kc = bpy.context.window_manager.keyconfigs.addon
            if kc is not None:
                # 1) '3D View' (WINDOW): the main viewport region.
                # 2) 'Window' (window level): catches the shortcut everywhere
                #    else — sidebar/N-panel, toolbars, headers, other editors.
                #    Region-specific addon keymaps never receive events for
                #    the sidebar (no dispatch handler exists for them), the
                #    window-level keymap is the only reliable way.
                for name, space, region in (("3D View", 'VIEW_3D', 'WINDOW'),
                                            ("Window", 'EMPTY', 'WINDOW')):
                    km = _get_or_new_keymap(name, space, region)
                    kmi = km.keymap_items.new("tw.add_entry_dialog", type='U',
                                              value='PRESS', ctrl=True,
                                              shift=True)
                    _KEYMAPS.append((km, kmi))
        except Exception:
            pass


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_export_menu_func)
    bpy.types.TOPBAR_MT_file_import.remove(_import_menu_func)
    bpy.types.VIEW3D_MT_add.remove(_add_menu_func)
    if tw_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(tw_frame_change)
    if tw_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(tw_depsgraph_update)
    if tw_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(tw_load_post)
    if tw_load_post_story in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(tw_load_post_story)
    if tw_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(tw_save_pre)
    if tw_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(tw_save_post)
    for job in (_deferred_sync_job, _auto_reload_timer, _do_reload_addon):
        try:
            if bpy.app.timers.is_registered(job):
                bpy.app.timers.unregister(job)
        except Exception:
            pass
    for km, kmi in list(_KEYMAPS):
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _KEYMAPS.clear()

    for prop in ("tw_entries", "tw_active_index", "tw_enabled", "tw_cps",
                 "tw_reveal", "tw_source_path", "tw_auto_reload",
                 "tw_uid_counter"):
        try:
            delattr(bpy.types.Object, prop)
        except Exception:
            pass
    for prop in ("tw_story", "tw_preview_set", "tw_autorewrite",
                 "tw_pending_renames", "tw_rename_index"):
        try:
            delattr(bpy.types.Scene, prop)
        except Exception:
            pass

    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass


if __name__ == "__main__":
    register()
