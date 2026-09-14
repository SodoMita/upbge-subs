# Talking Robots — 3D Typewriter Subtitles in UPBGE

Two low-effort primitive robots (**Cuby** the cube and **Sphero** the sphere)
having a 15-second talk-show conversation, subtitled with real 3D text via the
**Typewriter Subtitles** addon. Built with and for **UPBGE 0.50**
(Blender 5.0.1) — and it plays two ways: as a **timeline animation**
(Spacebar) and as a **real-time game** (P).

## Watch it

- `talking_robots_timeline.mp4` — the animation rendered in UPBGE
  (640×360, 24 fps, 15 s, H.264). Covers the main set (v1.6
  timeline); the choice + side sets below are playable via P
  or by scrubbing past frame 360.
- `test/test_0030.png`, `test/test_0330.png` — stills.

## Play it (needs UPBGE 0.36+ / Blender with the addon)

1. Open `talking_robots.blend` in UPBGE.
2. Install the addon once: `Edit → Preferences → Add-ons → Install…`,
   pick `typewriter_subtitles.py`, enable **Typewriter Subtitles (3D Text
   Keyframes)**. (Timeline subtitles are baked keys since v1.8.0, so they
   play and render even without it — the addon is for editing + re-baking.)
3. **Timeline mode:** press **Spacebar**. The 3D subtitles type live on the
   camera-mounted text object; mouths flap, arms wave, eyes blink.
4. **Game mode:** press **P**. The same story runs in the game engine:
   `game_subtitles.py` reads `story.yml` + `dialogue.srt` live, plays each
   actor's action, types the lines. Pick with **Up/Down + Enter**
   (or **1**/**2**), **R** restarts, **ESC** quits. The script writes
   `game_debug.log` next to the .blend (sets, choices, actors) so you can
   prove the logic ran.

## Story sets, actors & choices (v1.8.0+)

The demo is one choice ("Who gets the last word?") with two story sets
(`cuby`, `sphero`) after the opening `main` set. Authoring is editor-first:

- **Subtitles + animation in sync:** add subtitle entries (panel, `Import`,
  or edit `dialogue.srt` externally) and keyframes on any object on the
  same timeline. Subtitle text is baked to one text object per cue
  (**Bake to Objects**): each cue becomes its own live one-line object
  with its own key (draggable timing) + visibility keys — plays, scrubs
  and renders with no addon and no code (full-line cards without it,
  real typing with it).
- **`story.yml` wires it into a playable story:** group cues+frames into
  named **sets**, pick the **start** set, list the **actors** (objects
  whose own actions the game plays), add **options** to each choice, and
  set what each set activates on its **end** (`stop`, `goto <set>`,
  `choice <id>`). Sets ending in a choice end at their prompt cue's start.
- **Play:** the game reads `story.yml` + `dialogue.srt` live — tweak keys,
  retime cues, rewire sets, then press **P**. No rebuild, no re-export;
  the `tw_story` property holds only the path (property strings are
  length-capped, so no JSON lives in them anymore).

`[CAM Wide|Cuby|Sphero]` lines in `dialogue.srt` still steer the starter
camera bake from the staged rig (`Wide`/`WideEnd`/`Cuby`/`Sphero` camera
objects with per-shot lens); after the bake the keys are yours to edit.
A cue starting with `CUBY:`/`SPHERO:`/`BOTH:` is acted out by those robots;
anything else is narration. Timeline markers `SET-*`/`CH-*` mark each
set/choice — the addon's **Jump to Set...** button jumps the playhead to
one. The choice menu is a state-driven UI object (timeline preview keys +
in-game show/hide), never an actor.

Actor rule of thumb: every actor action should start and end at its rest
pose, so set entries never snap mid-gesture (the camera holds its last
frame at set end; the menu is driven by game state, not keys).

## Game subtitles in your own project (addon v1.6.0+)

Timeline subtitle handlers never run inside the game engine (Blender's clock
doesn't advance there), so the addon exports your lines for real-time play:

1. Select your subtitle text object (the one with the typewriter lines).
2. In the addon's panel: **Add Game Logic**. This exports the cues (as
   seconds) into a `tw_game_data` game property, embeds the `tw_game.py`
   driver as an internal text block, and wires
   `Always (pulse) -> Python (tw_game.update)`.
3. Press **P** — the lines type live in the game. **R** restarts.

Editing lines later? The panel shows **Game engine: data current/stale**;
the export refreshes automatically on edit, or press **Refresh** to force it.
Note: this generic path still exports JSON into a property string (fine for
typical line counts; file-based export is roadmap). The demo's own driver
already reads `story.yml` + SRT files instead — see above.
`tw_game.py` is also shipped standalone (the same file the addon embeds),
with its headless test `test_tw_game.py`.

If subtitles don't appear in-game: check `tw_game_debug.log` (written next to
the .blend, lists cues + first ticks), confirm the Always sensor pulses, the
controller is `MODULE tw_game.update`, and that you run UPBGE/BGE with a GPU
— software GL can't start the game rasterizer.

## Edit the subtitles

- Select the `Subtitles` object → Sidebar (`N`) → **Subtitles** panel.
  Each list entry is one timeline keyframe; drag the keys in the Timeline /
  Dope Sheet to retime — the lines follow.
- `Ctrl+Shift+U` adds a new subtitle at the playhead and jumps to it.
- `dialogue.srt` is the source script — re-import any time via the panel's
  **Import** button (or **Export** your edited lines back to SRT/VTT).
- Typing speed: the `Characters / Second` property (`30`).
- After any line/retime edit: press **Bake to Objects** so the timeline
  objects match the entries (the game reads `dialogue.srt` directly, so it
  never needs the bake).

## Subtitles gone? Recover in 10 seconds

What usually happened: the file opens at a frame with no text yet (an empty
3D text object is *invisible* — nothing to click), and/or the addon wasn't
enabled, so nothing types and the panel is missing. The lines themselves are
almost always still in the file — and v1.5+ also keeps an automatic backup
on the object plus self-repair on load.

1. **(Re)install the addon:** `Edit → Preferences → Add-ons → Install…`,
   pick `typewriter_subtitles.py` (v1.5.0+), enable it. If an older version
   is there, remove it first (or press the panel's *Reload Add-on* button).
2. **Reopen the .blend.** Watch `Window → Toggle System Console`: a line like
   `auto-recovered 8 line(s)` means it healed itself.
3. **Select the `Subtitles` object** (Outliner, if the 3D view shows nothing)
   → Sidebar (`N`) → **Subtitles**. The status line tells the truth, e.g.
   `8 lines, 8 keys — synced`, plus what the current frame shows.
4. If it says **needs repair**: press **Rebuild Keys** (lines exist, keys
   don't) or **Recover Backup** (lines themselves are gone).
5. **Worst case:** panel → **Import** → `dialogue.srt` with *Replace Existing*
   → everything (lines + keys) is rebuilt exactly.

The `talking_robots.blend` in this folder already has the backup embedded and
opens at frame 30 with visible text, so you can verify at a glance.

## Files

| File | What it is |
|---|---|
| `talking_robots.blend` | The scene: stage, robots, camera move, subtitle keys, game bricks |
| `talking_robots_timeline.mp4` | Rendered playback (this is the “played in UPBGE” proof) |
| `typewriter_subtitles.py` | The addon (install + enable, see above) |
| `dialogue.srt` | Subtitle text + [CAM] shot plan (15 cues, 24 fps) |
| `story.yml` | Story wiring: sets, actors, choices, ends (the game reads it live) |
| `story.py` | story.yml + SRT parser, validation, spans (game embeds copies) |
| `test_story.py` | Story/YAML tests + driver/addon parity checks |
| `game_subtitles.py` | Game-mode player (also embedded in the .blend as a text block) |
| `build_scene.py` | Rebuilds the whole .blend from scratch (headless-safe under Xvfb, see below) |
| `register_addon.py` | Registers the addon for background renders |
| `set_capture.py` | Makes a `*_capture.blend` that auto-records the game |
| `autostart_game.py` | Starts the game from a startup script, quits after |
| `test_game_logic.py` | Headless unit test for the game script (fake `bge` module) |
| `tw_game.py` | Generic game driver for your own projects (embedded by the addon) |
| `test_tw_game.py` | Headless unit test for the generic driver |
| `resave_game.py` | Refreshes demo game setup + self-tests the generic path (Xvfb) |
| `verify_game.py` | Headless wiring check for the demo .blend |
| `test/` | Poster stills |

## Reproduce / re-render (Linux, headless-safe)

```bash
# 1. rebuild the .blend (logic bricks need a display → use Xvfb)
xvfb-run -a upbge --factory-startup -P build_scene.py

# 2. render timeline animation (PNG sequence; addon auto-registers)
upbge -b talking_robots.blend -P register_addon.py \
      -o //render/frame_ -F PNG -a   # full scene range (1-708, ~1 h)

# 3. encode video
ffmpeg -framerate 24 -i render/frame_%04d.png -c:v libx264 \
       -pix_fmt yuv420p -crf 19 talking_robots_timeline.mp4

# 4. headless game capture (needs a GPU; won't run on pure software GL)
upbge -b talking_robots.blend -P set_capture.py
blenderplayer -w 640 360 talking_robots_capture.blend   # → capture/game_*.png
ffmpeg -framerate 30 -i capture/game_%04d.png -c:v libx264 \
       -pix_fmt yuv420p talking_robots_game.mp4

# 5. game-logic unit tests (no Blender needed)
python3 test_game_logic.py   # demo driver: sets, actors, choices, capture
python3 test_tw_game.py      # generic driver: same, via the addon's embed
python3 test_story.py        # story/YAML/SRT parsing + driver parity

# 6. refresh demo game setup (needs a display -> Xvfb) + verify headless
xvfb-run -a upbge talking_robots.blend -P resave_game.py   # validates story, quits itself
upbge -b talking_robots.blend -P verify_game.py
```

## Notes & troubleshooting

- **Camera "shots" are baked action keys.** `Wide`/`WideEnd`/`Cuby`/`Sphero`
  are a staging rig you frame visually (lens included: 50/50/55/45 mm);
  the main `Camera` (which carries the parented subtitles/HUD) gets its
  starter keys baked from them, then both the timeline and the game play
  the `Camera_anim` action — tweak keys and press **P** with no rebuild.
  (Separate *active* cameras would strand the HUD, hence the single
  render camera.) One lens per set in the game (`lens:` in `story.yml`);
  the timeline can key lens freely.
- **Never bpy-assign the `Text` game property.** Reading it and creating
  it are fine, but `prop.value = ...` on a `Text` property corrupts UPBGE
  0.50 state and segfaults on a later scene op (bisected: any value, any
  later mutation detonates it). `resave_game.py` ensures the props exist
  and leaves values alone; the game driver rewrites them every tick.
- **Never `hide_render` a game object.** UPBGE skips such objects at game
  conversion ("not in the same layer ... will not be converted") — the
  choice menu hides via scale-0 keys instead (v1.7.1+), and the driver
  resets its scale in-game. Game drivers also use `keyboard.inputs` on new
  UPBGE (`.events` is deprecated) with automatic fallback to `.events`.
- **Game mode needs a GPU.** The sandbox this was built in has none, so the
  live game was verified by unit tests + logic inspection only (software GL
  either crashes or hangs in the game rasterizer). On real hardware with
  UPBGE + OpenGL 4.3+, just press **P**.
- **If P freezes:** (1) check `game_debug.log` next to the .blend — its
  `init ok` / `tick N` lines prove how far the logic got before the freeze;
  (2) try the standalone player instead of in-Blender P:
  `blenderplayer talking_robots.blend`; (3) on hybrid-GPU laptops force the
  discrete GPU (`DRI_PRIME=1 blenderplayer ...`); (4) sanity-check P on an
  empty scene and report `glxinfo | grep "OpenGL version"`. A freeze with a
  healthy log means the rasterizer/GPU context, not the dialogue logic.
- **`bpy.ops.logic.*` segfaults in `blender -b`** (UPBGE 0.50): always run
  `build_scene.py` under `xvfb-run` *without* `-b` (it quits itself when done).
- Background *renders* (`-b … -a`) are fine; subtitle objects carry the
  typewriter effect, so it renders even with no addon loaded.
- The subtitle object is parented to the camera, so the slow push-in never
  moves the text off-screen. Sizes: text `0.10` at `(0, -0.44, -3.0)`.
- Mouths/arms/eyes are plain keyframed primitives — no armatures, maximum
  low-effort charm. Keyframes are dense (every frame); thin them in the
  Graph Editor if you want to hand-tweak.
- **v1.7.x → v1.8.0 migration:** `[BRANCH]`/`[CHOICE]`/`[OPT]`/`[GOTO]`/
  `[END]` SRT directives are legacy (stripped, warned, ignored) — wiring
  moved to `story.yml` (sets/actors/choices/ends); the `tw_branches` JSON
  property is now the `tw_story` path; camera `C`/`F1-F3` overrides and
  the driver's procedural acting are gone (actions play instead).

Enjoy! Press P. 🤖🤖
