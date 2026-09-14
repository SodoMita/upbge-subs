"""Persistence / recovery diagnostics for Typewriter Subtitles.

MODE=A: build a 3-entry subtitle object, save persist_A.blend (with addon).
MODE=B: open A WITHOUT the addon, inspect, save persist_B.blend.
MODE=C: open B, register the addon, run load_post repair, report.
MODE=R: open A, register the addon, test rebuild-keys + recover-from-backup.
"""
import bpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODE = os.environ.get("MODE", "A")


def log(m):
    print("[PERSIST-%s] %s" % (MODE, m), flush=True)


if MODE in ("A", "C", "R"):
    sys.path.insert(0, HERE)
    import typewriter_subtitles as tw
    if not hasattr(bpy.types.Object, "tw_entries"):
        tw.register()
        log("addon registered v%s"
            % (".".join(str(x) for x in tw.bl_info.get("version", ())),))

if MODE == "A":
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    scene = bpy.context.scene
    scene.render.fps = 24
    scene.frame_start = 1
    bpy.ops.object.text_add(location=(0, 0, 0))
    sub = bpy.context.active_object
    sub.name = "SubTest"
    sub.tw_enabled = True
    sub.tw_cps = 30.0
    sub.tw_reveal = 'LINEAR'
    for f, t in [(1, "Hello one"), (10, "Second line here"),
                 (20, "Third and last")]:
        tw._create_entry(sub, f, t)
    log("entries=%d" % len(sub.tw_entries))
    ad = sub.animation_data
    act = ad.action if ad else None
    log("action=%r legacy_fcurves=%s slotted_layers=%s"
        % (act is not None,
           hasattr(act, 'fcurves') if act else '-',
           hasattr(act, 'layers') if act else '-'))
    if act is not None and hasattr(act, 'layers'):
        for layer in act.layers:
            for strip in layer.strips:
                names = [a for a in dir(strip) if 'channel' in a.lower()]
                log("strip channel attrs: %s" % names)
                try:
                    bags = list(strip.channelbags)
                    for b in bags:
                        log("bag fcurves=%s"
                            % [fc.data_path for fc in b.fcurves])
                except Exception as ex:
                    log("channelbags FAIL: %r" % ex)
    fc = tw._find_fcurve(sub)
    log("find_fcurve=%s sig=%s" % (bool(fc), tw.keys_signature(sub)))
    if fc is not None:
        log("interp=%s" % [k.interpolation for k in fc.keyframe_points])
    log("backup_bytes=%d" % len(sub.get("tw_backup") or ""))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "persist_A.blend"))
    log("saved A")
elif MODE == "B":
    sub = bpy.data.objects.get("SubTest")
    log("obj=%s addon_registered=%s"
        % (bool(sub), hasattr(bpy.types.Object, 'tw_entries')))
    if sub is not None:
        log("id_props=%s" % [k for k in sub.keys() if not k.startswith('_')])
        log("has_backup=%s body=%r"
            % (bool(sub.get("tw_backup")),
               sub.data.body if sub.type == 'FONT' else None))
        ad = sub.animation_data
        nfc = 0
        if ad is not None and ad.action is not None:
            try:
                for layer in ad.action.layers:
                    for strip in layer.strips:
                        for bag in strip.channelbags:
                            nfc += len(bag.fcurves)
            except Exception as ex:
                log("slotted walk FAIL: %r" % ex)
        log("fcurves=%d" % nfc)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "persist_B.blend"))
    log("saved B (without addon)")
elif MODE == "C":
    sub = bpy.data.objects.get("SubTest")
    log("before: entries=%d backup=%s"
        % (len(sub.tw_entries), bool(sub.get("tw_backup"))))
    tw.tw_load_post()
    log("after load_post: entries=%d" % len(sub.tw_entries))
    for e in sorted(sub.tw_entries, key=lambda e: e.frame):
        log("  f%d uid%d %r" % (e.frame, e.uid, e.text))
    fc = tw._find_fcurve(sub)
    log("keys=%d sig=%s"
        % (len(fc.keyframe_points) if fc else 0, tw.keys_signature(sub)))
    scene = bpy.context.scene
    scene.frame_current = 12
    tw.update_object(sub, scene)
    log("body@12=%r" % sub.data.body)
elif MODE == "R":
    sub = bpy.data.objects.get("SubTest")
    scene = bpy.context.scene
    tw._delete_fcurve(sub)
    log("R1 keys_deleted=%s entries=%d"
        % (tw._find_fcurve(sub) is None, len(sub.tw_entries)))
    n = tw._rebuild_keys_impl(sub)
    log("R1 rebuilt=%d sig=%s" % (n, tw.keys_signature(sub)))
    sub.tw_entries.clear()
    tw._delete_fcurve(sub)
    log("R2 wiped entries=%d backup=%s"
        % (len(sub.tw_entries), bool(sub.get("tw_backup"))))
    n = tw._recover_from_backup_impl(sub, scene)
    log("R2 recovered=%d" % n)
    for e in sorted(sub.tw_entries, key=lambda e: e.frame):
        log("  f%d uid%d %r" % (e.frame, e.uid, e.text))
    log("R2 keys=%s" % (tw.keys_signature(sub),))
