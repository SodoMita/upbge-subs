"""Set the CAPTURE flag on GameDirector and save a capture variant.

Usage:
    upbge -b talking_robots.blend -P set_capture.py
"""
import bpy
import os

HERE = os.path.dirname(os.path.abspath(__file__))
d = bpy.data.objects.get("GameDirector")
assert d is not None, "GameDirector not found"
d["CAPTURE"] = 1
out = os.path.join(HERE, "talking_robots_capture.blend")
bpy.ops.wm.save_as_mainfile(filepath=out)
print("[CAPTURE] flag set ->", out)
