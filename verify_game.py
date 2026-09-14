"""Headless check of the sets+actors demo blend (run: blender -b
talking_robots.blend --python verify_game.py). Enables the add-on from the
file next to the blend, cross-checks story.yml + dialogue.srt against the
scene, and reports wiring state. Read-only (no save)."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import story  # noqa: E402

import bpy  # noqa: E402


def main():
    spec = importlib.util.spec_from_file_location(
        "typewriter_subtitles",
        os.path.join(HERE, "typewriter_subtitles.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register()

    loaded = story.load_story_files(os.path.join(HERE, "story.yml"))
    assert loaded["errors"] == [], loaded["errors"]
    assert loaded["warnings"] == [], loaded["warnings"]
    cues, yml = loaded["cues"], loaded["story"]

    sc = bpy.context.scene
    assert mod.bl_info["version"] == (1, 8, 0), mod.bl_info["version"]
    last_end = max(c["end"] for c in cues.values())
    want_end = int(round(last_end * 24)) + 24
    assert (sc.frame_start, sc.frame_end) == (1, want_end), \
        (sc.frame_start, sc.frame_end)

    sub = bpy.data.objects["Subtitles"]
    menu = bpy.data.objects["ChoiceMenu"]
    gd = bpy.data.objects["GameDirector"]
    cam = bpy.data.objects["Camera"]
    assert len(sub.tw_entries) == len(cues), \
        (len(sub.tw_entries), len(cues))
    assert sub.tw_enabled is False
    assert len(sub.get("tw_backup", "")) > 100
    assert "game_subtitles.py" in bpy.data.texts
    assert sub.game.properties.get("Text") is not None
    assert menu.game.properties.get("Text") is not None
    assert sub.parent == cam and menu.parent == cam

    assert gd.game.properties.get("tw_branches") is None
    sp = gd.game.properties.get("tw_story")
    assert sp is not None and sp.value == "//story.yml", \
        (sp.value if sp is not None else None)
    assert sorted(yml["sets"]) == ["cuby", "main", "sphero"]
    assert len(yml["choices"]["pick"]["options"]) == 2
    for aname in yml["actors"]:
        o = bpy.data.objects.get(aname)
        assert o is not None, aname
        assert o.animation_data is not None and \
            o.animation_data.action is not None, aname
    assert cam.animation_data.action.name == "Camera_anim"

    for cn in ("Wide", "WideEnd", "Cuby", "Sphero"):
        assert cn in bpy.data.objects, cn
        assert bpy.data.objects[cn].data.lens == story.SHOT_LENS[cn], cn
    dad = cam.data.animation_data
    assert dad is not None and dad.action is not None
    acts = [cam.animation_data.action]
    if dad.action is not cam.animation_data.action:
        acts.append(dad.action)
    assert any(fc.data_path == "lens"
               for a in acts for fc in mod._action_fcurves(a))

    baked = sorted((o for o in bpy.data.objects
                      if o.name.startswith("SubLine")),
                     key=lambda o: o.name)
    assert len(baked) == len(cues), (len(baked), len(cues))
    for i, o in enumerate(baked):
        assert o.parent == cam, o.name
        assert len(o.modifiers) == 0, o.name
        assert o.tw_enabled is True, o.name
        assert len(o.tw_entries) == 1, o.name
        assert o.tw_entries[0].text == cues[i + 1]["text"], o.name
        assert o.animation_data is not None and \
            o.animation_data.action is not None, o.name

    mad = menu.animation_data
    assert mad is not None and mad.action is not None
    assert mad.action.name == "ChoiceMenu_anim"

    sens = gd.game.sensors.get("Always")
    ctrl = gd.game.controllers.get("Dialogue")
    assert sens is not None and ctrl is not None
    assert getattr(sens, "use_pulse_true_level", False) is True
    assert getattr(ctrl, "mode", None) == "MODULE"
    assert getattr(ctrl, "module", None) == "game_subtitles.update"
    assert ctrl in list(sens.controllers)

    marks = {m.name: m.frame for m in sc.timeline_markers}
    want_marks = story.marker_frames(yml, cues, 24, 1)
    assert len(marks) == len(want_marks), marks
    for name, f in want_marks.items():
        assert marks.get(name) == f, (name, marks)

    ad = cam.animation_data
    n_cam_keys = sum(len(fc.keyframe_points)
                     for fc in mod._action_fcurves(ad.action)
                     if fc.data_path in ("location", "rotation_euler"))
    assert n_cam_keys >= 8, n_cam_keys

    assert menu.data.body == \
        story.menu_body(yml["choices"]["pick"]["options"]), \
        repr(menu.data.body)

    sc.frame_set(30)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    vis = sorted(o.name for o in baked
                 if tuple(o.evaluated_get(dg).scale) == (1.0, 1.0, 1.0))
    assert vis == ["SubLine01"], vis
    assert bpy.data.objects["SubLine01"].data.body.startswith("CUBY: Hey!")

    print("VERIFY addon=1.8.0 frames=1-%d entries=%d backup=%dB subobjs=%d"
          % (sc.frame_end, len(sub.tw_entries),
             len(sub.get("tw_backup", "")), len(baked)))
    print("VERIFY sets=%s actors=%d markers=%d cam_keys=%d"
          % (sorted(yml["sets"]), len(yml["actors"]), len(marks),
             n_cam_keys))
    print("VERIFY text-internal=True tw_story=%r menu=%r"
          % (sp.value, menu.data.body.splitlines()[0]))
    print("VERIFY body@30=%r" % sub.data.body)
    print("VERIFY bricks: Always pulse -> Dialogue MODULE "
          "game_subtitles.update linked")


main()
