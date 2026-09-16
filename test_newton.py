#!/usr/bin/env python3
"""test_newton.py - stdlib-only tests for the Newton-mechanics example.

Covers the story layer of newton.yml + newton_*.srt + newton.sync.json:
  1. every set loads, plans and chains intro->law1->law2->law3->outro->stop,
  2. every `action: Obj@Act` entry has a range in the sync sidecar and the
     range is long enough for the set that plays it (a trigger must still
     be mid-stroke when its narration ends; mouths cover their set),
  3. subtitle cues sit inside their set, speakers are Cuby/Sphero, and the
     reading speed stays under the project cps budget,
  4. camera entries name shots the builder creates (newton.yml SHOTS list
     mirrored here on purpose - a rename must break both).

Run: python3 test_newton.py        (exit 0 = pass)
Physics and game wiring are covered by tools/verify_newton_physics.py
(headless Bullet bake) and a live player run (tools/run_live_newton.sh).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb

FPS = 24
CPS_MAX = 30.0
SHOTS = {"NewtonWide", "Law1", "Law2", "Law3"}
CHAIN = [("intro", "law1"), ("law1", "law2"), ("law2", "law3"),
         ("law3", "outro"), ("outro", None)]
FAILS = []


def check(cond, msg):
    if cond:
        print("  ok  " + msg)
    else:
        print("  FAIL " + msg)
        FAILS.append(msg)


def main():
    print("== newton story layer ==")
    loaded = sb.load_story_files(os.path.join(HERE, "newton.yml"))
    check(not loaded["errors"], "story loads with no errors")
    check(not loaded["warnings"], "story loads with no warnings")
    story, files = loaded["story"], loaded["files"]
    check(sorted(story["sets"]) == ["intro", "law1", "law2", "law3",
                                    "outro"], "five sets present")

    plans = {}
    for name in ("intro", "law1", "law2", "law3", "outro"):
        plan = sb.set_plan(story, files, name, None)
        plans[name] = plan
        check(len(plan["subs"]) >= 2, "%s has cues (%d)"
              % (name, len(plan["subs"])))
    for name, nxt in CHAIN:
        end = plans[name]["end"]
        want = ("goto", nxt) if nxt else ("stop", None)
        check(end == want, "%s chains to %s" % (name, nxt or "STOP"))

    side = json.load(open(os.path.join(HERE, "newton.sync.json"),
                          encoding="utf-8"))
    ranges = side["actions"]
    check(len(ranges) >= 12, "sidecar carries %d action ranges"
          % len(ranges))
    for name, plan in plans.items():
        need = int(plan["dur"] * FPS) + 1  # action covers the set
        for entry in story["sets"][name].get("anims", []):
            if not str(entry).startswith("action:"):
                continue
            act = str(entry).split("@", 1)[1].strip()
            check(act in ranges, "%s: action %s has a sidecar range"
                  % (name, act))
            lo, hi = ranges.get(act, [0, 0])
            check(hi - lo + 1 >= need,
                  "%s: %s spans %d frames >= set need %d"
                  % (name, act, hi - lo + 1, need))
            if act.endswith(("cubymouth", "spheromouth")):
                check(lo <= FPS, "%s: mouth %s starts at frame %d"
                      % (name, act, lo))

    for name, plan in plans.items():
        for st, en, tx in plan["subs"]:
            check(0.0 <= st < en <= plan["dur"] + 0.01,
                  "%s: cue %.1f-%.1f inside set (%.1f s)"
                  % (name, st, en, plan["dur"]))
            body = tx.split(":", 1)[1] if ":" in tx else tx
            cps = len(body) / max(en - st, 0.1)
            check(cps <= CPS_MAX, "%s: %.0f cps <= %.0f ('%s...')"
                  % (name, cps, CPS_MAX, body[:18]))
        tagged = [t for _s, _e, t in plan["subs"]
                  if t.startswith(("CUBY:", "SPHERO:"))]
        check(bool(tagged) and
              plan["subs"][0][2].startswith(("CUBY:", "SPHERO:")),
              "%s: opens with a tagged cue (%d tagged)"
              % (name, len(tagged)))
        check({t.split(":")[0] for t in tagged} == {"CUBY", "SPHERO"}
              or name in ("intro", "outro"),
              "%s: both robots speak" % name)
        for entry in story["sets"][name].get("anims", []):
            if str(entry).startswith("camera:"):
                check(str(entry).split(":", 1)[1].strip() in SHOTS,
                      "%s: camera shot known to the builder" % name)

    print("== newton physics layer (static checks) ==")
    src = open(os.path.join(HERE, "tools", "verify_newton_physics.py"),
               encoding="utf-8").read()
    for token in ("law1 inertia", "law2 F=ma", "law3 reaction"):
        check(token in src, "verify script asserts " + token)
    cap = open(os.path.join(HERE, "tools", "make_newton_capture.py"),
               encoding="utf-8").read()
    for token in ("law1__pusher", "law2__hammer", "law3__door"):
        check(token in cap, "capture retime covers " + token)

    if FAILS:
        print("\n%d NEWTON TEST(S) FAILED" % len(FAILS))
        sys.exit(1)
    print("\nALL NEWTON TESTS PASSED")


main()
