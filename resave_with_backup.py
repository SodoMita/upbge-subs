"""Embed v1.5 backup + friendlier open state into talking_robots.blend.

Usage: upbge -b talking_robots.blend -P resave_with_backup.py
"""
import bpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw

if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
    print("[RESAVE] addon registered")

print("[RESAVE] addon v%s"
      % (".".join(str(x) for x in tw.bl_info.get("version", ())),))
sub = bpy.data.objects.get("Subtitles")
assert sub is not None, "Subtitles object missing!"
print("[RESAVE] entries: %d" % len(sub.tw_entries))
tw._write_backup(sub, force=True)
print("[RESAVE] backup bytes: %d" % len(sub.get("tw_backup") or ""))
scene = bpy.context.scene
scene.frame_current = 30
tw.update_object(sub, scene)
print("[RESAVE] body@30: %r" % sub.data.body)

txt = bpy.data.texts.get("README")
if txt is not None:
    txt.from_string(
        "TALKING ROBOTS - 3D typewriter subtitles demo (addon v1.5+)\n"
        "============================================================\n\n"
        "1) Install the addon: Edit > Preferences > Add-ons > Install...\n"
        "   pick typewriter_subtitles.py, enable 'Typewriter Subtitles'.\n"
        "   (Without it the text below stays frozen — that's normal.)\n\n"
        "2) TIMELINE MODE: press Spacebar (or drag the playhead). Lines\n"
        "   type live on the 3D text. Select 'Subtitles' > Sidebar (N) >\n"
        "   Subtitles to edit; the status line shows lines/keys/sync.\n"
        "   Drag keys in the Timeline to retime; Ctrl+Shift+U adds one.\n\n"
        "3) GAME MODE: press P (needs a GPU). R restarts, ESC quits.\n\n"
        "4) Render: F12 still / Ctrl+F12 animation (PNGs into ./render/).\n\n"
        "5) RECOVERY: lines are auto-backed-up on the object. If they\n"
        "   ever vanish, the panel status line tells you — press Recover\n"
        "   Backup (or Rebuild Keys if only keys are missing). Worst\n"
        "   case: Import dialogue.srt with Replace to restore everything.\n\n"
        "Files next to this .blend: dialogue.srt, game_subtitles.py,\n"
        "typewriter_subtitles.py (the addon), build_scene.py (rebuild).\n")
    print("[RESAVE] README text refreshed")

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "talking_robots.blend"))
print("[RESAVE] saved")
