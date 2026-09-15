#!/usr/bin/env python3
"""Mock test for the animation-set game driver v2 (no Blender needed).

Drives game_subtitles.update with fake bge + aud modules: per-set plans
from YAML+multi-SRT fixtures (story.py is loaded from the fixture dir,
like in the engine), action blocking incl. delays, procedural camera,
audio fire-and-forget, goto/choice/stop ends, chained choices, loops,
restart, dead-data paths, failure latches, capture mode, legacy keyboard
fallback, and an end-to-end pass over the real story files.
"""
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- fake bge ----
bge = types.ModuleType("bge")
logic = types.ModuleType("bge.logic")
events = types.ModuleType("bge.events")
render = types.ModuleType("bge.render")
bge.logic = logic
bge.events = events
bge.render = render


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
logic.KX_ACTION_MODE_PLAY = 7
logic.getLogicTicRate = lambda: 60.0
CURRENT = ["/tmp/nowhere"]
logic.expandPath = lambda p: p.replace("//", CURRENT[0] + "/")
ENDED = []
logic.endGame = lambda: ENDED.append(True)
SHOTS = []
render.makeScreenshot = lambda p: SHOTS.append(p)
for _n in ("ONEKEY", "TWOKEY", "THREEKEY", "FOURKEY", "FIVEKEY", "SIXKEY",
           "SEVENKEY", "EIGHTKEY", "NINEKEY", "UPARROWKEY", "DOWNARROWKEY",
           "ENTERKEY", "RETKEY", "SPACEKEY", "RKEY", "ESCKEY"):
    setattr(events, _n, _n)
sys.modules["bge"] = bge

# ---- fake aud ----
aud = types.ModuleType("aud")
PLAYED = []


class FakeSound:
    def __init__(self, path):
        self.path = path


class FakeDevice:
    def play(self, sound):
        PLAYED.append(sound.path)
        return len(PLAYED)


aud.Sound = FakeSound
aud.device = lambda: FakeDevice()
sys.modules["aud"] = aud

sys.path.insert(0, HERE)
import game_subtitles as gs

IDENT = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
ROT_Z90 = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]


class FakeObj(dict):
    def __init__(self, name="obj", playing=True):
        super().__init__()
        self.name = name
        self.text = ""
        self.lens = 35.0
        self.worldPosition = [5.0, 5.0, 5.0]
        self.worldOrientation = [r[:] for r in IDENT]
        self.worldScale = [1.0, 1.0, 1.0]
        self.playing = playing
        self.calls = []
        self.visible = True
        self.raise_play = False

    def isPlayingAction(self, layer=0):
        return self.playing

    def playAction(self, action, f0, f1, layer=0, priority=0, blendin=0,
                   mode=0):
        if self.raise_play:
            raise RuntimeError("boom")
        self.calls.append((action, f0, f1, mode))


class FakeScene:
    def __init__(self, objs):
        self.objects = {o.name: o for o in objs}
        self.active_camera = None


class FakeCont:
    def __init__(self, own, scene):
        self.owner = own
        own.scene = scene


FIX = tempfile.mkdtemp(prefix="twgame2")


def write_case(name, files):
    d = os.path.join(FIX, name)
    os.makedirs(d, exist_ok=True)
    shutil.copy(os.path.join(HERE, "story.py"), os.path.join(d, "story.py"))
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(p, mode) as fh:
            fh.write(content)
    return d


def run(cont, n, keys=None):
    keys = keys or {}
    for tick in range(1, n + 1):
        KEYBOARD.events = {}
        KEYBOARD.inputs = {}
        for k in keys.get(tick, []):
            KEYBOARD.events[k] = 1
            KEYBOARD.inputs[k] = FakeInput(True)
        gs.update(cont)


YML1 = """start: a
cps: 10
sets:
  a:
    anims:
      - subs: a.srt#1-2
      - camera: Wide
      - action: R@ActA
      - {action: R2@ActLate, at: 1.0}
      - audio: x.ogg
    end: goto b
  b:
    anims:
      - subs: b.srt#1
      - camera: Cuby
    end: choice pick
  c:
    anims:
      - subs: c.srt#1
    end: stop
choices:
  pick:
    prompt: b.srt#2
    options:
      - [1, "See C", c]
      - [2, "Back to A", a]
"""
SRTA = """1
00:00:00,000 --> 00:00:01,000
CUBY: Hi.

2
00:00:01,000 --> 00:00:02,000
[CAM Cuby]
SPHERO: Yo.
"""
SRTB = """1
00:00:00,000 --> 00:00:01,000
CUBY: Bee.

2
00:00:01,000 --> 00:00:02,000
Pick now?
"""
SRTC = """1
00:00:00,000 --> 00:00:01,000
CUBY: Cee.
"""
SYNC1 = {"uids": {}, "actions": {"ActA": [1, 61], "ActLate": [1, 31]}}
CASE1 = write_case("case1", {"story.yml": YML1, "a.srt": SRTA, "b.srt": SRTB,
                             "c.srt": SRTC, "audio/x.ogg": b"RIFF....",
                             "story.sync.json": json.dumps(SYNC1)})


def make_cont(case, capture=False):
    CURRENT[0] = case
    own = FakeObj("GameDirector", playing=False)
    own["tw_story"] = "//story.yml"
    if capture:
        own["CAPTURE"] = 1
    cam = FakeObj("Camera", playing=False)
    wide = FakeObj("Wide", playing=False)
    wide.worldPosition = [0.0, 0.0, 10.0]
    wide.lens = 50.0
    cuby = FakeObj("Cuby", playing=False)
    cuby.worldPosition = [10.0, 0.0, 0.0]
    cuby.worldOrientation = [r[:] for r in ROT_Z90]
    cuby.lens = 55.0
    sub = FakeObj("Subtitles", playing=False)
    menu = FakeObj("ChoiceMenu", playing=False)
    line = FakeObj("main_Line01", playing=False)
    robjs = {"R": FakeObj("R"), "R2": FakeObj("R2")}
    scene = FakeScene([own, cam, wide, cuby, sub, menu, line,
                       robjs["R"], robjs["R2"]])
    return FakeCont(own, scene), own, scene, cam, sub, menu, robjs


def log_text(case):
    with open(os.path.join(case, "game_debug.log")) as fh:
        return fh.read()


def col_norms(m):
    import math
    return [math.sqrt(sum(m[r][c] ** 2 for r in range(3))) for c in range(3)]


# 1) init: start set, actions by sidecar range, camera easing, audio, hiding
del PLAYED[:]
cont, own, scene, cam, sub, menu, robjs = make_cont(CASE1)
run(cont, 1)
assert own["tw_set"] == "a" and own["tw_state"] == "play"
assert robjs["R"].calls == [("ActA", 1, 61, 7)], robjs["R"].calls
assert robjs["R2"].calls == []  # at: 1.0, not due
assert PLAYED == [os.path.join(CASE1, "audio", "x.ogg")], PLAYED
assert menu.text == ""
assert scene.objects["main_Line01"].visible is False
assert scene.active_camera is cam
assert cam.worldPosition != [5.0, 5.0, 5.0]  # easing toward Wide
assert "init ok: 3 sets 4 cues 2 actions" in log_text(CASE1)
print("init/load ok")

# 2) camera settles on the opening shot
run(cont, 39)  # tick 40: blend (30 ticks) done
assert cam.worldPosition == [0.0, 0.0, 10.0], cam.worldPosition
assert cam.worldOrientation == IDENT
assert cam.lens == 50.0
print("camera open ok")

# 3) typing at 10 cps
run(cont, 1)  # tick 41... use fresh timing below instead
cont, own, scene, cam, sub, menu, robjs = make_cont(CASE1)
run(cont, 30)
assert sub.text == "CUBY:", repr(sub.text)  # 0.5 s * 10 cps
run(cont, 30)
assert sub.text == "", repr(sub.text)  # cue 2 just started
run(cont, 10)
assert sub.text == "S", repr(sub.text)
print("typing ok")

# 4) delayed action starts when due (t = 1.0, tick 60)
assert robjs["R2"].calls == [("ActLate", 1, 31, 7)], robjs["R2"].calls
print("delayed action ok")

# 5) [CAM] cut mid-set: blend Wide -> Cuby, orientation stays valid
assert cam.worldPosition != [0.0, 0.0, 10.0]  # blend restarted at tick 60
run(cont, 10)  # tick 80, mid-blend
for n in col_norms(cam.worldOrientation):
    assert abs(n - 1.0) < 0.01, (n, cam.worldOrientation)
run(cont, 20)  # tick 100: settled
assert cam.worldPosition == [10.0, 0.0, 0.0], cam.worldPosition
assert cam.worldOrientation == ROT_Z90
assert cam.lens == 55.0
print("camera cut ok")

# 6) wait-actions block past subs end; goto fires when all done
run(cont, 20)  # tick 120, t = 2.0, subs over, both still playing
assert own["tw_set"] == "a", own["tw_set"]
robjs["R"].playing = False
run(cont, 1)
assert own["tw_set"] == "a"  # R2 still playing
robjs["R2"].playing = False
run(cont, 1)
assert own["tw_set"] == "b", own["tw_set"]  # tick 122
assert "goto set 'b'" in log_text(CASE1)
print("blocking/goto ok")

# 7) choice entry at subs end (b dur 1.0 -> tick 182)
run(cont, 60)
assert own["tw_state"] == "choice", own["tw_state"]
assert sub.text == "Pick now?"
assert menu.text == "> 1: See C\n  2: Back to A", repr(menu.text)
assert "choice 'pick' (2 options)" in log_text(CASE1)
print("choice entry ok")

# 8) nav + confirm + restart + direct-key loop back through the choice
run(cont, 1, {1: ["DOWNARROWKEY"]})
assert own["tw_ci"] == 1 and menu.text.startswith("  1: See C")
run(cont, 1, {1: ["UPARROWKEY"]})
assert own["tw_ci"] == 0
run(cont, 1, {1: ["ENTERKEY"]})
assert own["tw_set"] == "c", own["tw_set"]
assert menu.text == ""
assert "option 1 'See C' -> set 'c'" in log_text(CASE1)
run(cont, 1, {1: ["RKEY"]})
assert own["tw_set"] == "a" and own["tw_state"] == "play"
run(cont, 120)  # R/R2 done now -> straight through at subs end
assert own["tw_set"] == "b", own["tw_set"]
run(cont, 60)
assert own["tw_state"] == "choice"
run(cont, 1, {1: ["TWOKEY"]})
assert own["tw_set"] == "a", own["tw_set"]
print("choice/rest/loop ok")

# 9) stop: last line lingers, then the replay hint
run(cont, 120)  # -> b
run(cont, 60)  # -> choice
run(cont, 1, {1: ["ENTERKEY"]})  # -> c
run(cont, 60)  # c dur 1.0 -> stop
assert own["tw_state"] == "stop" and sub.text == "CUBY: Cee."
run(cont, 121)
assert sub.text == "R = replay!   ESC = quit"
print("stop/hint ok")

# 10) ESC quits without advancing the tick
n_end = len(ENDED)
tick_before = own["tick"]
run(cont, 1, {1: ["ESCKEY"]})
assert len(ENDED) == n_end + 1 and own["tick"] == tick_before
print("esc ok")

# 11) dead data: missing file, bad YAML, story error, no story.py, no range
cont4, own4, _, _, sub4, _, _ = make_cont(CASE1)
own4["tw_story"] = "//nope.yml"
run(cont4, 1)
assert "tw_dead" in own4 and sub4.text.startswith("No dialogue data")
assert "INIT FAILED" in log_text(CASE1)
CASEB = write_case("badyaml", {"story.yml": "sets: [oops\n"})
cont5, own5, _, _, sub5, _, _ = make_cont(CASEB)
run(cont5, 1)
assert "tw_dead" in own5 and sub5.text.startswith("No dialogue data")
CASEC = write_case("badcheck", {"story.yml": YML1.replace("start: a",
                                                          "start: nope"),
                                "a.srt": SRTA, "b.srt": SRTB, "c.srt": SRTC,
                                "audio/x.ogg": b"RIFF....",
                                "story.sync.json": json.dumps(SYNC1)})
cont6, own6, *_ = make_cont(CASEC)
run(cont6, 1)
assert "tw_dead" in own6 and "'start' must name" in own6["tw_dead"]
CASEN = write_case("nolib", {"story.yml": YML1})
os.remove(os.path.join(CASEN, "story.py"))
cont7, own7, *_ = make_cont(CASEN)
run(cont7, 1)
assert "tw_dead" in own7 and "story.py" in own7["tw_dead"]
CASER = write_case("norange", {"story.yml": YML1, "a.srt": SRTA, "b.srt": SRTB,
                               "c.srt": SRTC, "audio/x.ogg": b"RIFF....",
                               "story.sync.json": json.dumps({"actions": {}})})
cont8, own8, *_ = make_cont(CASER)
run(cont8, 1)
assert "tw_dead" in own8 and "frame range" in own8["tw_dead"]
print("dead-data ok")

# 12) failed actors latch once and never hang the set
YMLG = YML1.replace("- action: R@ActA", "- action: Ghost@ActA")
YMLG = YMLG.replace("end: goto b", "end: stop", 1)
CASEG = write_case("ghost", {"story.yml": YMLG, "a.srt": SRTA, "b.srt": SRTB,
                             "c.srt": SRTC, "audio/x.ogg": b"RIFF....",
                             "story.sync.json": json.dumps(SYNC1)})
CURRENT[0] = CASEG
ownG = FakeObj("GameDirector", playing=False)
ownG["tw_story"] = "//story.yml"
camG = FakeObj("Camera", playing=False)
wideG = FakeObj("Wide", playing=False)
wideG.worldPosition = [0.0, 0.0, 10.0]
wideG.lens = 50.0
contG = FakeCont(ownG, FakeScene([ownG, camG, wideG,
                                  FakeObj("Subtitles", playing=False),
                                  FakeObj("ChoiceMenu", playing=False)]))
run(contG, 125)  # subs dur 2.0; Ghost+R2 missing but wait anyway
assert ownG["tw_state"] == "stop", ownG["tw_state"]
logG = log_text(CASEG)
assert logG.count("actor 'Ghost' unavailable (missing object)") == 1, logG
run(contG, 1, {1: ["RKEY"]})  # re-enter: still no repeats
run(contG, 5)
logG = log_text(CASEG)
assert logG.count("actor 'Ghost'") == 1
print("actor latch ok")

# 13) capture budget quits
saved_cap = gs.CAP_TICKS
gs.CAP_TICKS = 60
try:
    n_end = len(ENDED)
    cont9, *_ = make_cont(CASE1, capture=True)
    run(cont9, 60)
    assert len(ENDED) == n_end + 1
    assert "capture budget hit" in log_text(CASE1)
finally:
    gs.CAP_TICKS = saved_cap
print("capture budget ok")

# 14) log needles incl. heartbeat
run(cont, 600)
log1 = log_text(CASE1)
for needle in ("enter set 'a' (init)", "tick 5 t=", "heartbeat tick="):
    assert needle in log1, needle
print("log ok (%d bytes)" % len(log1))

# 15) legacy keyboard (no .inputs) falls back to .events
legacy = LegacyKeyboard()
CURRENT[0] = CASE1
ownL = FakeObj("GameDirector", playing=False)
ownL["tw_story"] = "//story.yml"
camL = FakeObj("Camera", playing=False)
wideL = FakeObj("Wide", playing=False)
subL = FakeObj("Subtitles", playing=False)
contL = FakeCont(ownL, FakeScene([ownL, camL, wideL, subL,
                                  FakeObj("ChoiceMenu", playing=False),
                                  FakeObj("R"), FakeObj("R2")]))
saved_kb = logic.keyboard
logic.keyboard = legacy
try:
    for _ in range(30):
        legacy.events = {}
        gs.update(contL)
    assert subL.text == "CUBY:", repr(subL.text)
    legacy.events = {"RKEY": 1}
    gs.update(contL)
    assert ownL["tw_t0"] == 30 and subL.text == "", (ownL.get("tw_t0"),
                                                    subL.text)
finally:
    logic.keyboard = saved_kb
print("legacy keyboard fallback ok")

# 16) end-to-end over the real story files (chained choice + loop)
CURRENT[0] = HERE
ownR = FakeObj("GameDirector", playing=False)
ownR["tw_story"] = "//story.yml"
names = ["CubyRoot", "SpheroRoot", "CubyMouth", "SpheroMouth", "CubyArmR",
         "SpheroArmR"]
robjsR = {n: FakeObj(n, playing=False) for n in names}
camR = FakeObj("Camera", playing=False)
staged = {}
for n, pos, lens in (("Wide", [0.0, 0.0, 10.0], 50.0),
                     ("Cuby", [10.0, 0.0, 0.0], 55.0),
                     ("Sphero", [-10.0, 0.0, 0.0], 45.0)):
    o = FakeObj(n, playing=False)
    o.worldPosition = pos
    o.lens = lens
    staged[n] = o
subR = FakeObj("Subtitles", playing=False)
menuR = FakeObj("ChoiceMenu", playing=False)
contR = FakeCont(ownR, FakeScene([ownR, camR, subR, menuR,
                                  FakeObj("main_Line01", playing=False),
                                  FakeObj("cuby_Line01", playing=False)]
                                 + list(staged.values())
                                 + list(robjsR.values())))
run(contR, 1)
assert ownR["tw_set"] == "main"
assert robjsR["CubyRoot"].calls == [("main__cuby", 1, 361, 7)]
assert robjsR["CubyArmR"].calls == [("main__cubyarm", 1, 361, 7)]
assert contR.owner.scene.objects["main_Line01"].visible is False
assert contR.owner.scene.objects["cuby_Line01"].visible is False
run(contR, 39)  # opening blend done
assert camR.lens == 50.0, camR.lens
assert camR.worldPosition == [0.0, 0.0, 10.0]
run(contR, 185)  # tick 225, t = 3.75: cue 3 [CAM Cuby] cut starts
assert camR.worldPosition != [0.0, 0.0, 10.0]
run(contR, 30)  # settled on Cuby
assert camR.lens == 55.0, camR.lens
assert camR.worldPosition == [10.0, 0.0, 0.0]
run(contR, 645)  # tick 900, t = 15.0 -> choice pick
assert ownR["tw_state"] == "choice", ownR["tw_state"]
assert subR.text == "Who gets the last word?", repr(subR.text)
assert menuR.text == ("> 1: Ask Cuby about cubes\n"
                      "  2: Ask Sphero about spheres"), repr(menuR.text)
run(contR, 1, {1: ["ENTERKEY"]})
assert ownR["tw_set"] == "cuby"
assert robjsR["CubyRoot"].calls[-1] == ("cuby__cuby", 1, 91, 7)
run(contR, 225)  # 3.75 s -> choice pick2 (chained choice)
assert ownR["tw_state"] == "choice", ownR["tw_state"]
assert subR.text == "CUBY: Stay square,\nfriend!", repr(subR.text)
run(contR, 1, {1: ["ONEKEY"]})
assert ownR["tw_set"] == "main", ownR["tw_set"]  # loop back
assert menuR.text == ""
run(contR, 900)  # main again -> choice pick
assert ownR["tw_state"] == "choice"
run(contR, 1, {1: ["TWOKEY"]})
assert ownR["tw_set"] == "sphero"
run(contR, 330)  # 5.5 s -> stop
assert ownR["tw_state"] == "stop", ownR["tw_state"]
assert subR.text.startswith("SPHERO: Stay round"), repr(subR.text)
logR = log_text(HERE)
assert "init ok: 3 sets 13 cues 12 actions" in logR, logR[:400]
print("real-story integration ok")

shutil.rmtree(FIX, ignore_errors=True)
try:
    os.remove(os.path.join(HERE, "game_debug.log"))
except OSError:
    pass
print("ALL GAME LOGIC TESTS PASSED")