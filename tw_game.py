'''Generic UPBGE/BGE game-mode driver for Typewriter Subtitles.

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
            fh.write(msg + "\n")
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
        _log("tw_game FATAL:\n" + traceback.format_exc())


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
                _log("tw_game: INIT FAILED: %r\n%s"
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
