#!/usr/bin/env python3
"""Tests for story.py v2: YAML subset + flow maps, refs, plans, sync/schema.

No Blender needed. Also checks the add-on's strip copy agrees (AST extract).
"""
import ast
import copy
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story

Y = story.parse_minimal_yaml


def yerr(text, needle):
    try:
        story.parse_minimal_yaml(text)
    except ValueError as ex:
        assert needle in str(ex), (needle, str(ex))
        return
    raise AssertionError("no error for %r" % text)


# 1) YAML subset: values incl. flow maps
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
assert Y("a: {}") == {"a": {}}
assert Y("a: {x: 1, y: [p, {z: q}]}") == \
    {"a": {"x": 1, "y": ["p", {"z": "q"}]}}
assert Y("a:\n  - {subs: f.srt#1-2, at: 3.0}\n  - action: O@A") == \
    {"a": [{"subs": "f.srt#1-2", "at": 3.0}, "action: O@A"]}
assert Y("a: {q: 'it''s #1'}") == {"a": {"q": "it's #1"}}
assert Y("a: f.srt#1-8  # tail") == {"a": "f.srt#1-8"}
print("yaml values ok")

# 2) YAML subset: errors
yerr("\ta: 1", "spaces, never tabs")
yerr("  a: 1", "column 0")
yerr("key value", "key: value")
yerr("a: 1\na: 2", "duplicate key")
yerr("a:\n    b: 1\n  c: 2", "bad indentation")
yerr("a: 1\n- x", "list item inside a mapping")
yerr("a: [1, 2", "flow list")
yerr('a: ["x]', "unterminated string")
yerr("a: [1] x", "trailing")
yerr("a: [1,,2]", "unexpected")
yerr(": v", "empty key")
yerr("a: {k}", "key: value")
yerr("a: {k: 1, k: 2}", "duplicate key")
yerr("a: {k: 1]", "'}' in flow map")
yerr("a:\n  - k: v\n    x: 1", "flow style")
print("yaml errors ok")

# 3) anim references
assert story.parse_subs_ref("f.srt#1-8") == ("f.srt", 1, 8, "")
assert story.parse_subs_ref("f.srt#9") == ("f.srt", 9, 9, "")
assert story.parse_subs_ref("f.srt#x")[3] != ""
assert story.parse_subs_ref("f.srt#8-1")[3] != ""
assert story.parse_subs_ref("f.srt")[3] != ""
assert story.parse_cue_ref("f.srt#9") == ("f.srt", 9, "")
assert story.parse_cue_ref("f.srt#1-2")[2] != ""
assert story.parse_action_ref("O@A") == ("O", "A", "")
assert story.parse_action_ref("O@")[2] != ""
assert story.parse_action_ref("OA")[2] != ""
print("refs ok")

# 4) anim normalization
a, e = story.normalize_anim("subs: f.srt#1-2")
assert e == "" and a == {"type": "subs", "value": "f.srt#1-2",
                         "at": 0.0, "wait": True}, (a, e)
a, e = story.normalize_anim({"action": "O@A", "at": 2, "wait": False})
assert e == "" and a == {"type": "action", "value": "O@A",
                         "at": 2.0, "wait": False}, (a, e)
a, e = story.normalize_anim("camera: Wide")
assert e == "" and a["wait"] is False
a, e = story.normalize_anim("audio: x.ogg")
assert e == "" and a["wait"] is False
for bad in ("dance: now", "subs:", {"subs": "f#1", "zz": 1},
            {"action": "O@A", "at": -1}, {"subs": "f#1", "wait": "yes"},
            {"subs": "f#1", "audio": "x"}, ["subs: f#1"], 42):
    assert story.normalize_anim(bad)[1] != "", bad
print("anims ok")

# 5) SRT + script parse ([CAM] passes through unvalidated)
SRT = ("1\n00:00:01,000 --> 00:00:02,000\n[CAM Cuby]\nCUBY: Hi.\n\n"
       "2\n00:00:03,000 --> 00:00:04,000\nPlain [laughs] ok.\n")
cues = story.parse_srt_simple(SRT)
assert cues == {1: {"start": 1.0, "end": 2.0, "text": "CUBY: Hi."},
                2: {"start": 3.0, "end": 4.0, "text": "Plain [laughs] ok."}}
scr = story.parse_script(
    "1\n00:00:01,000 --> 00:00:02,000\n[CAM Whatever]\n[BRANCH x]\nHi.\n")
assert scr["errors"] == [] and len(scr["warnings"]) == 1, scr
assert scr["cams"] == [[1.0, "Whatever", 1]]
bare = story.parse_script("1\n00:00:01,000 --> 00:00:02,000\n[CAM]\nX.\n")
assert bare["errors"] == ["[CAM] needs a shot name (cue 1)"]
assert story.parse_script("nothing here")["errors"] != []
print("srt parse ok")

# 6) check_story incl. instant loops
FILES = {"a.srt": {"cues": {1: {"start": 0.0, "end": 1.0, "text": "a"},
                            2: {"start": 1.0, "end": 2.0, "text": "b"}}},
         "b.srt": {"cues": {1: {"start": 0.0, "end": 1.0, "text": "c"}}}}
STORY_OK = {"start": "a", "cps": 10,
            "sets": {
                "a": {"anims": ["subs: a.srt#1-2", "camera: Wide",
                                "action: O@A"],
                      "end": "goto b"},
                "b": {"anims": ["subs: b.srt#1"], "end": "choice pick"}},
            "choices": {"pick": {"prompt": "b.srt#1",
                                 "options": [[1, "Go A", "a"],
                                             ["2", "Go B", "b"]]}}}
assert story.check_story(STORY_OK, FILES) == []


def chk(mut, needle):
    s = copy.deepcopy(STORY_OK)
    mut(s)
    errs = story.check_story(s, FILES)
    assert any(needle in e for e in errs), (needle, errs)


chk(lambda s: s.update(start="nope"), "'start' must name")
chk(lambda s: s["sets"]["a"].update(anims=[]), "non-empty 'anims'")
chk(lambda s: s["sets"]["a"]["anims"].append("dance: x"), "unknown type")
chk(lambda s: s["sets"]["a"]["anims"].__setitem__(0, "subs: z.srt#1"),
    "not found")
chk(lambda s: s["sets"]["a"]["anims"].__setitem__(0, "subs: a.srt#1-9"),
    "cue 3 is not in")
chk(lambda s: s["sets"]["a"]["anims"].__setitem__(2, "action: O"),
    "must be 'Object@Action'")
chk(lambda s: s["sets"]["a"]["anims"].append("camera: Two"),
    "only one 'camera'")
chk(lambda s: s["sets"]["a"].update(end="fly away"), "'end' must be")
chk(lambda s: s["sets"]["a"].update(end="goto nope"), "unknown set")
chk(lambda s: s["sets"]["b"].update(end="choice nope"), "unknown choice")
chk(lambda s: s["choices"]["pick"].update(prompt="b.srt#7"), "cue 7")
chk(lambda s: s["choices"]["pick"].update(options=[]), "non-empty 'options'")
chk(lambda s: s["choices"]["pick"]["options"].append([1]), "must be [key, label, set]")
chk(lambda s: s["choices"]["pick"]["options"][0].__setitem__(0, "x"),
    "key must be 1-9")
chk(lambda s: s["choices"]["pick"]["options"][0].__setitem__(1, " "),
    "label must be")
chk(lambda s: s.update(cps=0), "'cps' must be")
assert story.check_story([], FILES) == ["story must be a mapping of key: value lines"]


def loop_case(ends, anims):
    s = {"start": "a", "cps": 10, "sets": {}, "choices": {}}
    for name, end in ends.items():
        s["sets"][name] = {"anims": anims.get(name, ["camera: Wide"]),
                           "end": end}
    return story.check_story(s, FILES)


assert any("loop" in e for e in
           loop_case({"a": "goto a"}, {}))
assert any("loop" in e for e in
           loop_case({"a": "goto b", "b": "goto a"}, {}))
assert loop_case({"a": "goto b", "b": "stop"},
                 {"a": ["subs: a.srt#1-2"]}) == []
assert loop_case({"a": "goto b", "b": "goto a"},
                 {"b": ["action: O@A"]}) == []
print("check_story ok")

# 7) warnings: silence, echo, ignored wait, reachability
W = copy.deepcopy(STORY_OK)
W["sets"]["b"]["anims"] = ["subs: b.srt#1",
                           {"camera": "Wide", "wait": True}]
FILES2 = copy.deepcopy(FILES)
FILES2["b.srt"]["cues"][1] = {"start": 5.0, "end": 6.0, "text": "c"}
warns = story.warn_story(W, FILES2)
assert any("leading silence" in w for w in warns), warns
assert any("never blocks" in w for w in warns), warns
W["sets"]["b"]["anims"] = ["subs: a.srt#1-2"]
W["choices"]["pick"]["prompt"] = "a.srt#2"
assert any("shows twice" in w for w in story.warn_story(W, FILES))
W["sets"]["zzz"] = {"anims": ["subs: a.srt#1"], "end": "stop"}
W["choices"]["never"] = {"prompt": "a.srt#1", "options": [[1, "X", "a"]]}
warns = story.warn_story(W, FILES)
assert any("unreachable" in w for w in warns), warns
assert any("never offered" in w for w in warns), warns
print("warnings ok")

# 8) bindings
STORY_B = copy.deepcopy(STORY_OK)
assert story.check_bindings(STORY_B, ["O", "Wide"], ["A"], ["Wide"]) == []
errs = story.check_bindings(STORY_B, ["Wide"], ["A"], ["Wide"])
assert errs == ["set 'a' anim 3: object 'O' is not in the scene"], errs
errs = story.check_bindings(STORY_B, ["O", "Wide"], [], ["Wide"])
assert errs == ["set 'a' anim 3: action 'A' does not exist"], errs
errs = story.check_bindings(STORY_B, ["O", "Wide"], ["A"], [])
assert errs == ["set 'a' anim 2: shot 'Wide' is not a camera in the scene"]
print("bindings ok")

# 9) multi-file loading incl. ./subtitles + ./audio
FIX = tempfile.mkdtemp(prefix="twstory2")
os.makedirs(os.path.join(FIX, "subtitles"))
os.makedirs(os.path.join(FIX, "audio"))
open(os.path.join(FIX, "a.srt"), "w").write(
    "1\n00:00:00,000 --> 00:00:01,000\nHi.\n\n"
    "2\n00:00:01,000 --> 00:00:02,000\n[CAM C]\nYo.\n")
open(os.path.join(FIX, "subtitles", "b.srt"), "w").write(
    "1\n00:00:00,000 --> 00:00:01,000\nBee.\n")
open(os.path.join(FIX, "audio", "x.ogg"), "wb").write(b"RIFF....")
YML = ("start: a\ncps: 10\nsets:\n"
       "  a:\n    anims:\n      - subs: a.srt#1\n      - camera: Wide\n"
       "      - action: O@A\n      - audio: x.ogg\n    end: choice pick\n"
       "  b:\n    anims:\n      - subs: b.srt#1\n    end: stop\n"
       "choices:\n  pick:\n    prompt: a.srt#2\n    options:\n"
       "      - [1, Go, b]\n")
yp = os.path.join(FIX, "story.yml")
open(yp, "w").write(YML)
loaded = story.load_story_files(yp)
assert loaded["errors"] == [] and loaded["warnings"] == [], loaded
assert sorted(loaded["files"]) == ["a.srt", "b.srt"]
assert loaded["files"]["b.srt"]["path"].endswith(
    os.path.join("subtitles", "b.srt"))
assert loaded["audio"] == {"x.ogg": os.path.join(FIX, "audio", "x.ogg")}
assert loaded["files"]["a.srt"]["cams"] == [[1.0, "C", 2]]
open(yp, "w").write(YML.replace("b.srt#1", "nope.srt#1"))
assert any("not found" in e
           for e in story.load_story_files(yp)["errors"])
open(yp, "w").write(YML.replace("x.ogg", "nope.ogg"))
assert any("audio file" in e
           for e in story.load_story_files(yp)["errors"])
open(yp, "w").write("sets: [oops\n")
assert any("story.yml" in e
           for e in story.load_story_files(yp)["errors"])
assert "cannot read" in story.load_story_files(
    os.path.join(FIX, "nope.yml"))["errors"][0]
shutil.rmtree(FIX, ignore_errors=True)
print("loading ok")

# 10) plans on the starter story
loaded = story.load_story_files(os.path.join(HERE, "story.yml"))
assert loaded["errors"] == [] and loaded["warnings"] == [], loaded
assert sorted(loaded["files"]) == ["cuby.srt", "dialogue.srt", "sphero.srt"]
assert len(loaded["files"]["dialogue.srt"]["cues"]) == 9
story_v2, files_v2 = loaded["story"], loaded["files"]
plan = story.set_plan(story_v2, files_v2, "main")
assert len(plan["subs"]) == 8 and plan["dur"] == 15.0, plan["dur"]
assert plan["end"] == ("choice", "pick")
assert plan["prompt"] == "Who gets the last word?", repr(plan["prompt"])
assert plan["shots"] == [(0.0, "Wide"), (0.0, "Wide"), (3.75, "Cuby"),
                         (5.625, "Sphero"), (7.5, "Cuby"), (9.375, "Sphero"),
                         (11.25, "Cuby"), (13.125, "Wide")], plan["shots"]
assert len(plan["actions"]) == 6 and plan["audios"] == []
plan_c = story.set_plan(story_v2, files_v2, "cuby")
assert plan_c["dur"] == 3.75 and plan_c["end"] == ("choice", "pick2")
assert plan_c["prompt"] == "CUBY: Stay square,\nfriend!"
assert plan_c["shots"] == [(0.0, "Cuby"), (0.0, "Cuby")]
ranged = story.set_plan(story_v2, files_v2, "main",
                        {"main__cuby": [1, 361]})
assert story.set_duration(ranged) == 15.0  # (361-1)/24 = 15.0
ranged2 = story.set_plan(story_v2, files_v2, "sphero",
                         {"sphero__sphero": [1, 241]})
assert story.set_duration(ranged2) == 10.0, story.set_duration(ranged2)
print("plans ok")

# 11) sidecar + schema paths/builders
assert story.sync_path_for("/x/story.yml") == "/x/story.sync.json"
assert story.schema_path_for("/x/story.yml") == "/x/story.schema.json"
assert story.load_sidecar("/nonexistent.json") == {"uids": {}, "actions": {}}
tmp = tempfile.mkdtemp(prefix="twsync")
sp = os.path.join(tmp, "s.sync.json")
open(sp, "w").write('{"uids": {"u": {"name": "O", "type": "MESH"}}, '
                     '"actions": {"A": [1, 48], "bad": [1]}}')
assert story.load_sidecar(sp) == {
    "uids": {"u": {"name": "O", "type": "MESH"}}, "actions": {"A": [1, 48]}}
open(sp, "w").write("not json")
assert story.load_sidecar(sp) == {"uids": {}, "actions": {}}
shutil.rmtree(tmp, ignore_errors=True)
sch = story.build_schema(story_v2, ["CubyRoot", "Wide"], ["main__cuby"],
                         ["Wide", "Cuby"], ["dialogue.srt"], ["x.ogg"])
assert sch["properties"]["start"]["enum"] == ["cuby", "main", "sphero"]
cam = sch["properties"]["sets"]["patternProperties"]["^.+$"][
    "properties"]["anims"]["items"]["anyOf"][1]["properties"]["camera"]
assert cam["enum"] == ["Cuby", "Wide"]
opts = sch["properties"]["choices"]["patternProperties"]["^.+$"][
    "properties"]["options"]["items"]
assert opts["prefixItems"][2]["enum"] == ["cuby", "main", "sphero"]
json.dumps(sch)
print("sync/schema ok")

# 12) menu body
assert story.menu_body([[1, "A", "x"], [2, "B", "y"]]) == "> 1: A\n  2: B"
print("menu ok")

# 13) add-on strip parity (AST extract, no bpy import)
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

# 14) a half-written story.yml must REPORT errors, never crash. A childless
# key parses to None and `story.get("sets", {})` returns None (the key
# exists), so every walker used to raise AttributeError before check_story
# could say what was actually wrong.
assert story.parse_minimal_yaml("start: nope\nsets:\n") == {
    "start": "nope", "sets": None}
assert story.normalize_story({"sets": None, "choices": None}) == {
    "sets": {}, "choices": {}}
assert story.normalize_story(
    {"sets": {"a": {"anims": None}}})["sets"]["a"]["anims"] == []
assert story.normalize_story({"sets": "wrong type"})["sets"] == "wrong type", \
    "a wrong type must survive so check_story can report it"
FIX = tempfile.mkdtemp(prefix="twhalf")
open(os.path.join(FIX, "half.yml"), "w").write("start: nope\nsets:\n")
res = story.load_story_files(os.path.join(FIX, "half.yml"))
assert res["errors"] and "'sets' must be a non-empty mapping" in res["errors"][0], \
    res["errors"]
open(os.path.join(FIX, "half2.yml"), "w").write(
    "start: a\nsets:\n  a:\n    anims:\n    end: choice c\n"
    "choices:\n  c:\n    prompt: x.srt#1\n    options:\n")
res2 = story.load_story_files(os.path.join(FIX, "half2.yml"))
assert res2["errors"], res2
assert any("anims" in e for e in res2["errors"]), res2["errors"]
shutil.rmtree(FIX, ignore_errors=True)
print("half-written story ok")

print("ALL STORY TESTS PASSED")
