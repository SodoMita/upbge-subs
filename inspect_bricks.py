"""Inspect game setup in talking_robots.blend (background-safe)."""
import bpy

b = bpy.data.objects.get("GameDirector")
print("[INSPECT] director:", bool(b))
if b is not None and hasattr(b, "game"):
    for s in b.game.sensors:
        try:
            links = [c.name for c in s.controllers]
        except Exception as ex:
            links = "ERR %r" % ex
        print("[INSPECT] sensor: %r type=%s pulse_true=%s freq=%s "
              "tap=%s invert=%s -> %s"
              % (s.name, s.type,
                 getattr(s, "use_pulse_true_level", "?"),
                 getattr(s, "frequency", "?"),
                 getattr(s, "use_tap", "?"),
                 getattr(s, "use_inv_true_level", "?"), links))
    for c in b.game.controllers:
        try:
            slinks = [s.name for s in c.sensors]
        except Exception as ex:
            slinks = "ERR %r" % ex
        print("[INSPECT] controller: %r type=%s mode=%s module=%s state=%s <- %s"
              % (c.name, c.type, getattr(c, "mode", "?"),
                 getattr(c, "module", "?"),
                 getattr(c, "use_priority", "?"), slinks))
print("[INSPECT] scene.camera:",
      bpy.context.scene.camera.name if bpy.context.scene.camera else None)
sub = bpy.data.objects.get("Subtitles")
print("[INSPECT] internal game text:",
      "game_subtitles.py" in bpy.data.texts)
if sub is not None and hasattr(sub, "game"):
    try:
        print("[INSPECT] sub game props:",
              [(p.name, p.type) for p in sub.game.properties])
    except Exception as ex:
        print("[INSPECT] sub game props ERR %r" % ex)
print("[INSPECT] game_property ops:",
      [x for x in dir(bpy.ops.object) if "game_property" in x])
if sub is not None and hasattr(sub, "game"):
    print("[INSPECT] properties API:",
          [m for m in dir(sub.game.properties) if not m.startswith("_")])
