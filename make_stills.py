#!/usr/bin/env python3
"""Render the README stills for the v2 demo. Read-only: it never saves.

    blender -b talking_robots.blend -P make_stills.py            # all four
    MODE=noaddon blender -b talking_robots.blend -P make_stills.py

noaddon  v2_main_f30.png      frame 30 of the start set as the BAKED cards
                              render it with no add-on at all (full-line card)
         v2_main_f200.png     frame 200 - the set keeps going, still no add-on
addon    v2_main_f30_addon.png frame 30 with the add-on registered: the same
                              card mid-type (the typing proof - the baked body
                              and the typed body are logged so you can diff
                              them without opening the images)
         v2_menu_cuby.png     the choice menu proof: set 'cuby' previewed and
                              its menu posed with the real pick2 prompt +
                              options, at the frame where the game shows it
                              (cuby gates on a blocking action, so Preview Set
                              parks the menu - this poses what P displays)
"""
import bpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story as sb                     # noqa: E402

MODE = os.environ.get("MODE", "all")
OUT = os.path.join(HERE, "test")
STORY = os.path.join(HERE, "story.yml")


def log(m):
    print("[STILL] %s" % m, flush=True)


def want(name):
    return MODE in ("all", name)


def render(scene, path, frame):
    scene.render.filepath = path
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)
    log("wrote %s (frame %d)" % (os.path.basename(path), frame))


def main():
    os.makedirs(OUT, exist_ok=True)
    scene = bpy.context.scene
    start = sb.load_story_files(STORY)["story"].get("start")
    # the preview indicator is an add-on property, so it only exists in a
    # session where the add-on is registered - fall back to the story's start
    prev = getattr(scene, "tw_preview_set", "") or start
    if want("noaddon"):
        log("=== no add-on: the baked cards must play and render alone ===")
        log("add-on registered in this session: %s"
            % hasattr(bpy.types.Object, "tw_entries"))
        # only plain ID properties and real Blender data here: tw_entries is
        # an add-on PropertyGroup and does not exist without the add-on
        baked = sorted((o for o in scene.objects if o.get("_tw_bake") == prev),
                       key=lambda o: o.name)
        log("set '%s': %d baked line object(s)" % (prev, len(baked)))
        for o in baked:
            body = o.data.body.replace("\n", " / ") if o.data else ""
            log("  %-13s hide_render=%-5s body=%r"
                % (o.name, o.hide_render, body))
        if not baked:
            raise SystemExit("no baked objects for set '%s' - run "
                             "build_scene.py first" % prev)
        render(scene, os.path.join(OUT, "v2_main_f30.png"), 30)
        render(scene, os.path.join(OUT, "v2_main_f200.png"), 200)

    if want("addon"):
        log("=== add-on registered: live typing + the choice menu ===")
        import typewriter_subtitles as tw
        if not hasattr(bpy.types.Object, "tw_entries"):
            tw.register()
            log("add-on registered v%s"
                % ".".join(str(x) for x in tw.bl_info["version"]))
        # typing proof: what the handler has typed at frame 30
        ok, msg, info = tw.preview_set_impl(scene, "main")
        log("preview main: %s" % msg)
        sub = tw.role_object(scene, tw._ROLE_SUBS, ("Subtitles",))
        render(scene, os.path.join(OUT, "v2_main_f30_addon.png"), 30)
        baked = sorted((o for o in scene.objects if o.get("_tw_bake") == "main"),
                       key=lambda o: o.name)
        for o in baked[:1]:
            log("  baked body : %r" % (o.tw_entries[0].text
                                       if o.tw_entries else ""))
            log("  typed body : %r" % o.data.body)
            log("  -> the add-on types the card; without it the full line "
                "shows (compare v2_main_f30.png)")

        # menu proof: pose what the game shows at cuby's choice point
        ok, msg, info = tw.preview_set_impl(scene, "cuby")
        log("preview cuby: %s" % msg)
        loaded = sb.load_story_files(STORY)
        story = loaded["story"]
        plan = info["plan"]
        kind, target = plan["end"]
        menu = tw.role_object(scene, tw._ROLE_MENU, ("ChoiceMenu",))
        if kind == "choice" and menu is not None:
            ch = story["choices"][target]
            body = plan["prompt"] or ""
            opts = ch.get("options") or []
            body = (body + "\n\n" + sb.menu_body(opts)) if body else \
                sb.menu_body(opts)
            menu.data.body = body
            menu.scale = (1.0, 1.0, 1.0)
            log("  menu posed for choice '%s' with %d option(s)"
                % (target, len(opts)))
            render(scene, os.path.join(OUT, "v2_menu_cuby.png"),
                   scene.frame_end)
        tw.preview_set_impl(scene, "main")     # leave it as the file has it
    log("done")


main()
