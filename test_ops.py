"""End-to-end operator test (background): polls + rebuild + recover ops."""
import bpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import typewriter_subtitles as tw

if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()

sub = bpy.data.objects.get("SubTest")
assert sub is not None
bpy.context.view_layer.objects.active = sub
print("[OPS] poll rebuild:", bpy.ops.tw.rebuild_keys.poll())
print("[OPS] poll recover:", bpy.ops.tw.recover_backup.poll())
tw._delete_fcurve(sub)
print("[OPS] keys wiped, entries:", len(sub.tw_entries))
r = bpy.ops.tw.rebuild_keys()
print("[OPS] rebuild result:", r, "keys:", tw.keys_signature(sub))
assert r == {'FINISHED'} and len(tw.keys_signature(sub)) == 3
sub.tw_entries.clear()
tw._delete_fcurve(sub)
r = bpy.ops.tw.recover_backup()
print("[OPS] recover result:", r, "entries:", len(sub.tw_entries))
assert r == {'FINISHED'} and len(sub.tw_entries) == 3
scene = bpy.context.scene
scene.frame_current = 12
tw.update_object(sub, scene)
print("[OPS] body@12:", repr(sub.data.body))
assert sub.data.body == "Se"
print("[OPS] ALL OPS TESTS PASSED")
