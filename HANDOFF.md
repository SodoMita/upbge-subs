# HANDOFF — upbge-subs (Talking Robots + Typewriter Subtitles)

For the next agent session. Read this first, then `README.md`, then `story.yml`.

## What this repo is

- `typewriter_subtitles.py` — Blender add-on: timeline-keyed 3D typewriter
  subtitles (SRT/VTT link, live typing, Bake to Objects, game-logic export).
- `story.py` + `story.yml` + `*.srt` + `story.sync.json` — animation-centric
  story engine (sets of animations, choices, loops).
- `game_subtitles.py` — UPBGE game driver (Module: `game_subtitles.update`).
- `build_scene.py` / `resave_game.py` / `verify_game.py` — headless pipeline.
- `talking_robots.blend` — the demo scene (two robot actors, staged cams).
- Target engine: **UPBGE 0.50 (Blender 5.0.1)**, Linux x64.

## Current state (2026-09-15): v2 migration DONE — all 5 QUEUED items shipped

Branch `v2-migration`. Every suite green on UPBGE 0.50 (Blender 5.0.1):
`test_story.py` / `test_game_logic.py` / `test_tw_game.py` (pure Python),
`test_addon_story.py` (132 checks), `test_panel_draw.py` (25),
`test_persistence.py` MODE A/B/C/R + `test_load_repair.py` + `test_ops.py`,
`resave_game.py` (134 checks), `verify_game.py` (133 checks).

1. **Add-on v1.9.0** — Preview Set (set-local frame range, assign/detach set
   actions, load the set's `.srt` cues, snap the opening shot + lens, pose or
   park the menu, auto-manage speakers, `tw_preview_set`/`tw_preview_range`
   indicators), fake-user guard, invisible `_tw_uid` rename watcher (depsgraph
   diff, pending list, Apply → targeted YAML rewrite, `tw_autorewrite`),
   Refresh Sync (`story.sync.json` + `story.schema.json`, also on save),
   validator as text lists only, per-set Bake `<set>_Line##`. Tests pin
   `_is_directive_line` + `TW_GAME_DRIVER_SOURCE`; both unchanged.
2. **`build_scene.py` v2** — additive/idempotent: opens the existing
   `talking_robots.blend` (as a command-line argument, re-exec'ing itself),
   `ensure_action()` never touches existing keys, `use_mat()`/`prim()`/
   `add_text()` reuse by name. Per-set local actions derived FROM story.yml,
   ranges asserted against the pinned spans. Staged cams with their own lens,
   no camera bake, no markers, plain menu, idempotent bricks, eyes keep shared
   blink actions, auto-preview + bake of the start set (skipped when already
   correct, so hand-retimed reveal keys survive). `TW_FRESH=1` = from scratch.
3. **`resave_game.py`** — v2: per-set plans instead of `loaded["cues"]`,
   sidecar drift repair via Refresh Sync, fake-user guard, create-if-missing
   bricks via build_scene's own helpers (its `main()` is `__name__`-guarded so
   importing does not rebuild), no-Text-write rule kept, generic-driver
   self-test kept. Verified inert (identical fingerprint across two runs).
4. **`verify_game.py`** — read-only, previews EVERY set, slot-binding proof
   per action (depsgraph value must equal what the action keys), sidecar +
   schema checks, v1-leftover assertions, bake checks, and the build-twice
   key-survival proof in a throwaway copy of the project.
5. **Chain + stills + README** — full chain run (build → resave → verify →
   stills), `make_stills.py` (baked card / typing proof / menu proof / later
   line), README rewritten for the v2 workflow + schema reference, pushed to
   branch `v2-migration` with a PR (never straight to main).

### Gotchas added since the v1 list
- **Never `bpy.ops.wm.open_mainfile()` inside a UI session** on UPBGE 0.50:
  it segfaults at teardown (probed: open + quit with nothing else in between
  exits 139) and `bpy.ops.object.game_property_new` after it hangs. Loading
  the same file as a command-line ARGUMENT is clean — hence build_scene's
  re-exec with `bpy.app.binary_path` (NOT `sys.executable`: in UPBGE that is
  the bundled python3.11, which tried to run the .blend as a script).
- `Matrix.identity()` mutates in place and returns None — never assign its
  result. `_pose_menu` / parenting use `.identity()` as a statement.
- Comparing an animated value against a rest pose is a bad evaluation proof
  (an idle bob can be exactly sin(36*pi)==0 at its last frame): compare the
  evaluated depsgraph value against what the ACTION keys say at that frame.

## Locked design decisions (do not relitigate without the user)

- Anim types: `subs` / `action` / `camera` / `audio`. Sets play anims from
  t=0; `subs` blocks on cue-span end; `action` blocks via `isPlayingAction`;
  camera/audio **never** block; `{at: s, wait: false}` for offsets/ambience.
- Options are key+label+target only (no `do:` — reactions live in the target
  set). Chains + loops via `goto`/`choice`; instant goto-cycles are errors.
- Per-set relative timing; ONE Blender scene; authoring = **Option B**
  (per-set local actions, Preview-set switches context, press P is the full
  preview). No master assembly, no bake compiler, no `frames:` ranges.
- References are Blender **names** (custom-prop manual ids rejected as worse
  UI); renames handled by invisible auto-UIDs + watcher + Apply/auto-rewrite.
- SRT homes: `.` and `./subtitles/`. Audio: `./audio/` (`.` fallback),
  timeline preview via auto-managed speakers, `aud` in game, never blocking.
- VSCode JSON Schema (`story.schema.json`, regenerated on save) is the
  autocomplete vehicle. No `.rpy` (user: ".rpy not needed here"). No graph UI.
- Game property strings are length-capped → store paths only, never JSON.

## Commands

```bash
cd /home/user/talking_robots
python3 test_story.py && python3 test_game_logic.py && python3 test_tw_game.py
U=/home/user/upbge/upbge-0.50-linux-x64
timeout 600 xvfb-run -a -s "-screen 0 1280x800x24" $U/blender --factory-startup -P build_scene.py
timeout 300 xvfb-run -a -s "-screen 0 1280x800x24" $U/blender talking_robots.blend --python resave_game.py
$U/blender -b talking_robots.blend --python verify_game.py
$U/blender -b talking_robots.blend -o //test/x_ -f 30            # no addon
$U/blender -b talking_robots.blend -P /tmp/reg_addon.py -o //test/xa_ -f 30  # +addon
```

`/tmp/reg_addon.py` (recreate, /tmp does not persist):
```python
import sys; sys.path.insert(0, "/home/user/talking_robots")
import typewriter_subtitles as tw; tw.register()
```

## Gotchas (earned the hard way — respect all of them)

1. **NEVER bpy-write the `Text` game property.** `prop.value = …` on a
   `Text` prop corrupts UPBGE 0.50 state → deterministic segfault on a later
   scene op (bisected over ~10 probes). Create/read OK; the game driver
   rewrites values every tick via the KX path (safe).
2. **Env amnesia:** `/home/user/upbge`, `libpulse0`, `xvfb` vanish on snapshot
   restore. Reinstall: `apt install libpulse0 xvfb`, redownload
   `upbge-0.50-linux-x64.tar.xz` from github.com/UPBGE/upbge/releases (v0.50).
3. **Never two edits to the SAME file in one parallel block** — the merge
   corrupts (duplicated tail observed). Batch across different files only.
4. **Chunk large file writes.** A ~13 KB `write_file` was truncated mid-file
   (literal `...[truncated N chars]` marker on disk). Write + append via
   `edit_file` for big files; always `py_compile` + run tests after.
5. Blender probes must quit (`blender -b … --python` exits; scripts run under
   xvfb Blender must call `bpy.ops.wm.quit_blender()` or they hang to timeout).
6. Shell: `cmd | grep … && next` dies when grep is empty — use `;` + banners.
   Raw Blender tracebacks say `File "…", line N` (grep `Traceback`, not `line`).
7. Blender 5: actions are layered/slotted — `action.fcurves` is EMPTY legacy
   API; walk `layers → strips → channelbags` (addon has `_action_fcurves`);
   `channelbag()` needs a `slot` arg; name shared actions once (Camera).
8. `sorted(bpy objects)` → TypeError; always `key=lambda o: o.name`.
9. Animated values: read via `evaluated_depsgraph_get()` + `evaluated_get`,
   never raw props. Pre-first-key CONSTANT extrapolation HOLDS the first key.
10. `Curve.body` is NOT animatable; Build modifier reveals font faces
    tail-first (never use for typing — baked one-liners + live typing won).
11. Game-object discovery: `scene.objects[name]` with linear fallback;
    `KX_FontObject.text` + `["Text"]` both writable in-game (KX path safe).
12. `test*.py` run order-independent; game_debug.log is written next to the
    story in tests — integration cleans `HERE/game_debug.log`, keep that.

## Related: UPVN (github.com/SodoMita/UPVN)

Ren'Py-like VN framework for UPBGE (v0.6.x, 391 files, 335 tests): direct
`.rpy` parsing (explicitly no YAML), `blf` dialogue box, 2D planes + 3D stage
stubs, full label/menu/jump/vars/save. Comparison done 2026-09-15: UPVN is a
platform (script-first), this repo is a feature + 3D short (timeline-first);
they converge from opposite ends. UPVN may borrow the 3D typewriter renderer
and staged-cam bake later. Its `.rpy` approach was explicitly rejected FOR
THIS repo — do not "unify" them without the user.

## Git

- Remote: `github.com/SodoMita/upbge-subs` (push needs a PAT — ask the user;
  never commit tokens; use a `/tmp` askpass helper, never a stored remote URL
  with credentials). Default branch `main`.
- History: `08c50b9` v1.8.0 talking-robots demo → v2 core (this handoff).
