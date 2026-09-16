# Talking Robots — 3D Typewriter Subtitles + Story Sets in UPBGE

Two low-effort primitive robots (**Cuby** the cube and **Sphero** the sphere)
host a 15-second talk show, subtitled with real 3D text by the **Typewriter
Subtitles** add-on, and wired into a branching story by **story.yml**. Built
with and for **UPBGE 0.50** (Blender 5.0.1) — it plays two ways: as a
**timeline animation** (Spacebar, one story set at a time) and as a
**real-time game** (P, the whole branching story).

This is **v2**: the story is *animation-centric*. A set is a bundle of
animations that all start at the set's own `t=0` — subtitles are one animation
type among others (`subs` / `action` / `camera` / `audio`) instead of a master
timeline everything else is measured against.

## Watch it

- `talking_robots_timeline.mp4` — the **start set** (`main`) rendered from the
  timeline: 640×360, 24 fps, 15 s, H.264. That is what the timeline shows
  today: one set, its own frame range `1-361`, its cues typing live on the
  baked per-cue text objects. The shot cuts and the rest of the story are the
  game's business (press **P**) — see [the authoring
  model](#the-authoring-model-one-scene-one-set-at-a-time).
- `test/` — stills: `v2_main_*.png` (typing proofs from the animation render),
  `v2_noaddon_0030.png` vs `v2_addon_0030.png` (full-line card without the
  add-on, live typing with it), `v2_menu_*.png` (a previewed set's choice
  menu), `v2_cuby_*.png` / `v2_sphero_*.png` (the other two sets previewed).

## Play it

1. Open `talking_robots.blend` in **UPBGE 0.50**.
2. Install the add-on once: `Edit → Preferences → Add-ons → Install…`, pick
   `typewriter_subtitles.py`, enable **Typewriter Subtitles (3D Text
   Keyframes)**. Timeline subtitles are baked, so they play and render even
   without it — the add-on is for editing, previewing sets and re-baking.
3. **Timeline:** press **Spacebar**. The `main` set plays: the robots'
   `main__*` actions run, the cues type onto the camera-mounted 3D text.
   Switch to another set with `N → Subtitles → Story sets → Preview <set>`.
4. **Game:** press **P**. `game_subtitles.py` reads `story.yml` + the `.srt`
   files live, plays each set's action bundle from `t=0`, moves the render
   camera between the staged shot cameras, types the lines. **Up/Down +
   Enter** (or **1**/**2**) picks a choice, **R** restarts, **ESC** quits.
   `game_debug.log` next to the .blend records sets, choices and actors, so
   you can prove the logic ran. The sandbox this was built in has no GPU, so
   the game was verified by unit tests + wiring checks, not by a live run.

## The authoring model: one scene, one set at a time

v2 deliberately has **no master timeline** and **no bake compiler**:

- **Motion** lives in per-set Blender actions named by the story
  (`main__cuby`, `cuby__cubymouth`, …), each authored in **set-local time**
  (frame 1 = the set's `t=0`).
- **Text** lives in one `.srt` file per set (`dialogue.srt`, `cuby.srt`,
  `sphero.srt`; also found in `./subtitles/`), rebased to `00:00`, editable
  with any subtitle software.
- **Sound** lives in `./audio/` and never blocks a set.
- The **scene's frame range belongs to whichever set is previewed**. Pressing
  Space plays that set; pressing P plays the whole story. `story.yml` only
  wires the three together.

Because frames are set-local, only the previewed set is baked to objects (two
sets baked into one scene would overlap in time). That is exactly what
**Preview Set** is for — it is the editor half of the game, not a preview of
a render.

## Story sets, actors & choices

`story.yml` (parsed by `story.py`, stdlib only — the game, the add-on and the
tools all share that one parser):

```yaml
start: main            # the set played at start
cps: 30                # typewriter speed (chars per second)
sets:
  main:
    anims:
      - subs: dialogue.srt#1-8      # typewriter over cues 1..8, blocks
      - camera: Wide                # opening staged camera (never blocks)
      - action: CubyRoot@main__cuby # play main__cuby on CubyRoot, blocks
      - action: SpheroMouth@main__spheromouth
      - {action: CubyArmR@main__cubyarm, at: 2.5}   # offset in seconds
      - {audio: room.ogg, wait: false}              # ambience, never blocks
    end: choice pick   # stop | goto <set> | choice <id>
  cuby:
    anims:
      - subs: cuby.srt#1-2
      - camera: Cuby
    end: choice pick2
choices:
  pick:
    prompt: dialogue.srt#9          # cue whose text is the prompt
    options:
      - [1, "Ask Cuby about cubes", cuby]     # key, label, target set
      - [2, "Ask Sphero about spheres", sphero]
```

Rules worth knowing:

- **Blocking**: `subs` blocks until the last cue's span end, `action` until
  `isPlayingAction` goes false. `camera` and `audio` **never** block (write
  `wait: false` for ambience; `wait: true` on them is a warning, not a lie).
- **Chains and loops are welcome**: `cuby → pick2 → main` is a real loop in
  this demo. A `goto` cycle with nothing blocking in it is an *error* (it
  would spin the engine at full speed), and unreachable sets/choices are
  warnings.
- **One `camera:` per set** is the opening shot; mid-set cuts come from
  `[CAM Wide]`-style directive lines inside the `.srt` cue text.
- **References are Blender names** (`Object@Action`, shot = staged camera
  object name). Renames are handled for you by the add-on's rename watch.
- A cue starting `CUBY:` / `SPHERO:` / `BOTH:` is acted out by those robots;
  anything else is narration. Every actor action starts and ends on its rest
  pose, so entering a set never snaps mid-gesture.
- Only this YAML subset parses: `#` comments, `key: value`, `- items`,
  single-line `[flow, lists]` and `{flow: maps}`, quoted strings, spaces
  (never tabs).

## The add-on (v1.9.0): what the Subtitles panel does

Select a 3D text object → Sidebar (`N`) → **Subtitles**. Everything from v1 is
still there (entries + keys, live typing, `Ctrl+Shift+U` to add a line, SRT/VTT
import/export, the auto-backup + Rebuild/Recover self-heal, Add Game Logic for
projects without a story). New in v1.9.0 — the **Story sets** box, which works
with any selection (or none):

| Control | What it does |
|---|---|
| `Story File` | Which `story.yml` the tools read (default `//story.yml`, next to the .blend). The game reads its own path from the `tw_story` game property; this one is for the editor. |
| **Preview `main` / `cuby` / …** | The per-set context switch: that set's cues become the subtitle lines (set-local frames), its `action:` animations get assigned to the actors (action + slot), the render camera snaps to its opening staged shot (lens included), its `end: choice` fills the menu, its audio becomes sequencer sound strips, and the frame range becomes the set's length. It only ever *assigns* — no action, key or line of another set is deleted. |
| **Refresh Sync** | Writes `story.sync.json` (every action's keyframe range + the uid map the rename watch needs) and `story.schema.json` (VSCode completion for `story.yml`), and sets a fake user on the story's actions so a purge can't eat them. **Also runs automatically on save**, so the sidecar can never drift from your keys. |
| **Check** | Validates `story.yml` + every `.srt` it references and cross-checks the object/action/camera names against this scene. Results are plain text lines in the box (no graph UI — that was a deliberate veto). |
| **Apply to Story** | When you rename a referenced object/action/camera, the panel lists the pending renames; Apply rewrites them **only where they are used as references** (`Obj@Act`, `camera: Shot`, `[CAM Shot]` in the `.srt` files) — labels, set names and prose are untouched, and the write is atomic. `Auto-rewrite refs on rename` applies them the moment they are detected instead. |

How the rename watch works: references are names, and names change. So every
block the story points at carries an invisible `_tw_uid` ID property, and
`story.sync.json` records `uid → name`. A name whose uid is still there is a
rename, and a rename is a pending edit rather than a broken story.

**Bake to Objects** is per set now: with a set previewed it bakes
`<set>_Line01…` (one text object per cue, each with its own draggable key +
visibility keys, so scrubbing, rendering and the Graph Editor need no add-on
and no code; without the add-on each cue is a clean full-line card). Re-baking
reuses the same names, so a re-bake replaces its own previous bake instead of
stacking `.001` copies. **Jump to Set** is gone: the Preview Set buttons do
that job (and much more).

## Files

| File | What it is |
|---|---|
| `talking_robots.blend` | The scene: stage, robots, per-set actions, staged shot cams, game bricks |
| `talking_robots_timeline.mp4` | The rendered **start set** (the timeline proof) |
| `typewriter_subtitles.py` | The add-on (v1.9.0: subtitles + story preview/sync/rename/validation) |
| `story.yml` | The wiring: sets (anims + end), choices, start, cps |
| `dialogue.srt` / `cuby.srt` / `sphero.srt` | Subtitle text per set (+ `[CAM]` shot lines) |
| `story.sync.json` | Generated: action frame ranges + the `uid → name` map (refreshed on save) |
| `story.schema.json` | Generated: JSON Schema for `story.yml` (VSCode autocomplete via the `# yaml-language-server=$schema` line) |
| `story.py` | Shared parser: story + srt parsing, validation, per-set plans, sidecar/schema, targeted reference rewrite |
| `game_subtitles.py` | The game driver (also embedded in the .blend as a text block) |
| `tw_game.py` / `test_tw_game.py` | Generic driver for projects *without* a story (embedded by Add Game Logic) + its test |
| `build_scene.py` | Builds/refreshes the .blend headless (additive: create-if-missing, never deletes keys) |
| `resave_game.py` | Refreshes the demo's game setup + self-tests the generic path (Xvfb) |
| `verify_game.py` | Headless wiring check of the .blend (82 checks; never saves) |
| `register_addon.py` | Registers the add-on for background renders |
| `set_capture.py`, `autostart_game.py` | Make a `*_capture.blend` that auto-records the game |
| `test_story.py` | Story/YAML/SRT parsing, plans, sidecar/schema, rewrite, add-on parity |
| `test_game_logic.py` | Headless unit test for the game driver (sets, choices, loops, capture) |
| `test_addon_story.py` | In-Blender test of the v2 add-on tools (preview, sync, rename watch, per-set bake) |
| `test_persistence.py`, `test_ops.py`, `test_load_repair.py` | Recovery/persistence/operator tests (inside Blender) |
| `test/` | Poster stills |
| `HANDOFF.md` | State + gotchas for the next session — read it before changing anything |

## Reproduce / re-render (Linux, headless-safe)

```bash
cd /home/user/talking_robots          # or wherever you cloned this
U=/home/user/upbge/upbge-0.50-linux-x64   # UPBGE 0.50 (Blender 5.0.1)

# 0. headless tests (no Blender needed)
python3 test_story.py && python3 test_game_logic.py && python3 test_tw_game.py

# 1. build/refresh the .blend (logic bricks need a display → Xvfb; quits itself)
#    first run wants --factory-startup; re-runs are additive (keys survive)
timeout 600 xvfb-run -a -s "-screen 0 1280x800x24" \
    $U/blender --factory-startup -P build_scene.py

# 2. refresh the demo game setup, then verify the wiring
timeout 300 xvfb-run -a -s "-screen 0 1280x800x24" \
    $U/blender talking_robots.blend -P resave_game.py
$U/blender -b talking_robots.blend -P verify_game.py

# 3. in-Blender tests (add-on operators + the v2 story tools)
$U/blender -b --factory-startup -P test_addon_story.py
$U/blender -b --factory-startup -P test_persistence.py          # MODE=A
$U/blender -b persist_A.blend -P test_load_repair.py
$U/blender -b persist_A.blend -P test_ops.py

# 4. render the start set (PNG sequence), then encode
$U/blender -b talking_robots.blend -P register_addon.py -o //render/frame_ -F PNG -a
ffmpeg -framerate 24 -i render/frame_%04d.png -c:v libx264 -pix_fmt yuv420p \
       -crf 19 talking_robots_timeline.mp4

# 5. a single frame, with and without the add-on (typing vs full-line card)
$U/blender -b talking_robots.blend -o //test/noaddon_ -F PNG -f 30
$U/blender -b talking_robots.blend -P /tmp/reg_addon.py -o //test/addon_ -F PNG -f 30

# 6. headless game capture (needs a GPU; won't run on software GL)
$U/blender -b talking_robots.blend -P set_capture.py
blenderplayer -w 640 360 talking_robots_capture.blend   # → capture/game_*.png
```

## Notes & troubleshooting

- **The camera is procedural, not baked.** `Wide` / `Cuby` / `Sphero` are real
  camera objects carrying the framing *and the lens* (50/55/45 mm). The game
  smoothsteps + slerps the single render camera between them and follows the
  lens live, so re-aiming a shot is moving a camera and pressing P — no bake,
  no rebuild. One render camera (not a per-shot active camera) so the parented
  subtitles/HUD never get stranded. In the timeline the render camera holds the
  opening shot of the previewed set (`Preview Set` snaps it for you).
- **Never bpy-write the `Text` game property.** Reading it and creating it are
  fine; `prop.value = …` on a `Text` property corrupts UPBGE 0.50 state and
  segfaults on a later scene op (bisected over ~10 probes). `resave_game.py`
  and `build_scene.py` create the props and leave the values alone; the game
  driver rewrites them every tick through the KX path, which is safe.
- **Never `hide_render` a game object** — UPBGE skips such objects at game
  conversion. The menu is a plain text object the driver shows by writing its
  text (and hides by writing `""`), which is why v2 has no menu scale keys.
- **Game property strings are length-capped**, so they hold *paths only*
  (`tw_story = //story.yml`); the story itself stays in files next to the
  .blend, which is also what makes "tweak and press P, no rebuild" work.
- **Game mode needs a GPU** (OpenGL 4.3+). If **P** freezes with a healthy
  `game_debug.log`, that is the rasterizer, not the dialogue logic: check the
  log, try `blenderplayer talking_robots.blend`, force the discrete GPU
  (`DRI_PRIME=1`), and sanity-check P on an empty scene.
- **Subtitles gone?** The lines almost always survived: reopen the file (the
  add-on auto-recovers from the object's embedded backup and prints
  `auto-recovered N line(s)`), select `Subtitles`, and press **Rebuild Keys**
  or **Recover Backup**. Worst case: `Import → dialogue.srt` with *Replace
  Existing*. In v2 the panel's Preview Set is the other one-click fix — it
  reloads the lines from the set's `.srt` anyway.
- **After editing keys, cues or the story:** the game needs no rebuild (it
  reads the files live), but the *editor* needs `Refresh Sync` if you added or
  retimed an action (that is where the game reads its durations from — and it
  also runs on save), plus `Bake to Objects` if you want add-on-free renders
  of the changed set.
- `bpy.ops.logic.*` segfaults in `blender -b` on UPBGE 0.50: run
  `build_scene.py` / `resave_game.py` under `xvfb-run` **without** `-b` (they
  quit themselves). Background *renders* (`-b … -a`) are fine.
- Mouths/arms/eyes are plain keyframed primitives — no armatures, maximum
  low-effort charm. Keys are dense (every frame) because they are generated;
  thin them in the Graph Editor if you want to hand-tweak.
- Blender 5 actions are layered/slotted: `action.fcurves` is the empty legacy
  API — walk `layers → strips → channelbags` (the add-on's `_action_fcurves`
  does), and assign `animation_data.action` *before* `action_slot`.
- **v1.8 → v1.9/v2 migration:** `SET-*` / `CH-*` timeline markers are gone
  (Preview Set replaced them), `[BRANCH]`/`[CHOICE]`/`[OPT]`/`[GOTO]`/`[END]`
  directives were already legacy (stripped with a warning), the baked
  `Camera_anim` action is gone (the driver is procedural), `story.yml` no
  longer has a `frames:` range or an `actors:` list (actors are implied by the
  `action:` references), and the game no longer reads one absolute-time
  `dialogue.srt` for everything — each set rebases to `00:00` in its own file.

Enjoy! Press P. 🤖🤖
