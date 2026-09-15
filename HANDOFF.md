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

## Current state (2026-09-15): v2 migration HALF DONE

The v1 master-timeline pipeline (single `dialogue.srt`, absolute frames,
one baked `Camera_anim`, one choice) is being replaced by the v2
animation-centric pipeline. Status:

**DONE + tested (pure Python, no Blender needed):**
- `story.py` v2 — flow-map YAML, `file.srt#a-b` / `file.srt#N` / `Obj@Act`
  refs, multi-SRT (`.` + `./subtitles/`), audio checks (`./audio/` + `.`),
  `check_story` + `warn_story` (incl. unreachable-set/choice + instant-loop
  detection) + `check_bindings`, per-set runtime plans, sidecar/schema builders.
- `game_subtitles.py` v2 — loads `story.py` from disk, plays anim bundles
  from t=0, procedural camera (smoothstep + slerp between staged shots,
  lens followed live), `aud` audio, chained choices, loops, capture mode.
- Content: `dialogue.srt` (main cues 1–9), `cuby.srt`, `sphero.srt`
  (rebased to 00:00), v2 `story.yml` (`pick` → `pick2` chain, loop to `main`),
  starter `story.sync.json` (action ranges — **build must generate these
  exact ranges**: `main__*` [1,361], `cuby__*` [1,91], `sphero__*` [1,133]).
- Suites green: `test_story.py`, `test_game_logic.py` (16 sections, incl.
  real-file integration with loop), `test_tw_game.py` (unchanged, passing).

**QUEUED (not started):**
1. **Add-on v2 → 1.9.0** (`typewriter_subtitles.py`): Preview-set operator
   (assign set actions, load set SRT into entries, frame range, snap opening
   shot, menu preview, auto-manage speaker objects, `tw_preview_set` scene
   indicator), fake-user guard for set actions, rename watcher (auto-stamp
   invisible `tw_uid`, depsgraph name-diff, pending list + Apply button +
   `tw_autorewrite` toggle, targeted YAML rewrite), Refresh Sync (write
   `story.sync.json` + `story.schema.json`, also on save), validator panel
   (errors/warnings/bindings text lists — **no graph rendering, user veto**),
   Bake to Objects per set (prefix `<set>_Line##`), repurpose Jump to Set.
   Keep: entries/keys/live typing, import/export, save_pre/post,
   `setup_game_logic`, `_is_directive_line`, `TW_GAME_DRIVER_SOURCE`
   (tests pin the last two via AST/extract).
2. **`build_scene.py` v2**: additive/idempotent (create-if-missing, **never
   delete actions or keys**), per-set local-timed starter actions matching
   the sync ranges above, staged cams carrying their own `lens`, NO camera
   bake (driver is procedural), NO SET-/CH- markers, plain menu object,
   idempotent bricks, auto-preview start set at the end.
3. `resave_game.py`: update for v2 (keep the no-Text-write rule, see below).
4. `verify_game.py`: rewrite for v2 (story clean, sidecar present, bindings
   vs scene, preview works, build-twice key survival, bricks, v1.9.0).
5. Full chain + stills (preview main, addon-render typing proof, menu proof),
   keep `test_persistence/test_load_repair/test_ops` passing, rewrite README
   (v2 workflow + schema reference), push.

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
