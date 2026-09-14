"""Verify load_post auto-repair: wipe entries+keys (backup stays), then run
tw_load_post() and check everything is restored (issue: subtitles vanish)."""
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
print("[LOADREPAIR] before wipe: entries=%d keys=%s backup=%s"
      % (len(sub.tw_entries), tw.keys_signature(sub),
         bool(sub.get("tw_backup"))))
# simulate loss: entries + keys gone, backup ID property survives as always
sub.tw_entries.clear()
tw._delete_fcurve(sub)
assert len(sub.tw_entries) == 0 and tw._find_fcurve(sub) is None
assert sub.get("tw_backup"), "backup should survive"
tw.tw_load_post()
print("[LOADREPAIR] after load_post: entries=%d" % len(sub.tw_entries))
for e in sorted(sub.tw_entries, key=lambda e: e.frame):
    print("[LOADREPAIR]   f%d uid%d %r" % (e.frame, e.uid, e.text))
print("[LOADREPAIR] keys=%s" % (tw.keys_signature(sub),))
assert len(sub.tw_entries) == 3
assert tw.keys_signature(sub) == ((1.0, 1.0), (10.0, 2.0), (20.0, 3.0))
print("[LOADREPAIR] AUTO-REPAIR OK")
