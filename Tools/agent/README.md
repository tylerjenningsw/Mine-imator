# Mine-imator agent CLI

Build, animate, save and render a Mine-imator scene from one JSON job file. Mine-imator runs the job with its own scene, keyframe, project and render routines. `mineimator_agent.py` starts one isolated app process per job, stops it on timeout and prints the result JSON.

Requires Python 3.8 or later and a built Mine-imator executable with its `Data` folder. No other packages.

## Run a job

```
python Tools/agent/mineimator_agent.py example > job.json
python Tools/agent/mineimator_agent.py run job.json --exe path/to/Mine-imator.exe --timeout 600
```

The result JSON goes to stdout. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | `status` is `ok` |
| 1 | Usage error, or the executable could not start |
| 2 | Job error. Read `error.code`, `error.message` and `error.step` |
| 3 | Timeout. The app was stopped |
| 4 | The app crashed, refused the job or wrote no valid result |
| 130 | Cancelled with Ctrl+C. The app and its child processes were stopped |

On failure the result has a `launcher` object with the app exit code, the work directory and the last lines of the job log.

The app window opens while the job runs. Rendering needs a graphics device, so the app needs a desktop session (on Linux, a display server such as Xvfb).

## Job format

```json
{
	"schema": "mineimator.job/1",
	"id": "walk-demo",
	"outputDirectory": "walk-demo-output",
	"overwrite": false,
	"project": { "mode": "new", "name": "Walk demo", "tempo": 24, "width": 640, "height": 360, "seed": 42 },
	"steps": [ { "op": "create", "id": "cam", "kind": "camera" } ]
}
```

- `outputDirectory` is absolute or relative to the job file. Every output path in a step is relative to it. Paths are resolved through symbolic links, and a path that ends up outside `outputDirectory` is rejected.
- `overwrite: false` (default) fails with `OUTPUT_EXISTS` when an output already exists. Sequence directories must always be new, including when `overwrite` is true.
- `project.mode` is `new` or `open`. `open` takes `path`, a current-format `.miproject`. The app loads a working copy and never writes the input file.
- Unknown fields, wrong types, non-integer frames and out-of-range numbers fail with `INVALID_REQUEST` before the scene changes.

Run `catalog` steps to discover names. The lists come from the running app and its loaded assets.

| Op | Fields | Result |
| --- | --- | --- |
| `catalog` | `kind`: `kinds`, `characters`, `blocks`, `values`, `transitions` or `parts` (with `asset`). Optional `query`, `limit` | `items`, `total` |
| `create` | `id`, `kind`: `camera`, `point_light`, `block`, `character`, `cube`. `asset` for `block` and `character` | `saveId`, `parts` |
| `keyframe.set` | `target`, `frame`, `values`, optional `transition` | `created` |
| `inspect` | `frame` | Evaluated values and keyframe frames per object |
| `save` | `path` ending in `.miproject` | Project artifact |
| `render.image` | `camera`, `frame`, `path` ending in `.png`, optional `quality: "shaded"` | Image artifact |
| `render.sequence` | `camera`, `start`, `count`, `fps`, `path` (a new directory), optional `quality` | Sequence artifact |

### Objects and parts

`create.id` is the public name of an object. Use it as `target` and `camera` in later steps. Address a character part as `<id>/<part>`, for example `actor/right_arm`. A target ending in `/` is rejected. `catalog` with `kind: "parts"` and the character `asset` lists the part names before you create the character. The `create` result lists them as well.

The app stores the id as the timeline name. When a job opens a project, every root timeline with a unique name made of letters, digits, `_` or `-` gets that name as its id.

### Keyframes

`frame` is a timeline frame at `project.tempo` frames per second. `keyframe.set` updates the keyframe at exactly that frame, or creates it. A new keyframe starts from the values the object has at that frame, so values you leave out keep their animated value. The app clamps values with its own limits.

`values` uses native value names. `catalog` with `kind: "values"` lists each name, its type and the kinds that accept it: positions, rotations, scales, part bends, `ALPHA`, `VISIBLE`, camera field of view and orbit (`CAM_ROTATE*`), and point light strength and range. Positions are in Mine-imator units (16 per block) with Z up. Angles are in degrees.

A new camera starts at the editor's work camera, so set its position and rotation explicitly. `CAM_ROTATE` orbits the camera around its position but does not turn it. To look at the orbit center, set `ROT_X` to `CAM_ROTATE_ANGLE_Z` and `ROT_Z` to `CAM_ROTATE_ANGLE_XY`, as the editor's "look at rotation point" option does.

### Rendering

Renders use shaded quality, the camera you name and that camera's size settings, or the project size. `render.sequence` writes `count` files `frame_000000.png` and up. File `i` shows timeline frame `start + i * tempo / fps`, which the artifact lists per file. Trial installs keep the watermark. Artifacts report `watermark`.

Each image is written to a temporary file, checked by reading it back, then moved into place. A sequence directory appears only after every frame is saved. A project file appears only after the app parses it back. An error result lists only artifacts that were completed.

## Errors

`INVALID_REQUEST`, `UNSUPPORTED`, `ASSET_NOT_FOUND`, `STARTUP_FAILED`, `ASSETS_UNAVAILABLE`, `PROJECT_LOAD_FAILED`, `OBJECT_NOT_FOUND`, `DUPLICATE_ID`, `CAMERA_REQUIRED`, `RENDER_FAILED`, `OUTPUT_EXISTS`, `OUTPUT_WRITE_FAILED`, `CANCELLED`, `FATAL`, `INTERNAL`. The launcher adds `TIMEOUT`, `NO_RESULT` and `INVALID_RESULT`, `CANCELLED` on Ctrl+C, and `STARTUP_FAILED` when the executable cannot start. When the app reported its own error message key, the result has `error.appError`.

## Isolation

The launcher sets `MINEIMATOR_AUTOMATION_JOB` and `MINEIMATOR_AUTOMATION_RESULT` for the child process only. In this mode the app:

- Uses `<result>.work/tmp/` for temporary files and `<result>.work/log.txt` for its log, so a normal Mine-imator session keeps its own.
- Reads settings, assets and the license key but never writes settings or the recent-projects list.
- Skips the asset-update and news requests, the welcome popup and the new-assets screen.
- Closes dialogs without showing them and reports errors in the result, including fatal errors.
- Ignores keyboard shortcuts, playback, autosave backups and object placement.

The app refuses to start (exit code 3) when the result file or its work directory already exists, so a leaked environment variable cannot rerun a job.

Replays are compared by `inspect` output for the same job, app version, assets and seed, not by project bytes or pixels.

## Not supported yet

Movie export, high-quality renders, world import, custom skins, packs and models, scenery, particles, audio, and a persistent session or MCP server.

## Checks

- `python -m unittest discover -s Tools/agent/tests` runs the launcher tests with a stand-in app.
- `python Tools/agent/tests/integration_check.py --exe path/to/Mine-imator.exe` runs real jobs against a built app and checks results, the project round trip and decoded PNGs.

The integration check keeps its jobs, results and PNGs in the printed directory for visual inspection.
