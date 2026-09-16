# HANDOFF — upbge-subs (Talking Robots + Typewriter Subtitles)

For the next agent session. Read this first, then `README.md`, then `story.yml`.

## What this repo is

- `typewriter_subtitles.py` — Blender add-on **v1.9.1**: timeline-keyed 3D
  typewriter subtitles **+ v2 story tools** (Preview Set + the game-parity
  preview camera follow, Leave Preview, Refresh Sync, rename watch, validator,
  per-set bake).
- `story.py` + `story.yml` + `*.srt` + `story.sync.json` +
  `story.schema.json` — the animation-centric story engine (sets of
  animations, choices, loops) + shared parser/validation/rewrite helpers.
- `game_subtitles.py` — UPBGE game driver (Module: `game_subtitles.update`).
- `build_scene.py` / `resave_game.py` / `verify_game.py` — headless pipeline.
- `make_stills.py` — renders `test/*.png` **and** verifies the committed ones
  pixel-for-pixel (`MODE=noaddon|addon|sets|camera`, `OVERWRITE=1` to accept).
- `test_panel_draw.py` — drives the panel `draw()` in a fake layout (the only
  test that reaches the UI code).
- `talking_robots.blend` — the demo scene (two robot actors, staged shot cams).
- Target engine: **UPBGE 0.50 (Blender 5.0.1)**, Linux x64.

## Current state (2026-09-16): v2 done, side branches merged, verified except on a GPU

### Merged from `dev-v2` + `v2-migration` (both branched off `6ccdb28`, the
same base as main, and each re-implemented the same 5 queued items)

Main was already the superset for the *pipeline*; what these two branches still
had that was worth taking, and what was deliberately **not** taken:

**Taken**
- `dev-v2`: the **per-frame preview camera follow** (`preview_camera_tick`,
  `camera_pose_at`, `_mat_close`, `_mtime_of`): poses the render camera as a
  pure function of the frame from the previewed set's shots, with the game's
  0.5 s smoothstep. Main only snapped the opening shot, so a scrubbed timeline
  or a render showed one framing while the game cut. Gated on a new
  `Scene.tw_preview_camera` checkbox, restricted to the previewed range, and it
  steps aside when the camera has its own action (same rule as the menu).
  Rewired to run from `frame_change_post` + `load_post` **only** — the
  depsgraph site was dropped on purpose, or editing any object would snap the
  view back under the mouse.
- `v2-migration`: `make_stills.py` (proof stills as a committed script instead
  of a `/tmp` throwaway), `test_panel_draw.py` (headless `draw()` test), and the
  **build-twice fingerprint proof** inside `verify_game.py` (a hand tweak to a
  generated action's keys, then a rebuild in a throwaway copy of the project,
  then a digest comparison — plus the "the repo's own .blend was untouched"
  sha256 assertion). Main had the same idea as a scratch script that does not
  survive a snapshot.
- `v2-migration`: slot binding hardening — prefer `action_suitable_slots`, then
  the slot named `OB<name>`, then a slot with the right identifier prefix, and
  only then create one. In 5.0.1 `ActionSlot.id_type` does **not** exist (use
  the `identifier` prefix) but `AnimData.action_suitable_slots` does and is what
  keeps a shape-key (`ME…`) slot from being bound to an object.
- `v2-migration` docs: three gotchas main had not written down (below:
  `open_mainfile` segfault, `Matrix.identity()`, animated-vs-rest-pose proofs).

**Checked and rejected** (all three verified against this build, not assumed)
- Their `_load_sound` / speaker-dedupe helpers iterate `bpy.data.sounds` and
  call `bpy.data.sounds.load()` — **that API is gone in Blender 5**, so the
  helper silently returns `None`. Main's `strips.new_sound` path is the one that
  works.
- Their `promote_sidecar_names` (re-record uid names after a rename so Apply does
  not immediately re-report it) duplicates main's `rewrite_story_refs` →
  `refresh_sync_impl` call.
- `TW_OT_jump_to_set` just called `preview_set_impl` (the marker op was deleted
  in v2 on purpose), `TW_OT_bake_set` re-exposed the per-set bake prefix the
  Preview Set already owns, and `TW_OT_clear_preview` there *cleared*
  `tw_preview_set`, which would hide every baked line and blank the render —
  main's version re-arms the start set instead and only switches the follow off.
- Their `VIEW3D_PT_tw_story` panel + `_fill_report` report box: main already
  shows the same text lists in the Story box (and the veto on graph UI is
  respected in both).
- Their `test/` re-adds the v1 stills (`br_*`, `fx_*`, `st_*`, `v8*`, `test_*`)
  that show the deleted marker / baked-camera pipeline; their `.blend`, `.mp4`
  and README/HANDOFF rewrites are rival versions of the same work and are
  *behind* main (no `diff_uids`/`rewrite_refs`/`sidecar_payload` in `story.py`,
  no `test_addon_story.py` in `dev-v2`, fewer verify checks).
  Nothing is left in either branch that main does not have; both can be deleted.

### The five queued items this all came from

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
4. **verify_game.py v2** — **117 checks, 0 failures** on the shipped .blend
   (was 86; +15 preview-camera checks, +16 build-twice/idempotency checks)
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
`test_persistence.py` (MODE=A/B/C/R), `test_load_repair.py`, `test_ops.py` (all
against `persist_A.blend` — it needs the `SubTest` object that mode A creates),
and `test_panel_draw.py` (10 states, and it asserts the .blend was not modified).
`make_stills.py` reproduces all 9 committed stills byte-for-byte in IDAT terms
(pixel-identical); re-running it is the media regression check.

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
  timeline. It was rendered **without** the add-on, so it holds each set's
  opening framing: with v1.9.1 the same command (step 4 in README, which passes
  `register_addon.py`) also cuts between shots — re-rendering it is ~40 min of
  2-vCPU Cycles for 361 frames and nothing else in the repo depends on it.
  `test/v2_cam_hold.png` vs `v2_cam_follow.png` is the cheap proof.
- `inspect_bricks.py`, `resave_with_backup.py` are v1 leftovers, unused.
- `verify_game.py`'s build-twice proof needs a re-executable Blender
  (`bpy.app.binary_path`) and ~2 spare GB; it skips (loudly, with a `SKIP` log
  line, not a failure) otherwise, and `TW_SKIP_BUILD_TWICE=1` skips it on
  purpose. On this box all four child runs cost ~3 s.

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
$U/blender -b talking_robots.blend -P test_panel_draw.py     # panel draw states
$U/blender -b --factory-startup -P test_persistence.py        # MODE=A first
MODE=B $U/blender -b persist_A.blend -P test_persistence.py   # no add-on
MODE=C $U/blender -b persist_B.blend -P test_persistence.py   # repair on load
MODE=R $U/blender -b persist_A.blend -P test_persistence.py   # rebuild/recover
$U/blender -b persist_A.blend -P test_load_repair.py         # needs SubTest
$U/blender -b persist_A.blend -P test_ops.py                 # ditto
$U/blender -b talking_robots.blend -P make_stills.py         # render + verify
$U/blender -b talking_robots.blend -P register_addon.py -o //test/xa_ -f 30   # +addon
$U/blender -b talking_robots.blend -o //test/x_ -f 30                        # no addon
rm -f persist_*.blend && git status --short                   # scratch cleanup
```

`test_persistence.py` MODE=A writes `persist_A.blend` **in the repo dir** and
(the load/save handlers fire) used to rewrite `story.sync.json` from that
scratch scene; the guard in `write_sync_files` is what stops that now — check
`git status` after the persistence chain anyway. `register_addon.py` is
committed for one-off background renders (`/tmp` scripts do not survive a
snapshot, which is why `make_stills.py` exists).

Idempotency / no-clobber proof: **committed in `verify_game.py`** as
`build_twice()` (merged from `v2-migration`, which is how it was proven there
instead of in a throwaway script). It copies the project to a temp dir, nudges
every inner key of the story's first per-set action and saves, re-runs
`build_scene.py` over that file, then compares an action-key fingerprint
(digest + counts + `_tw_uid` stamps + frame range + fake users + ranges vs the
sidecar) and asserts the repo's own `.blend` kept the same sha256. Skip it with
`TW_SKIP_BUILD_TWICE=1`; it also skips itself (with a `SKIP` line, not a failure)
when `bpy.app.binary_path` or `xvfb-run` is unavailable. Last full run:
**117 ok / 0 failed**, and the rebuild left all 21 actions untouched.

The older manual version of that check (per-action key counts before/after a
rebuild, then `diff`) still works if you want to eyeball it: snapshot
`{action: sum(len(kf))}` from `a.layers → strips → channelbags → fcurves`,
rebuild with `xvfb-run $U/blender talking_robots.blend -P build_scene.py`,
snapshot again, compare. It reported IDENTICAL (21 actions, 8 baked objects,
54 objects, 1 sensor, 1 controller) and it is what caught gotchas 10 and 11.

Stills: `test/` holds v2 frames only (the v1 `br_*`/`fx_*`/`st_*`/`v8*`
shots were dropped — they showed the removed markers + baked-camera pipeline,
and both side branches still carry them). `make_stills.py` renders every one of
them and compares the result with the committed file, so a still can never rot
silently; `MODE=camera` is the preview camera follow pair, `OVERWRITE=1` accepts
a deliberate change (read the two PNGs first - see gotcha 27).

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

21. `write_sync_files` (the save handler) refuses to write unless the scene
    actually holds the story's objects: saving a scratch .blend in the same
    folder as someone's `story.yml` used to rewrite `story.sync.json` with the
    scratch scene's actions (`test_persistence.py` MODE=A did exactly that to
    the repo copy once - restore with git if it ever happens again). The
    explicit **Refresh Sync** button always writes (user intent). `test_addon_
    story.py` section 10 pins both directions.

22. **Never `bpy.ops.wm.open_mainfile()` inside a UI session** on UPBGE 0.50:
    it segfaults at teardown (open + quit with nothing between exits 139) and a
    later `bpy.ops.object.game_property_new` hangs. Loading the same file as a
    command-line ARGUMENT is clean — which is why `verify_game.build_twice`
    re-execs with `bpy.app.binary_path`, **not** `sys.executable` (under UPBGE
    that is the bundled python, which tries to run the `.blend` as a script).
    (from `v2-migration`'s HANDOFF)
23. `Matrix.identity()` mutates in place and returns `None` — never assign its
    result. (also from `v2-migration`)
24. Comparing an animated value against the **rest pose** is a weak evaluation
    proof: an idle bob can be exactly `sin(36*pi) == 0` at its last frame.
    Compare the evaluated depsgraph value against what the ACTION keys say at
    that frame. (also from `v2-migration`)
25. A preview camera tick must **not** be wired into `depsgraph_update_post`.
    It writes the camera, so the depsgraph re-fires; converged by an epsilon
    compare, but any unrelated edit would still snap the camera back to the
    shot. `frame_change_post` + `load_post` is enough (and the reason
    `preview_camera_tick` returns False right after a `frame_set`: the handler
    already posed it — assert on the **pose**, not the return value, or you get
    a test that fails while the code is right; that is exactly how the first
    version of `test_addon_story.py` section 11 read).
26. Two more Blender 5.0.1 API facts, both hit while merging:
    `ActionSlot.id_type` does not exist (match `slot.identifier` prefixes:
    `OB`=object, `ME`=mesh/shape-key), and RNA collection elements come back as
    fresh wrappers, so `action.slots[i] is the_slot` is False — compare with
    `==`. `Mesh.shape_key_add()` is gone too, so probe slot behaviour without
    shape keys.
27. Blender embeds a render timestamp in a PNG `tEXt` chunk, so **file hashes
    differ for identical pixels**. `make_stills.py` therefore hashes the
    concatenated `IDAT` payloads (and a whole-image check needs
    `ffmpeg -lavfi psnr`, where `inf` means identical). Do not "fix" a
    mismatched still by force-overwriting it: read the two images first —
    `v2_main_0200.png` legitimately changed because the camera follow now cuts
    there, which is the feature working, not drift.
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
  driver, content, tests) → `7df54f7..cbb8e65` v1.9.0 add-on story tools + v2
  scene pipeline + docs → v1.9.1 (this), which merges everything of value out of
  the two side branches (`dev-v2` 9fffaf7, `v2-migration` 59efed1 = PR #1) that
  re-implemented the same five queued items. Both are now fully consumed and can
  be deleted (PR #1 closed) — nothing else in them is worth taking: see
  "Checked and rejected" above.
- The v1.9.1 merge is four commits (`63eced1` addon, `043df84` verify,
  `35f26b0` tooling + the regenerated `v2_main_0200.png`, then docs). As
  of writing they are LOCAL ONLY — the sandbox has no push credential, so
  `git push` still has to happen (with a fresh PAT from the user, via a `/tmp`
  askpass helper). If they are gone from `origin/main`, that is the step that
  was never taken.
- `git merge` was deliberately not used for that: every one of their three
  files (`build_scene.py`, `verify_game.py`, `typewriter_subtitles.py`) is a
  rival rewrite of main's verified version, so a merge would have regressed the
  pipeline while resolving. The valuable pieces were ported and re-tested
  instead.
- Repo hygiene: `*.blend1`, `persist_*.blend`, `render/`, `capture/`,
  `*debug.log` and `*.tmp` writes are git-ignored; `story.sync.json` and
  `story.schema.json` ARE committed (the game reads the sidecar without the
  add-on, and `verify_game.py` compares it against the live scene).
