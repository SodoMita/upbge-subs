# HANDOFF — upbge-subs (Talking Robots + Typewriter Subtitles)

For the next agent session. Read this first, then `README.md`, then `story.yml`.

## What this repo is

- `typewriter_subtitles.py` — Blender add-on **v1.9.0**: timeline-keyed 3D
  typewriter subtitles **+ v2 story tools** (Preview Set, Refresh Sync,
  rename watch, validator, per-set bake).
- `story.py` + `story.yml` + `*.srt` + `story.sync.json` +
  `story.schema.json` — the animation-centric story engine (sets of
  animations, choices, loops) + shared parser/validation/rewrite helpers.
- `game_subtitles.py` — UPBGE game driver (Module: `game_subtitles.update`).
- `build_scene.py` / `resave_game.py` / `verify_game.py` — headless pipeline.
- `talking_robots.blend` — the demo scene (two robot actors, staged shot cams).
- Target engine: **UPBGE 0.50 (Blender 5.0.1)**, Linux x64.

## Current state (2026-09-15): v2 migration DONE, verified except on a GPU

The v1 master-timeline pipeline (single `dialogue.srt`, absolute frames, one
baked `Camera_anim`, one choice) is fully replaced by the v2 animation-centric
pipeline. All five queued items from the last handoff are done:

1. **Add-on 1.9.0** — Preview-set operator (cues→entries, set actions
   assigned with slots, opening-shot snap incl. lens, menu fill, auto-managed
   VSE sound strips, set-local frame range, `Scene.tw_preview_set`);
   fake-user guard on story actions; rename watch (invisible `_tw_uid` on
   objects+actions, depsgraph name-diff, pending list + **Apply to Story** +
   `tw_autorewrite`, targeted rewrite of `Obj@Act` / `camera:` / `[CAM]` only);
   **Refresh Sync** (`story.sync.json` + `story.schema.json`, also on save via
   `write_sync_files`, which stamps nothing so the file stays clean);
   validator text lists in the panel (no graph, per the veto); Bake to Objects
   per set (`<set>_Line##`); `sync_bake_visibility` hides other sets' bakes
   during a preview (set-local frames would stack) and live typing mirrors the
   bake (baked set -> its baked objects carry the text, unbaked set -> live
   typing on); `Jump to Set` removed (Preview Set replaced it).
   Subtitle/menu plates hang 0.45 m in front of the camera (small size, same
   apparent size) so close-ups can't occlude them - `SUB_LOCAL`/`MENU_LOCAL`
   in build_scene.py; a 3 m plate put the robots' faces in front of the line.
   Kept: entries/keys/live typing, import/export, save_pre/post,
   `setup_game_logic`, `_is_directive_line`, `TW_GAME_DRIVER_SOURCE` (tests
   pin the last two via AST/extract). Story `cps` is applied to the previewed
   subtitle object(s).
2. **build_scene.py v2** — additive/idempotent (create-if-missing; never
   deletes actions/keys; only clears factory-startup junk on a first build),
   per-set local-timed starter actions exactly matching the sidecar ranges
   (`main__*` [1,361], `cuby__*` [1,91], `sphero__*` [1,133]), staged cams
   carrying their own `lens` (Wide 50 / Cuby 55 / Sphero 45), **no** camera
   bake, **no** markers, plain menu object, idempotent bricks, preview+bake of
   the start set at the end.
3. **resave_game.py v2** — v2 story validation + `check_bindings` before it
   saves, sidecar refresh, generic-path self-test, keeps the no-`Text`-write
   rule.
4. **verify_game.py v2** — **86 checks, 0 failures** on the shipped .blend
   (story clean, sidecar present + ranges == live keys, uids cover every
   reference, bindings vs scene, per-set fake users, frame range = start set,
   no markers, camera unbaked, baked `<set>_Line##` per previewed set, menu
   plain, bricks 1/1, embedded driver byte-identical to disk, every set
   previews + bakes). It mutates the in-memory preview on purpose and never
   saves.
5. **Full chain re-run + stills + README** — done; `talking_robots_timeline.mp4`
   re-rendered from the v2 scene (start set), `test/v2_*.png` stills, README
   rewritten for the v2 workflow.

Suites green as of this commit: `test_story.py` (incl. new section 14:
diff_uids / rewrite_refs / sidecar write), `test_game_logic.py`,
`test_tw_game.py`, `test_addon_story.py` (NEW, runs inside Blender: check,
Refresh Sync, both previews, per-set bake, rename→Apply, auto-rewrite, camera
rename→`[CAM]` in .srt, refuses to rewrite a broken story),
`test_persistence.py` (MODE=A), `test_load_repair.py`, `test_ops.py`.

## Still open / known gaps (pick from here)

- **Nothing has ever run the game for real** (no GPU here): `playAction(name,
  f0, f1, …)` finding a *named* action among several on one object is only
  proven against the mocked `bge`. If a real UPBGE run shows actors not
  moving, that call is the suspect — the fallback would be assigning
  `animation_data.action` per set before playing (the preview already does
  exactly that, so `preview` correctness transfers).
- `game_debug.log` capture mode (`set_capture.py`) was not re-run for v2.
- **No audio content**: `./audio/` doesn't exist, so `audio:` anims and the
  speaker-strip path are only covered by the fake-`bge` tests. Blender 5 has
  no `bpy.data.sounds`/`AudioPreview`; the add-on uses
  `scene.sequence_editor.strips.new_sound(...)` + `scene.use_audio`.
- v1's eye-blink/master-timeline charm: blink keys are not part of any set
  action (`story.yml` has no eye actor). Adding one means a new `action:`
  line — which changes `plan["actions"]` counts that `test_story.py` pins.
- `test/` holds only v2 stills now (v1's `br_*`/`fx_*`/`st_*`/`v8*`/`test_*`
  frames were deleted: they showed the removed marker / baked-camera
  pipeline). Re-render them only as v2 frames.
- The old `talking_robots_timeline.mp4` (v1, push-in + cuts) was replaced by a
  start-set-only render; a "whole story" video needs the game capture, not the
  timeline.
- `inspect_bricks.py`, `resave_with_backup.py` are v1 leftovers, unused.

## Locked design decisions (do not relitigate without the user)

- Anim types: `subs` / `action` / `camera` / `audio`. Sets play anims from
  t=0; `subs` blocks on cue-span end; `action` blocks via `isPlayingAction`;
  camera/audio **never** block; `{at: s, wait: false}` for offsets/ambience.
- Options are key+label+target only (no `do:` — reactions live in the target
  set). Chains + loops via `goto`/`choice`; instant goto-cycles are errors.
- Per-set relative timing; ONE Blender scene; authoring = **Option B**
  (per-set local actions, Preview-set switches context, press P is the full
  preview). No master assembly, no bake compiler, no `frames:` ranges. Only
  the previewed set is baked (set-local frames would otherwise overlap).
- References are Blender **names** (custom-prop manual ids rejected as worse
  UI); renames handled by invisible auto-UIDs + watcher + Apply/auto-rewrite.
- SRT homes: `.` and `./subtitles/`. Audio: `./audio/` (`.` fallback),
  timeline preview via auto-managed speakers, `aud` in game, never blocking.
- VSCode JSON Schema (`story.schema.json`, regenerated on save) is the
  autocomplete vehicle. No `.rpy` (user: ".rpy not needed here"). No graph UI.
- Game property strings are length-capped → store paths only, never JSON.
- Stamping `_tw_uid` deliberately covers only what the story references
  (plus their actions), not every object in the file: fewer side effects,
  and Refresh Sync (also on save) adopts new references. A rename before the
  first sync after adding a reference is the accepted hole.

## Commands

```bash
cd /home/user/talking_robots
python3 test_story.py && python3 test_game_logic.py && python3 test_tw_game.py
U=/home/user/upbge/upbge-0.50-linux-x64
timeout 600 xvfb-run -a -s "-screen 0 1280x800x24" $U/blender --factory-startup -P build_scene.py
timeout 300 xvfb-run -a -s "-screen 0 1280x800x24" $U/blender talking_robots.blend -P resave_game.py
$U/blender -b talking_robots.blend -P verify_game.py
$U/blender -b --factory-startup -P test_addon_story.py
$U/blender -b talking_robots.blend -P register_addon.py -o //test/xa_ -f 30   # +addon
$U/blender -b talking_robots.blend -o //test/x_ -f 30                        # no addon
```

`/tmp/reg_addon.py` (recreate, /tmp does not persist; point it at the repo):

```python
import sys; sys.path.insert(0, "/home/user/upbge-subs")   # repo root
import typewriter_subtitles as tw; tw.register()
```

Idempotency check (a rebuild must never lose keys or duplicate bricks):
snapshot per-action keyframe counts + object/baked/brick counts, rebuild over
the saved file, then compare. Last run on the shipped files: IDENTICAL
(21 actions, 8 baked objects, 54 objects, 1 sensor, 1 controller) - and it is
what caught gotchas 10 and 11. The script:

```bash
cat > /tmp/keycount.py <<'PY'
import bpy, json, os
out = {}
for a in bpy.data.actions:
    out[a.name] = sum(len(fc.keyframe_points) for L in a.layers
                      for s in L.strips for cb in s.channelbags
                      for fc in cb.fcurves)
out["__objects"] = len(bpy.data.objects)
out["__baked"] = len([o for o in bpy.data.objects if "_Line" in o.name])
out["__sensors"] = len(bpy.data.objects["GameDirector"].game.sensors)
out["__controllers"] = len(bpy.data.objects["GameDirector"].game.controllers)
out["__frames"] = "%d-%d" % (bpy.context.scene.frame_start,
                             bpy.context.scene.frame_end)
out["__markers"] = len(bpy.context.scene.timeline_markers)
open("/tmp/count_%s.json" % os.environ.get("TAG", "x"), "w").write(
    json.dumps(out, indent=1, sort_keys=True))
print("COUNTS written", flush=True)
PY
U=/home/user/upbge/upbge-0.50-linux-x64
TAG=before $U/blender -b talking_robots.blend -P /tmp/keycount.py
xvfb-run -a -s "-screen 0 1280x800x24" \
    $U/blender talking_robots.blend -P build_scene.py     # additive refresh
TAG=after $U/blender -b talking_robots.blend -P /tmp/keycount.py
diff /tmp/count_before.json /tmp/count_after.json && echo IDENTICAL
```

Stills: `test/` now holds v2 frames only (the v1 `br_*`/`fx_*`/`st_*`/`v8*`
shots were dropped - they showed the removed markers + baked-camera pipeline).
A single set-preview frame comes from `preview_set_impl` + one frame render:

```bash
cat > /tmp/still.py <<'PY'
import bpy, os, sys
sys.path.insert(0, "/home/user/upbge-subs")
import typewriter_subtitles as tw
if not hasattr(bpy.types.Object, "tw_entries"):
    tw.register()
SET = os.environ.get("STILL_SET", "")
if SET:
    ok, msgs = tw.preview_set_impl(bpy.context.scene, SET)
    print("[STILL]", SET, ok, "; ".join(msgs)[:120], flush=True)
    bpy.context.scene.frame_set(min(30, bpy.context.scene.frame_end))
    bpy.context.view_layer.update()
# no quit_blender(): a -b run exits on its own (and must not before -f)
PY
STILL_SET=cuby $U/blender -b talking_robots.blend -P /tmp/still.py \
    -o //test/v2_cuby_ -F PNG -f 30
```


## Gotchas (earned the hard way — respect all of them)

1. **NEVER bpy-write the `Text` game property.** `prop.value = …` on a `Text`
   prop corrupts UPBGE 0.50 state → deterministic segfault on a later scene op
   (bisected over ~10 probes). Create/read OK; the game driver rewrites values
   every tick via the KX path (safe). `tw_story` is a *String* prop — assigning
   it is fine.
2. **Env amnesia:** `/home/user/upbge`, `xvfb`, `libpulse0`, `ffmpeg` vanish on
   snapshot restore. Reinstall: `sudo apt-get update && sudo apt-get install
   -y xvfb libpulse0 ffmpeg` (the `update` is required or ffmpeg's deps 404),
   then redownload `upbge-0.50-linux-x64.tar.xz` from
   github.com/UPBGE/upbge/releases (tag v0.50, ~408 MB, unpacks to
   `upbge-0.50-linux-x64/`).
3. **Never two edits to the SAME file in one parallel block** — the merge
   corrupts (duplicated tail observed). Batch across different files only.
   Prefer `python3 - <<'EOF'` patch scripts with an `assert count == 1` on
   each anchor over blind `edit_file` on big files: an edit_file on this
   add-on silently ate a `def draw_item` line and only showed up as a
   `NameError: name 'self' is not defined` at import.
4. **Chunk large file writes.** A ~13 KB `write_file` was truncated mid-file
   once (literal `...[truncated N chars]` marker on disk); tool output also
   caps around 10 KB, so write big files by appending pieces and always
   `python3 -m py_compile` + run tests after.
5. **`_log` and `_scene_obj` in `typewriter_subtitles.py` are NOT module
   functions** — they exist only inside the `TW_GAME_DRIVER_SOURCE` string
   (and in `game_subtitles.py`). The add-on's own console helper is `_say()`.
   Grepping the file is not enough; check the line numbers against the string.
6. Blender probes must quit (`blender -b … --python` exits; scripts run under
   xvfb Blender must call `bpy.ops.wm.quit_blender()` or they hang to timeout).
7. Shell: `cmd | grep … && next` dies when grep is empty — use `;` + banners.
   Raw Blender tracebacks say `File "…", line N` (grep `Traceback`, not `line`).
8. Blender 5: actions are layered/slotted — `action.fcurves` is EMPTY legacy
   API; walk `layers → strips → channelbags` (use `_action_fcurves`);
   `Action.channelbag()` does not exist in this build; `action.slots.new(
   id_type='OBJECT', name=...)` is the creator (slot `identifier` =
   `"OB"+name`, `slot.name` does not exist). **Assign `animation_data.action`
   BEFORE `action_slot`** or Blender raises "This slot does not belong to the
   assigned Action". Separate Action datablocks on one object keep their own
   keys when swapped (verified) — that is what makes per-set actions safe.
9. `bpy.data.sounds` / `AudioPreview` / `screen.animation_playback` are gone
   in Blender 5. Timeline audio = `scene.sequence_editor_create()`,
   `strips.new_sound(name, filepath, channel, frame_start)` (a `SoundStrip`
   has `frame_start`/`volume`/`mute`, **no** `frame_end`), plus
   `scene.use_audio`.
10. `bake_typewriter` must drop its own orphaned `<prefix>*` curve/mesh blocks
    (and zero-user `<prefix>*_anim` actions, never fake-user ones) before
    re-baking, or the previous bake's orphan data squats the names and every
    re-bake produces `main_Line01.001`.
11. `live_action_ranges()` skips actions with `users == 0 and not
    use_fake_user`, so orphaned bake actions never pollute `story.sync.json`.
12. Sorted subtitle frames: reveal is `int(elapsed_frames * cps / fps)`, so a
    cue shows **0 chars on its own start frame** — a test asserting "typed
    text at the first cue frame" is wrong, not the code. `tw_cps` defaults to
    15; the story's `cps` is applied to the object by Preview Set.
13. `sorted(bpy objects)` → TypeError; always `key=lambda o: o.name`.
14. Animated values: read via `evaluated_depsgraph_get()` + `evaluated_get`,
    never raw props. Pre-first-key CONSTANT extrapolation HOLDS the first key.
15. `Curve.body` is NOT animatable; Build modifier reveals font faces
    tail-first (never use for typing — baked one-liners + live typing won).
16. Game-object discovery: `scene.objects[name]` with linear fallback;
    `KX_FontObject.text` + `["Text"]` both writable in-game (KX path safe).
17. `test*.py` run order-independent; `game_debug.log` is written next to the
    story in tests — integration cleans `HERE/game_debug.log`, keep that.
18. The ChoiceMenu is **not** shown by writing its body at preview time — the
    first v2 render had the menu on every frame because of that. `menu_preview`
    only arms `menu["_tw_menu_choice"]`; `_menu_tick()` (called from the frame +
    depsgraph handlers and on load) decides visibility: shown when
    `frame >= scene.frame_end - max(2, round(fps*MENU_LEAD))` and the armed text
    is non-empty, hidden otherwise, and never when the menu has its own action.
19. A **baked set must keep its source plate silent**: `preview_set_impl`
    clears `tw_enabled` *and* `data.body` for the multi-line plate when
    `<set>_Line##` objects exist, and `tw_save_pre` re-clears multi-entry
    plates on save. Without that, the file keeps the last live body and every
    render (with or without the add-on) draws it over the baked cues — that is
    what the garbled `test/v2_noaddon_*.png` looked like before this rule.
20. `Scene.tw_uid_seq` (int ID property) is the uid counter; `name_watch_scan`
    skips automatic work when `bpy.app.background` (so headless builds stay
    deterministic) unless called with `force=True` — that is how
    `test_addon_story.py` drives the watcher.

## Related: UPVN (github.com/SodoMita/UPVN)

Ren'Py-like VN framework for UPBGE (v0.6.x, 391 files, 335 tests): direct
`.rpy` parsing (explicitly no YAML), `blf` dialogue box, 2D planes + 3D stage
stubs, full label/menu/jump/vars/save. Comparison done 2026-09-15: UPVN is a
platform (script-first), this repo is a feature + 3D short (timeline-first);
they converge from opposite ends. UPVN may borrow the 3D typewriter renderer
and the set-preview idea later. Its `.rpy` approach was explicitly rejected
FOR THIS repo — do not "unify" them without the user.

## Git

- Remote: `github.com/SodoMita/upbge-subs` (push needs a PAT — ask the user;
  never commit tokens; use a `/tmp` askpass helper, never a stored remote URL
  with credentials). Default branch `main`.
- History: `08c50b9` v1.8.0 talking-robots demo → `6ccdb28` v2 core (parser,
  driver, content, tests) → this commit (v1.9.0 add-on story tools + v2
  scene pipeline + docs).
- Repo hygiene: `*.blend1`, `persist_*.blend`, `render/`, `capture/`,
  `*debug.log` and `*.tmp` writes are git-ignored; `story.sync.json` and
  `story.schema.json` ARE committed (the game reads the sidecar without the
  add-on, and `verify_game.py` compares it against the live scene).
