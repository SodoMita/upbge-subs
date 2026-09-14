"""Story wiring for Talking Robots: sets, actors, choices, ends (YAML).

Plain SRT stays valid for external editors (Subtitle Edit, Aegisub, ...):
subtitle text + [CAM] shot plan live in dialogue.srt, everything else lives
in story.yml. This module (stdlib only, no bpy) parses both into the
structure consumed by build_scene.py (starter camera keys, menu preview,
markers, frame range), resave_game.py (validation) and verify_game.py.
The game driver (game_subtitles.py) carries verbatim copies of the two
parsers + the checker - it cannot import sibling files in the engine.

story.yml scheme (short on purpose):
  start: <set>            the set played at start
  cps: <number>           typewriter speed (chars per second)
  subs: <path>            subtitle file (relative to story.yml, // = blend)
  actors: [<object>...]   objects whose own actions the game plays
  sets:
    <name>:
      cues: [first, last] SRT cue numbers (inclusive)
      frames: [f0, f1]    timeline range the actors play in-game
      lens: <mm>          camera lens while this set plays
      end: stop | goto <set> | choice <id>
  choices:
    <id>:
      prompt_cue: <n>     SRT cue whose text is the prompt
      options:
        - [key, label, set]   (key 1-9)

  Only this YAML subset is supported: # comments, key: value maps, - list
  items, [flow, lists] (lists only - maps use block style), single/double
  quoted strings ("\\n" etc. inside double quotes). Anything else is a
  plain string. Indent with spaces, never tabs.

[CAM <shot>] lines inside SRT cues steer the starter camera bake only
(shots: Wide, Cuby, Sphero). The pre-1.8 wiring directives ([BRANCH],
[CHOICE], [OPT], [GOTO], [END]) are legacy: still stripped from visible
text, but ignored with a warning - wiring lives in story.yml now.

Speaker convention: text starting with "CUBY:", "SPHERO:" or "BOTH:"
(case-insensitive) is acted out by those robots; anything else is narration.
"""

import os
import re

DIRECTIVES = ("CAM",)
LEGACY_DIRECTIVES = ("BRANCH", "CHOICE", "OPT", "GOTO", "END")
SHOTS = ("Wide", "Cuby", "Sphero")

# Staged shot cameras: ((loc_x, loc_y, loc_z), (target_x, target_y, target_z)).
# Editor framing rig (tweak the objects in the viewport); the starter camera
# keys are baked from them, then the keys are yours to edit. Wide drifts
# A -> B (the slow push-in) inside Wide spans.
WIDE_A = ((0.0, -7.3, 3.35), (0.0, 0.0, 1.05))
WIDE_B = ((0.0, -6.7, 3.05), (0.0, 0.0, 1.05))
CUBY_SHOT = ((-1.25, -4.6, 2.0), (-1.25, 0.0, 1.05))
SPHERO_SHOT = ((1.25, -4.6, 2.1), (1.25, 0.0, 1.05))
SHOT_POSES = {"Wide": WIDE_A, "WideEnd": WIDE_B,
              "Cuby": CUBY_SHOT, "Sphero": SPHERO_SHOT}
SHOT_LENS = {"Wide": 50, "WideEnd": 50, "Cuby": 55, "Sphero": 45}


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
    row are skipped. NOTE: game_subtitles.py embeds a verbatim copy.
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

    cues = parse_srt_simple(); cams = [[absolute_time, shot]] from [CAM].
    Legacy wiring directives are ignored with a warning each.
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
                elif arg not in SHOTS:
                    errors.append("[CAM %s]: unknown shot, want %s"
                                  % (arg, "/".join(SHOTS)))
                else:
                    cams.append([t0, arg])
    if not cues:
        errors.append("no usable cues (need number/timing/text blocks)")
    cams.sort(key=lambda c: c[0])
    return {"errors": errors, "warnings": warnings,
            "cues": cues, "cams": cams}


# --------------------------------------------------------------------------
# minimal YAML subset (see the scheme in the module docstring)
# NOTE: game_subtitles.py embeds a verbatim copy of everything down to
# parse_minimal_yaml; test_story.py checks both copies agree.
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# story checking
# --------------------------------------------------------------------------

def _is_num(x):
    return type(x) in (int, float)


def check_story(story, cues):
    """Crash-path validation of a parsed story -> [error strings].

    Every path the game driver walks (start set, ends, options, cue
    ranges) is covered: a story with no errors here cannot KeyError the
    driver. NOTE: game_subtitles.py embeds a verbatim copy.
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


def range_checks(story, cues, f0, f_end):
    """Timeline-fit validation (tool side; the game never needs it)."""
    errs = []
    for name, s in story.get("sets", {}).items():
        fr = s.get("frames")
        if (isinstance(fr, list) and len(fr) == 2
                and all(type(v) is int for v in fr)):
            if fr[0] < f0 or fr[1] > f_end:
                errs.append("set '%s': frames %s exceed the timeline %d-%d"
                            % (name, fr, f0, f_end))
        bits = str(s.get("end")).strip().split(None, 1)
        if len(bits) == 2 and bits[0] == "choice":
            ch = story.get("choices", {}).get(bits[1])
            if isinstance(ch, dict) and type(ch.get("prompt_cue")) is int:
                cr = s.get("cues")
                if (isinstance(cr, list) and len(cr) == 2
                        and all(type(v) is int for v in cr)):
                    if not cr[0] <= ch["prompt_cue"] <= cr[1]:
                        errs.append("choice '%s' prompt cue %d is outside "
                                    "set '%s' cues %s"
                                    % (bits[1], ch["prompt_cue"], name, cr))
    return errs


def validate_story(story, cues, f0, f_end):
    """check_story + range_checks (build/resave/verify entry point)."""
    errs = check_story(story, cues)
    if errs:
        return errs
    return range_checks(story, cues, f0, f_end)


def load_story_files(story_path, f0=None, f_end=None):
    """Read story.yml + its subtitle file -> one dict.

    {"errors", "warnings", "story", "cues", "cams", "srt_path"}.
    Pass the scene range (f0, f_end) for timeline-fit validation too.
    """
    blank = {"errors": [], "warnings": [], "story": {}, "cues": {},
             "cams": [], "srt_path": ""}
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
    subs_name = story.get("subs", "dialogue.srt") \
        if isinstance(story, dict) else "dialogue.srt"
    if not isinstance(subs_name, str) or not subs_name.strip():
        subs_name = "dialogue.srt"
    if os.path.isabs(subs_name):
        srt_path = subs_name
    else:
        srt_path = os.path.join(os.path.dirname(os.path.abspath(story_path)),
                                subs_name)
    blank["srt_path"] = srt_path
    try:
        with open(srt_path, encoding="utf-8-sig") as fh:
            script = parse_script(fh.read())
    except OSError as ex:
        blank["errors"] = ["cannot read %s (%s)" % (srt_path, ex)]
        return blank
    blank["warnings"] = list(script["warnings"])
    blank["cues"] = script["cues"]
    blank["cams"] = script["cams"]
    blank["errors"] = list(script["errors"])
    blank["errors"].extend(check_story(story, script["cues"]))
    if f0 is not None and f_end is not None and not blank["errors"]:
        blank["errors"].extend(range_checks(story, script["cues"], f0, f_end))
    return blank


# --------------------------------------------------------------------------
# timeline helpers (starter bake + markers + menu preview)
# --------------------------------------------------------------------------

def ordered_cues(cues):
    """[(number, start, end, text, speakers)] sorted by number."""
    return [(n, cues[n]["start"], cues[n]["end"], cues[n]["text"],
             speakers_of(cues[n]["text"])) for n in sorted(cues)]


def cam_spans(cams, fps, f0, f_end):
    """[(frame0, frame1, shot)] with inclusive frames, covering f0..f_end.

    Consecutive same-shot ranges are merged. With no [CAM] at all the
    whole timeline is one Wide span (so the push-in still applies).
    """
    pts = []
    for (ct, shot) in cams:
        f = f0 + round(ct * fps)
        pts.append((min(max(f, f0), f_end), shot))
    if not pts:
        return [(f0, f_end, "Wide")]
    pts.sort(key=lambda p: p[0])  # stable: file order wins ties
    merged = []
    for (f, s) in pts:
        if merged and merged[-1][0] == f:
            merged[-1] = (f, s)  # same frame: last one wins
        else:
            merged.append((f, s))
    spans = []
    cur_f, cur_s = f0, merged[0][1]
    for (f, s) in merged:
        if f > cur_f:
            spans.append((cur_f, f - 1, cur_s))
            cur_f, cur_s = f, s
        else:
            cur_s = s
    spans.append((cur_f, f_end, cur_s))
    out = []
    for (a, z, s) in spans:
        if out and out[-1][2] == s and out[-1][1] + 1 == a:
            out[-1] = (out[-1][0], z, s)
        else:
            out.append((a, z, s))
    return out


def menu_body(options):
    """Static timeline text for the choice menu (cursor on option 1)."""
    return "\n".join("%s %s: %s" % (">" if i == 0 else " ", o[0], o[1])
                     for i, o in enumerate(options))


def marker_frames(story, cues, fps, f0):
    """{"SET-<set>": frame, "CH-<choice>": prompt frame} for markers."""
    marks = {}
    for name, s in story.get("sets", {}).items():
        marks["SET-" + str(name)] = s["frames"][0]
    for cid, ch in story.get("choices", {}).items():
        t = cues[ch["prompt_cue"]]["start"]
        marks["CH-" + str(cid)] = f0 + round(t * fps)
    return marks
