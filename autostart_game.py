"""Auto-start the UPBGE game (used under Xvfb for headless capture),
then quit Blender once the game ends.

Usage:
    xvfb-run -a upbge talking_robots_capture.blend -P autostart_game.py
"""
import bpy

started = False
try:
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type != 'WINDOW':
                    continue
                with bpy.context.temp_override(window=win, screen=win.screen,
                                               area=area, region=region):
                    bpy.ops.view3d.game_start()
                started = True
                break
            if started:
                break
        if started:
            break
except Exception as ex:
    print("[GAME] override start failed:", ex)
    try:
        bpy.ops.view3d.game_start()
        started = True
    except Exception as ex2:
        print("[GAME] direct start failed:", ex2)
print("[GAME] session over (started=%s), quitting" % started)
bpy.ops.wm.quit_blender()
