"""Headless draw test for the story panel (panels only draw in a real UI).

    upbge -b --factory-startup -P test_panel_draw.py

Drives VIEW3D_PT_tw_story.draw() / VIEW3D_PT_tw_subtitles.draw() through a
recording fake layout in every state the panel can be in (no story, broken
story, clean story, previewing, pending renames, validator report), so a
NameError/AttributeError in the UI code cannot ship unnoticed.
"""
import bpy
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw      # noqa: E402

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("  ok   %s" % label, flush=True)
    else:
        print("  FAIL %s %s" % (label, extra), flush=True)
        FAILS.append(label)


class FakeLayout:
    """Records every call; returns itself so chained row()/box() works."""

    def __init__(self, log, depth=0):
        self._log = log
        self._depth = depth

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self._log.append((self._depth, name, args, kwargs))
            if name in ("box", "row", "column", "split", "grid", "flow",
                        "column_flow"):
                return FakeLayout(self._log, self._depth + 1)
            return None
        return call


class _Shim:
    """Stands in for the Panel instance: draw() only ever reads self.layout.

    bpy.types.Panel.layout is a read-only RNA property, so the real class
    cannot be instantiated headlessly - but its draw() is a plain function
    that can be handed any object with a `.layout`.
    """

    def __init__(self, layout):
        self.layout = layout


def draw_panel(cls, scene, active=None):
    """Draw one panel into a fresh fake layout; returns the call log."""
    log = []
    bpy.context.view_layer.objects.active = active
    cls.draw(_Shim(FakeLayout(log)), bpy.context)
    return log


def labels(log):
    """All text passed to label()/operator() - what the user would read."""
    out = []
    for _d, name, args, kwargs in log:
        if name == "label":
            out.append(str(kwargs.get("text", args[0] if args else "")))
        elif name == "operator":
            out.append(str(args[0]) if args else "")
    return out


TMP = tempfile.mkdtemp(prefix="twpanel")
for fn in ("story.py", "story.yml", "dialogue.srt", "cuby.srt", "sphero.srt"):
    shutil.copy(os.path.join(HERE, fn), os.path.join(TMP, fn))
STORY = os.path.join(TMP, "story.yml")

if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
scene = bpy.context.scene
scene.render.fps = 24
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
sub = bpy.data.objects.new("Subtitles", bpy.data.curves.new("Subtitles", 'FONT'))
scene.collection.objects.link(sub)
sub["_tw_role"] = "subs"
menu = bpy.data.objects.new("ChoiceMenu", bpy.data.curves.new("ChoiceMenu", 'FONT'))
scene.collection.objects.link(menu)
menu["_tw_role"] = "menu"
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(cam)
scene.camera = cam

print("=== state 1: no story file ===", flush=True)
scene.tw_story_path = os.path.join(TMP, "missing.yml")
tw.invalidate_story_cache()
tw._PANEL_CACHE[1] = None
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("No story here" in txt, "reports the missing story", txt)
check(len(log) > 2, "panel drew something", str(len(log)))

print("=== state 2: broken story ===", flush=True)
open(os.path.join(TMP, "broken.yml"), "w", encoding="utf-8").write(
    "start: nope\nsets:\n")
scene.tw_story_path = os.path.join(TMP, "broken.yml")
tw.invalidate_story_cache()
tw._PANEL_CACHE[1] = None
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("error" in txt, "reports story errors", txt)
check("tw.validate_story" in txt, "offers the validator", txt)

print("=== state 3: clean story, nothing previewed ===", flush=True)
scene.tw_story_path = STORY
tw.invalidate_story_cache()
tw._PANEL_CACHE[1] = None
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("3 sets, 2 choices, start 'main'" in txt, "summary line", txt)
check("No set previewed" in txt, "preview indicator empty", txt)
check("tw.preview_set" in txt and "tw.refresh_sync" in txt
      and "tw.validate_story" in txt, "main buttons present", txt)
check("tw.bake_set" in txt, "bake button present", txt)

print("=== state 4: previewing a set ===", flush=True)
for nm, f1 in (("CubyRoot", 361),):
    o = bpy.data.objects.new(nm, None)
    scene.collection.objects.link(o)
    o.location = (0, 0, 0)
    o.keyframe_insert("location", frame=1)
    o.location = (0, 0, 1)
    o.keyframe_insert("location", frame=f1)
    o.animation_data.action.name = "main__cuby"
scene.tw_preview_set = "main"
scene.tw_preview_range = "1-361"
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("Previewing 'main' (1-361)" in txt, "preview indicator shows the set", txt)
check("tw.clear_preview" in txt, "clear button present", txt)

print("=== state 5: pending renames ===", flush=True)
row = scene.tw_renames.add()
row.uid, row.old, row.new, row.kind = "tw-x", "CubyRoot", "CubyBoss", "EMPTY"
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("1 rename(s) not in story.yml" in txt, "rename banner", txt)
check("CubyRoot -> CubyBoss" in txt, "rename row rendered", txt)
check("tw.apply_renames" in txt and "tw.forget_renames" in txt,
      "Apply/Forget buttons present", txt)
scene.tw_renames.clear()

print("=== state 6: validator report ===", flush=True)
tw._fill_report(scene, ["err one"], ["warn one", "warn two"],
                ["bind one"], "3 set(s), 2 choice(s)")
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("Errors (1)" in txt and "err one" in txt, "errors listed", txt)
check("Bindings (1)" in txt and "bind one" in txt, "bindings listed", txt)
check("Warnings (2)" in txt and "warn two" in txt, "warnings listed", txt)

print("=== state 7: long messages are wrapped, never truncated silently ===",
      flush=True)
long_msg = "x" * 300
tw._fill_report(scene, [long_msg], [], [], "note")
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = "".join(labels(log))
check(txt.count("x") == 300, "all 300 chars are drawn across wrapped lines",
      "got %d" % txt.count("x"))
check(all(len(t) <= 46 for t in labels(log)),
      "no single label exceeds the panel width",
      str(max(len(t) for t in labels(log))))
check("... and" not in txt, "small reports are not elided", txt)

print("=== state 8: many rows ARE elided ===", flush=True)
tw._fill_report(scene, ["e%d" % i for i in range(20)], [], [], "note")
log = draw_panel(tw.VIEW3D_PT_tw_story, scene)
txt = " | ".join(labels(log))
check("... and 12 more" in txt, "long lists are capped with a counter", txt)

print("=== state 9: the v1 subtitle panel still draws ===", flush=True)
tw._create_entry(sub, 1, "Hello there")
tw._set_key(sub, sub.tw_entries[0].uid, 1)
bpy.context.view_layer.objects.active = sub
log = draw_panel(tw.VIEW3D_PT_tw_subtitles, scene, active=sub)
txt = " | ".join(labels(log))
check("tw.bake_typewriter" in txt, "v1 Bake button still there", txt)
check("tw.setup_game_logic" in txt, "v1 Add Game Logic still there", txt)
check("tw.jump_to_marker" not in txt, "v1 marker button is gone", txt)
check("1 lines, 1 keys" in txt, "v1 health line still drawn", txt)

print("=== state 10: subtitle panel with no text object ===", flush=True)
log = draw_panel(tw.VIEW3D_PT_tw_subtitles, scene, active=None)
check(any("No 3D text object" in str(k.get("text", "")) for _d, n, a, k in log
          if n == "label"), "empty-state message drawn")

shutil.rmtree(TMP, ignore_errors=True)
print("=== RESULT ===", flush=True)
if FAILS:
    print("PANEL DRAW FAILURES (%d): %s" % (len(FAILS), FAILS), flush=True)
else:
    print("ALL PANEL DRAW TESTS PASSED", flush=True)
bpy.ops.wm.quit_blender()
