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
    "description": "3D text subtitles keyframed on the timeline. Each key starts a fresh "
                   "character-by-character (typewriter) reveal; the text is cleared "
                   "automatically when the next key starts. Keys are draggable in the "
                   "Timeline. Link an SRT or WebVTT file to reload it with one "
                   "click (or automatically) after editing it externally. Lines are "
                   "auto-backed-up on the object; the panel shows sync status and "
                   "one-click Rebuild / Recover. Add Game Logic exports lines "
                   "for real-time play in UPBGE/BGE. SRT [CAM] lines are understood, "
                   "not shown. v1.9.0 adds the story-v2 editor: Preview Set "
                   "switches the whole scene to one animation set, Refresh Sync "
                   "writes story.sync.json + story.schema.json, a rename watcher "
                   "keeps story.yml references honest, and Validate lists story "
                   "errors/warnings/bindings.",
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


def bake_typewriter(obj, scene, prefix="SubLine", tag=None):
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
    Previous bakes are removed first - anything this add-on tagged with
    `_tw_bake`, plus the legacy name-prefix match, never the source object and
    never anything else. Story sets are local-timed, so a stale bake from
    another set would overlap the new one. The source object's live typing
    switches off (tw_enabled False), so re-bake after edits.
    `tag` records which set a bake belongs to (per-set bakes: `<set>_Line##`).
    Returns the object count.
    """
    if obj is None or obj.type != 'FONT' or len(obj.tw_entries) == 0:
        return 0
    stale = []
    for o in scene.objects:
        if o == obj:
            continue
        try:
            tagged = bool(o.get(_BAKE_TAG))
        except Exception:
            tagged = False
        if tagged or o.name.startswith(prefix):
            stale.append(o)
    for o in stale:
        data = getattr(o, "data", None)
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            continue
        # Removing the object leaves its FONT datablock as an orphan, which
        # would steal the name on the next bake (main_Line01 -> .001), so drop
        # it while it has no users left.
        if data is not None and getattr(data, "users", 1) == 0:
            try:
                bpy.data.curves.remove(data)
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
        try:                      # so the next bake can find and replace it
            o[_BAKE_TAG] = tag or prefix
        except Exception:
            pass
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
    # shared with Preview Set: identical entry/key rules, seconds -> frames
    count = _load_cues_into(obj, scene, cues, replace=replace,
                            insert_clears=insert_clears)
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


# ===========================================================================
# Story v2: sets, preview, rename watching, sync sidecar, validation
# ===========================================================================
#
# The v2 pipeline is animation-centric: story.yml wires named SETS of
# animations (subs / action / camera / audio) that all play from t=0, and
# game_subtitles.py plays them in the engine. This add-on is the editor side
# of that contract:
#
#   Preview Set   switches the whole Blender context to one set (assign its
#                 actions, load its .srt cues, set the frame range, snap the
#                 opening staged shot, pose the choice menu, auto-manage
#                 speaker objects for audio) so Spacebar is a faithful
#                 timeline preview and P is the real thing.
#   Refresh Sync  writes story.sync.json (action frame ranges + the uid
#                 rename map the game driver reads) and story.schema.json
#                 (VSCode YAML completion) - also on every save.
#   Rename watch  keeps story.yml honest: references are Blender NAMES, so
#                 renaming in the Outliner would silently strand an
#                 `Obj@Act` token. Every referenced id carries an invisible
#                 `_tw_uid` stamp; the watcher diffs live names against the
#                 sidecar and offers a targeted, comment-preserving rewrite.
#   Validate      runs story.py's check_story / warn_story / check_bindings
#                 against the live scene and lists the results as text.
#
# story.py ships with the STORY (next to story.yml), not with the add-on, so
# it is imported from disk here exactly like the game driver does.

_UID_ID = "_tw_uid"        # leading "_" keeps it out of the Custom Props UI
_STORY_DEFAULT = "//story.yml"
_SYNC_NOTE = ("generated by Refresh Sync (add-on) - action frame ranges + "
              "rename map; refreshed on save")
_AUTO_TAG = "_tw_auto"          # marks add-on-managed helper objects
_BAKE_TAG = "_tw_bake"          # marks baked subtitle objects (+ their set)
_ROLE_SUBS = "subs"
_ROLE_MENU = "menu"
_ROLE_CAMERA = "camera"
_NLA_PREFIX = "TW_Set_"         # NLA tracks this add-on owns (for {at: s})
_SUBS_EXTS = (".srt", ".vtt")
_AUDIO_EXTS = (".ogg", ".wav", ".mp3", ".flac")

_STORY_LIB = {}        # abs story.py path -> (mtime_ns, module)
_STORY_BUNDLE = {}     # abs story.yml path -> (project signature, loaded)
_SIDECAR_CACHE = {}    # abs sync path -> (mtime_ns, sidecar dict)
_RENAME_STATE = [0.0, False, None]   # [last scan, diff seen, debounce sig]


def _story_abspath(scene):
    """Absolute path of the scene's story.yml (default //story.yml)."""
    raw = ""
    try:
        raw = scene.tw_story_path or ""
    except Exception:
        raw = ""
    if not raw.strip():
        raw = _STORY_DEFAULT
    try:
        return bpy.path.abspath(raw)
    except Exception:
        return raw


def _import_story_lib(story_path):
    """(module, error): story.py imported from disk beside story.yml.

    Deliberately NOT registered in sys.modules: the game driver loads its own
    copy under a different name and the two must not fight.
    """
    lib_path = os.path.join(os.path.dirname(story_path), "story.py")
    if not os.path.isfile(lib_path):
        return None, ("story.py is missing next to %s "
                      "(it ships with the story)" % story_path)
    key = os.path.abspath(lib_path)
    try:
        stamp = os.stat(key).st_mtime_ns
    except OSError as ex:
        return None, "cannot stat story.py (%s)" % ex
    hit = _STORY_LIB.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1], ""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(
            "tw_story_editor_lib", key)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as ex:
        return None, "story.py failed to import: %r" % (ex,)
    _STORY_LIB[key] = (stamp, mod)
    return mod, ""


def _stat_sig(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def _project_signature(story_path):
    """Fingerprint of story.yml + every .srt/.vtt/audio file near it.

    The parsed story is cached, so external edits (the whole point of the
    file-based v2 workflow) have to invalidate it - including new files
    appearing in ./subtitles or ./audio.
    """
    base = os.path.dirname(story_path)
    parts = [os.path.basename(story_path), _stat_sig(story_path)]
    for sub in ("", "subtitles", "audio"):
        d = os.path.join(base, sub) if sub else base
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if fn.lower().endswith(_SUBS_EXTS + _AUDIO_EXTS):
                parts.append(fn)
                parts.append(_stat_sig(os.path.join(d, fn)))
    return tuple(parts)


def story_bundle(scene):
    """(lib, loaded, error) for the scene's story.yml, parse cached.

    `loaded` is story.load_story_files(): {"errors", "warnings", "story",
    "dir", "files", "audio"}. `error` is a hard failure (no story.yml, no
    story.py, parser crash) - loaded["errors"] are story-content errors.
    """
    sp = _story_abspath(scene)
    if not sp or not os.path.isfile(sp):
        return None, None, "story file not found: %s" % (sp or "(unset)")
    lib, err = _import_story_lib(sp)
    if lib is None:
        return None, None, err
    key = os.path.abspath(sp)
    sig = _project_signature(sp)
    hit = _STORY_BUNDLE.get(key)
    if hit is not None and hit[0] == sig:
        return lib, hit[1], ""
    try:
        loaded = lib.load_story_files(sp)
    except Exception as ex:
        return lib, None, "story parse crashed: %r" % (ex,)
    _STORY_BUNDLE[key] = (sig, loaded)
    return lib, loaded, ""


def invalidate_story_cache():
    """Drop the cached parse (after any story.yml rewrite)."""
    _STORY_BUNDLE.clear()
    _SIDECAR_CACHE.clear()


def _sidecar_for(scene, lib):
    """Cached story.sync.json next to story.yml."""
    sp = _story_abspath(scene)
    path = lib.sync_path_for(sp)
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        stamp = -1
    hit = _SIDECAR_CACHE.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1], path
    data = lib.load_sidecar(path)
    _SIDECAR_CACHE[path] = (stamp, data)
    return data, path


def story_refs(lib, loaded):
    """Every Blender id NAME story.yml references.

    {"objects": {obj: [actions]}, "actions": {names}, "cameras": {names}}.
    Malformed entries are skipped - check_story already reported them.
    """
    objs, acts, cams = {}, set(), set()
    story = (loaded or {}).get("story") or {}
    for s in (story.get("sets") or {}).values():
        if not isinstance(s, dict):
            continue
        for e in s.get("anims", []):
            if not isinstance(e, (str, dict)):
                continue
            try:
                anim, err = lib.normalize_anim(e)
            except Exception:
                continue
            if err or not anim:
                continue
            if anim["type"] == "action":
                o, a, _x = lib.parse_action_ref(anim["value"])
                if o:
                    slot = objs.setdefault(o, [])
                    if a and a not in slot:
                        slot.append(a)
                if a:
                    acts.add(a)
            elif anim["type"] == "camera" and anim["value"]:
                cams.add(anim["value"])
    # [CAM Shot] lines inside the .srt files are scene-name references too
    for fdata in ((loaded or {}).get("files") or {}).values():
        for cam in (fdata.get("cams") or []):
            try:
                if cam[1]:
                    cams.add(cam[1])
            except Exception:
                pass
    return {"objects": objs, "actions": acts, "cameras": cams}


def scene_name_lists(scene):
    """(objects, actions, cameras) name lists for check_bindings/build_schema.

    sorted() over names only: sorting bpy structs raises TypeError.
    """
    objects = sorted(o.name for o in scene.objects)
    cameras = sorted(o.name for o in scene.objects if o.type == 'CAMERA')
    actions = sorted(a.name for a in bpy.data.actions)
    return objects, actions, cameras


def project_media_lists(story_path):
    """(srt_files, audio_files) name lists for the schema enums."""
    base = os.path.dirname(story_path)
    srt, audio = set(), set()
    for sub in ("", "subtitles"):
        d = os.path.join(base, sub) if sub else base
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if fn.lower().endswith(_SUBS_EXTS):
                srt.add(fn)
    for sub in ("audio", ""):
        d = os.path.join(base, sub) if sub else base
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if fn.lower().endswith(_AUDIO_EXTS):
                audio.add(fn)
    return sorted(srt), sorted(audio)


def action_frame_range(action):
    """[f0, f1] of an action's keys, or None when it has none.

    Blender 5 actions are layered: the legacy `action.fcurves` attribute is
    GONE (it raises AttributeError), so Action.frame_range is used first and
    _action_fcurves() (layers -> strips -> channelbags) is the fallback.
    """
    if action is None:
        return None
    try:
        if action.is_empty:
            return None
    except Exception:
        pass
    try:
        rng = action.frame_range
        f0, f1 = int(round(rng[0])), int(round(rng[1]))
        if f1 >= f0 >= 0 and (f0 or f1):
            return [f0, f1]
    except Exception:
        pass
    xs = []
    for fc in _action_fcurves(action):
        for k in fc.keyframe_points:
            xs.append(k.co.x)
    if not xs:
        return None
    return [int(round(min(xs))), int(round(max(xs)))]


def role_object(scene, role, fallbacks=()):
    """The object tagged `_tw_role == role`, else the first fallback name.

    build_scene.py tags Subtitles / ChoiceMenu / Camera so the add-on does
    not have to guess; untagged (v1) files still work via the names.
    """
    for o in scene.objects:
        try:
            if o.get("_tw_role") == role:
                return o
        except Exception:
            pass
    for nm in fallbacks:
        o = scene.objects.get(nm)
        if o is not None:
            return o
    return None
# ---------------------------------------------------------------------------
# Stable ids (invisible uids) + the rename watcher
# ---------------------------------------------------------------------------
#
# story.yml references Blender NAMES (custom-prop manual ids were rejected as
# worse UI), so a rename in the Outliner silently strands `Obj@Act` /
# `camera: Shot` tokens. Every referenced id therefore carries an invisible
# `_tw_uid` stamp; story.sync.json records uid -> the name story.yml currently
# uses. The watcher diffs live names against that record, and Apply performs a
# TARGETED rewrite (only the value token of action:/camera: keys - comments,
# ordering and formatting survive, the file is never re-serialised).

def _uid_of(idblock):
    try:
        v = idblock.get(_UID_ID)
    except Exception:
        return ""
    return v if isinstance(v, str) and v else ""


def _stamp_uid(idblock):
    """Ensure the invisible uid stamp on an object/action; returns it."""
    cur = _uid_of(idblock)
    if cur:
        return cur
    import uuid
    try:
        idblock[_UID_ID] = "tw-" + uuid.uuid4().hex[:12]
    except Exception:
        return ""
    return _uid_of(idblock)


def _kind_type(kind, idblock):
    if kind == "action":
        return "ACTION"
    try:
        return str(idblock.type)
    except Exception:
        return "OBJECT"


def stamped_ids():
    """[(kind, idblock, uid)] for every object/action carrying the stamp."""
    out = []
    for o in bpy.data.objects:
        u = _uid_of(o)
        if u:
            out.append(("obj", o, u))
    for a in bpy.data.actions:
        u = _uid_of(a)
        if u:
            out.append(("action", a, u))
    return out


def _resolve_by_recorded(refs, old_uids):
    """{recorded name: live id} for ids whose story.yml name is stale.

    Lets Refresh Sync/Preview still find an action or object that was renamed
    after story.yml was written (the uid bridges the gap).
    """
    live = {}
    for uid, rec in (old_uids or {}).items():
        if not isinstance(rec, dict):
            continue
        name = rec.get("name")
        if not isinstance(name, str) or not name:
            continue
        live[name] = uid
    by_rec_obj, by_rec_act = {}, {}
    for kind, idb, uid in stamped_ids():
        if uid not in live:
            continue
        if kind == "action":
            by_rec_act.setdefault(live[uid], idb)
        else:
            by_rec_obj.setdefault(live[uid], idb)
    return by_rec_obj, by_rec_act


def find_action(name, by_rec_act=None):
    """Action by live name, falling back to its recorded (pre-rename) name."""
    act = bpy.data.actions.get(name)
    if act is None and by_rec_act:
        act = by_rec_act.get(name)
    return act


def find_object(scene, name, by_rec_obj=None):
    """Scene object by live name, falling back to its recorded name."""
    obj = scene.objects.get(name)
    if obj is None and by_rec_obj:
        obj = by_rec_obj.get(name)
    return obj


def action_ranges(scene, refs, old_uids=None):
    """{story action name: [f0, f1]} for every action the story references.

    Keyed by the name story.yml uses (not the live name) so a renamed action
    still yields its range - the game driver looks ranges up by story name.
    """
    by_rec_obj, by_rec_act = _resolve_by_recorded(refs, old_uids or {})
    out = {}
    for name in sorted(refs["actions"]):
        rng = action_frame_range(find_action(name, by_rec_act))
        if rng:
            out[name] = rng
    return out


def build_uids(scene, refs, old_uids, stamp=True):
    """(uids, pending) for the sync sidecar.

    uids maps uid -> {"name", "type"} where "name" is the name story.yml
    currently uses. That is the whole trick: when a live id's name differs
    from its recorded name the OLD name is kept (so the pending rename stays
    visible and story.yml stays resolvable) instead of being overwritten -
    only Apply Renames promotes the new name.
    """
    uids, pending = {}, []
    if stamp:
        for name in sorted(set(refs["objects"]) | set(refs["cameras"])):
            obj = scene.objects.get(name)
            if obj is not None:
                _stamp_uid(obj)
        for name in sorted(refs["actions"]):
            act = bpy.data.actions.get(name)
            if act is not None:
                _stamp_uid(act)
    for kind, idb, uid in stamped_ids():
        live = idb.name
        rec = (old_uids or {}).get(uid)
        rec_name = rec.get("name") if isinstance(rec, dict) else None
        if isinstance(rec_name, str) and rec_name and rec_name != live:
            uids[uid] = {"name": rec_name,
                         "type": rec.get("type") or _kind_type(kind, idb)}
            pending.append({"uid": uid, "old": rec_name, "new": live,
                            "kind": _kind_type(kind, idb)})
        else:
            uids[uid] = {"name": live, "type": _kind_type(kind, idb)}
    # ids that are gone from the file but still named by story.yml: keep the
    # record so an undo / re-create / hand-fix does not lose the mapping
    named = set(refs["objects"]) | set(refs["cameras"]) | set(refs["actions"])
    for uid, rec in (old_uids or {}).items():
        if uid in uids or not isinstance(rec, dict):
            continue
        if rec.get("name") in named:
            uids[uid] = {"name": rec["name"], "type": rec.get("type", "OBJECT")}
    ordered = {u: uids[u] for u in sorted(uids)}
    pending.sort(key=lambda p: (p["kind"], p["old"]))
    return ordered, pending


def guard_fake_users(refs, old_uids=None):
    """Set actions must survive Blender's purge - count newly guarded.

    A set action is only assigned while its set is previewed, so between
    previews it has zero real users and Recursive Purge would eat it (taking
    the animation with it). use_fake_user is the cheap, honest fix.
    """
    _o, by_rec_act = _resolve_by_recorded(refs, old_uids or {})
    n = 0
    for name in sorted(refs["actions"]):
        act = find_action(name, by_rec_act)
        if act is None:
            continue
        try:
            if not act.use_fake_user:
                act.use_fake_user = True
                n += 1
        except Exception:
            pass
    return n


def _json_dump(payload):
    import json
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _write_text_if_changed(path, text):
    """Write only when the content actually differs (no mtime churn, so the
    save handler cannot loop and git diffs stay empty)."""
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return True


def _scene_belongs_to_story(scene, refs):
    """True when the scene holds at least one id story.yml references."""
    for name in list(refs["objects"]) + list(refs["cameras"]):
        if scene.objects.get(name) is not None:
            return True
    for name in refs["actions"]:
        if bpy.data.actions.get(name) is not None:
            return True
    return False


def refresh_sync_impl(scene, write=True, quiet=False):
    """(ok, messages, info) - the Refresh Sync core (operator + save handler).

    Stamps uids, fake-users the set actions, then writes story.sync.json
    (action ranges + rename map, read by game_subtitles.py) and
    story.schema.json (VSCode YAML completion).
    """
    lib, loaded, err = story_bundle(scene)
    if err:
        return False, [err], {}
    if loaded["errors"]:
        # structural/file errors: the story is unusable, so a fresh sidecar
        # would only mislead the game. Binding errors are NOT in here (they
        # are computed by the validator), so renames still refresh fine.
        return False, list(loaded["errors"]), {}
    sp = _story_abspath(scene)
    old = lib.load_sidecar(lib.sync_path_for(sp))
    refs = story_refs(lib, loaded)
    uids, pending = build_uids(scene, refs, old.get("uids") or {})
    ranges = action_ranges(scene, refs, old.get("uids") or {})
    guarded = guard_fake_users(refs, old.get("uids") or {})
    msgs = []
    info = {"uids": uids, "ranges": ranges, "pending": pending,
            "guarded": guarded, "refs": refs, "skipped": False}
    # Guard: this scene may not be the story's scene at all - a scratch file
    # saved inside the project folder would otherwise overwrite a good
    # sidecar with an empty one and silently break the game's action timing.
    if write and not _scene_belongs_to_story(scene, refs):
        info["skipped"] = True
        msgs.append("skipped: this scene has none of story.yml's "
                    "objects/actions - sidecars left untouched")
        return True, msgs, info
    if write:
        sync_text = _json_dump({"_note": _SYNC_NOTE, "uids": uids,
                                "actions": ranges})
        objects, actions, cameras = scene_name_lists(scene)
        srt_files, audio_files = project_media_lists(sp)
        schema_text = _json_dump(lib.build_schema(
            loaded["story"], objects, actions, cameras, srt_files, audio_files))
        try:
            w1 = _write_text_if_changed(lib.sync_path_for(sp), sync_text)
            w2 = _write_text_if_changed(lib.schema_path_for(sp), schema_text)
        except OSError as ex:
            return False, ["cannot write the sync files: %s" % ex], info
        msgs.append("story.sync.json: %d action range(s), %d uid(s) - %s"
                    % (len(ranges), len(uids),
                       "written" if w1 else "unchanged"))
        msgs.append("story.schema.json - %s" % ("written" if w2 else
                                                "unchanged"))
    missing = sorted(n for n in refs["actions"] if n not in ranges)
    if missing:
        msgs.append("no frame range for action(s): %s" % ", ".join(missing))
    if guarded and not quiet:
        msgs.append("fake-user guard set on %d action(s)" % guarded)
    if pending:
        msgs.append("%d rename(s) pending - see the Story panel" % len(pending))
    invalidate_story_cache()
    return True, msgs, info
# ---------------------------------------------------------------------------
# Rename detection + targeted story.yml rewrite
# ---------------------------------------------------------------------------

_RE_ACT_TOKEN = re.compile(
    r'(?P<head>(?<![\w-])action\s*:\s*)'
    r'(?P<val>"[^"]*"|\'[^\']*\'|[^\s,\}\]]+)')
_RE_CAM_TOKEN = re.compile(
    r'(?P<head>(?<![\w-])camera\s*:\s*)'
    r'(?P<val>"[^"]*"|\'[^\']*\'|[^\s,\}\]]+)')
_RE_SRT_CAM = re.compile(r'^(?P<head>\s*\[\s*CAM\s+)(?P<shot>[^\]\s]+)'
                         r'(?P<tail>\s*\]\s*)$', re.IGNORECASE)


def rewrite_story_yaml(path, obj_renames, act_renames):
    """(replacements, new_text, had_bom, eol): rename story.yml tokens.

    Line-targeted on purpose: story.yml is the user's authored source, so its
    comments, key order and formatting must survive. Only the VALUE token of
    an `action:` / `camera:` key is touched, comment lines are skipped, and
    unknown tokens are left exactly as they were.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    total = 0
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#"):
            continue
        new = ln
        for rex, is_cam in ((_RE_ACT_TOKEN, False), (_RE_CAM_TOKEN, True)):
            changed = []

            def _sub(m, is_cam=is_cam, changed=changed):
                val = m.group("val")
                quote, body = "", val
                if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                    quote, body = val[0], val[1:-1]
                if is_cam:
                    fixed = obj_renames.get(body, body)
                else:
                    bits = body.split("@")
                    if len(bits) != 2:
                        return m.group(0)
                    fixed = "%s@%s" % (obj_renames.get(bits[0], bits[0]),
                                       act_renames.get(bits[1], bits[1]))
                if fixed == body:
                    return m.group(0)
                changed.append(fixed)
                return m.group("head") + quote + fixed + quote
            new = rex.sub(_sub, new)
            total += len(changed)      # subn() counts no-op matches too
        lines[i] = new
    return total, ("\xef\xbb\xbf" if bom else "") + eol.join(lines), bom, eol


def rewrite_srt_cams(path, obj_renames):
    """(replacements, new_text): rename [CAM Shot] lines in one .srt file.

    Same targeted rule as the YAML rewrite: only the shot token of a [CAM]
    directive line changes, dialogue text is never touched.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    total = 0
    for i, ln in enumerate(lines):
        m = _RE_SRT_CAM.match(ln)
        if m is None:
            continue
        fixed = obj_renames.get(m.group("shot"))
        if not fixed or fixed == m.group("shot"):
            continue
        lines[i] = m.group("head") + fixed + m.group("tail")
        total += 1
    return total, ("\xef\bb\xbf" if bom else "") + eol.join(lines), bom


def scan_renames_impl(scene):
    """Diff live names against the sidecar -> [(uid, old, new, kind)].

    Read-only: safe to call from depsgraph_update_post (mutating an RNA
    collection inside that handler re-enters it, so the caller defers the
    list update to a timer).
    """
    lib, loaded, err = story_bundle(scene)
    if err or loaded is None:
        return []
    sidecar, _p = _sidecar_for(scene, lib)
    recorded = sidecar.get("uids") or {}
    if not recorded:
        return []
    found = []
    for kind, idb, uid in stamped_ids():
        rec = recorded.get(uid)
        if not isinstance(rec, dict):
            continue
        old = rec.get("name")
        if isinstance(old, str) and old and old != idb.name:
            found.append({"uid": uid, "old": old, "new": idb.name,
                          "kind": rec.get("type") or _kind_type(kind, idb)})
    found.sort(key=lambda d: (d["kind"], d["old"]))
    return found


def _pending_rows(scene):
    try:
        return [(r.uid, r.old, r.new, r.kind) for r in scene.tw_renames]
    except Exception:
        return []


def sync_pending_list(scene, found):
    """Refresh scene.tw_renames from a scan (idempotent, order-stable)."""
    want = {(d["uid"], d["new"]) for d in found}
    have = {(u, n) for (u, _o, n, _k) in _pending_rows(scene)}
    if want == have:
        return 0
    scene.tw_renames.clear()
    for d in found:
        row = scene.tw_renames.add()
        row.uid, row.old, row.new, row.kind = d["uid"], d["old"], d["new"], d["kind"]
    scene.tw_renames_index = 0
    return len(found)


def promote_sidecar_names(story_path, rows):
    """Point the sidecar's uid records at the NEW names.

    build_uids() deliberately keeps the name story.yml uses, so without this
    step Apply would rewrite the YAML and then immediately re-report the same
    rename (the sidecar would still claim the old name).
    """
    path = os.path.splitext(story_path)[0] + ".sync.json"
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            side = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(side, dict):
        return False
    uids = side.get("uids")
    if not isinstance(uids, dict):
        return False
    for row in rows:
        uid = row[0] if isinstance(row, (tuple, list)) else row.get("uid")
        new = row[2] if isinstance(row, (tuple, list)) else row.get("new")
        rec = uids.get(uid)
        if isinstance(rec, dict) and new:
            rec["name"] = new
    try:
        _write_text_if_changed(path, _json_dump(
            {"_note": side.get("_note") or _SYNC_NOTE,
             "uids": {u: uids[u] for u in sorted(uids)},
             "actions": side.get("actions") or {}}))
    except OSError:
        return False
    return True


def apply_renames_impl(scene):
    """(count, messages): rewrite story.yml for every pending rename.

    Also promotes the sidecar's recorded names and drops the pending list, so
    the game driver and the validator immediately agree with the scene.
    """
    rows = _pending_rows(scene)
    if not rows:
        return 0, ["no pending renames"]
    lib, loaded, err = story_bundle(scene)
    if err:
        return 0, [err]
    sp = _story_abspath(scene)
    obj_renames, act_renames = {}, {}
    for uid, old, new, kind in rows:
        if kind == "ACTION":
            act_renames[old] = new
        else:
            obj_renames[old] = new
    try:
        n, text, bom, eol = rewrite_story_yaml(sp, obj_renames, act_renames)
    except OSError as ex:
        return 0, ["cannot rewrite story.yml: %s" % ex]
    try:
        with open(sp, "w", encoding="utf-8-sig" if bom else "utf-8",
                  newline="") as fh:
            fh.write(text)
    except OSError as ex:
        return 0, ["cannot write story.yml: %s" % ex]
    # [CAM Shot] directives live in the .srt files, so a renamed camera has
    # references there too - rewrite them in the same pass
    if obj_renames:
        for fname, fdata in sorted(((loaded.get("files") or {}).items())):
            fpath = fdata.get("path")
            if not fpath or not os.path.isfile(fpath):
                continue
            try:
                sn, stext, sbom = rewrite_srt_cams(fpath, obj_renames)
            except OSError:
                continue
            if not sn:
                continue
            try:
                with open(fpath, "w",
                          encoding="utf-8-sig" if sbom else "utf-8",
                          newline="") as fh:
                    fh.write(stext)
                n += sn
            except OSError:
                pass
    promote_sidecar_names(sp, rows)
    invalidate_story_cache()
    # with the sidecar promoted, the refresh below agrees with story.yml and
    # the rename is not reported again
    ok, msgs, _info = refresh_sync_impl(scene)
    scene.tw_renames.clear()
    scene.tw_renames_index = 0
    out = ["story.yml: %d reference(s) rewritten for %d rename(s)"
           % (n, len(rows))]
    out.extend(msgs)
    if not ok:
        out.append("WARNING: the story still reports errors - open Validate")
    return n, out


def forget_renames_impl(scene):
    """Accept the live names WITHOUT touching story.yml.

    For when the user already fixed story.yml by hand (or the rename was
    intentional and the reference is meant to disappear): the sidecar is
    re-stamped from the scene and the pending list is cleared.
    """
    n = len(scene.tw_renames)
    invalidate_story_cache()
    lib, loaded, err = story_bundle(scene)
    if not err and loaded is not None and not loaded["errors"]:
        sp = _story_abspath(scene)
        path = lib.sync_path_for(sp)
        side = lib.load_sidecar(path)
        uids = side.get("uids") or {}
        for kind, idb, uid in stamped_ids():
            if uid in uids and isinstance(uids[uid], dict):
                uids[uid]["name"] = idb.name
                uids[uid]["type"] = _kind_type(kind, idb)
        try:
            _write_text_if_changed(
                path, _json_dump({"_note": _SYNC_NOTE,
                                  "uids": {u: uids[u] for u in sorted(uids)},
                                  "actions": side.get("actions") or {}}))
        except OSError:
            pass
    scene.tw_renames.clear()
    scene.tw_renames_index = 0
    invalidate_story_cache()
    return n


# ---------------------------------------------------------------------------
# Preview Set (Option B authoring: one set in the editor at a time)
# ---------------------------------------------------------------------------

def _slot_id_type(obj):
    return 'OBJECT'


def _fix_action_slot(obj, ad, act):
    """Bind AnimData to a slot of `act` so it actually evaluates.

    Blender 5 actions are slotted: `ad.action = act` on an object the action
    was not authored on leaves action_slot None and the action SILENTLY does
    nothing. Slots are matched by target id type, so prefer the slot named
    after this object, then the only suitable one (a renamed object keeps its
    original "OB<oldname>" slot), and only then create a fresh slot.
    """
    try:
        if ad.action_slot is not None:
            return True
    except Exception:
        return False
    want = "OB" + obj.name
    slot = None
    try:
        suitable = list(ad.action_suitable_slots)
    except Exception:
        suitable = []
    for s in suitable:
        if s.identifier == want:
            slot = s
            break
    if slot is None and len(suitable) == 1:
        slot = suitable[0]
    if slot is None:
        try:
            slot = act.slots.new(id_type=_slot_id_type(obj), name=obj.name)
        except Exception:
            return False
    try:
        ad.action_slot = slot
    except Exception:
        return False
    try:
        return ad.action_slot is not None
    except Exception:
        return False


def clear_preview_nla(ad):
    """Drop the NLA tracks this add-on created for a previous preview."""
    try:
        tracks = [t for t in ad.nla_tracks if t.name.startswith(_NLA_PREFIX)]
    except Exception:
        return 0
    n = 0
    for t in tracks:
        try:
            ad.nla_tracks.remove(t)
            n += 1
        except Exception:
            pass
    return n


def assign_set_action(obj, act, at, fps, set_name, f0=1):
    """Bind one set anim's action so it plays from the set's frame 1.

    `at` > 0 needs an NLA strip: Blender 5's AnimData has NO action offset
    (no action_frame_start), and a strip's frame_start is exactly the offset
    we want. Tagged tracks are removed again by the next preview.
    """
    ad = obj.animation_data
    if ad is None:
        ad = obj.animation_data_create()
    clear_preview_nla(ad)
    if at and at > 0.0:
        start = int(f0) + int(round(at * fps))
        try:
            ad.action = None
            track = ad.nla_tracks.new()
            track.name = _NLA_PREFIX + set_name
            strip = track.strips.new(act.name, start, act)
            try:
                strip.use_auto_blend = False
            except Exception:
                pass
            return True
        except Exception:
            return False
    try:
        ad.action = act
    except Exception:
        return False
    return _fix_action_slot(obj, ad, act)
# ---------------------------------------------------------------------------
# Loading set cues into a subtitle object (shared with the SRT importer)
# ---------------------------------------------------------------------------

def _load_cues_into(obj, scene, cues, replace=True, insert_clears=True):
    """Fill an object's subtitle entries from (start_sec, end_sec, text).

    Seconds are relative to scene.frame_start, so a set's LOCAL-timed cues
    land inside the preview range exactly like an imported SRT would. Shares
    the key-collision rules with the importer (_set_key skips claimed frames).
    Returns the number of text cues.
    """
    if not cues:
        return 0
    if replace:
        obj.tw_entries.clear()
        _delete_fcurve(obj)
        if _KEY_ID in obj.keys():
            try:
                del obj[_KEY_ID]
            except Exception:
                pass
    fps = scene_fps(scene)
    f0 = int(scene.frame_start)
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
        if landed != e.frame:
            e.frame = landed
    obj.tw_active_index = 0
    _sort_entries(obj)
    _write_backup(obj, force=True)
    update_object(obj, scene)
    return count


def _set_body(obj, text):
    """Set a text object's body without tripping the re-entrancy guard."""
    global _IN_HANDLER
    was = _IN_HANDLER
    _IN_HANDLER = True
    try:
        if obj.data.body != text:
            obj.data.body = text
    finally:
        _IN_HANDLER = was


def _snap_camera(cam, staged):
    """Copy a staged shot's pose + lens onto the render camera (no keys).

    v2 has NO camera bake - the game moves procedurally between the staged
    shots - so the timeline preview just poses the render camera on the set's
    opening shot. Staged cams carry their own lens, hence the copy.
    """
    cam.location = tuple(staged.location)
    try:
        cam.rotation_mode = staged.rotation_mode
        if staged.rotation_mode == 'QUATERNION':
            cam.rotation_quaternion = tuple(staged.rotation_quaternion)
        elif staged.rotation_mode == 'AXIS_ANGLE':
            cam.rotation_axis_angle = tuple(staged.rotation_axis_angle)
        else:
            cam.rotation_euler = tuple(staged.rotation_euler)
    except Exception:
        cam.rotation_euler = tuple(staged.rotation_euler)
    try:
        cam.scale = tuple(staged.scale)
    except Exception:
        pass
    try:
        cam.data.lens = staged.data.lens
        cam.data.shift_x = staged.data.shift_x
        cam.data.shift_y = staged.data.shift_y
        cam.data.sensor_fit = staged.data.sensor_fit
    except Exception:
        pass


def _camera_is_baked(cam):
    """True when an action still drives the camera pose (a v1 leftover)."""
    ad = getattr(cam, "animation_data", None)
    if ad is None or ad.action is None:
        return False
    for fc in _action_fcurves(ad.action):
        if fc.data_path in ("location", "rotation_euler", "rotation_quaternion"):
            return True
    return False


def _load_sound(path):
    """Reuse an already-loaded sound for this path (no duplicate datablocks)."""
    try:
        for s in bpy.data.sounds:
            try:
                if s.filepath and bpy.path.abspath(s.filepath) == \
                        os.path.abspath(path):
                    return s
            except Exception:
                continue
        return bpy.data.sounds.load(path)
    except Exception:
        return None


def _remove_auto_objects(scene):
    """Delete the add-on's own helper objects (auto speakers). Count."""
    n = 0
    for o in list(scene.objects):
        try:
            if not o.get(_AUTO_TAG):
                continue
        except Exception:
            continue
        data = getattr(o, "data", None)
        try:
            bpy.data.objects.remove(o, do_unlink=True)
            n += 1
        except Exception:
            continue
        if data is not None and getattr(data, "users", 1) == 0:
            try:
                bpy.data.speakers.remove(data)
            except Exception:
                pass
    return n


def sync_speakers(scene, plan, loaded, set_name, fps, f0):
    """Auto-manage one Speaker object per `audio` anim (timeline preview).

    The game plays audio through `aud`; Blender's timeline needs real speaker
    objects instead. They are tagged `_tw_auto` so the add-on replaces/removes
    them itself - the user never cleans up, and they are never mistaken for
    scene content. Blender starts a speaker's sound when it unmutes, so the
    trigger is a CONSTANT `data.muted` key pair.
    """
    n = _remove_auto_objects(scene)
    made = 0
    for i, (name, at) in enumerate(plan.get("audios") or []):
        path = (loaded.get("audio") or {}).get(name)
        if not path or not os.path.isfile(path):
            continue
        snd = _load_sound(path)
        if snd is None:
            continue
        try:
            spk = bpy.data.speakers.new("TW_Spk_%s_%d" % (set_name, i + 1))
            spk.sound = snd
            o = bpy.data.objects.new(spk.name, spk)
            scene.collection.objects.link(o)
            o[_AUTO_TAG] = "speaker:%s:%d" % (set_name, i + 1)
            start = int(f0) + int(round(float(at) * fps))
            o.data.muted = True
            o.keyframe_insert("data.muted", frame=max(int(f0), start - 1))
            o.data.muted = False
            o.keyframe_insert("data.muted", frame=start)
            try:
                dur = float(getattr(snd, "duration", 0.0) or 0.0)
            except Exception:
                dur = 0.0
            if dur > 0.0:
                o.data.muted = True
                o.keyframe_insert(
                    "data.muted", frame=start + int(round(dur * fps)) + 1)
            ad = o.animation_data
            if ad is not None and ad.action is not None:
                ad.action.name = "%s_anim" % o.name
                ad.action.use_fake_user = False
                for fc in _action_fcurves(ad.action):
                    for k in fc.keyframe_points:
                        k.interpolation = 'CONSTANT'
                    fc.update()
            made += 1
        except Exception:
            import traceback
            traceback.print_exc()
    return made, n


def preview_set_impl(scene, set_name):
    """(ok, message, info) - switch the whole editor context to one set.

    This is Option B authoring: per-set local actions, one Blender scene, and
    Preview Set as the context switch. Spacebar then plays a faithful
    timeline preview of that set and P plays the real thing in the engine.
    """
    lib, loaded, err = story_bundle(scene)
    if err:
        return False, err, {}
    if loaded["errors"]:
        return False, ("story.yml has errors - fix them first (Validate):\n- "
                       + "\n- ".join(loaded["errors"][:6])), {}
    story = loaded["story"]
    sets = story.get("sets") or {}
    if set_name not in sets:
        return False, ("no set '%s' in story.yml (have: %s)"
                       % (set_name, ", ".join(sorted(sets)) or "none")), {}
    fps = scene_fps(scene)
    sp = _story_abspath(scene)
    old = lib.load_sidecar(lib.sync_path_for(sp))
    old_uids = old.get("uids") or {}
    refs = story_refs(lib, loaded)
    by_rec_obj, by_rec_act = _resolve_by_recorded(refs, old_uids)
    ranges = dict(old.get("actions") or {})
    ranges.update(action_ranges(scene, refs, old_uids))
    plan = lib.set_plan(story, loaded["files"], set_name, ranges, fps)

    # ---- frame range: set-local, so every set starts at frame 1 ----------
    f0 = 1
    f1 = int(round(lib.set_duration(plan) * fps))
    for (_o, a, at, _w, _d) in plan["actions"]:
        rng = ranges.get(a)
        if rng:
            f1 = max(f1, f0 + int(round(at * fps)) + int(rng[1]) - int(rng[0]))
    f1 = max(f1, f0 + 1)
    try:
        scene.tw_prev_range = "%d,%d" % (int(scene.frame_start),
                                         int(scene.frame_end))
    except Exception:
        pass
    scene.frame_end = max(f1, int(scene.frame_start))
    scene.frame_start = f0
    scene.frame_end = f1

    # ---- actions: only THIS set may play (others share the frame range) --
    all_acts = set(refs["actions"])
    detached = 0
    for o in scene.objects:
        ad = getattr(o, "animation_data", None)
        if ad is None:
            continue
        clear_preview_nla(ad)
        if ad.action is not None and ad.action.name in all_acts:
            ad.action = None
            detached += 1
    assigned, missing = [], []
    for (oname, aname, at, _wait, _dur) in plan["actions"]:
        obj = find_object(scene, oname, by_rec_obj)
        act = find_action(aname, by_rec_act)
        if obj is None:
            missing.append("object '%s'" % oname)
            continue
        if act is None:
            missing.append("action '%s'" % aname)
            continue
        if assign_set_action(obj, act, at, fps, set_name, f0):
            assigned.append("%s@%s" % (obj.name, act.name))
        else:
            missing.append("bind failed %s@%s" % (oname, aname))

    # ---- subtitle cues ----------------------------------------------------
    n_cues = 0
    sub = role_object(scene, _ROLE_SUBS, ("Subtitles",))
    if sub is None or sub.type != 'FONT':
        missing.append("no subtitle text object (Subtitles)")
    else:
        n_cues = _load_cues_into(sub, scene, list(plan["subs"]),
                                 replace=True, insert_clears=True)
        try:
            sub.tw_enabled = True
            if story.get("cps") is not None:
                sub.tw_cps = float(story["cps"])
        except Exception:
            pass

    # ---- opening shot -----------------------------------------------------
    shot_note = ""
    cam = role_object(scene, _ROLE_CAMERA, ("Camera",)) or scene.camera
    if plan["shots"] and cam is not None:
        shot_name = plan["shots"][0][1]
        staged = find_object(scene, shot_name, by_rec_obj)
        if staged is None:
            shot_note = "shot '%s' is not in the scene" % shot_name
        elif staged != cam:
            _snap_camera(cam, staged)
            shot_note = "camera on '%s'" % shot_name
            if _camera_is_baked(cam):
                shot_note += " (baked keys still win - v1 leftover)"

    # ---- choice menu ------------------------------------------------------
    menu_note = ""
    menu = role_object(scene, _ROLE_MENU, ("ChoiceMenu",))
    if menu is not None and menu.type == 'FONT':
        kind, target = plan["end"]
        try:
            menu.tw_enabled = False    # never let the typing handler touch it
        except Exception:
            pass
        if kind == "choice":
            ch = (story.get("choices") or {}).get(target) or {}
            body = plan["prompt"] or ""
            opts = ch.get("options") or []
            if opts:
                body = (body + "\n\n" + lib.menu_body(opts)) if body else \
                    lib.menu_body(opts)
            _set_body(menu, body)
            menu.scale = (1.0, 1.0, 1.0)
            menu_note = "menu: choice '%s'" % target
        else:
            _set_body(menu, "")
            menu.scale = (0.0, 0.0, 0.0)   # scale, never hide_render (UPBGE)
            menu_note = "menu hidden (end: %s)" % kind

    # ---- audio: auto-managed speakers ------------------------------------
    n_spk, _removed = sync_speakers(scene, plan, loaded, set_name, fps, f0)

    scene.tw_preview_set = set_name
    scene.tw_preview_range = "%d-%d" % (f0, f1)
    try:
        scene.frame_set(f0)
    except Exception:
        scene.frame_current = f0
    info = {"set": set_name, "range": (f0, f1), "assigned": assigned,
            "detached": detached, "cues": n_cues, "missing": missing,
            "speakers": n_spk, "plan": plan}
    msg = "set '%s' -> frames %d-%d, %d action(s), %d cue(s)" % (
        set_name, f0, f1, len(assigned), n_cues)
    bits = [b for b in (shot_note, menu_note) if b]
    if n_spk:
        bits.append("%d speaker(s)" % n_spk)
    if bits:
        msg += " | " + "; ".join(bits)
    if missing:
        msg += " | MISSING: " + ", ".join(missing)
    guard_fake_users(refs, old_uids)
    return True, msg, info


def clear_preview_impl(scene):
    """(ok, message): leave set-preview mode.

    Restores the frame range and removes the add-on's own helpers (NLA
    tracks, auto speakers). Assigned actions are the user's data, so they
    stay - Edit > Undo reverts the whole preview instead.
    """
    name = ""
    try:
        name = scene.tw_preview_set or ""
    except Exception:
        pass
    n_nla = 0
    for o in scene.objects:
        ad = getattr(o, "animation_data", None)
        if ad is not None:
            n_nla += clear_preview_nla(ad)
    n_spk = _remove_auto_objects(scene)
    prev = ""
    try:
        prev = scene.tw_prev_range or ""
    except Exception:
        pass
    if "," in prev:
        a, b = prev.split(",")[:2]
        try:
            scene.frame_end = max(int(b), int(scene.frame_start) + 1)
            scene.frame_start = int(a)
        except Exception:
            pass
    scene.tw_preview_set = ""
    scene.tw_preview_range = ""
    scene.tw_prev_range = ""
    return True, ("preview cleared%s (%d helper track(s), %d speaker(s))"
                  % (" for set '%s'" % name if name else "", n_nla, n_spk))
# ---------------------------------------------------------------------------
# Validation (text lists - no graph rendering, by explicit design)
# ---------------------------------------------------------------------------

def validate_impl(scene):
    """(errors, warnings, bindings, summary) for the validator panel.

    story.py does the structural work (check_story + warn_story, including
    unreachable sets/choices and instant goto-loops); check_bindings adds the
    live-scene truth (are the objects/actions/shots actually there?). On top
    of that the editor checks two things only Blender can know: whether
    story.sync.json is STALE (the game would then play wrong action
    durations) and whether the set actions are purge-guarded.
    """
    lib, loaded, err = story_bundle(scene)
    if err:
        return [err], [], [], "story unavailable"
    errors = list(loaded["errors"])
    warnings = list(loaded["warnings"])
    bindings = []
    story = loaded["story"] or {}
    refs = story_refs(lib, loaded)
    sp = _story_abspath(scene)
    side = lib.load_sidecar(lib.sync_path_for(sp))
    old_uids = side.get("uids") or {}
    _o, by_rec_act = _resolve_by_recorded(refs, old_uids)
    if not errors:
        objects, actions, cameras = scene_name_lists(scene)
        try:
            bindings = lib.check_bindings(story, objects, actions, cameras)
        except Exception as ex:
            bindings = ["binding check crashed: %r" % (ex,)]
        # story.py's check_bindings only sees `camera:` anims - the [CAM Shot]
        # directives inside the .srt files are checked here, or not at all
        known = set(cameras) | set(objects)
        bad_shots = {}
        for fname, fdata in sorted((loaded.get("files") or {}).items()):
            for cam in (fdata.get("cams") or []):
                try:
                    shot, cue = cam[1], cam[2]
                except Exception:
                    continue
                if shot and shot not in known:
                    bad_shots.setdefault((fname, shot), []).append(cue)
        for (fname, shot), cue_nos in sorted(bad_shots.items()):
            bindings.append(
                "%s: [CAM %s] (cue %s) is not a camera in the scene"
                % (fname, shot,
                   ", ".join(str(c) for c in sorted(set(cue_nos))[:6])))
        live = action_ranges(scene, refs, old_uids)
        recorded = side.get("actions") or {}
        stale = sorted(k for k in live if list(recorded.get(k) or []) != live[k])
        if stale:
            warnings.append(
                "story.sync.json is stale for %d action(s): %s - press "
                "Refresh Sync" % (len(stale), ", ".join(stale[:6])))
        unguarded = []
        for name in sorted(refs["actions"]):
            act = find_action(name, by_rec_act)
            if act is not None and not act.use_fake_user:
                unguarded.append(name)
        if unguarded:
            warnings.append(
                "%d action(s) have no fake user (purge could delete them): %s"
                % (len(unguarded), ", ".join(unguarded[:6])))
        pending = scan_renames_impl(scene)
        if pending:
            warnings.append(
                "%d rename(s) not written back to story.yml: %s"
                % (len(pending),
                   ", ".join("%s->%s" % (p["old"], p["new"])
                             for p in pending[:6])))
    n_sets = len(story.get("sets") or {})
    n_ch = len(story.get("choices") or {})
    summary = "%d set(s), %d choice(s), start '%s' | %d error(s), " \
              "%d warning(s), %d binding error(s)" % (
                  n_sets, n_ch, story.get("start", "?"), len(errors),
                  len(warnings), len(bindings))
    return errors, warnings, bindings, summary


def _fill_report(scene, errors, warnings, bindings, summary):
    for prop, rows in (("tw_report_errors", errors),
                       ("tw_report_warnings", warnings),
                       ("tw_report_bindings", bindings)):
        try:
            col = getattr(scene, prop)
        except Exception:
            continue
        col.clear()
        for text in rows:
            col.add().text = str(text)
    try:
        scene.tw_report_note = summary
    except Exception:
        pass


def rename_diff_quick(scene):
    """Lean read-only name diff for the depsgraph handler (no YAML parse).

    Only the sidecar's uid map and the stamped ids are looked at, and the
    sidecar itself is mtime-cached, so this is safe to run twice a second
    while the playhead moves.
    """
    sp = _story_abspath(scene)
    path = os.path.splitext(sp)[0] + ".sync.json"
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return []
    hit = _SIDECAR_CACHE.get(path)
    if hit is None or hit[0] != stamp:
        try:
            import json
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        hit = (stamp, data)
        _SIDECAR_CACHE[path] = hit
    recorded = hit[1].get("uids")
    if not isinstance(recorded, dict) or not recorded:
        return []
    found = []
    for kind, idb, uid in stamped_ids():
        rec = recorded.get(uid)
        if not isinstance(rec, dict):
            continue
        old = rec.get("name")
        if isinstance(old, str) and old and old != idb.name:
            found.append({"uid": uid, "old": old, "new": idb.name,
                          "kind": rec.get("type") or _kind_type(kind, idb)})
    found.sort(key=lambda d: (d["kind"], d["old"]))
    return found


def _note_rename_diff(scene):
    """depsgraph hook: throttled, read-only, only sets the dirty flag."""
    now = time.monotonic()
    if now - _RENAME_STATE[0] < 0.5:
        return
    _RENAME_STATE[0] = now
    try:
        if rename_diff_quick(scene):
            _RENAME_STATE[1] = True
    except Exception:
        pass


def _rename_watch_tick():
    """1 Hz timer: publish a detected rename (and auto-rewrite if enabled).

    The diff runs in depsgraph_update_post but the LIST is written here:
    mutating RNA from inside that handler re-enters it. Auto-rewrite is
    debounced over two identical scans so a half-finished rename can never
    rewrite story.yml.
    """
    if _modal_active() or not _RENAME_STATE[1]:
        return 1.0
    scene = bpy.context.scene
    if scene is None:
        return 1.0
    _RENAME_STATE[1] = False
    try:
        found = rename_diff_quick(scene)
    except Exception:
        return 1.0
    if not found:
        sync_pending_list(scene, [])
        return 1.0
    sig = tuple(sorted((d["uid"], d["old"], d["new"]) for d in found))
    stable = (_RENAME_STATE[2] == sig)
    _RENAME_STATE[2] = sig
    n = sync_pending_list(scene, found)
    if n:
        print("Typewriter Subtitles: %d rename(s) pending - story.yml still "
              "uses the old names (%s)"
              % (n, ", ".join("%s->%s" % (d["old"], d["new"])
                              for d in found[:4])), flush=True)
    auto = False
    try:
        auto = bool(scene.tw_autorewrite)
    except Exception:
        pass
    if auto and stable:
        try:
            count, msgs = apply_renames_impl(scene)
            print("Typewriter Subtitles: auto-rewrote story.yml (%s)"
                  % "; ".join(msgs[:2]), flush=True)
        except Exception:
            import traceback
            traceback.print_exc()
    return 1.0


# ---------------------------------------------------------------------------
# Story v2 data model + operators
# ---------------------------------------------------------------------------

class TW_MsgEntry(bpy.types.PropertyGroup):
    text: bpy.props.StringProperty(name="Text")


class TW_RenameEntry(bpy.types.PropertyGroup):
    uid: bpy.props.StringProperty(name="UID")
    old: bpy.props.StringProperty(name="Was")
    new: bpy.props.StringProperty(name="Now")
    kind: bpy.props.StringProperty(name="Kind")


def _story_path_updated(self, context):
    invalidate_story_cache()


def _set_items(self, context):
    """EnumProperty items: the story's sets, live from story.yml."""
    try:
        lib, loaded, err = story_bundle(context.scene)
    except Exception:
        return [("NONE", "(story error)", "")]
    if err or loaded is None:
        return [("NONE", "(no story)", (err or "")[:60])]
    if loaded["errors"]:
        return [("NONE", "(story has errors)", loaded["errors"][0][:60])]
    names = sorted((loaded["story"].get("sets") or {}).keys())
    if not names:
        return [("NONE", "(no sets)", "")]
    return [(n, n, "Preview set '%s'" % n) for n in names]


class TW_OT_preview_set(bpy.types.Operator):
    bl_idname = "tw.preview_set"
    bl_label = "Preview Set"
    bl_description = (
        "Switch the editor to one story set: assign its actions, load its "
        ".srt cues into the subtitle object, set the frame range to the set, "
        "snap the render camera to its opening staged shot, pose the choice "
        "menu and auto-manage speaker objects for its audio. Spacebar is "
        "then a faithful timeline preview; P plays the real thing")
    bl_options = {'REGISTER', 'UNDO'}

    set_name: bpy.props.EnumProperty(name="Set", items=_set_items)

    def invoke(self, context, event):
        cur = ""
        try:
            cur = context.scene.tw_preview_set or ""
        except Exception:
            cur = ""
        if cur and any(i[0] == cur for i in _set_items(self, context)):
            self.set_name = cur
        return context.window_manager.invoke_props_dialog(
            self, title="Preview Story Set")

    def execute(self, context):
        ok, msg, info = preview_set_impl(context.scene, self.set_name)
        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        self.report({'WARNING'} if info.get("missing") else {'INFO'}, msg)
        return {'FINISHED'}


class TW_OT_jump_to_set(bpy.types.Operator):
    bl_idname = "tw.jump_to_set"
    bl_label = "Jump to Set"
    bl_description = (
        "Jump the playhead to a story set. Repurposed for v2: sets are "
        "local-timed and there are no SET-/CH- timeline markers any more, so "
        "jumping to a set means previewing it (playhead to its frame 1)")
    bl_options = {'REGISTER', 'UNDO'}

    set_name: bpy.props.EnumProperty(name="Set", items=_set_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, title="Jump to Story Set")

    def execute(self, context):
        ok, msg, info = preview_set_impl(context.scene, self.set_name)
        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        self.report({'WARNING'} if info.get("missing") else {'INFO'}, msg)
        return {'FINISHED'}


class TW_OT_clear_preview(bpy.types.Operator):
    bl_idname = "tw.clear_preview"
    bl_label = "Clear Preview"
    bl_description = ("Leave set-preview mode: restore the frame range and "
                      "remove the add-on's helper NLA tracks and speaker "
                      "objects (assigned actions are your data - they stay; "
                      "use Edit > Undo for a full revert)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            return bool(context.scene.tw_preview_set)
        except Exception:
            return False

    def execute(self, context):
        _ok, msg = clear_preview_impl(context.scene)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class TW_OT_refresh_sync(bpy.types.Operator):
    bl_idname = "tw.refresh_sync"
    bl_label = "Refresh Sync"
    bl_description = (
        "Write story.sync.json (action frame ranges + the uid rename map the "
        "game driver reads) and story.schema.json (VSCode YAML completion), "
        "stamp the invisible uids and fake-user the set actions so purge "
        "cannot eat them. Also runs automatically on save")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ok, msgs, _info = refresh_sync_impl(context.scene)
        for m in msgs:
            self.report({'ERROR'} if not ok else {'INFO'}, m)
        return {'FINISHED'} if ok else {'CANCELLED'}


class TW_OT_validate_story(bpy.types.Operator):
    bl_idname = "tw.validate_story"
    bl_label = "Validate Story"
    bl_description = ("Check story.yml + the .srt files against the live "
                      "scene: structure errors, warnings (unreachable sets, "
                      "instant loops, stale sync) and binding errors, listed "
                      "as text below")
    bl_options = {'REGISTER'}

    def execute(self, context):
        errors, warnings, bindings, summary = validate_impl(context.scene)
        _fill_report(context.scene, errors, warnings, bindings, summary)
        kind = 'ERROR' if (errors or bindings) else (
            'WARNING' if warnings else 'INFO')
        self.report({kind}, summary)
        return {'FINISHED'}


class TW_OT_apply_renames(bpy.types.Operator):
    bl_idname = "tw.apply_renames"
    bl_label = "Apply Renames"
    bl_description = ("Rewrite the renamed object/action references in "
                      "story.yml (targeted: only action:/camera: value "
                      "tokens, comments and formatting survive), then refresh "
                      "the sync sidecar")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            return len(context.scene.tw_renames) > 0
        except Exception:
            return False

    def execute(self, context):
        n, msgs = apply_renames_impl(context.scene)
        for m in msgs:
            self.report({'INFO'}, m)
        return {'FINISHED'} if n else {'CANCELLED'}


class TW_OT_forget_renames(bpy.types.Operator):
    bl_idname = "tw.forget_renames"
    bl_label = "Forget (Accept Names)"
    bl_description = ("Accept the live names WITHOUT rewriting story.yml - "
                      "use this when you already fixed the references by "
                      "hand; the sidecar is re-stamped from the scene")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            return len(context.scene.tw_renames) > 0
        except Exception:
            return False

    def execute(self, context):
        n = forget_renames_impl(context.scene)
        self.report({'INFO'}, "Accepted %d live name(s); story.yml untouched"
                    % n)
        return {'FINISHED'}


class TW_OT_bake_set(bpy.types.Operator):
    bl_idname = "tw.bake_set"
    bl_label = "Bake Set to Objects"
    bl_description = (
        "Bake the previewed set's subtitle cues to one text object per cue, "
        "named <set>_Line## (own key + visibility keys, so it renders and "
        "scrubs with no add-on). Sets are local-timed, so a bake replaces "
        "the previous one instead of overlapping it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        obj = context.active_object
        try:
            if not scene.tw_preview_set:
                return False
        except Exception:
            return False
        if obj is not None and obj.type == 'FONT' and len(obj.tw_entries):
            return True
        sub = role_object(scene, _ROLE_SUBS, ("Subtitles",))
        return sub is not None and sub.type == 'FONT' and len(sub.tw_entries)

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        if obj is None or obj.type != 'FONT' or not len(obj.tw_entries):
            obj = role_object(scene, _ROLE_SUBS, ("Subtitles",))
        if obj is None or obj.type != 'FONT':
            self.report({'ERROR'}, "No subtitle text object to bake")
            return {'CANCELLED'}
        set_name = scene.tw_preview_set
        n = bake_typewriter(obj, scene, prefix="%s_Line" % set_name,
                            tag=set_name)
        if n <= 0:
            self.report({'WARNING'}, "Nothing baked (no lines?)")
            return {'CANCELLED'}
        self.report({'INFO'}, "Baked %d object(s) as %s_Line##" % (n, set_name))
        return {'FINISHED'}
# ---------------------------------------------------------------------------
# Story panel
# ---------------------------------------------------------------------------

_PANEL_CACHE = [0.0, None]


def _bundle_for_panel(scene):
    """story_bundle() throttled to 2 Hz - panels redraw on every mouse move,
    and the bundle fingerprints a directory of .srt files."""
    now = time.monotonic()
    if _PANEL_CACHE[1] is not None and now - _PANEL_CACHE[0] < 0.5:
        return _PANEL_CACHE[1]
    try:
        data = story_bundle(scene)
    except Exception as ex:
        data = (None, None, "panel: %r" % (ex,))
    _PANEL_CACHE[0] = now
    _PANEL_CACHE[1] = data
    return data


class VIEW3D_PT_tw_story(bpy.types.Panel):
    bl_label = "Story Sets"
    bl_idname = "VIEW3D_PT_tw_story"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Subtitles"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "tw_story_path", text="story.yml")

        lib, loaded, err = _bundle_for_panel(scene)
        if err:
            box = layout.box()
            box.label(text="No story here", icon='ERROR')
            for chunk in _wrap(err, 42):
                box.label(text=chunk)
            return
        if loaded["errors"]:
            box = layout.box()
            box.label(text="story.yml: %d error(s)" % len(loaded["errors"]),
                      icon='ERROR')
            for e in loaded["errors"][:3]:
                for chunk in _wrap(e, 42):
                    box.label(text=chunk)
            layout.operator("tw.validate_story", text="Show All Errors",
                            icon='VIEWZOOM')
            return

        story = loaded["story"] or {}
        sets = sorted((story.get("sets") or {}).keys())
        n_ch = len(story.get("choices") or {})
        row = layout.row()
        row.label(text="%d sets, %d choices, start '%s'"
                  % (len(sets), n_ch, story.get("start", "?")), icon='CHECKMARK')

        # --- preview -------------------------------------------------------
        box = layout.box()
        cur = ""
        rng = ""
        try:
            cur = scene.tw_preview_set or ""
            rng = scene.tw_preview_range or ""
        except Exception:
            pass
        if cur:
            box.label(text="Previewing '%s' (%s)" % (cur, rng or "?"),
                      icon='PLAY')
            box.label(text="Spacebar = timeline, P = game", icon='BLANK1')
        else:
            box.label(text="No set previewed", icon='INFO')
        prow = box.row(align=True)
        prow.operator("tw.preview_set", text="Preview Set...", icon='PLAY')
        prow.operator("tw.clear_preview", text="", icon='X')
        box.operator("tw.bake_set", text="Bake Set to Objects", icon='KEY_HLT')

        # --- sync + validate ----------------------------------------------
        row = layout.row(align=True)
        row.operator("tw.refresh_sync", text="Refresh Sync", icon='FILE_REFRESH')
        row.operator("tw.validate_story", text="Validate", icon='VIEWZOOM')

        # --- pending renames ----------------------------------------------
        n_ren = 0
        try:
            n_ren = len(scene.tw_renames)
        except Exception:
            n_ren = 0
        if n_ren:
            box = layout.box()
            box.label(text="%d rename(s) not in story.yml" % n_ren,
                      icon='ERROR')
            for r in scene.tw_renames:
                for chunk in _wrap("%s -> %s  (%s)" % (r.old, r.new, r.kind),
                                   42):
                    box.label(text=chunk)
            box.row(align=True).operator("tw.apply_renames",
                                         text="Apply", icon='CHECKMARK')
            box.row(align=True).operator("tw.forget_renames",
                                         text="Forget", icon='X')
        row = layout.row()
        row.prop(scene, "tw_autorewrite", text="Auto-rewrite story.yml on rename",
                 icon='AUTO')

        # --- validator report ---------------------------------------------
        note = ""
        try:
            note = scene.tw_report_note or ""
        except Exception:
            pass
        if note:
            box = layout.box()
            for chunk in _wrap(note, 44):
                box.label(text=chunk)
            _draw_report(box, "Errors", scene.tw_report_errors, 'ERROR')
            _draw_report(box, "Bindings", scene.tw_report_bindings, 'CANCEL')
            _draw_report(box, "Warnings", scene.tw_report_warnings, 'INFO')


def _wrap(text, width):
    """Split a message into panel-sized lines (panels cannot scroll text)."""
    out = []
    for para in str(text).split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > width:
            cut = para.rfind(" ", 0, width)
            if cut <= 0:
                cut = width
            out.append(para[:cut])
            para = para[cut:].strip()
        out.append(para)
    return out or [""]


def _draw_report(box, title, rows, icon):
    n = len(rows) if rows is not None else 0
    if not n:
        return
    box.separator()
    box.label(text="%s (%d)" % (title, n), icon=icon)
    for r in list(rows)[:8]:
        for chunk in _wrap(r.text, 42):
            box.label(text=chunk)
    if n > 8:
        box.label(text="... and %d more" % (n - 8))


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
        n = bake_typewriter(obj, context.scene)
        if n > 0:
            self.report({'INFO'}, "Baked %d subtitle objects (live typing off)"
                        % n)
            return {'FINISHED'}
        self.report({'WARNING'}, "Nothing baked (no lines?)")
        return {'CANCELLED'}


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
                     "Story Sets panel: preview a set, sync, validate.",
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
    add/remove/re-uid keys) is deferred to a timer. Also the read-only half
    of the story rename watcher (the pending list is filled by a timer)."""
    global _IN_HANDLER
    if _IN_HANDLER:
        return
    dragging = _modal_active()
    if not dragging:
        _note_rename_diff(scene)
    _IN_HANDLER = True
    try:
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


@bpy.app.handlers.persistent
def tw_load_post(*args):
    _BODY_STATE.clear()
    _BACKUP_STATE.clear()
    invalidate_story_cache()
    _PANEL_CACHE[1] = None
    _RENAME_STATE[0] = 0.0
    _RENAME_STATE[1] = True     # scan once: catch renames made while closed
    _RENAME_STATE[2] = None
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
    """Recompute live bodies, then refresh the story sidecars.

    story.sync.json + story.schema.json are written on save so the game
    driver and VSCode completion never lag behind the file (HANDOFF: Refresh
    Sync, also on save). Never raises - a sidecar must not endanger a save."""
    scene = bpy.context.scene
    if scene is None:
        return
    for obj in bpy.data.objects:
        try:
            if obj.type == 'FONT' and len(obj.tw_entries):
                _update_body_impl(obj, scene)
        except Exception:
            pass
    try:
        if os.path.isfile(_story_abspath(scene)):
            _ok, msgs, info = refresh_sync_impl(scene, quiet=True)
            if not info.get("skipped"):
                for m in msgs:
                    print("Typewriter Subtitles: " + m, flush=True)
    except Exception:
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_SCENE_PROPS = (
    "tw_story_path", "tw_preview_set", "tw_preview_range", "tw_prev_range",
    "tw_autorewrite", "tw_renames", "tw_renames_index", "tw_report_errors",
    "tw_report_warnings", "tw_report_bindings", "tw_report_note",
)


classes = (
    TW_SubtitleEntry,
    TW_MsgEntry,
    TW_RenameEntry,
    TW_OT_add_text_object,
    TW_OT_snap_to_camera,
    TW_OT_add_entry,
    TW_OT_add_entry_dialog,
    TW_OT_remove_entry,
    TW_OT_select_keys,
    TW_OT_clear_animation,
    TW_OT_rebuild_keys,
    TW_OT_recover_backup,
    TW_OT_preview_set,
    TW_OT_jump_to_set,
    TW_OT_clear_preview,
    TW_OT_refresh_sync,
    TW_OT_validate_story,
    TW_OT_apply_renames,
    TW_OT_forget_renames,
    TW_OT_bake_set,
    TW_OT_setup_game_logic,
    TW_OT_refresh_game_data,
    TW_OT_bake_typewriter,
    TW_OT_import_subtitles,
    TW_OT_export_subtitles,
    TW_OT_reload_subtitles,
    TW_OT_reload_addon,
    TW_UL_entries,
    VIEW3D_PT_tw_subtitles,
    VIEW3D_PT_tw_story,
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

    for prop in _SCENE_PROPS:
        if hasattr(bpy.types.Scene, prop):
            try:
                delattr(bpy.types.Scene, prop)
            except Exception:
                pass
    bpy.types.Scene.tw_story_path = bpy.props.StringProperty(
        name="Story File", subtype='FILE_PATH', default=_STORY_DEFAULT,
        description="story.yml that wires the animation sets (the game reads "
                    "the same file). story.py must sit next to it",
        update=_story_path_updated)
    bpy.types.Scene.tw_preview_set = bpy.props.StringProperty(
        name="Preview Set", default="",
        description="The story set the editor is currently previewing "
                    "(empty = none)")
    bpy.types.Scene.tw_preview_range = bpy.props.StringProperty(
        name="Preview Range", default="",
        description="Frame range the previewed set occupies")
    bpy.types.Scene.tw_prev_range = bpy.props.StringProperty(
        name="Previous Range", default="",
        description="Frame range to restore when the preview is cleared")
    bpy.types.Scene.tw_autorewrite = bpy.props.BoolProperty(
        name="Auto-rewrite story.yml", default=False,
        description="Rewrite story.yml automatically when a referenced "
                    "object or action is renamed. Off = review the pending "
                    "list in the Story panel and press Apply")
    bpy.types.Scene.tw_renames = bpy.props.CollectionProperty(
        type=TW_RenameEntry)
    bpy.types.Scene.tw_renames_index = bpy.props.IntProperty(default=0)
    bpy.types.Scene.tw_report_errors = bpy.props.CollectionProperty(
        type=TW_MsgEntry)
    bpy.types.Scene.tw_report_warnings = bpy.props.CollectionProperty(
        type=TW_MsgEntry)
    bpy.types.Scene.tw_report_bindings = bpy.props.CollectionProperty(
        type=TW_MsgEntry)
    bpy.types.Scene.tw_report_note = bpy.props.StringProperty(
        name="Last Validation", default="")

    bpy.app.handlers.frame_change_post.append(tw_frame_change)
    bpy.app.handlers.depsgraph_update_post.append(tw_depsgraph_update)
    bpy.app.handlers.load_post.append(tw_load_post)
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
    try:
        if not bpy.app.timers.is_registered(_rename_watch_tick):
            bpy.app.timers.register(_rename_watch_tick, first_interval=1.0,
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
    if tw_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(tw_save_pre)
    if tw_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(tw_save_post)
    for job in (_deferred_sync_job, _auto_reload_timer, _rename_watch_tick,
                _do_reload_addon):
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
    for prop in _SCENE_PROPS:
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
