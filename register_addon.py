"""Register the Typewriter Subtitles addon in a background render session.

Usage:
    upbge -b talking_robots.blend -P register_addon.py -o //render/frame_ -F PNG -a
"""
import bpy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import typewriter_subtitles as tw

if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
    print("[RENDER] typewriter subtitles addon registered")
else:
    print("[RENDER] typewriter subtitles addon already active")
