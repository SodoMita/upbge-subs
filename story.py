"""Story wiring v2 for Talking Robots: animation-centric sets (YAML).

Subtitle text + [CAM] shot plans live in .srt files (one per set, edited
with any subtitle software); everything else lives in story.yml. This
module (stdlib only, no bpy) parses both into per-set runtime plans
consumed by the game driver (game_subtitles.py, loaded from disk next to
the .blend), the Blender add-on (preview/validation/sync) and the tools.

A set = a bundle of animations played together from t=0. Subtitles are
one animation type among others; blocking animations gate the set's end:

story.yml scheme:
  start: <set>            the set played at start
  cps: <number>           typewriter speed (chars per second)
  sets:
    <name>:
      anims:
        - subs: file.srt#a-b    typewriter over cues a..b of that file
        - camera: ShotName       opening staged camera (optional)
        - action: Obj@Act        play action Act on object Obj
        - audio: file.ogg        fire-and-forget sound
        - {action: Obj@Act, at: 2.0, wait: false}   offset + ambience
      end: stop | goto <set> | choice <id>
  choices:
    <id>:
      prompt: file.srt#N    cue whose text is the prompt
      options:
        - [key, label, set]   (key 1-9)

Rules: cue numbers are per-file; SRT times are set-local (author each
file from 00:00); `at:` shifts an anim in seconds; subs/action block the
end unless `wait: false`; camera/audio never block; [CAM X] cues switch
shots mid-set; references are Blender object names (the add-on watches
renames and syncs them back). Files resolve next to story.yml: subtitles
in `.` and `./subtitles/`, audio in `./audio/` and `.`.

Only this YAML subset is supported: # comments, key: value maps,
- list items, [flow, lists], {flow: maps} (single-line; use them for
anim options), single/double quoted strings. Anything else is a plain
string. Indent with spaces, never tabs.

Speaker convention: text starting with "CUBY:", "SPHERO:" or "BOTH:"
(case-insensitive) is acted out by those robots; anything else is narration.
"""

import json
import os
import re

DIRECTIVES = ("CAM",)
LEGACY_DIRECTIVES = ("BRANCH", "CHOICE", "OPT", "GOTO", "END")
ANIM_TYPES = ("subs", "action", "camera", "audio")
BLOCKING_DEFAULT = {"subs": True, "action": True,
                    "camera": False, "audio": False}
SUBS_DIRS = (".", "subtitles")
AUDIO_DIRS = ("audio", ".")


def is_directive(line):
    """True for [CAM ...] and the legacy wiring directives.

    NOTE: the add-on carries an identical copy (it must stay a single
    installable file); test_story.py verifies the two agree.
    """
    s = line.strip()
    if len(s) < 3 or s[0] != "[" or s[-1] != "]":
        return False
    inner = s[1:-1].strip()
    if not inner:
        return False
    return inner.split(None, 1)[0] in DIRECTIVES + LEGACY_DIRECTIVES


def strip_directives(text):
    """Remove directive lines; plain [bracket] text is kept."""
    return "\n".join(ln for ln in text.split("\n")
                     if not is_directive(ln)).strip()


_SPEAKERS = ("CUBY", "SPHERO", "BOTH")


def speakers_of(text):
    """["CUBY"] / ["SPHERO"] / ["CUBY", "SPHERO"] / [] from the prefix."""
    first = text.strip().split("\n", 1)[0].strip().upper()
    for name in _SPEAKERS:
        if first.startswith(name + ":"):
            return ["CUBY", "SPHERO"] if name == "BOTH" else [name]
    return []


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
    """Numbered SRT blocks -> {cue_no: {"start", "end", "text"}}.

    The cue number is the SRT number row (block position if missing);
    directive lines are stripped from the text. Blocks without a timing
    row are skipped.
    """
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


def parse_script(text):
    """Full subtitle parse -> {"errors", "warnings", "cues", "cams"}.

    cues = parse_srt_simple(); cams = [[time, shot, cue_no]] from [CAM].
    Shot names are NOT validated here (the scene owns them; see
    check_bindings). Legacy wiring directives are ignored with a warning.
    """
    errors, warnings = [], []
    cues = parse_srt_simple(text)
    cams = []
    seen = set()
    for pos, rows in enumerate(_split_blocks(text), 1):
        tidx = None
        for j, r in enumerate(rows):
            if '-->' in r:
                tidx = j
                break
        if tidx is None:
            warnings.append("block %d has no timing row; skipped" % pos)
            continue
        number = pos
        if tidx > 0 and rows[tidx - 1].strip().isdigit():
            number = int(rows[tidx - 1].strip())
        else:
            warnings.append("cue at block %d has no number row; using %d"
                            % (pos, pos))
        if number in seen:
            warnings.append("cue number %d appears twice; last one wins"
                            % number)
        seen.add(number)
        parts = rows[tidx].split('-->')
        t0 = _ts_to_seconds(parts[0])
        if t0 is None:
            continue
        for ln in rows[tidx + 1:]:
            if not is_directive(ln):
                continue
            d = ln.strip()[1:-1].strip()
            tok, _, arg = d.partition(" ")
            arg = arg.strip()
            if tok in LEGACY_DIRECTIVES:
                warnings.append("[%s] is legacy (cue %d): wiring lives in "
                                "story.yml; ignored" % (tok, number))
            elif tok == "CAM":
                if not arg:
                    errors.append("[CAM] needs a shot name (cue %d)" % number)
                else:
                    cams.append([t0, arg, number])
    if not cues:
        errors.append("no usable cues (need number/timing/text blocks)")
    cams.sort(key=lambda c: c[0])
    return {"errors": errors, "warnings": warnings,
            "cues": cues, "cams": cams}


# --------------------------------------------------------------------------
# minimal YAML subset (see the scheme in the module docstring)
# --------------------------------------------------------------------------

def _strip_comment(line):
    q = None
    esc = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if q is not None:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == q:
                if q == "'" and i + 1 < n and line[i + 1] == "'":
                    i += 2  # doubled quote: literal, stays open
                    continue
                q = None
        else:
            if ch in ("'", '"') and (i == 0 or line[i - 1] in " \t:[,{"):
                q = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                # real YAML rule: '#' comments only after whitespace, so
                # cue refs like file.srt#1-8 survive unquoted
                return line[:i]
        i += 1
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
        if ch in "[],{}:":
            toks.append(ch)
            i += 1
            continue
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
        while j < n and s[j] not in ",[]{}:":
            j += 1
        atom = s[i:j].strip()
        if not atom:
            raise ValueError("empty value in flow collection")
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

    def scalar(t):
        if isinstance(t, tuple):
            return t[1] if t[0] == "str" else _parse_scalar(t[1])
        raise ValueError("unexpected %r in flow collection" % (t,))

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
        if t == "{":
            out = {}
            if peek() == "}":
                nxt()
                return out
            while True:
                k = scalar(nxt())
                if not isinstance(k, str) or not k:
                    raise ValueError("flow map keys must be names")
                if k in out:
                    raise ValueError("duplicate key %r in flow map" % k)
                if nxt() != ":":
                    raise ValueError("want 'key: value' in flow map")
                out[k] = value()
                t2 = nxt()
                if t2 == "}":
                    return out
                if t2 != ",":
                    raise ValueError("want ',' or '}' in flow map")
            return out
        return scalar(t)

    v = value()
    if peek() is not None:
        raise ValueError("trailing %r after flow collection" % (peek(),))
    if not isinstance(v, (list, dict)):
        raise ValueError("flow value must be [...] or {...}")
    return v


def parse_minimal_yaml(text):
    """Parse the story.yml subset -> nested dicts/lists/scalars.

    Raises ValueError("line N: ...") on anything outside the subset.
    """
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
        if val.startswith(("[", "{")):
            try:
                return _parse_flow(val)
            except ValueError as ex:
                raise ValueError("line %d: %s" % (no, ex))
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
                if (pos[0] < len(raw) and raw[pos[0]][0] > indent
                        and ":" in item
                        and not item.startswith(("[", "{"))):
                    raise ValueError(
                        "line %d: multi-line list items need flow style, "
                        "e.g. - {subs: f.srt#1-2, at: 3.0}" % no)
                out.append(flow_or_scalar(item, no))
        if pos[0] < len(raw) and raw[pos[0]][0] > indent:
            bad = raw[pos[0]]
            raise ValueError("line %d: bad indentation" % bad[1])
        return out

    return parse_block(0)


# --------------------------------------------------------------------------
# animation references
# --------------------------------------------------------------------------

def parse_subs_ref(value):
    """'file.srt#a-b' / 'file.srt#N' -> (file, a, b, error)."""
    if not isinstance(value, str):
        return None, 0, 0, "subs reference must be 'file.srt#a-b'"
    if value.count("#") != 1:
        return None, 0, 0, "subs reference %r needs one '#'" % (value,)
    fname, _, span = value.partition("#")
    fname = fname.strip()
    if not fname:
        return None, 0, 0, "subs reference %r needs a file" % (value,)
    bits = span.split("-")
    if len(bits) == 1:
        bits = bits * 2
    if len(bits) != 2 or not all(b.strip().isdigit() for b in bits):
        return None, 0, 0, "subs reference %r needs cue numbers" % (value,)
    a, b = int(bits[0]), int(bits[1])
    if a < 1 or b < a:
        return None, 0, 0, "subs cue range %r must rise from 1+" % (value,)
    return fname, a, b, ""


def parse_cue_ref(value):
    """'file.srt#N' -> (file, n, error)."""
    fname, a, b, err = parse_subs_ref(value)
    if err:
        return None, 0, err
    if a != b:
        return None, 0, "cue reference %r must be a single cue" % (value,)
    return fname, a, ""


def parse_action_ref(value):
    """'Object@Action' -> (object, action, error)."""
    if not isinstance(value, str):
        return None, None, "action reference must be 'Object@Action'"
    bits = value.split("@")
    if len(bits) != 2 or not bits[0].strip() or not bits[1].strip():
        return None, None, "action reference %r must be 'Object@Action'" % (
            value,)
    return bits[0].strip(), bits[1].strip(), ""


def normalize_anim(entry):
    """Anim entry -> ({"type", "value", "at", "wait"}, error).

    Accepts the 'type: value' shorthand string and the {type: value,
    at:, wait:} flow map. wait defaults per type (subs/action block).
    """
    at, wait, at_set, wait_set = 0.0, None, False, False
    if isinstance(entry, dict):
        types = [k for k in entry if k in ANIM_TYPES]
        if len(types) != 1:
            return None, "anim %r needs exactly one of %s" % (
                entry, "/".join(ANIM_TYPES))
        atype = types[0]
        value = entry[atype]
        for k in entry:
            if k not in ANIM_TYPES + ("at", "wait"):
                return None, "anim %r: unknown key '%s'" % (entry, k)
        if "at" in entry:
            at, at_set = entry["at"], True
        if "wait" in entry:
            wait, wait_set = entry["wait"], True
    elif isinstance(entry, str) and ":" in entry:
        atype, _, value = entry.partition(":")
        atype, value = atype.strip(), value.strip()
        if atype not in ANIM_TYPES:
            return None, "anim %r: unknown type '%s'" % (entry, atype)
        if not value:
            return None, "anim %r needs a value" % (entry,)
    else:
        return None, "anim %r must be 'type: value' or {type: value, ...}" % (
            entry,)
    if at_set and (type(at) is bool or not isinstance(at, (int, float))
                   or not at >= 0):
        return None, "anim '%s: %s': 'at' must be seconds >= 0" % (
            atype, value)
    if wait_set and type(wait) is not bool:
        return None, "anim '%s: %s': 'wait' must be true/false" % (
            atype, value)
    if wait is None:
        wait = BLOCKING_DEFAULT[atype]
    return {"type": atype, "value": value, "at": float(at), "wait": wait}, ""


def _is_num(x):
    return type(x) in (int, float)


def _end_bits(end):
    if end is None:
        return []
    return str(end).strip().split(None, 1)


def check_story(story, files):
    """Crash-path validation -> [error strings].

    files = {name: {"cues": {n: {"start", "end", "text"}}}} as loaded.
    Every path the game driver walks is covered: no errors here means the
    driver cannot KeyError on story data.
    """
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
    anims_of = {}
    for name, s in sets.items():
        tag = "set '%s'" % (name,)
        if not isinstance(s, dict):
            errs.append("%s must be a mapping" % tag)
            anims_of[name] = []
            continue
        anims = s.get("anims")
        if not isinstance(anims, list) or not anims:
            errs.append("%s needs a non-empty 'anims' list" % tag)
            anims_of[name] = []
        else:
            ok = []
            cams = 0
            for i, e in enumerate(anims):
                atag = "%s anim %d" % (tag, i + 1)
                anim, err = normalize_anim(e)
                if err:
                    errs.append("%s: %s" % (atag, err))
                    continue
                ok.append(anim)
                if anim["type"] == "subs":
                    f, a, b, referr = parse_subs_ref(anim["value"])
                    if referr:
                        errs.append("%s: %s" % (atag, referr))
                    elif f not in files:
                        errs.append("%s: subtitle file '%s' not found "
                                    "(looked in . and ./subtitles)" % (atag, f))
                    else:
                        cues = files[f]["cues"]
                        for v in range(a, b + 1):
                            if v not in cues:
                                errs.append("%s: cue %d is not in %s"
                                            % (atag, v, f))
                elif anim["type"] == "action":
                    _o, _a, referr = parse_action_ref(anim["value"])
                    if referr:
                        errs.append("%s: %s" % (atag, referr))
                elif anim["type"] == "camera":
                    cams += 1
                    if not isinstance(anim["value"], str) \
                            or not anim["value"].strip():
                        errs.append("%s: camera needs a shot object name"
                                    % atag)
                elif anim["type"] == "audio":
                    if not isinstance(anim["value"], str) \
                            or not anim["value"].strip():
                        errs.append("%s: audio needs a file name" % atag)
            if cams > 1:
                errs.append("%s: only one 'camera' anim per set "
                            "(shots switch via [CAM])" % tag)
            anims_of[name] = ok
        bits = _end_bits(s.get("end"))
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
        f, n, referr = parse_cue_ref(ch.get("prompt"))
        if referr:
            errs.append("%s: bad 'prompt': %s" % (tag, referr))
        elif f not in files:
            errs.append("%s: subtitle file '%s' not found "
                        "(looked in . and ./subtitles)" % (tag, f))
        elif n not in files[f]["cues"]:
            errs.append("%s: cue %d is not in %s" % (tag, n, f))
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
    cps = story.get("cps")
    if not _is_num(cps) or not cps > 0:
        errs.append("'cps' must be a number above zero")
    if not errs:
        errs.extend(_loop_checks(story, anims_of))
    return errs


def _blocking_set(anims):
    return any(a["wait"] and a["type"] in ("subs", "action") for a in anims)


def _loop_checks(story, anims_of):
    """Instant-loop detection: a goto cycle with no blocking set and no
    choice/stop inside would hang the game on one tick."""
    errs = []
    sets = story.get("sets", {})
    for name in sets:
        seen = []
        cur = name
        while True:
            if cur in seen:
                cyc = seen[seen.index(cur):]
                if not any(_blocking_set(anims_of.get(c, [])) for c in cyc):
                    errs.append("sets %s loop with no blocking animation "
                                "(game would hang)" % " -> ".join(cyc))
                break
            seen.append(cur)
            s = sets.get(cur)
            if not isinstance(s, dict):
                break
            bits = _end_bits(s.get("end"))
            if len(bits) == 2 and bits[0] == "goto" and bits[1] in sets:
                cur = bits[1]
                continue
            break
    return sorted(set(errs))


def warn_story(story, files):
    """Non-fatal smells -> [warning strings] (tools show these)."""
    warns = []
    if not isinstance(story, dict):
        return warns
    sets = story.get("sets", {})
    choices = story.get("choices", {}) or {}
    for name, s in sets.items():
        if not isinstance(s, dict):
            continue
        for e in s.get("anims", []):
            anim, err = normalize_anim(e) if isinstance(
                e, (str, dict)) else (None, "x")
            if err or anim is None:
                continue
            if anim["type"] in ("camera", "audio") and anim["wait"]:
                warns.append("set '%s': '%s' never blocks; 'wait: true' "
                             "is ignored" % (name, anim["type"]))
            if anim["type"] == "subs":
                f, a, _b, referr = parse_subs_ref(anim["value"])
                if referr or f not in files:
                    continue
                cues = files[f]["cues"]
                if a in cues and cues[a]["start"] + anim["at"] > 1.0:
                    warns.append("set '%s': subs start %.1fs in "
                                 "(leading silence)" % (
                                     name, cues[a]["start"] + anim["at"]))
    # prompt echoed inside its own offering set's subs range
    for name, s in sets.items():
        if not isinstance(s, dict):
            continue
        bits = _end_bits(s.get("end"))
        if len(bits) != 2 or bits[0] != "choice":
            continue
        ch = choices.get(bits[1])
        if not isinstance(ch, dict):
            continue
        f, n, referr = parse_cue_ref(ch.get("prompt"))
        if referr or f not in files:
            continue
        for e in s.get("anims", []):
            anim, err = normalize_anim(e) if isinstance(
                e, (str, dict)) else (None, "x")
            if err or anim is None or anim["type"] != "subs":
                continue
            sf, sa, sb, serr = parse_subs_ref(anim["value"])
            if not serr and sf == f and sa <= n <= sb:
                warns.append("choice '%s' prompt %s#%d is inside set '%s' "
                             "subs (shows twice)" % (bits[1], f, n, name))
    # reachability from start
    if isinstance(sets, dict) and story.get("start") in sets:
        seen_sets, seen_choices = set(), set()

        def visit(sname):
            if sname in seen_sets:
                return
            seen_sets.add(sname)
            s = sets.get(sname)
            if not isinstance(s, dict):
                return
            bits = _end_bits(s.get("end"))
            if len(bits) == 2 and bits[0] == "goto":
                visit(bits[1])
            elif len(bits) == 2 and bits[0] == "choice":
                seen_choices.add(bits[1])
                ch = choices.get(bits[1])
                if isinstance(ch, dict):
                    for o in ch.get("options", []):
                        if isinstance(o, list) and len(o) == 3:
                            visit(o[2])

        visit(story["start"])
        for sname in sets:
            if sname not in seen_sets:
                warns.append("set '%s' is unreachable from 'start'" % sname)
        for cid in choices:
            if cid not in seen_choices:
                warns.append("choice '%s' is never offered" % cid)
    return warns


def check_bindings(story, objects, actions, cameras):
    """Scene-name validation -> [error strings].

    objects/actions/cameras are name collections from the live scene (or
    the sync sidecar for actions). Call after check_story passes.
    """
    errs = []
    objects, actions, cameras = set(objects), set(actions), set(cameras)
    for name, s in story.get("sets", {}).items():
        if not isinstance(s, dict):
            continue
        for i, e in enumerate(s.get("anims", [])):
            atag = "set '%s' anim %d" % (name, i + 1)
            anim, err = normalize_anim(e) if isinstance(
                e, (str, dict)) else (None, "x")
            if err or anim is None:
                continue
            if anim["type"] == "action":
                o, a, _ = parse_action_ref(anim["value"])
                if o not in objects:
                    errs.append("%s: object '%s' is not in the scene"
                                % (atag, o))
                if a not in actions:
                    errs.append("%s: action '%s' does not exist" % (atag, a))
            elif anim["type"] == "camera":
                if anim["value"] not in cameras:
                    errs.append("%s: shot '%s' is not a camera in the scene"
                                % (atag, anim["value"]))
    return errs


def _resolve_here(base_dir, name, dirs):
    for d in dirs:
        cand = os.path.normpath(os.path.join(base_dir, d, name))
        if os.path.isfile(cand):
            return cand
    return ""


def _gather_refs(story):
    """(subs_files, prompt_files, audio_files) referenced by the story."""
    subs, audio = set(), set()
    if not isinstance(story, dict):
        return subs, audio
    for s in story.get("sets", {}).values():
        if not isinstance(s, dict):
            continue
        for e in s.get("anims", []):
            anim, err = normalize_anim(e) if isinstance(
                e, (str, dict)) else (None, "x")
            if err or anim is None:
                continue
            if anim["type"] == "subs":
                f, _a, _b, referr = parse_subs_ref(anim["value"])
                if not referr:
                    subs.add(f)
            elif anim["type"] == "audio" and isinstance(anim["value"], str):
                audio.add(anim["value"].strip())
    for ch in (story.get("choices", {}) or {}).values():
        if not isinstance(ch, dict):
            continue
        f, _n, referr = parse_cue_ref(ch.get("prompt"))
        if not referr:
            subs.add(f)
    return subs, audio


def load_story_files(story_path):
    """Read story.yml + every referenced .srt/.ogg path check -> one dict.

    {"errors", "warnings", "story", "dir", "files": {name: {"path",
    "cues", "cams"}}, "audio": {name: path}}. Missing files are errors.
    """
    blank = {"errors": [], "warnings": [], "story": {}, "dir": "",
             "files": {}, "audio": {}}
    try:
        with open(story_path, encoding="utf-8-sig") as fh:
            story = parse_minimal_yaml(fh.read())
    except OSError as ex:
        blank["errors"] = ["cannot read %s (%s)" % (story_path, ex)]
        return blank
    except ValueError as ex:
        blank["errors"] = ["%s: %s" % (story_path, ex)]
        return blank
    blank["story"] = story
    base = os.path.dirname(os.path.abspath(story_path))
    blank["dir"] = base
    subs_refs, audio_refs = _gather_refs(story)
    for name in sorted(subs_refs):
        path = _resolve_here(base, name, SUBS_DIRS)
        if not path:
            blank["errors"].append(
                "subtitle file '%s' not found (. and ./subtitles)" % name)
            continue
        try:
            with open(path, encoding="utf-8-sig") as fh:
                script = parse_script(fh.read())
        except OSError as ex:
            blank["errors"].append("cannot read %s (%s)" % (path, ex))
            continue
        blank["files"][name] = {"path": path, "cues": script["cues"],
                                "cams": script["cams"]}
        blank["warnings"].extend("%s: %s" % (name, w)
                                 for w in script["warnings"])
        blank["errors"].extend("%s: %s" % (name, e)
                               for e in script["errors"])
    for name in sorted(audio_refs):
        if not name:
            continue
        path = _resolve_here(base, name, AUDIO_DIRS)
        if not path:
            blank["errors"].append(
                "audio file '%s' not found (./audio and .)" % name)
        else:
            blank["audio"][name] = path
    blank["errors"].extend(check_story(story, blank["files"]))
    if not blank["errors"]:
        blank["warnings"].extend(warn_story(story, blank["files"]))
    return blank


def ordered_cues(cues):
    """[(number, start, end, text, speakers)] sorted by number."""
    return [(n, cues[n]["start"], cues[n]["end"], cues[n]["text"],
             speakers_of(cues[n]["text"])) for n in sorted(cues)]


def menu_body(options):
    """Static menu text (cursor on option 1)."""
    return "\n".join("%s %s: %s" % (">" if i == 0 else " ", o[0], o[1])
                     for i, o in enumerate(options))


# --------------------------------------------------------------------------
# runtime plans (game + preview share these)
# --------------------------------------------------------------------------

def set_plan(story, files, set_name, action_ranges=None, fps=24.0):
    """Per-set runtime plan with set-local seconds.

    {"subs": [(start, end, text)], "dur": subs-blocking end (0 if none),
     "actions": [(obj, act, at, wait, dur_or_None)],
     "shots": [(t, shot_obj)] (opening first; [] = hold framing),
     "audios": [(file, at)], "end": ("stop"|"goto"|"choice", target),
     "prompt": text}. action_ranges = {act: [f0, f1]} from the sync sidecar
    (or the live scene); durations divide by fps.
    """
    s = story["sets"][set_name]
    subs, shots, actions, audios = [], [], [], []
    opening = None
    for e in s.get("anims", []):
        anim, err = normalize_anim(e)
        if err:
            continue
        at = anim["at"]
        if anim["type"] == "subs":
            f, a, b, _ = parse_subs_ref(anim["value"])
            cues = files[f]["cues"]
            for n in range(a, b + 1):
                c = cues[n]
                rounding = (round(c["start"] + at, 4), round(c["end"] + at, 4),
                            c["text"], anim["wait"])
                subs.append(rounding)
            for (ct, shot, cn) in files[f]["cams"]:
                if a <= cn <= b:
                    shots.append((round(ct + at, 4), shot))
        elif anim["type"] == "camera":
            opening = anim["value"]
        elif anim["type"] == "action":
            o, a, _ = parse_action_ref(anim["value"])
            dur = None
            if action_ranges and a in action_ranges:
                f0, f1 = action_ranges[a]
                dur = max(0.0, (f1 - f0) / fps)
            actions.append((o, a, at, anim["wait"], dur))
        elif anim["type"] == "audio":
            audios.append((anim["value"], at))
    subs.sort(key=lambda c: c[0])
    shots.sort(key=lambda c: c[0])
    if opening is not None:
        # opening first: a t=0 [CAM] cue wins ties (runtime takes the last
        # shot at or before now), an identical one is harmless
        shots = [(0.0, opening)] + shots
    dur = max([c[1] for c in subs if c[3]] or [0.0])
    bits = _end_bits(s.get("end"))
    if bits == ["stop"]:
        end, prompt = ("stop", None), ""
    elif bits[0] == "goto":
        end, prompt = ("goto", bits[1]), ""
    else:
        ch = story["choices"][bits[1]]
        f, n, _ = parse_cue_ref(ch["prompt"])
        end, prompt = ("choice", bits[1]), files[f]["cues"][n]["text"]
    return {"subs": [(st, en, tx) for (st, en, tx, _w) in subs], "dur": dur,
            "actions": actions, "shots": shots, "audios": audios,
            "end": end, "prompt": prompt}


def set_duration(plan):
    """Full set length incl. blocking action anims (tools/preview)."""
    ends = [plan["dur"]]
    for (_o, _a, at, wait, dur) in plan["actions"]:
        if wait and dur is not None:
            ends.append(at + dur)
    return max(ends)


# --------------------------------------------------------------------------
# sync sidecar + editor schema (written by the add-on, read by game/tools)
# --------------------------------------------------------------------------

def sync_path_for(story_path):
    """story.yml -> story.sync.json (action ranges + rename map)."""
    root, _ = os.path.splitext(story_path)
    return root + ".sync.json"


def schema_path_for(story_path):
    """story.yml -> story.schema.json (VSCode YAML completion)."""
    root, _ = os.path.splitext(story_path)
    return root + ".schema.json"


def load_sidecar(path):
    """{"uids": {uid: {"name", "type"}}, "actions": {act: [f0, f1]}}.

    Missing/corrupt files yield empty maps (callers report what's missing).
    """
    blank = {"uids": {}, "actions": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return blank
    if not isinstance(data, dict):
        return blank
    uids = data.get("uids")
    if isinstance(uids, dict):
        blank["uids"] = uids
    acts = data.get("actions")
    if isinstance(acts, dict):
        for k, v in acts.items():
            if isinstance(v, list) and len(v) == 2 and all(
                    type(x) in (int, float) for x in v):
                blank["actions"][k] = [v[0], v[1]]
    return blank


# --------------------------------------------------------------------------
# rename watch + targeted reference rewrite (editor-side, bpy-free)
# --------------------------------------------------------------------------

UID_KINDS = ("object", "action", "camera")


def diff_uids(recorded, live):
    """Stable-uid name diff -> (renames, new, missing).

    recorded/live are {uid: {"name": str, "type": str}} maps (the sidecar's
    "uids" vs the live scene). Same uid, different name == a rename, which
    is the only story-breaking edit an id can catch (story.yml references
    Blender NAMES). Returns:
      renames [{"uid", "type", "old", "new"}]  (needs a story.yml rewrite)
      new     [{"uid", "type", "name"}]        (never recorded yet)
      missing ["uid", ...]                     (recorded, gone from scene)
    """
    renames, new, missing = [], [], []
    for uid, rec in sorted((recorded or {}).items()):
        if not isinstance(rec, dict):
            continue
        cur = (live or {}).get(uid)
        if cur is None:
            missing.append(uid)
            continue
        if str(cur.get("name", "")) != str(rec.get("name", "")):
            renames.append({"uid": uid, "type": str(rec.get("type", "object")),
                            "old": str(rec.get("name", "")),
                            "new": str(cur.get("name", ""))})
    for uid, cur in sorted((live or {}).items()):
        if uid not in (recorded or {}):
            new.append({"uid": uid, "type": str(cur.get("type", "object")),
                        "name": str(cur.get("name", ""))})
    return renames, new, missing


def rewrite_refs(text, renames, kind="yml"):
    """Rewrite name references in place -> (new_text, [applied notes]).

    Targeted: only occurrences used as a reference of that kind are touched,
    never labels, set names or prose; comments included (they read as refs).
    kind="yml" handles story.yml (`Obj@Act`, `camera: Shot`); kind="srt"
    handles [CAM Shot] directive lines. renames: [{"type","old","new"}].
    No-op (text unchanged, empty report) when nothing applies.
    """
    applied = []
    out = text
    for r in renames or []:
        kind_of = str(r.get("type", "object"))
        old, new = str(r.get("old", "")), str(r.get("new", ""))
        if not old or not new or old == new:
            continue
        o = re.escape(old)
        if kind == "srt":
            if kind_of != "camera":
                continue
            pat, repl = r"(?m)(\[CAM\s+)%s(\s*\])" % o, r"\g<1>%s\g<2>" % new
        elif kind_of == "action":
            pat, repl = r"(@\s*)%s(?![\w.\-@])" % o, r"\g<1>%s" % new
        elif kind_of == "camera":
            pat, repl = r"(camera:\s*['\"]?)%s(?![\w.\-@])" % o, r"\g<1>%s" % new
        elif kind_of == "object":
            pat, repl = r"(?<![\w.\-@])%s(?=\s*@)" % o, new
        else:
            continue
        out, n = re.subn(pat, repl, out)
        if n:
            applied.append("%s: %s -> %s (%d)" % (kind_of, old, new, n))
    return out, applied


# --------------------------------------------------------------------------
# sync sidecar (story.sync.json): action frame ranges + rename map
# --------------------------------------------------------------------------

SIDECAR_NOTE = ("generated by Refresh Sync (add-on) - action frame ranges + "
                "rename map; refreshed on save")


def sidecar_payload(uids, action_ranges, note=SIDECAR_NOTE):
    """Sidecar dict in the load_sidecar() shape (sorted, plain JSON)."""
    acts = {}
    for name, rng in sorted((action_ranges or {}).items()):
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            acts[name] = [int(rng[0]), int(rng[1])]
    return {"_note": note, "uids": dict(uids or {}), "actions": acts}


def save_sidecar(path, payload):
    """Write the sidecar atomically -> error string or \"\"."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as ex:
        return "cannot write %s (%s)" % (path, ex)
    return ""


def build_schema(story, objects, actions, cameras, srt_files, audio_files):
    """JSON Schema dict for story.yml (VSCode $schema completion).

    objects/actions/cameras/srt_files/audio_files are name lists from the
    live scene + project scan. Regenerate on save to keep enums fresh.
    """
    sets = sorted(str(k) for k in story.get("sets", {}))
    choices = sorted(str(k) for k in (story.get("choices") or {}))
    anim_short = {
        "type": "string",
        "pattern": "^(subs|action|camera|audio):\\s*\\S",
        "description": "subs: file.srt#a-b | action: Obj@Act | "
                       "camera: Shot | audio: file.ogg"},
    anim_full = {
        "type": "object",
        "properties": {
            "subs": {"type": "string",
                     "description": "cue range in %s"
                     % (", ".join(sorted(srt_files)) or "no .srt found")},
            "action": {"type": "string",
                       "description": "Obj@Act, e.g. %s" % (
                           ", ".join(sorted(objects)[:6]) or "no objects")},
            "camera": {"type": "string", "enum": sorted(cameras) or ["Shot"]},
            "audio": {"type": "string",
                      "enum": sorted(audio_files) or ["sound.ogg"]},
            "at": {"type": "number", "minimum": 0},
            "wait": {"type": "boolean"}},
        "additionalProperties": False,
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Talking Robots story.yml",
        "type": "object",
        "required": ["start", "sets"],
        "properties": {
            "start": {"type": "string", "enum": sets or ["main"]},
            "cps": {"type": "number", "exclusiveMinimum": 0},
            "sets": {
                "type": "object",
                "patternProperties": {
                    "^.+$": {
                        "type": "object",
                        "required": ["anims", "end"],
                        "properties": {
                            "anims": {"type": "array", "minItems": 1,
                                      "items": {"anyOf": [anim_short,
                                                          anim_full]}},
                            "end": {
                                "type": "string",
                                "pattern": "^(stop|goto \\S+|choice \\S+)$",
                                "description": "goto: %s; choice: %s" % (
                                    ", ".join(sets), ", ".join(choices))},
                        },
                        "additionalProperties": False,
                    }
                },
            },
            "choices": {
                "type": "object",
                "patternProperties": {
                    "^.+$": {
                        "type": "object",
                        "required": ["prompt", "options"],
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "pattern": "^\\S+#\\d+$",
                                "description": "cue ref in %s" % (
                                    ", ".join(sorted(srt_files))
                                    or "no .srt found")},
                            "options": {
                                "type": "array", "minItems": 1,
                                "items": {
                                    "type": "array",
                                    "prefixItems": [
                                        {"description": "key 1-9"},
                                        {"type": "string"},
                                        {"type": "string",
                                         "enum": sets or ["main"]}],
                                    "minItems": 3, "maxItems": 3,
                                },
                            },
                        },
                        "additionalProperties": False,
                    }
                },
            },
        },
        "additionalProperties": False,
    }
