"""Talking Robots - UPBGE game-mode story player (sets + actors).

Setup (already done in talking_robots.blend):
  GameDirector (Empty): Always sensor [TRUE pulse] -> Python controller
  (Module: "game_subtitles.update"). Also works in Script mode.
  Story data comes from FILES: the "tw_story" String game property on
  GameDirector holds the story path (default "//story.yml"); the subtitle
  file it names sits next to it. Paths only - property strings are
  length-capped, so no JSON lives in them anymore.

  A set = SRT cue range + timeline frame range + lens + end action. At set
  entry the driver plays every actor's own action over the set's frames
  and sets the camera lens; subtitles type from the SRT text. All motion
  lives on objects (tweak keys + press P, no rebuild); this driver only
  plays actions, types text, runs choices and follows ends.

Controls:
  Up/Down + Enter/Space = move in / confirm a dialogue choice
  1-9                   = pick a choice option directly
  R                     = restart story, ESC = quit game.

Mouse clicks (roadmap): funnel through confirm_choice(own, scene, tick,
index) and move_choice(own, delta) - the keyboard handlers below already
do, so click support only needs hit-testing, no state surgery.

Diagnostics: writes game_debug.log next to the .blend (init info, set
jumps, choices, missing actors, heartbeats, tracebacks). The tick counter
also lives on GameDirector as the "tick" property - enable Show Debug
Properties to watch it count.
Headless capture: set a game property CAPTURE on GameDirector (any value)
  -> screenshots every 2nd tick into //capture/, choices auto-confirm
  option 1, auto-quit at story end (30 s backstop).
"""

import bge
import os
import re

STORY_PROP = "tw_story"
STORY_DEFAULT = "//story.yml"
SUBS_DEFAULT = "dialogue.srt"
CAM_OBJ = "Camera"
SUB_OBJ = "Subtitles"
MENU_OBJ = "ChoiceMenu"
LOG_PATH = "//game_debug.log"
DEAD_MESSAGE = "No dialogue data (see game_debug.log)"
CAP_TICKS = 1800       # capture-mode backstop: auto-quit after this long
CHOICE_AUTO_TICKS = 30  # capture mode confirms option 1 after this many ticks
STOP_HOLD = 2.0        # seconds the last line lingers before the replay hint
CPS_FALLBACK = 30.0


# --------------------------------------------------------------------------
# embedded story parsers (verbatim copies of story.py's: the driver is a
# single self-contained file - it cannot import siblings in the engine)
# --------------------------------------------------------------------------

DIRECTIVES = ("CAM",)
LEGACY_DIRECTIVES = ("BRANCH", "CHOICE", "OPT", "GOTO", "END")


def is_directive(line):
    s = line.strip()
    if len(s) < 3 or s[0] != "[" or s[-1] != "]":
        return False
    inner = s[1:-1].strip()
    if not inner:
        return False
    return inner.split(None, 1)[0] in DIRECTIVES + LEGACY_DIRECTIVES


def strip_directives(text):
    return "\n".join(ln for ln in text.split("\n")
                     if not is_directive(ln)).strip()


_TS_RE = re.compile(r'(?:(\d+):)?(\d+):(\d+)[,.](\d+)')


def _ts_to_seconds(token):
    m = _TS_RE.search(token)
    if m is None:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    return (h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            + int(m.group(4)) / 1000.0)


def _split_blocks(text):
    blocks = []
    for block in re.split(r'\n\s*\n', text.strip()):
        rows = [r for r in block.splitlines() if r.strip()]
        if rows:
            blocks.append(rows)
    return blocks


def parse_srt_simple(text):
    cues = {}
    for pos, rows in enumerate(_split_blocks(text), 1):
        tidx = None
        for j, r in enumerate(rows):
            if '-->' in r:
                tidx = j
                break
        if tidx is None:
            continue
        number = pos
        if tidx > 0 and rows[tidx - 1].strip().isdigit():
            number = int(rows[tidx - 1].strip())
        parts = rows[tidx].split('-->')
        t0 = _ts_to_seconds(parts[0])
        t1 = _ts_to_seconds(parts[1]) if len(parts) > 1 else None
        if t0 is None:
            continue
        if t1 is None or t1 <= t0:
            t1 = t0 + 1.0
        cues[number] = {"start": t0, "end": t1,
                        "text": strip_directives("\n".join(rows[tidx + 1:]))}
    return cues


def _strip_comment(line):
    q = None
    esc = False
    for i, ch in enumerate(line):
        if q is not None:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == q:
                q = None
        else:
            if ch in ("'", '"') and (i == 0 or line[i - 1] in " \t:[,"):
                q = ch
            elif ch == "#":
                return line[:i]
    return line


def _parse_scalar(tok):
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        body = t[1:-1]
        if t[0] == "'":
            return body.replace("''", "'")
        out = []
        esc = False
        for ch in body:
            if esc:
                out.append({"n": "\n", "t": "\t", '"': '"',
                            "\\": "\\"}.get(ch, ch))
                esc = False
            elif ch == "\\":
                esc = True
            else:
                out.append(ch)
        if esc:
            out.append("\\")
        return "".join(out)
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _flow_tokenize(s):
    toks = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in " \t":
            i += 1
            continue
        if ch in "[],":
            toks.append(ch)
            i += 1
            continue
        if ch in "{}":
            raise ValueError("flow {...} is not supported (use block style)")
        if ch in ("'", '"'):
            q = ch
            j = i + 1
            buf = []
            esc = False
            while j < n:
                c = s[j]
                if q == '"' and not esc and c == "\\":
                    esc = True
                    j += 1
                    continue
                if not esc and c == q:
                    if q == "'" and j + 1 < n and s[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                if esc:
                    buf.append({"n": "\n", "t": "\t", '"': '"',
                                "\\": "\\"}.get(c, c))
                    esc = False
                else:
                    buf.append(c)
                j += 1
            if j >= n:
                raise ValueError("unterminated string")
            toks.append(("str", "".join(buf)))
            i = j + 1
            continue
        j = i
        while j < n and s[j] not in ",[]":
            if s[j] in "{}":
                raise ValueError("flow {...} is not supported "
                                 "(use block style)")
            j += 1
        atom = s[i:j].strip()
        if not atom:
            raise ValueError("empty value in flow list")
        toks.append(("atom", atom))
        i = j
    return toks


def _parse_flow(s):
    toks = _flow_tokenize(s)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def nxt():
        t = peek()
        pos[0] += 1
        return t

    def value():
        t = nxt()
        if t == "[":
            out = []
            if peek() == "]":
                nxt()
                return out
            while True:
                out.append(value())
                t2 = nxt()
                if t2 == "]":
                    return out
                if t2 != ",":
                    raise ValueError("want ',' or ']' in flow list")
            return out
        if isinstance(t, tuple):
            return t[1] if t[0] == "str" else _parse_scalar(t[1])
        raise ValueError("unexpected %r in flow list" % (t,))

    v = value()
    if peek() is not None:
        raise ValueError("trailing %r after flow list" % (peek(),))
    if not isinstance(v, list):
        raise ValueError("flow value must be a [...] list")
    return v


def parse_minimal_yaml(text):
    raw = []
    for no, line in enumerate(text.splitlines(), 1):
        code = _strip_comment(line).rstrip()
        if not code.strip():
            continue
        nospace = code.lstrip(" ")
        if nospace != nospace.lstrip("\t"):
            raise ValueError("line %d: indent with spaces, never tabs" % no)
        indent = len(code) - len(nospace)
        raw.append((indent, no, code.strip()))
    if not raw:
        return {}
    if raw[0][0] != 0:
        raise ValueError("line %d: first line must start at column 0"
                         % raw[0][1])
    pos = [0]

    def nested(indent, no):
        if pos[0] < len(raw) and raw[pos[0]][0] > indent:
            return parse_block(raw[pos[0]][0])
        return None

    def flow_or_scalar(val, no):
        if val.startswith("["):
            try:
                return _parse_flow(val)
            except ValueError as ex:
                raise ValueError("line %d: %s" % (no, ex))
        if val.startswith("{"):
            raise ValueError("line %d: flow {...} is not supported "
                             "(use block style)" % no)
        return _parse_scalar(val)

    def parse_block(indent):
        if raw[pos[0]][2] == "-" or raw[pos[0]][2].startswith("- "):
            return parse_list(indent)
        return parse_map(indent)

    def parse_map(indent):
        out = {}
        while pos[0] < len(raw) and raw[pos[0]][0] == indent:
            _i, no, content = raw[pos[0]]
            if content == "-" or content.startswith("- "):
                raise ValueError("line %d: list item inside a mapping" % no)
            if ":" not in content:
                raise ValueError("line %d: want 'key: value'" % no)
            key, _, val = content.partition(":")
            key = key.strip()
            if not key:
                raise ValueError("line %d: empty key" % no)
            if key in out:
                raise ValueError("line %d: duplicate key %r" % (no, key))
            val = val.strip()
            pos[0] += 1
            if val == "":
                out[key] = nested(indent, no)
            else:
                out[key] = flow_or_scalar(val, no)
        if pos[0] < len(raw) and raw[pos[0]][0] > indent:
            bad = raw[pos[0]]
            raise ValueError("line %d: bad indentation" % bad[1])
        return out

    def parse_list(indent):
        out = []
        while pos[0] < len(raw) and raw[pos[0]][0] == indent:
            _i, no, content = raw[pos[0]]
            if content == "-":
                item = ""
            elif content.startswith("- "):
                item = content[2:].strip()
            else:
                break
            pos[0] += 1
            if item == "":
                out.append(nested(indent, no))
            else:
                out.append(flow_or_scalar(item, no))
        if pos[0] < len(raw) and raw[pos[0]][0] > indent:
            bad = raw[pos[0]]
            raise ValueError("line %d: bad indentation" % bad[1])
        return out

    return parse_block(0)


def _is_num(x):
    return type(x) in (int, float)


def check_story(story, cues):
    if not isinstance(story, dict):
        return ["story must be a mapping of key: value lines"]
    errs = []
    sets = story.get("sets")
    if not isinstance(sets, dict) or not sets:
        errs.append("'sets' must be a non-empty mapping")
        sets = {}
    start = story.get("start")
    if start not in sets:
        errs.append("'start' must name one of the sets: %s"
                    % ", ".join(sorted(str(k) for k in sets)))
    choices = story.get("choices", {})
    if choices is None:
        choices = {}
    if not isinstance(choices, dict):
        errs.append("'choices' must be a mapping")
        choices = {}
    for name, s in sets.items():
        tag = "set '%s'" % (name,)
        if not isinstance(s, dict):
            errs.append("%s must be a mapping" % tag)
            continue
        cr = s.get("cues")
        if (not isinstance(cr, list) or len(cr) != 2
                or not all(type(v) is int for v in cr)):
            errs.append("%s: 'cues' must be [first, last] cue numbers" % tag)
        elif cr[0] > cr[1]:
            errs.append("%s: cue range must rise, got %s" % (tag, cr))
        else:
            for v in cr:
                if v not in cues:
                    errs.append("%s: cue %d is not in dialogue.srt"
                                % (tag, v))
        fr = s.get("frames")
        if (not isinstance(fr, list) or len(fr) != 2
                or not all(type(v) is int for v in fr)):
            errs.append("%s: 'frames' must be [first, last] frames" % tag)
        elif fr[0] < 1 or fr[1] < fr[0]:
            errs.append("%s: frames must start at 1+ and rise, got %s"
                        % (tag, fr))
        lens = s.get("lens")
        if not _is_num(lens) or not lens > 0:
            errs.append("%s: 'lens' must be a number above zero" % tag)
        end = s.get("end")
        bits = str(end).strip().split(None, 1) if end is not None else []
        if bits == ["stop"]:
            pass
        elif len(bits) == 2 and bits[0] == "goto":
            if bits[1] not in sets:
                errs.append("%s targets unknown set '%s'" % (tag, bits[1]))
        elif len(bits) == 2 and bits[0] == "choice":
            if bits[1] not in choices:
                errs.append("%s offers unknown choice '%s'" % (tag, bits[1]))
        else:
            errs.append("%s: 'end' must be 'stop', 'goto <set>' or "
                        "'choice <id>'" % tag)
    for cid, ch in choices.items():
        tag = "choice '%s'" % (cid,)
        if not isinstance(ch, dict):
            errs.append("%s must be a mapping" % tag)
            continue
        pc = ch.get("prompt_cue")
        if type(pc) is not int or pc not in cues:
            errs.append("%s: 'prompt_cue' must be a cue number in "
                        "dialogue.srt" % tag)
        opts = ch.get("options")
        if not isinstance(opts, list) or not opts:
            errs.append("%s needs a non-empty 'options' list" % tag)
            continue
        for i, o in enumerate(opts):
            otag = "%s option %d" % (tag, i + 1)
            if not isinstance(o, list) or len(o) != 3:
                errs.append("%s must be [key, label, set]" % otag)
                continue
            key, label, dest = o
            if key not in (1, 2, 3, 4, 5, 6, 7, 8, 9) and not (
                    isinstance(key, str) and len(key) == 1
                    and key.isdigit() and key != "0"):
                errs.append("%s: key must be 1-9" % otag)
            if not isinstance(label, str) or not label.strip():
                errs.append("%s: label must be non-empty text" % otag)
            if dest not in sets:
                errs.append("%s targets unknown set '%s'" % (otag, dest))
    subs = story.get("subs")
    if not isinstance(subs, str) or not subs.strip():
        errs.append("'subs' must be the subtitle file path (a string)")
    actors = story.get("actors")
    if not isinstance(actors, list) or not actors:
        errs.append("'actors' must be a non-empty list of object names")
    else:
        for i, a in enumerate(actors):
            if not isinstance(a, str) or not a.strip():
                errs.append("actor %d must be an object name" % (i + 1))
    cps = story.get("cps")
    if not _is_num(cps) or not cps > 0:
        errs.append("'cps' must be a number above zero")
    return errs


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

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


def _resolve(path, base_dir):
    if path.startswith("//"):
        return bge.logic.expandPath(path)
    if os.path.isabs(path):
        return path
    return os.path.join(base_dir, path)


def _load_story_files(expanded_story_path):
    """Read + parse + check story.yml and its subtitle file.

    Returns (story, cues, sets_rt, choices_rt); raises ValueError(msg).
    """
    with open(expanded_story_path, encoding="utf-8-sig") as fh:
        story = parse_minimal_yaml(fh.read())
    subs_rel = story.get("subs", SUBS_DEFAULT) \
        if isinstance(story, dict) else SUBS_DEFAULT
    if not isinstance(subs_rel, str) or not subs_rel.strip():
        subs_rel = SUBS_DEFAULT
    subs_path = _resolve(subs_rel,
                         os.path.dirname(expanded_story_path))
    with open(subs_path, encoding="utf-8-sig") as fh:
        cues = parse_srt_simple(fh.read())
    errs = check_story(story, cues)
    if errs:
        raise ValueError("; ".join(errs))
    sets_rt = {}
    for name, s in story["sets"].items():
        a, b = s["cues"]
        first = cues[a]["start"]
        local = []
        for n in range(a, b + 1):
            c = cues[n]
            local.append([round(c["start"] - first, 4),
                          round(c["end"] - first, 4), c["text"]])
        bits = str(s["end"]).strip().split(None, 1)
        if bits[0] == "choice":
            ch = story["choices"][bits[1]]
            p = cues[ch["prompt_cue"]]
            dur = round(p["start"] - first, 4)
            end = ("choice", bits[1])
            prompt = p["text"]
        elif bits[0] == "goto":
            dur = local[-1][1]
            end = ("goto", bits[1])
            prompt = ""
        else:
            dur = local[-1][1]
            end = ("stop", None)
            prompt = ""
        sets_rt[name] = {"cues": local, "dur": max(dur, 0.0),
                         "frames": list(s["frames"]),
                         "lens": float(s["lens"]), "end": end,
                         "prompt": prompt}
    choices_rt = {}
    for cid, ch in story.get("choices", {}).items():
        choices_rt[cid] = {"prompt_cue": ch["prompt_cue"],
                           "options": [tuple(o) for o in ch["options"]]}
    return story, cues, sets_rt, choices_rt


def _actor_failed(own, name, why):
    """Log-once latch for actors the game cannot play."""
    try:
        failed = own["tw_actor_fail"]
    except Exception:
        failed = {}
        own["tw_actor_fail"] = failed
    if name in failed:
        return
    failed[name] = why
    _log("game_subtitles: actor '%s' unavailable (%s)" % (name, why))


def _enter_set(own, scene, set_id, tick, why):
    own["tw_set"] = set_id
    own["tw_t0"] = tick
    own["tw_state"] = "play"
    own["tw_ci"] = 0
    S = own["tw_sets"][set_id]
    f0, f1 = S["frames"]
    try:
        mode = bge.logic.KX_ACTION_MODE_PLAY
    except Exception:
        mode = 0
    for name in own["tw_actors"]:
        obj = _obj(scene, name)
        if obj is None:
            _actor_failed(own, name, "missing object")
            continue
        try:
            action = obj.getActionName(0)
        except Exception:
            action = ""
        if not action:
            _actor_failed(own, name, "no action on layer 0")
            continue
        try:
            obj.playAction(action, f0, f1, 0, 0, 0, mode)
        except Exception as ex:
            _actor_failed(own, name, "playAction: %r" % (ex,))
    cam = _obj(scene, CAM_OBJ)
    if cam is not None:
        try:
            cam.lens = float(S["lens"])
        except Exception:
            pass
    menu = _obj(scene, MENU_OBJ)
    if menu is not None:
        _set_text(menu, "")
    _log("game_subtitles: enter set '%s' (%s)" % (set_id, why))


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


def confirm_choice(own, scene, tick, idx):
    """Jump to the set of option idx (keyboard AND future clicks)."""
    try:
        key, label, dest = own["tw_opts"][idx]
    except Exception:
        return False
    _log("game_subtitles: choice -> option %s '%s' -> set '%s'"
         % (key, label, dest))
    _enter_set(own, scene, dest, tick, "choice")
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
        own["tw_opts"] = []
        own["tw_prompt"] = ""
        own["tw_actor_fail"] = {}
        try:
            own["tw_dt"] = 1.0 / float(bge.logic.getLogicTicRate())
        except Exception:
            own["tw_dt"] = 1.0 / 60.0
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
            story, cues, sets_rt, choices_rt = \
                _load_story_files(story_path)
            ns = len(sets_rt)
            nc = sum(len(s["cues"]) for s in sets_rt.values())
            na = len(story["actors"])
            own["tw_sets"] = sets_rt
            own["tw_choices"] = choices_rt
            own["tw_actors"] = list(story["actors"])
            own["tw_start"] = story["start"]
            own["tw_cps"] = float(story.get("cps", CPS_FALLBACK))
            _enter_set(own, scene, story["start"], 0, "init")
            try:  # the game always plays through the main camera
                maincam = _obj(scene, CAM_OBJ)
                if maincam is not None:
                    scene.active_camera = maincam
            except Exception:
                pass
            try:  # timeline subtitle objects stay in the editor
                try:
                    objs = list(scene.objects.values())
                except Exception:
                    try:
                        objs = list(scene.objects)
                    except Exception:
                        objs = []
                for o in objs:
                    try:
                        if o.name.startswith("SubLine"):
                            o.visible = False
                    except Exception:
                        pass
            except Exception:
                pass
            _log("game_subtitles: init ok: %d sets %d cues %d actors "
                 "dt=%.4f" % (ns, nc, na, own["tw_dt"]))
        except Exception as ex:
            own["tw_dead"] = "bad story data: %r" % (ex,)
            own["tw_state"] = "play"
            own["tw_set"] = ""
            own["tw_t0"] = 0
            own["tw_sets"] = {}
            own["tw_choices"] = {}
            own["tw_actors"] = []
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
        _enter_set(own, scene, own["tw_start"], tick, "restart")

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
    dur = S["dur"]

    # ---- transitions (may switch set/state; rendering refetches after) ----
    if state == "play" and t >= dur:
        kind, target = S["end"]
        if kind == "choice":
            own["tw_state"] = "choice"
            own["tw_ctick"] = tick
            own["tw_ci"] = 0
            own["tw_opts"] = list(own["tw_choices"][target]["options"])
            own["tw_prompt"] = S["prompt"]
            _log("game_subtitles: choice '%s' (%d options)"
                 % (target, len(own["tw_opts"])))
        elif kind == "goto":
            _log("game_subtitles: goto set '%s'" % target)
            _enter_set(own, scene, target, tick, "goto")
        else:
            own["tw_state"] = "stop"
            own["tw_stop"] = tick
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
                    confirm_choice(own, scene, tick, i)
                    break
        elif (_just_pressed(keyboard, bge.events, "ENTERKEY")
              or _just_pressed(keyboard, bge.events, "RETKEY")
              or _just_pressed(keyboard, bge.events, "SPACEKEY")):
            try:
                ci = int(own["tw_ci"])
            except Exception:
                ci = 0
            confirm_choice(own, scene, tick, ci)
        elif capturing and (tick - own["tw_ctick"]) >= CHOICE_AUTO_TICKS:
            _log("game_subtitles: capture auto-picks option 1")
            confirm_choice(own, scene, tick, 0)

    # ---- render (refetch: the set may just have changed) ----
    state = own["tw_state"]
    set_id = own["tw_set"]
    S = own["tw_sets"][set_id]
    t = (tick - own["tw_t0"]) * dt
    cues = S["cues"]

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
            body = cues[-1][2]
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
    # the menu hides via scale-0 keys on the timeline (hide_render would
    # exclude it from game conversion); force it visible in-game
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
    if capturing and tick % 2 == 0:
        try:
            import os as _os
            capdir = bge.logic.expandPath("//capture")
            _os.makedirs(capdir, exist_ok=True)
            own["cap_n"] += 1
            bge.render.makeScreenshot(
                _os.path.join(capdir, "game_%04d.png" % own["cap_n"]))
        except Exception as ex:
            print("capture failed:", ex)
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
