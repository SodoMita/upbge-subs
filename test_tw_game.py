#!/usr/bin/env python3
"""Mock test for the generic tw_game driver (no Blender needed).

Extracts TW_GAME_DRIVER_SOURCE from the add-on file (validating it), then
drives it with a fake bge module: typing, cue advance, R restart, CONSTANT
mode, bad-data and missing-font paths, script-mode entry, log output.
"""
import ast
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
FAKEBLEND = "/tmp/fakeblend_twgame"

src = open(os.path.join(HERE, "typewriter_subtitles.py")).read()
driver_src = None
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "TW_GAME_DRIVER_SOURCE":
                driver_src = ast.literal_eval(node.value)
assert isinstance(driver_src, str) and len(driver_src) > 1000, \
    "driver not embedded"
assert '"""' not in driver_src, "embedder constraint violated"
code = compile(driver_src, "tw_game.py", "exec")
print("driver extracted (%d bytes) and compiles" % len(driver_src))

# ---- fake bge ----
bge = types.ModuleType("bge")
logic = types.ModuleType("bge.logic")
events = types.ModuleType("bge.events")
bge.logic = logic
bge.events = events


class FakeInput:
    def __init__(self, activated=False):
        self.activated = activated


class FakeKeyboard:
    def __init__(self):
        self.events = {}
        self.inputs = {}


class LegacyKeyboard:  # classic BGE: .events only, no .inputs
    def __init__(self):
        self.events = {}


KEYBOARD = FakeKeyboard()
logic.keyboard = KEYBOARD
logic.KX_INPUT_JUST_ACTIVATED = 1
logic.getLogicTicRate = lambda: 60.0
logic.expandPath = lambda p: p.replace("//", FAKEBLEND + "/")
logic.getCurrentController = lambda: SCRIPT_CONT[0]
events.RKEY = "R"
events.ESCKEY = "ESC"
SCRIPT_CONT = [None]
sys.modules["bge"] = bge


class FakeObj(dict):
    def __init__(self, name, props=None):
        super().__init__(props or {})
        self.name = name
        self.text = ""
        self.childrenRecursive = []


class FakeScene:
    def __init__(self, objs):
        self.objects = {o.name: o for o in objs}


class FakeCont:
    def __init__(self, own, scene):
        self.owner = own
        own.scene = scene


def load_driver(entry_name):
    ns = {"__name__": entry_name}
    exec(code, ns)
    return ns


shutil.rmtree(FAKEBLEND, ignore_errors=True)
os.makedirs(FAKEBLEND, exist_ok=True)

CUES = [[0.0, "Hi there"], [2.0, "Second\nline"], [4.0, "Last"]]
DATA = json.dumps({"version": 1, "cps": 10.0, "reveal": "LINEAR",
                   "cues": CUES})

drv = load_driver("tw_game")
update = drv["update"]

# 1) typing progression, both text APIs
font = FakeObj("Subtitles", {"tw_game_data": DATA})
scene = FakeScene([font])
cont = FakeCont(font, scene)
bodies = []
for _tick in range(1, 31):
    KEYBOARD.events = {}
    update(cont)
    bodies.append(font.text)
assert font["tw_tick"] == 30, font.get("tw_tick")
assert bodies[-1] == "Hi th", repr(bodies[-1])   # 0.5 s * 10 cps
assert font["Text"] == "Hi th", "legacy Text prop not mirrored"
assert bodies[0] == "", repr(bodies[0])          # first tick: nothing yet
# 2) cue advance + hold
for _tick in range(31, 151):
    KEYBOARD.events = {}
    update(cont)
assert font.text == "Secon", repr(font.text)     # 0.5 s into cue 2
for _tick in range(151, 400):
    KEYBOARD.events = {}
    update(cont)
assert font.text == "Last", repr(font.text)      # last cue fully held
print("typing/cue tests passed")
# 3) R restart (via the new keyboard.inputs API)
KEYBOARD.events = {"R": 1}
KEYBOARD.inputs = {"R": FakeInput(True)}
update(cont)
assert font["tw_tick"] == 1, font.get("tw_tick")
assert font.text == "", repr(font.text)
KEYBOARD.events = {}
KEYBOARD.inputs = {}
print("restart test passed")
# 4) CONSTANT mode
font2 = FakeObj("Sub2", {"tw_game_data": json.dumps(
    {"version": 1, "cps": 10.0, "reveal": "CONSTANT",
     "cues": [[0.0, "Instant"], [5.0, "Later"]]})})
scene2 = FakeScene([font2])
KEYBOARD.events = {}
update(FakeCont(font2, scene2))
assert font2.text == "Instant", repr(font2.text)
print("constant-mode test passed")
# 5) bad JSON -> dead message + log, no crash
font3 = FakeObj("Sub3", {"tw_game_data": "{oops"})
update(FakeCont(font3, FakeScene([font3])))
assert font3.text.startswith("Subtitles unavailable"), repr(font3.text)
# 6) missing font entirely -> no crash
lonely = FakeObj("Director")
update(FakeCont(lonely, FakeScene([lonely])))
assert "tw_dead" in lonely
# 7) script-mode entry (__main__)
font4 = FakeObj("Sub4", {"tw_game_data": DATA})
cont4 = FakeCont(font4, FakeScene([font4]))
SCRIPT_CONT[0] = cont4
KEYBOARD.events = {}
load_driver("__main__")
assert font4.get("tw_tick") == 1, font4.get("tw_tick")
print("script-mode test passed")
# 8) legacy keyboard (no .inputs) falls back to .events
legacy = LegacyKeyboard()
fontL = FakeObj("SubL", {"tw_game_data": DATA})
contL = FakeCont(fontL, FakeScene([fontL]))
saved_kb = logic.keyboard
logic.keyboard = legacy
try:
    for _tick in range(30):
        legacy.events = {}
        update(contL)
    assert fontL.text == "Hi th", repr(fontL.text)
    legacy.events = {"R": 1}
    update(contL)
    assert fontL.get("tw_tick") == 1, fontL.get("tw_tick")
finally:
    logic.keyboard = saved_kb
print("legacy keyboard fallback passed")
# 9) log file contents
log = open(os.path.join(FAKEBLEND, "tw_game_debug.log")).read()
assert "init ok: 3 cues" in log, log
assert "INIT FAILED" in log, log  # from the bad-JSON case
print("log test passed")
print("ALL TW_GAME TESTS PASSED")
