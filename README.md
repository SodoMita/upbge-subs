# Talking Robots — 3D Typewriter Subtitles in UPBGE

Two low-effort primitive robots (**Cuby** the cube and **Sphero** the sphere)
having a talk-show conversation, subtitled with real 3D text via the
**Typewriter Subtitles** addon. Built with and for **UPBGE 0.50**
(Blender 5.0.1) — and it plays two ways: as a **timeline animation**
(Spacebar) and as a **real-time game** (P).

## Watch it

- `test/v2_main_f30.png` — the start set (`main`, frames 1-361) rendered with
  **no addon at all**: the baked subtitle card carries the whole line.
- `test/v2_main_f30_addon.png` — the same frame with the addon registered:
  the card is mid-type (`…I'm a cube, obv`). Baked = full line, addon = typing.
- `test/v2_menu_cuby.png` — the choice menu: set `cuby`'s close-up shot with
  its prompt and both options posed exactly as the game shows them.
- `test/v2_main_f200.png` — later in the same set (line 5 of 8).
- `talking_robots_timeline.mp4` — **v1-era**: rendered from the old
  master timeline (it contains the retired baked camera moves). v2's
  timeline plays each set from frame 1 with a static opening shot; the game
  moves the camera procedurally. Re-render with the commands below if you
  want a current video.

## Play it (UPBGE 0.50 + the addon)

1. Open `talking_robots.blend` in UPBGE.
2. Install the addon once: `Edit → Preferences → Add-ons → Install…`, pick
   `typewriter_subtitles.py` (v1.9.0), enable **Typewriter Subtitles**.
   (Timeline subtitles are baked keys, so the file plays and renders even
   without the addon — it is for editing, previewing sets and re-baking.)
3. **Timeline mode — Spacebar.** The file opens on the story's `start` set
   (`main`): its actions play from frame 1, its cues are loaded into
   `Subtitles`, the render camera is snapped to that set's opening shot, and
   `main_Line##` baked objects carry the text for addon-free playback.
4. **Work on another set:** select `Subtitles` → Sidebar (`N`) →
   **Story Sets** → **Preview Set…**. That is the whole context switch:
   set-local frame range (every set starts at 1), this set's actions
   assigned (every other set's detached), its `.srt` cues loaded, camera
   snapped to its opening shot, choice menu posed or parked.
5. **Game mode — P.** `game_subtitles.py` reads `story.yml` + the `.srt`
   files + `story.sync.json` live, plays each set's actions from t=0, moves
   the camera between the staged shots, types the lines. **Up/Down + Enter**
   (or **1**/**2**) picks, **R** restarts, **ESC** quits. It writes
   `game_debug.log` next to the .blend (sets, choices, ticks) as proof the
   logic ran.

## The story: sets of animations (v2)

v2 is **animation-centric**. Text lives in per-set SubRip files, motion in
per-set Blender actions, and `story.yml` only wires them together:

```yaml
start: main
cps: 30
sets:
  main:
    anims:
      - subs: dialogue.srt#1-8      # cue span; blocks on the cue-span end
      - camera: Wide                # opening shot; [CAM x] cues switch mid-set
      - action: CubyRoot@main__cuby # Obj@Action; blocks until the action ends
      - action: SpheroArmR@main__spheroarm
    end: choice pick                # stop | goto <set> | choice <id>
choices:
  pick:
    prompt: dialogue.srt#9
    options:
      - [1, "Ask Cuby about cubes", cuby]
```

- **Everything in a set plays from t=0.** Each set's actions are *local-timed*
  starter keys: `main__*` spans frames 1-361, `cuby__*` 1-91, `sphero__*`
  1-133. `story.sync.json` records those ranges; the game divides them by fps
  to know how long a blocking action runs.
- **Anim types:** `subs` (blocks), `action` (blocks), `camera` (never blocks),
  `audio` (fire-and-forget; `./audio/` then `.`; never blocks). Offsets and
  ambience: `{…, at: 2.5}` and `{…, wait: false}`.
- **Subtitles:** `.srt` files are found in `.` or `./subtitles/`; edit them
  with any subtitle software. `[CAM Shot]` lines switch cameras mid-set;
  `CUBY:` / `SPHERO:` / `BOTH:` prefixes are acted out by those robots.
- **Options are key + label + target only** — reactions live in the target
  set. Chains and loops via `goto`/`choice`; a set that ends in an instant
  goto-cycle is an error.
- **Editor completion:** `story.yml` starts with a
  `# yaml-language-server=$schema=./story.schema.json` directive and
  `story.schema.json` is written by the addon (Refresh Sync / on save), so a
  YAML-language-server editor autocompletes sets, anim types, refs and
  choices. `story.sync.json` also carries the `_tw_uid` rename map.
- **Rename an object or action** and the Story panel lists the pending
  rename; **Apply** rewrites `story.yml` for you (auto-rewrite is optional).
  Refs are Blender names resolved through that map, so nothing breaks in
  between.
- **Hand-tweak anything** — keys, cue timing, set wiring — then press **P**.
  No rebuild, no re-export. `build_scene.py` is additive: it never deletes an
  action, a keyframe, an object or a material slot, so your tweaks survive a
  rebuild (verified: an identical action-key fingerprint across rebuilds).

Actor rule of thumb: every set action starts and ends at its rest pose, so
sets never snap mid-gesture. The choice menu is a state-driven plain object
(scale 0 when parked, never `hide_render` — UPBGE skips hidden objects at
game conversion), posed by the game and by Preview Set.

## Game subtitles in your own project

Timeline subtitle handlers never run inside the game engine (Blender's clock
does not advance there), so the addon exports your lines for real-time play:

1. Select your subtitle text object (the one with the typewriter lines).
2. Panel → **Add Game Logic**: exports the cues (as seconds) into a
   `tw_game_data` game property, embeds the `tw_game.py` driver as an internal
   text block, wires `Always (pulse) -> Python (tw_game.update)`.
3. Press **P** — the lines type live. **R** restarts. The panel shows
   **Game engine: data current/stale**; edits re-export automatically, or
   press **Refresh**.

Note the generic path still stores JSON in a property string (fine for
typical line counts); the demo's own driver reads files instead.
`tw_game.py` ships standalone with its headless test `test_tw_game.py`.
If subtitles don't appear in-game: check `tw_game_debug.log`, confirm the
Always sensor pulses and the controller is `MODULE tw_game.update`, and that
you run on a GPU — software GL cannot start the game rasterizer.

## Edit the subtitles

- Select `Subtitles` → Sidebar (`N`) → **Subtitles** panel. Each list entry is
  one timeline keyframe; drag keys in the Timeline / Dope Sheet to retime.
- `Ctrl+Shift+U` adds a subtitle at the playhead and jumps to it.
- Re-import any `.srt` via the panel's **Import** (or **Export** your edits).
- Typing speed: `Characters / Second` (`30`, or `cps:` in story.yml).
- After line/retime edits press **Bake Set to Objects** so the `<set>_Line##`
  cards match; the game reads the `.srt` directly and never needs the bake.

## Subtitles gone? Recover in 10 seconds

An empty 3D text object is invisible, and a file may open on a frame with no
text yet — so "nothing on screen" usually means nothing more than that. The
lines are almost always still in the file, and the addon keeps an automatic
backup plus self-repair on load:

1. (Re)install + enable the addon; reopen the .blend and watch the system
   console for `auto-recovered N line(s)`.
2. Select `Subtitles` (Outliner if the viewport shows nothing) → the panel's
   status line tells the truth, e.g. `8 lines, 8 keys — synced`.
3. **needs repair** → **Rebuild Keys** (lines exist, keys don't) or
   **Recover Backup** (lines gone).
4. Worst case: **Import** → `dialogue.srt` with *Replace Existing*.

## Files

| File | What it is |
|---|---|
| `talking_robots.blend` | Scene: stage, robots, per-set actions, staged cams, game bricks |
| `typewriter_subtitles.py` | The addon v1.9.0 (story editor, preview sets, sync sidecars, rename watcher) |
| `story.yml` | Story wiring: sets of anims, choices, ends (the game reads it live) |
| `story.py` | story.yml + SRT parser, validation, plans (game embeds copies) |
| `story.sync.json` | Addon-written: action frame ranges + `_tw_uid` rename map |
| `story.schema.json` | Addon-written: JSON schema for story.yml (editor completion) |
| `dialogue.srt`, `cuby.srt`, `sphero.srt` | Per-set subtitle text + `[CAM]` shots |
| `game_subtitles.py` | Game-mode driver (also embedded in the .blend as a text block) |
| `build_scene.py` | Builds / tops-up the .blend (additive, Xvfb, quits itself) |
| `resave_game.py` | Refreshes the demo's game setup + self-tests the generic path (Xvfb) |
| `verify_game.py` | Read-only verification incl. the build-twice key-survival proof |
| `make_stills.py` | Renders the README stills (read-only, never saves) |
| `register_addon.py` | Registers the addon for background renders |
| `set_capture.py` / `autostart_game.py` | Auto-record the game / start it from a startup script |
| `test_story.py` / `test_game_logic.py` / `test_tw_game.py` | Pure-Python tests (no Blender) |
| `test_addon_story.py` / `test_panel_draw.py` | Addon story + headless panel-draw tests |
| `test_persistence.py` / `test_load_repair.py` / `test_ops.py` | Save/load + repair + operator tests |
| `test/` | Stills (regenerate with make_stills.py) |

## Reproduce / re-render (Linux, headless-safe)

```bash
U=/path/to/upbge-0.50-linux-x64

# 1. build / top-up the .blend (logic bricks need a display -> Xvfb)
timeout 900 xvfb-run -a -s "-screen 0 1280x800x24" \
    $U/blender --factory-startup -P build_scene.py
#    TW_FRESH=1 in front rebuilds from scratch instead of topping up

# 2. refresh the demo's game setup + self-test (Xvfb, quits itself)
timeout 300 xvfb-run -a -s "-screen 0 1280x800x24" \
    $U/blender talking_robots.blend --python resave_game.py

# 3. read-only verification (133 checks incl. build-twice key survival)
$U/blender -b talking_robots.blend --python verify_game.py

# 4. stills (read-only): baked cards without the addon, typing + menu with it
$U/blender -b talking_robots.blend -P make_stills.py             # all four
MODE=noaddon $U/blender -b talking_robots.blend -P make_stills.py
MODE=addon   $U/blender -b talking_robots.blend -P make_stills.py

# 5. timeline animation (PNG sequence) + encode
$U/blender -b talking_robots.blend -P register_addon.py \
      -o //render/frame_ -F PNG -a          # start set range (1-361, ~20 min)
ffmpeg -framerate 24 -i render/frame_%04d.png -c:v libx264 \
       -pix_fmt yuv420p -crf 19 talking_robots_timeline.mp4

# 6. headless game capture (needs a GPU; won't run on pure software GL)
$U/blender -b talking_robots.blend -P set_capture.py

# 7. tests
python3 test_story.py && python3 test_game_logic.py && python3 test_tw_game.py
$U/blender -b --factory-startup -P test_addon_story.py       # 132 checks
$U/blender -b --factory-startup -P test_panel_draw.py        # 25 checks
MODE=A|B|C|R $U/blender -b [persist_A/B.blend] -P test_persistence.py
```

## Notes & troubleshooting

- **The camera is procedural in-game and snapped per set in the editor.**
  `Wide`/`WideEnd`/`Cuby`/`Sphero` are real camera objects (a staging rig with
  their own lens: 50/50/55/45 mm). v2 has NO camera bake and NO `SET-*`/`CH-*`
  markers: Preview Set snaps the single render camera onto a set's opening
  shot, `[CAM]` cues switch mid-set on the timeline, and the game
  interpolates between shots (smoothstep + slerp, lens followed live).
  Separate active cameras would strand the camera-parented subtitles, hence
  one render camera.
- **Never bpy-assign the `Text` game property.** Creating and reading it are
  fine; `prop.value = …` on a `Text` property corrupts UPBGE 0.50 state and
  segfaults on a later scene op. The game driver rewrites those values every
  tick through the KX path. Only paths live in game property strings (they
  are length-capped), so `tw_story` holds `//story.yml`, never JSON.
- **Never `hide_render` a game object.** UPBGE skips such objects at game
  conversion. The menu parks at scale 0 instead; the driver resets its scale
  in-game. Keyboard input uses `keyboard.inputs` on new UPBGE with an
  automatic `.events` fallback.
- **`bpy.ops.logic.*` and game-property ops need a display** (UPBGE 0.50):
  run `build_scene.py` / `resave_game.py` under `xvfb-run` *without* `-b`.
  And **never `bpy.ops.wm.open_mainfile()` inside a UI session**: it segfaults
  at teardown (probed — open + quit alone exits 139, and `game_property_new`
  after it hangs). That is why an additive build re-runs itself with the
  `.blend` passed as a command-line argument. Background renders
  (`-b … -a`) are fine.
- **Game mode needs a GPU.** This sandbox has none, so the live game is
  verified by unit tests + logic inspection + `game_debug.log` only. On real
  hardware with UPBGE + OpenGL 4.3+, just press **P**.
- **If P freezes:** read `game_debug.log` (`init ok` / `tick N` show how far
  the logic got), try the standalone player, and on hybrid-GPU laptops force
  the discrete GPU (`DRI_PRIME=1`). A freeze with a healthy log is the
  rasterizer/GPU context, not the dialogue logic.
- **Keys are dense (every frame)** — thin them in the Graph Editor to
  hand-tweak. Mouths/arms/eyes are plain keyframed primitives; no armatures,
  maximum low-effort charm. Blinking is ambience, not a story anim: each eye
  keeps one long periodic action of its own.
- **v1 → v2 migration:** the master timeline, `Camera_anim`, `ChoiceMenu_anim`,
  `SubLine##` bakes, `SET-*`/`CH-*` markers and the `tw_branches` JSON are
  gone. `story.yml` replaced its `actors:` list with per-set `anims:`
  (`Obj@Action` refs) and gained `subs:` cue spans + `camera:` opening shots;
  subtitles moved from one `dialogue.srt` on absolute frames to per-set spans
  (`file.srt#a-b`). v1.8's `[BRANCH]`/`[CHOICE]`/`[OPT]` SRT directives remain
  legacy-stripped. `verify_game.py` asserts none of the v1 leftovers exist.

Enjoy! Press P. 🤖
