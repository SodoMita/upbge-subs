#!/usr/bin/env python3
"""Tests for story.py: YAML subset, SRT+[CAM] parse, validation, spans.

No Blender needed. Also checks the game driver's embedded parser copies
agree with story.py, and the add-on's strip copy agrees (AST extract).
"""
import ast
import copy
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story

# ---- game driver import (fake bge; parsers only, no engine calls) ----
bge = types.ModuleType("bge")
bge.logic = types.ModuleType("bge.logic")
bge.events = types.ModuleType("bge.events")
sys.modules["bge"] = bge
import game_subtitles as gs

Y = story.parse_minimal_yaml


def yerr(text, needle):
    try:
        story.parse_minimal_yaml(text)
    except ValueError as ex:
        assert needle in str(ex), (needle, str(ex))
        return
    raise AssertionError("no error for %r" % text)


# 1) YAML subset: values
assert Y("a: 1\nb: x") == {"a": 1, "b": "x"}
assert Y("a:\n  b: 2\n  c:\n    - 1\n    - x") == \
    {"a": {"b": 2, "c": [1, "x"]}}
assert Y("- a\n- b") == ["a", "b"]
assert Y("a: 2.5\nb: -3\nc: 0") == {"a": 2.5, "b": -3, "c": 0}
assert Y("a: [1, x, 2.5]") == {"a": [1, "x", 2.5]}
assert Y("a: [[1, 2], [x]]") == {"a": [[1, 2], ["x"]]}
assert Y("a: []") == {"a": []}
assert Y("a: [Ask Cuby, x]") == {"a": ["Ask Cuby", "x"]}
assert Y('a: "x\\nY"\nb: \'it\'\'s\'') == {"a": "x\nY", "b": "it's"}
assert Y("a: 1 # trailing\n# full line\nb: 'a#b'") == {"a": 1, "b": "a#b"}
assert Y("a:\n") == {"a": None}
assert Y("") == {}
assert Y("a:\n  - [1, \"Ask\", cuby]\n  - [2, Tell, sphero]") == \
    {"a": [[1, "Ask", "cuby"], [2, "Tell", "sphero"]]}
assert Y("end: choice pick") == {"end": "choice pick"}
print("yaml values ok")

# 2) YAML subset: errors
yerr("\ta: 1", "spaces, never tabs")
yerr("  a: 1", "column 0")
yerr("key value", "key: value")
yerr("a: 1\na: 2", "duplicate key")
yerr("a:\n    b: 1\n  c: 2", "bad indentation")
yerr("a: 1\n- x", "list item inside a mapping")
yerr("a: [1, 2", "flow list")
yerr("a: {k: v}", "block style")
yerr('a: ["x]', "unterminated string")
yerr("a: [1] x", "trailing")
yerr("a: [1,,2]", "unexpected")
yerr(": v", "empty key")
print("yaml errors ok")

# 3) SRT simple parse
SRT = ("1\n00:00:01,000 --> 00:00:02,000\n[CAM Cuby]\nCUBY: Hi.\n\n"
       "2\n00:00:03,000 --> 00:00:04,000\nPlain [laughs] ok.\n")
cues = story.parse_srt_simple(SRT)
assert cues == {1: {"start": 1.0, "end": 2.0, "text": "CUBY: Hi."},
                2: {"start": 3.0, "end": 4.0, "text": "Plain [laughs] ok."}}, cues
assert story.parse_srt_simple("00:00:01,000 --> 00:00:02,000\nNo number.\n") == \
    {1: {"start": 1.0, "end": 2.0, "text": "No number."}}
assert story.parse_srt_simple("garbage\n\nno timing here\n") == {}
assert story.parse_srt_simple(
    "1\n00:00:01,000 --> 00:00:01,000\nX.\n")[1]["end"] == 2.0
print("srt parse ok")

# 4) script parse (cams + legacy + errors)
scr = story.parse_script(
    "1\n00:00:01,000 --> 00:00:02,000\n[CAM Cuby]\n[BRANCH x]\nHi.\n")
assert scr["errors"] == [] and len(scr["warnings"]) == 1, scr
assert "legacy" in scr["warnings"][0] and scr["cams"] == [[1.0, "Cuby"]], scr
bad = story.parse_script("1\n00:00:01,000 --> 00:00:02,000\n[CAM Nope]\nX.\n")
assert len(bad["errors"]) == 1 and "unknown shot" in bad["errors"][0], bad
bare = story.parse_script("1\n00:00:01,000 --> 00:00:02,000\n[CAM]\nX.\n")
assert len(bare["errors"]) == 1 and "needs a shot name" in bare["errors"][0]
assert story.parse_script("nothing here")["errors"] != []
print("script parse ok")

# 5) check_story: one good story, many bad ones
CUES = {1: {"start": 0.0, "end": 1.0, "text": "a"},
        2: {"start": 1.0, "end": 2.0, "text": "b"},
        3: {"start": 5.0, "end": 6.0, "text": "c"}}
STORY_OK = {"start": "a", "cps": 10, "subs": "d.srt", "actors": ["Camera"],
            "sets": {
                "a": {"cues": [1, 2], "frames": [1, 48], "lens": 50,
                      "end": "goto b"},
                "b": {"cues": [3, 3], "frames": [49, 60], "lens": 45,
                      "end": "stop"}},
            "choices": {}}
assert story.check_story(STORY_OK, CUES) == []
CH_OK = copy.deepcopy(STORY_OK)
CH_OK["sets"]["b"]["end"] = "choice pick"
CH_OK["choices"] = {"pick": {"prompt_cue": 3,
                             "options": [[1, "Go A", "a"],
                                         ["2", "Go B", "b"]]}}
assert story.check_story(CH_OK, CUES) == []


def chk(mut, needle):
    s = copy.deepcopy(CH_OK)
    mut(s)
    errs = story.check_story(s, CUES)
    assert any(needle in e for e in errs), (needle, errs)
    # the driver's embedded copy must agree exactly
    assert gs.check_story(s, CUES) == errs, (needle, errs)


chk(lambda s: s.update(start="nope"), "'start' must name")
chk(lambda s: s["sets"]["a"].update(cues=[1]), "'cues' must be")
chk(lambda s: s["sets"]["a"].update(cues=[2, 1]), "must rise")
chk(lambda s: s["sets"]["a"].update(cues=[1, 9]), "not in dialogue.srt")
chk(lambda s: s["sets"]["a"].update(frames=[0, 5]), "frames must start")
chk(lambda s: s["sets"]["a"].update(lens=0), "'lens' must be")
chk(lambda s: s["sets"]["a"].update(end="fly away"), "'end' must be")
chk(lambda s: s["sets"]["a"].update(end="goto nope"), "unknown set")
chk(lambda s: s["sets"]["b"].update(end="choice nope"), "unknown choice")
chk(lambda s: s["choices"]["pick"].update(prompt_cue=9), "'prompt_cue'")
chk(lambda s: s["choices"]["pick"].update(options=[]), "non-empty 'options'")
chk(lambda s: s["choices"]["pick"]["options"].append([1]), "must be [key, label, set]")
chk(lambda s: s["choices"]["pick"]["options"][0].__setitem__(0, "x"),
    "key must be 1-9")
chk(lambda s: s["choices"]["pick"]["options"][0].__setitem__(1, " "),
    "label must be")
chk(lambda s: s.update(subs=""), "'subs' must be")
chk(lambda s: s.update(actors=[]), "'actors' must be")
chk(lambda s: s.update(cps=0), "'cps' must be")
assert story.check_story([], CUES) == ["story must be a mapping of key: value lines"]
print("check_story ok (driver copy agrees)")

# 6) range checks
assert story.range_checks(CH_OK, CUES, 1, 708) == []
r = copy.deepcopy(CH_OK)
r["sets"]["a"]["frames"] = [1, 999]
assert any("exceed the timeline" in e
           for e in story.range_checks(r, CUES, 1, 708))
r = copy.deepcopy(CH_OK)
r["sets"]["b"]["cues"] = [3, 3]
r["choices"]["pick"]["prompt_cue"] = 1
assert any("outside set" in e for e in story.range_checks(r, CUES, 1, 708))
print("range checks ok")

# 7) cam spans (starter timings pin the 9 baked spans)
loaded = story.load_story_files(os.path.join(HERE, "story.yml"))
assert loaded["errors"] == [] and loaded["warnings"] == [], loaded
assert len(loaded["cues"]) == 15 and len(loaded["cams"]) == 10, \
    (len(loaded["cues"]), len(loaded["cams"]))
spans = story.cam_spans(loaded["cams"], 24, 1, 708)
assert spans == [(1, 90, "Wide"), (91, 135, "Cuby"), (136, 180, "Sphero"),
                 (181, 225, "Cuby"), (226, 270, "Sphero"), (271, 315, "Cuby"),
                 (316, 408, "Wide"), (409, 552, "Cuby"), (553, 708, "Sphero")], spans
assert story.cam_spans([], 24, 1, 100) == [(1, 100, "Wide")]
assert story.cam_spans([[0, "Wide"], [0, "Cuby"]], 24, 1, 50) == [(1, 50, "Cuby")]
print("cam spans ok")

# 8) menu body (+ driver format parity) and markers
opts = [[1, "Ask Cuby about cubes", "cuby"],
        [2, "Ask Sphero about spheres", "sphero"]]
assert story.menu_body(opts) == "> 1: Ask Cuby about cubes\n  2: Ask Sphero about spheres"
assert story.menu_body(opts) == gs._menu_body([tuple(o) for o in opts], 0)
assert story.marker_frames(loaded["story"], loaded["cues"], 24, 1) == \
    {"SET-main": 1, "SET-cuby": 409, "SET-sphero": 553, "CH-pick": 361}
print("menu + markers ok")

# 9) ordered cues
ordc = story.ordered_cues(loaded["cues"])
assert len(ordc) == 15 and ordc[0][0] == 1 and ordc[-1][0] == 15
assert ordc[0][3].startswith("CUBY: Hey!") and ordc[0][4] == ["CUBY"]
assert ordc[8][0] == 9 and ordc[8][4] == []  # prompt is narration
print("ordered cues ok")

# 10) full starter validation incl. timeline fit
assert story.validate_story(loaded["story"], loaded["cues"], 1, 708) == []
assert story.load_story_files(os.path.join(HERE, "story.yml"),
                              1, 708)["errors"] == []
missing = story.load_story_files(os.path.join(HERE, "nope.yml"))
assert missing["errors"] and "cannot read" in missing["errors"][0]
print("starter story ok")

# 11) driver parser parity on real + tricky input
tyml = open(os.path.join(HERE, "story.yml")).read()
tsrt = open(os.path.join(HERE, "dialogue.srt")).read()
assert gs.parse_minimal_yaml(tyml) == story.parse_minimal_yaml(tyml)
assert gs.parse_srt_simple(tsrt) == story.parse_srt_simple(tsrt)
tricky = ('# c\ntop:\n  flow: [1, "a,b", it\'s, -2.5, [x, []]]\n'
          '  q: "A\\"B\\\\C\\nD"\n  s: don\'t # tail\n  e: []\nlist:\n  - 1\n  - [2, three]\n')
assert gs.parse_minimal_yaml(tricky) == story.parse_minimal_yaml(tricky)
tsrt2 = ("7\n00:00:01,000 --> 00:00:02,000\n[CAM Wide]\n[END]\nX [Y] Z.\n\n"
         "00:00:03,500 --> 00:00:04,000\nSecond.\n")
assert gs.parse_srt_simple(tsrt2) == story.parse_srt_simple(tsrt2)
print("driver parity ok")

# 12) add-on strip parity (AST extract, no bpy import)
addon_src = open(os.path.join(HERE, "typewriter_subtitles.py")).read()
tree = ast.parse(addon_src)
fn_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_is_directive_line":
        fn_node = node
        break
assert fn_node is not None, "add-on lost _is_directive_line"
fn_src = ast.get_source_segment(addon_src, fn_node)
const_names = sorted(set(re.findall(r"\bin\s+([A-Za-z_][A-Za-z0-9_]*)",
                                    fn_src)))
ns = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in const_names:
                exec(ast.get_source_segment(addon_src, node), ns)
exec(fn_src, ns)
addon_is_directive = ns["_is_directive_line"]
fixture = ["[CAM Wide]", "[BRANCH x]", "[CHOICE p]", "[OPT 1 A -> b]",
           "[GOTO x]", "[END]", "[laughs]", "plain", "[cam x]", "[]", "[ ]",
           "  [CAM Sphero]  "]
for ln in fixture:
    assert addon_is_directive(ln) == story.is_directive(ln), ln
multi = "[CAM Cuby]\nCUBY: X [Y].\n[END]\nplain"
addon_stripped = "\n".join(ln for ln in multi.split("\n")
                           if not addon_is_directive(ln)).strip()
assert addon_stripped == story.strip_directives(multi)
print("addon strip parity ok")

print("ALL STORY TESTS PASSED")
