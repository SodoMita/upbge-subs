"""Headless draw test for the Subtitles panel (panels only draw in a real UI).

    upbge -b talking_robots.blend -P test_panel_draw.py

Drives VIEW3D_PT_tw_subtitles.draw() - and the Story box inside it - through a
recording fake layout in every state the panel can be in: no story, broken
story, clean story, previewing a set, pending renames, an armed list of rename
rows, and with / without an active 3D text object. A NameError or an Attribute
Error in the UI code therefore cannot ship unnoticed (the panel is the one part
of the add-on no other test reaches).

It never saves: the .blend's sha256 is compared before and after. Deliberately
no bpy.ops.wm.quit_blender() either, so this can be chained before other
arguments.
"""
import hashlib
import os
import shutil
import sys
import tempfile

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw                      # noqa: E402

BLEND = os.path.join(HERE, "talking_robots.blend")
FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("  ok   %s" % label, flush=True)
    else:
        print("  FAIL %s %s" % (label, extra), flush=True)
        FAILS.append(label)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


class FakeLayout:
    """Records every call; returns a child for nested rows/boxes/columns."""

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


class _Row:
    """A UIList needs nothing but layout_type to draw a row."""

    def __init__(self, layout_type):
        self.layout_type = layout_type


class _Shim:
    """Stands in for the Panel: draw() only ever reads self.layout."""

    def __init__(self, layout):
        self.layout = layout


def draw(active=None):
    """Draw the panel; returns (call log, all text a user would read)."""
    log = []
    try:
        bpy.context.view_layer.objects.active = active
    except Exception:
        pass
    tw.VIEW3D_PT_tw_subtitles.draw(_Shim(FakeLayout(log)), bpy.context)
    txt = []
    for _d, name, args, kwargs in log:
        if name == "label":
            txt.append(str(kwargs.get("text", "")
                            or (args[0] if args else "")))
        elif name == "prop":
            txt.append("prop:%s:%s" % (args[1] if len(args) > 1 else "",
                                       kwargs.get("text", "")))
        elif name in ("operator", "operator_enum", "operator_menu_enum"):
            txt.append(str(args[0] if args else kwargs.get("text", "")))
    return log, " | ".join(txt)


if not hasattr(bpy.types.Scene, "tw_story"):
    tw.register()
scene = bpy.context.scene
if bpy.data.objects.get("Subtitles") is None:
    raise SystemExit("run me on the demo: blender -b talking_robots.blend -P "
                     "test_panel_draw.py")
start_sha = sha(BLEND) if os.path.isfile(BLEND) else ""
sub = bpy.data.objects["Subtitles"]
story_real = scene.tw_story
armed = scene.tw_preview_set          # the demo is saved with the start set on

print("=== state 1: a story path that does not exist ===", flush=True)
TMP = tempfile.mkdtemp(prefix="twpanel")
scene.tw_story = os.path.join(TMP, "nope.yml")
log, txt = draw(sub)
check("No story.yml next to the blend" in txt,
      "empty-state message drawn", txt)
check("tw.bake_typewriter" in txt and "prop:tw_enabled" in txt,
      "the v1 half still draws with a text object active", txt[-200:])
log, txt = draw(None)
check("No 3D text object selected" in txt, "no active object: v1 half is the "
      "add-button", txt)
check("tw.add_text_object" in txt, "and it offers to make one", txt)

print("=== state 2: a story that does not parse ===", flush=True)
with open(os.path.join(TMP, "story.yml"), "w", encoding="utf-8") as fh:
    fh.write("start: nope\nsets:\n  main:\n    anims: [{bogus: 1}]\n")
shutil.copy(os.path.join(HERE, "story.py"), os.path.join(TMP, "story.py"))
scene.tw_story = os.path.join(TMP, "story.yml")
tw.story_check_impl(scene)
log, txt = draw(sub)
check("error" in txt.lower(), "the broken story is reported as text", txt)
PLAIN = ("label", "box", "row", "column", "separator", "prop", "operator",
         "operator_enum", "operator_menu_enum", "template_list",
         "template_ID", "menu_pie", "alert", "scale_x", "use_property_split")
odd = sorted({n for _d, n, _a, _k in log if n not in PLAIN})
check(not odd, "every draw call is a plain layout call", odd)
banned = [n for _d, n, _a, _k in log
          if n.startswith(("graph_", "node_", "template_node", "gl_"))]
check(not banned, "no graph/canvas UI in the validator (user veto)", banned)

print("=== state 3: the repo's clean story ===", flush=True)
scene.tw_story = story_real
rep = tw.story_check_impl(scene)
check(rep["ok"], "story reports clean", rep["errors"] + rep["bindings"])
log, txt = draw(sub)
check("story clean" in txt, "clean line drawn", txt)
check("tw.preview_set" in txt, "Preview Set enum drawn", txt)
check("tw.refresh_sync" in txt and "tw.check_story" in txt,
      "sync + check buttons drawn", txt)
check("Camera follows the preview" in txt,
      "the new camera-follow checkbox is drawn", txt)
scene.tw_preview_set = ""
log, txt = draw(sub)
check("tw.clear_preview" not in txt,
      "Leave Preview is hidden while nothing is previewed", txt)
scene.tw_preview_set = armed

print("=== state 4: previewing the start set ===", flush=True)
ok, msgs = tw.preview_set_impl(scene, str(rep["start"]))
check(ok, "preview ran", "; ".join(msgs)[:120])
log, txt = draw(sub)
check("in: " + str(rep["start"]) in txt, "the armed set is shown", txt)
check("tw.clear_preview" in txt, "Leave Preview appears once armed", txt)

print("=== state 5: pending renames ===", flush=True)
scene.tw_autorewrite = False
cuby = bpy.data.objects.get("Cuby")
check(cuby is not None, "the demo has a Cuby to rename")
old = cuby.name
cuby.name = "CubyRenamed"
found = tw.name_watch_scan(scene, force=True)
check(len(scene.tw_pending_renames) > 0, "the watcher filled the list",
      [(i.kind, i.old, i.new) for i in scene.tw_pending_renames])
log, txt = draw(sub)
check("break story refs" in txt, "the panel warns in words", txt)
check("tw.apply_renames" in txt, "Apply to Story is offered", txt)
for i in range(len(scene.tw_pending_renames)):
    item = scene.tw_pending_renames[i]
    row = FakeLayout([])
    for lt in ("DEFAULT", "COMPACT", "BOTH"):
        tw.TW_UL_renames.draw_item(_Row(lt), bpy.context, row, scene, item, 0,
                                   scene, "tw_pending_renames", i)
    check(len(row._log) >= 3,
          "list row %d draws in every layout type (%s %s->%s)"
          % (i, item.kind, item.old, item.new), len(row._log))
cuby.name = old
tw.name_watch_scan(scene, force=True)
scene.tw_pending_renames.clear()
log, txt = draw(sub)
check("break story refs" not in txt, "the warning clears with the list")

print("=== state 6: nothing active ===", flush=True)
log, txt = draw(None)
check("No 3D text object selected" in txt, "empty object state drawn", txt)

print("=== state 7: the v1 half with the baked plate ===", flush=True)
log, txt = draw(sub)
check("lines, " in txt, "health line drawn", txt)
check("tw.rebuild_keys" in txt and "tw.recover_backup" in txt,
      "recovery buttons drawn", txt)
check("Frame " in txt, "reveal read-out drawn", txt)

print("=== state 8: every set previews and draws ===", flush=True)
for s in sorted(str(k) for k in (rep.get("sets") or [])):
    ok, _m = tw.preview_set_impl(scene, s)
    log, txt = draw(sub)
    check(ok and "in: " + s in txt, "draws while previewing '%s'" % s)

scene.tw_preview_camera = False
scene.tw_story = story_real
shutil.rmtree(TMP, ignore_errors=True)
check(not os.path.isfile(os.path.join(TMP, "story.yml")), "temp project gone")
if start_sha:
    check(sha(BLEND) == start_sha, "talking_robots.blend was not modified")
print("=== RESULT ===", flush=True)
if FAILS:
    print("PANEL DRAW FAILURES (%d): %s" % (len(FAILS), FAILS), flush=True)
    sys.exit(1)
print("ALL PANEL DRAW TESTS PASSED", flush=True)
