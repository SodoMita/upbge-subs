#!/usr/bin/env python3
"""Mock test for the sets+actors game driver (no Blender needed).

Drives game_subtitles.update with a fake bge module: story loading from
YAML+SRT fixture files, actor playback, typing, goto/choice/stop ends,
restart, dead-data paths, actor-failure latch, capture mode, legacy
keyboard fallback, and an end-to-end pass over the real story files.
"""
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
sys.path.insert(0, HERE)
import game_subtitles as gs


class FakeObj(dict):
    def __init__(self, name="obj", action="Act_anim"):
        super().__init__()
        self.name = name
        self.text = ""
        self.lens = 50.0
        self.worldScale = [1.0, 1.0, 1.0]
        self.action_name = action
        self.calls = []
        self.visible = True

    def getActionName(self, layer=0):
        return self.action_name

    def playAction(self, action, f0, f1, layer=0, priority=0, blendin=0,
                   mode=0):
        self.calls.append((action, f0, f1, mode))


class FakeScene:
    def __init__(self, objs):
        self.objects = {o.name: o for o in objs}
        self.active_camera = None


class FakeCont:
    def __init__(self, own, scene):
        self.owner = own
        own.scene = scene


FIX = tempfile.mkdtemp(prefix="twstory")


def write_case(name, yml, srt):
    d = os.path.join(FIX, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "story.yml"), "w") as fh:
        fh.write(yml)
    with open(os.path.join(d, "dialogue.srt"), "w") as fh:
        fh.write(srt)
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
subs: dialogue.srt
actors:
  - Camera
sets:
  a:
    cues: [1, 2]
    frames: [1, 48]
    lens: 50
    end: goto b
  b:
    cues: [3, 5]
    frames: [49, 200]
    lens: 55
    end: choice pick
  c:
    cues: [6, 6]
    frames: [201, 240]
    lens: 45
    end: stop
choices:
  pick:
    prompt_cue: 5
    options:
      - [1, "See C", c]
      - [2, "Back to A", a]
"""
SRT1 = """1
00:00:00,000 --> 00:00:01,000
CUBY: Hi.

2
00:00:01,000 --> 00:00:02,000
SPHERO: Yo.

3
00:00:00,000 --> 00:00:01,000
CUBY: Bee.

4
00:00:01,500 --> 00:00:02,500
Gap holder.

5
00:00:03,000 --> 00:00:04,000
Pick now?

6
00:00:00,000 --> 00:00:01,000
CUBY: Cee.
"""
CASE1 = write_case("case1", YML1, SRT1)


def make_cont(case, capture=False):
    CURRENT[0] = case
    own = FakeObj("GameDirector", action="")
    own["tw_story"] = "//story.yml"
    if capture:
        own["CAPTURE"] = 1
    cam = FakeObj("Camera", "Camera_anim")
    sub = FakeObj("Subtitles", "")
    menu = FakeObj("ChoiceMenu", "")
    subline = FakeObj("SubLine01", "")
    scene = FakeScene([own, cam, sub, menu, subline])
    return FakeCont(own, scene), own, scene, cam, sub, menu


def log_text(case):
    with open(os.path.join(case, "game_debug.log")) as fh:
        return fh.read()


# 1) init: start set entered, actor played once, lens set
cont, own, scene, cam, sub, menu = make_cont(CASE1)
run(cont, 1)
assert own["tw_set"] == "a" and own["tw_state"] == "play"
assert cam.calls == [("Camera_anim", 1, 48, 7)], cam.calls
assert cam.lens == 50
assert scene.active_camera is cam
assert menu.text == ""
assert scene.objects["SubLine01"].visible is False
assert "init ok: 3 sets 6 cues 1 actors" in log_text(CASE1)
print("init/load ok")

# 2) typing
run(cont, 29)
assert sub.text == "CUBY:", repr(sub.text)  # 0.5 s * 10 cps
run(cont, 30)
assert sub.text == "", repr(sub.text)  # cue 2 just started
run(cont, 10)
assert sub.text == "S", repr(sub.text)
print("typing ok")

# 3) goto end
run(cont, 50)  # tick 120, t = 2.0 -> goto b
assert own["tw_set"] == "b", own["tw_set"]
assert cam.calls[-1] == ("Camera_anim", 49, 200, 7)
assert cam.lens == 55
assert "goto set 'b'" in log_text(CASE1)
print("goto ok")

# 4) gap hold + choice entry at prompt start (t = 3.0, tick 300)
run(cont, 72)  # tick 192, local t = 1.2: inside the 1.0-1.5 gap
assert sub.text == "CUBY: Bee.", repr(sub.text)
run(cont, 108)  # tick 300
assert own["tw_state"] == "choice", own["tw_state"]
assert sub.text == "Pick now?"
assert menu.text == "> 1: See C\n  2: Back to A", repr(menu.text)
assert "choice 'pick' (2 options)" in log_text(CASE1)
print("choice entry ok")

# 5) nav + confirm
run(cont, 1, {1: ["DOWNARROWKEY"]})
assert own["tw_ci"] == 1 and menu.text.startswith("  1: See C")
run(cont, 1, {1: ["UPARROWKEY"]})
assert own["tw_ci"] == 0
run(cont, 1, {1: ["ENTERKEY"]})
assert own["tw_set"] == "c", own["tw_set"]
assert cam.calls[-1] == ("Camera_anim", 201, 240, 7)
assert cam.lens == 45 and menu.text == ""
assert "option 1 'See C' -> set 'c'" in log_text(CASE1)
print("choice nav/confirm ok")

# 6) R restart + direct-key pick
run(cont, 1, {1: ["RKEY"]})
assert own["tw_set"] == "a" and own["tw_state"] == "play"
run(cont, 120)  # -> b
assert own["tw_set"] == "b"
run(cont, 180)  # -> choice
assert own["tw_state"] == "choice"
run(cont, 1, {1: ["TWOKEY"]})
assert own["tw_set"] == "a", own["tw_set"]
print("restart/direct-key ok")

# 7) capture: auto-pick, screenshots, auto-quit at story end
shots0, ended0 = len(SHOTS), len(ENDED)
cont2, own2, *_ = make_cont(CASE1, capture=True)
run(cont2, 120)  # -> b
run(cont2, 180)  # -> choice
run(cont2, 30)  # auto-picks option 1
assert own2["tw_set"] == "c", own2["tw_set"]
assert "capture auto-picks option 1" in log_text(CASE1)
run(cont2, 60)  # c ends -> stop
assert own2["tw_state"] == "stop"
run(cont2, 121)  # past STOP_HOLD -> endGame
assert len(ENDED) == ended0 + 1
assert len(SHOTS) - shots0 >= 100
print("capture ok (%d screenshots)" % (len(SHOTS) - shots0))

# 8) stop: last line lingers, then the replay hint
cont3, own3, _, _, sub3, _ = make_cont(CASE1)
run(cont3, 120)
run(cont3, 180)
run(cont3, 1, {1: ["ENTERKEY"]})
run(cont3, 60)  # tick 361: c ends
assert own3["tw_state"] == "stop" and sub3.text == "CUBY: Cee."
run(cont3, 121)
assert sub3.text == "R = replay!   ESC = quit"
print("stop/hint ok")

# 9) ESC quits without advancing the tick
n_end = len(ENDED)
tick_before = own["tick"]
run(cont, 1, {1: ["ESCKEY"]})
assert len(ENDED) == n_end + 1 and own["tick"] == tick_before
print("esc ok")

# 10) dead data: missing file, bad YAML, failed check
cont4, own4, _, _, sub4, _ = make_cont(CASE1)
own4["tw_story"] = "//nope.yml"
run(cont4, 1)
assert "tw_dead" in own4 and sub4.text.startswith("No dialogue data")
assert "INIT FAILED" in log_text(CASE1)
write_case("badyaml", "sets: [oops\n", SRT1)
cont5, own5, _, _, sub5, _ = make_cont(os.path.join(FIX, "badyaml"))
run(cont5, 1)
assert "tw_dead" in own5 and sub5.text.startswith("No dialogue data")
write_case("badcheck", YML1.replace("start: a", "start: nope"), SRT1)
cont6, own6, *_ = make_cont(os.path.join(FIX, "badcheck"))
run(cont6, 1)
assert "tw_dead" in own6 and "'start' must name" in own6["tw_dead"]
print("dead-data ok")

# 11) missing actors latch (logged once, play continues)
YMLA = YML1.replace("actors:\n  - Camera",
                    "actors:\n  - Camera\n  - Ghost\n  - NoAct")
YMLA = YMLA.replace("end: goto b", "end: stop", 1)
CASEA = write_case("actors", YMLA, SRT1)
CURRENT[0] = CASEA
ownA = FakeObj("GameDirector", action="")
ownA["tw_story"] = "//story.yml"
camA = FakeObj("Camera", "Camera_anim")
noact = FakeObj("NoAct", "")
contA = FakeCont(ownA, FakeScene([ownA, camA, noact,
                                  FakeObj("Subtitles", ""),
                                  FakeObj("ChoiceMenu", "")]))
run(contA, 1)
assert camA.calls == [("Camera_anim", 1, 48, 7)]
run(contA, 200)
logA = log_text(CASEA)
assert logA.count("actor 'Ghost' unavailable (missing object)") == 1, logA
assert logA.count("actor 'NoAct' unavailable (no action on layer 0)") == 1
run(contA, 1, {1: ["RKEY"]})  # re-enter: still no repeats
run(contA, 5)
logA = log_text(CASEA)
assert logA.count("actor 'Ghost'") == 1 and logA.count("NoAct") == 1
assert len(camA.calls) == 2  # the working actor replays fine
print("actor latch ok")

# 12) capture budget quits
saved_cap = gs.CAP_TICKS
gs.CAP_TICKS = 60
try:
    n_end = len(ENDED)
    cont7, *_ = make_cont(CASE1, capture=True)
    run(cont7, 60)
    assert len(ENDED) == n_end + 1
    assert "capture budget hit" in log_text(CASE1)
finally:
    gs.CAP_TICKS = saved_cap
print("capture budget ok")

# 13) log needles incl. heartbeat
run(cont, 600)
log1 = log_text(CASE1)
for needle in ("enter set 'a' (init)", "tick 5 t=", "heartbeat tick="):
    assert needle in log1, needle
print("log ok (%d bytes)" % len(log1))

# 14) legacy keyboard (no .inputs) falls back to .events
legacy = LegacyKeyboard()
CURRENT[0] = CASE1
ownL = FakeObj("GameDirector", action="")
ownL["tw_story"] = "//story.yml"
camL = FakeObj("Camera", "Camera_anim")
subL = FakeObj("Subtitles", "")
contL = FakeCont(ownL, FakeScene([ownL, camL, subL,
                                  FakeObj("ChoiceMenu", "")]))
saved_kb = logic.keyboard
logic.keyboard = legacy
try:
    for _ in range(30):
        legacy.events = {}
        gs.update(contL)
    assert subL.text == "CUBY:", repr(subL.text)
    legacy.events = {"RKEY": 1}
    gs.update(contL)
    assert ownL["tw_t0"] == 30 and subL.text == "", (ownL.get("tw_t0"), subL.text)
finally:
    logic.keyboard = saved_kb
print("legacy keyboard fallback ok")

# 15) end-to-end over the real story files
CURRENT[0] = HERE
ownR = FakeObj("GameDirector", action="")
ownR["tw_story"] = "//story.yml"
names = ["Camera", "CubyRoot", "SpheroRoot", "CubyMouth", "SpheroMouth",
         "CubyArmR", "SpheroArmR", "CubyEyeL", "CubyEyeR", "SpheroEyeL",
         "SpheroEyeR"]
robjs = {n: FakeObj(n, n + "_anim") for n in names}
subR = FakeObj("Subtitles", "")
menuR = FakeObj("ChoiceMenu", "")
contR = FakeCont(ownR, FakeScene([ownR, subR, menuR, FakeObj("SubLine01", ""),
                                  FakeObj("SubLine02", "")] + list(robjs.values())))
run(contR, 1)
assert ownR["tw_set"] == "main"
assert robjs["Camera"].calls == [("Camera_anim", 1, 361, 7)]
assert robjs["CubyRoot"].calls == [("CubyRoot_anim", 1, 361, 7)]
assert robjs["Camera"].lens == 50
assert contR.owner.scene.objects["SubLine01"].visible is False
assert contR.owner.scene.objects["SubLine02"].visible is False
run(contR, 899)  # tick 900, t = 15.0 -> choice at prompt start
assert ownR["tw_state"] == "choice", ownR["tw_state"]
assert subR.text == "Who gets the last word?", repr(subR.text)
assert menuR.text == ("> 1: Ask Cuby about cubes\n"
                      "  2: Ask Sphero about spheres"), repr(menuR.text)
run(contR, 1, {1: ["ENTERKEY"]})
assert ownR["tw_set"] == "cuby"
assert robjs["Camera"].calls[-1] == ("Camera_anim", 409, 541, 7)
assert robjs["Camera"].lens == 55
run(contR, 330)  # 5.5 s -> stop
assert ownR["tw_state"] == "stop", ownR["tw_state"]
assert subR.text.startswith("CUBY: Stay square"), repr(subR.text)
print("real-story integration ok")

shutil.rmtree(FIX, ignore_errors=True)
try:
    os.remove(os.path.join(HERE, "game_debug.log"))
except OSError:
    pass
print("ALL GAME LOGIC TESTS PASSED")
