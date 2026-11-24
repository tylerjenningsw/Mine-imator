"""Run real jobs against a built Mine-imator and check results, projects and PNGs.

Usage: python Tools/agent/tests/integration_check.py --exe path/to/Mine-imator.exe [--keep DIR]

Each check prints PASS or FAIL. Look at the PNGs it lists before calling rendering correct:
these checks prove files, sizes, frame mapping and pixel changes, not that the picture looks right.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import mineimator_agent as agent  # noqa: E402
import pngdecode  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f": {detail}"))
    if not ok:
        failures.append(name)
    return ok


def run(exe, root, name, steps, project=None, overwrite=False, timeout=600, output_directory=None):
    job = {
        "schema": "mineimator.job/1",
        "id": name,
        "outputDirectory": str(output_directory if output_directory is not None else root / name),
        "overwrite": overwrite,
        "project": project or {"mode": "new", "tempo": 24, "width": 320, "height": 180, "seed": 7},
        "steps": steps,
    }
    path = root / f"{name}.job.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    code, result = agent.run_job(path, [exe], timeout)
    (root / f"{name}.result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return code, result


def step(result, index):
    return next(s for s in result["steps"] if s["index"] == index)


def artifacts(result, kind):
    return [a for a in result["artifacts"] if a["kind"] == kind]


def timeline(inspect, alias):
    return next(t for t in inspect["timelines"] if t["id"] == alias)


def scene_steps():
    return [
        {"op": "catalog", "kind": "characters", "query": "human"},
        {"op": "catalog", "kind": "blocks", "query": "stone", "limit": 5},
        {"op": "catalog", "kind": "parts", "asset": "human"},
        {"op": "catalog", "kind": "values"},
        {"op": "create", "id": "floor", "kind": "block", "asset": "grass_block"},
        {"op": "create", "id": "actor", "kind": "character", "asset": "human"},
        {"op": "create", "id": "box", "kind": "cube"},
        {"op": "create", "id": "lamp", "kind": "point_light"},
        {"op": "create", "id": "front", "kind": "camera"},
        {"op": "create", "id": "side", "kind": "camera"},
        {"op": "keyframe.set", "target": "floor", "frame": 0, "values": {"POS_Z": -16, "SCA_X": 6, "SCA_Y": 6}},
        {"op": "keyframe.set", "target": "box", "frame": 0, "values": {"POS_X": 32, "POS_Y": 16}},
        {"op": "keyframe.set", "target": "actor", "frame": 0, "values": {"POS_X": -24}, "transition": "linear"},
        {"op": "keyframe.set", "target": "actor", "frame": 24, "values": {"POS_X": 10}},
        {"op": "keyframe.set", "target": "actor", "frame": 24, "values": {"POS_X": 24}},
        {"op": "keyframe.set", "target": "actor/right_arm", "frame": 0, "values": {"ROT_X": -40}, "transition": "linear"},
        {"op": "keyframe.set", "target": "actor/right_arm", "frame": 24, "values": {"ROT_X": 40}},
        {"op": "keyframe.set", "target": "lamp", "frame": 0, "values": {"POS_Z": 64, "LIGHT_RANGE": 300}},
        {"op": "keyframe.set", "target": "front", "frame": 0, "values": {"POS_X": 0, "POS_Y": 0, "POS_Z": 16, "ROT_X": 10, "ROT_Y": 0, "ROT_Z": 0, "CAM_ROTATE": True, "CAM_ROTATE_DISTANCE": 120, "CAM_ROTATE_ANGLE_XY": 0, "CAM_ROTATE_ANGLE_Z": 10}},
        {"op": "keyframe.set", "target": "side", "frame": 0, "values": {"POS_X": 0, "POS_Y": 0, "POS_Z": 16, "ROT_X": 35, "ROT_Y": 0, "ROT_Z": 90, "CAM_ROTATE": True, "CAM_ROTATE_DISTANCE": 120, "CAM_ROTATE_ANGLE_XY": 90, "CAM_ROTATE_ANGLE_Z": 35}},
        {"op": "inspect", "frame": 12},
        {"op": "save", "path": "scene.miproject"},
        {"op": "render.image", "camera": "front", "frame": 12, "path": "front.png"},
        {"op": "render.image", "camera": "side", "frame": 12, "path": "side.png"},
        {"op": "render.image", "camera": "front", "frame": 0, "path": "front_start.png"},
        {"op": "render.sequence", "camera": "front", "start": 0, "count": 5, "fps": 4, "path": "frames"},
    ]


def check_png(name, path, size):
    try:
        width, height, channels, rows = pngdecode.decode(path)
    except (OSError, ValueError) as e:
        check(name, False, str(e))
        return None
    check(name + " size", (width, height) == size, f"{width}x{height}")
    check(name + " not flat", not pngdecode.is_flat(width, channels, rows))
    return rows


def check_scene(exe, root):
    code, result = run(exe, root, "scene", scene_steps())
    if not check("scene job ok", code == agent.EXIT_OK and result["status"] == "ok", json.dumps(result.get("error"))):
        return None

    names = [i["name"] for i in step(result, 0)["items"]]
    check("catalog characters lists human", "human" in names, names)
    parts = [i["name"] for i in step(result, 2)["items"]]
    check("catalog parts lists right_arm", "right_arm" in parts, parts)
    created = step(result, 5)
    check("create reports actor parts", any(p["id"] == "actor/right_arm" for p in created["parts"]), created["parts"])
    check("first key at a frame is created", step(result, 13)["created"] is True)
    check("same-frame key updates in place", step(result, 14)["created"] is False)

    inspect = step(result, 20)
    actor = timeline(inspect, "actor")
    check("upsert keeps one key per frame", actor["keyframes"] == [0, 24], actor["keyframes"])
    check("midpoint is interpolated from the updated key", abs(actor["values"]["POS_X"]) < 0.01, actor["values"])
    arm = next((p for p in actor["parts"] if p["id"] == "actor/right_arm"), None)
    check("part key is inspectable", arm is not None and abs(arm["values"]["ROT_X"]) < 0.01, arm)

    project = artifacts(result, "project")
    check("project artifact exists", len(project) == 1 and Path(project[0]["path"]).is_file(), project)

    images = {Path(a["path"]).name: a for a in artifacts(result, "image")}
    rows = {}
    for name in ("front.png", "side.png", "front_start.png"):
        art = images.get(name)
        if check(name + " artifact", art is not None and art["width"] == 320 and art["height"] == 180, art):
            rows[name] = check_png(name, art["path"], (320, 180))
    if all(rows.get(n) for n in ("front.png", "side.png", "front_start.png")):
        check("two cameras give different images", pngdecode.difference(rows["front.png"], rows["side.png"]) > 0.05)
        check("moving actor changes pixels", pngdecode.difference(rows["front.png"], rows["front_start.png"]) > 0.001)

    seq = artifacts(result, "sequence")
    if check("sequence artifact", len(seq) == 1, seq):
        files = seq[0]["files"]
        check("sequence has exact count", len(files) == 5 and len(list(Path(seq[0]["path"]).glob("*.png"))) == 5, files)
        check("sequence frame mapping", [f["frame"] for f in files] == [0, 6, 12, 18, 24], files)
        decoded = [check_png("sequence " + f["file"], Path(seq[0]["path"]) / f["file"], (320, 180)) for f in files]
        if decoded[0] and decoded[-1]:
            check("sequence shows motion", pngdecode.difference(decoded[0], decoded[-1]) > 0.001)

    print("Look at:", *(a["path"] for a in artifacts(result, "image")), seq[0]["path"] if seq else "", sep="\n  ")
    return result


def check_reopen(exe, root, scene):
    saved = artifacts(scene, "project")[0]["path"]
    original = Path(saved).read_bytes()
    code, result = run(exe, root, "reopen", [{"op": "inspect", "frame": 12}, {"op": "save", "path": "copy.miproject"}], project={"mode": "open", "path": saved})
    if not check("reopen job ok", code == agent.EXIT_OK, json.dumps(result.get("error"))):
        return
    check("reopen keeps normalized inspect state", step(result, 0) == {**step(scene, 20), "index": 0}, json.dumps(step(result, 0))[:400])
    check("open never writes the input project", Path(saved).read_bytes() == original)


def check_output_isolation(exe, root, scene):
    saved = Path(artifacts(scene, "project")[0]["path"])
    original = saved.read_bytes()
    code, result = run(
        exe, root, "input-alias", [{"op": "save", "path": saved.name}],
        project={"mode": "open", "path": str(saved)}, overwrite=True,
        output_directory=str(saved.parent) + "/.",
    )
    check("aliased output cannot replace input", code == agent.EXIT_JOB_ERROR and result.get("error", {}).get("code") == "INVALID_REQUEST", result.get("error"))
    check("aliased input remains unchanged", saved.read_bytes() == original)

    output = root / "staging-isolation"
    output.mkdir()
    stale_project = output / ".scene.miproject.partial"
    stale_project.write_bytes(b"keep project staging")
    stale_sequence = output / ".frames.partial"
    stale_sequence.mkdir()
    sentinel = stale_sequence / "keep.txt"
    sentinel.write_bytes(b"keep sequence staging")
    code, result = run(exe, root, "staging-isolation", [
        {"op": "create", "id": "cam", "kind": "camera"},
        {"op": "save", "path": "scene.miproject"},
        {"op": "render.sequence", "camera": "cam", "start": 0, "count": 1, "fps": 24, "path": "frames"},
    ])
    check("job uses fresh staging paths", code == agent.EXIT_OK, result.get("error"))
    check("preexisting project staging is untouched", stale_project.is_file() and stale_project.read_bytes() == b"keep project staging")
    check("preexisting sequence staging is untouched", sentinel.is_file() and sentinel.read_bytes() == b"keep sequence staging")

    frames = output / "frames"
    if frames.is_dir():
        existing = frames / "keep.txt"
        existing.write_bytes(b"keep published sequence")
        code, result = run(exe, root, "sequence-no-replace", [
            {"op": "create", "id": "cam", "kind": "camera"},
            {"op": "render.sequence", "camera": "cam", "start": 0, "count": 1, "fps": 24, "path": "frames"},
        ], overwrite=True, output_directory=output)
        check("sequence directory is never replaced", code == agent.EXIT_JOB_ERROR and result.get("error", {}).get("code") == "OUTPUT_EXISTS", result.get("error"))
        check("existing sequence is untouched", existing.is_file() and existing.read_bytes() == b"keep published sequence")

    linked_output = root / "linked-output"
    linked_output.mkdir()
    link = linked_output / "source"
    try:
        link.symlink_to(saved.parent, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        print("SKIP symlink output containment:", e)
    else:
        code, result = run(
            exe, root, "symlink-output", [{"op": "save", "path": "source/" + saved.name}],
            overwrite=True, output_directory=linked_output,
        )
        check("symlink cannot escape outputDirectory", code == agent.EXIT_JOB_ERROR and result.get("error", {}).get("code") == "INVALID_REQUEST", result.get("error"))
        check("symlink target remains unchanged", saved.read_bytes() == original)


def case_sensitive_directory(path):
    path.mkdir()
    if os.name == "nt":
        subprocess.run(["fsutil.exe", "file", "setCaseSensitiveInfo", str(path), "enable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (path / "Out").mkdir()
    try:
        (path / "out").mkdir()
    except FileExistsError:
        return False
    return True


def check_case_distinct_containment(exe, root):
    base = root / "case-escape"
    if not case_sensitive_directory(base):
        print("SKIP case-distinct containment: no case-sensitive directory available")
        return
    victim = base / "out" / "victim.miproject"
    victim.write_bytes(b"outside outputDirectory")
    try:
        (base / "Out" / "link").symlink_to(base / "out", target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        print("SKIP case-distinct containment:", e)
        return
    code, result = run(
        exe, root, "case-escape", [{"op": "create", "id": "cam", "kind": "camera"}, {"op": "save", "path": "link/victim.miproject"}],
        overwrite=True, output_directory=base / "Out",
    )
    check("case-distinct symlink cannot escape outputDirectory", code == agent.EXIT_JOB_ERROR and result.get("error", {}).get("code") == "INVALID_REQUEST", result.get("error"))
    check("file outside outputDirectory is untouched", victim.read_bytes() == b"outside outputDirectory")


def check_light_frame_order(exe, root):
    steps = [
        {"op": "create", "id": "box", "kind": "cube"},
        {"op": "create", "id": "lamp", "kind": "point_light"},
        {"op": "create", "id": "cam", "kind": "camera"},
        {"op": "keyframe.set", "target": "cam", "frame": 0, "values": {"POS_X": 0, "POS_Y": 0, "POS_Z": 8, "ROT_X": 20, "ROT_Y": 0, "ROT_Z": 0, "CAM_ROTATE": True, "CAM_ROTATE_DISTANCE": 80, "CAM_ROTATE_ANGLE_XY": 0, "CAM_ROTATE_ANGLE_Z": 20}},
        {"op": "keyframe.set", "target": "lamp", "frame": 0, "values": {"POS_X": -20, "POS_Y": -20, "POS_Z": 12, "LIGHT_RANGE": 40, "LIGHT_STRENGTH": 1}, "transition": "linear"},
        {"op": "keyframe.set", "target": "lamp", "frame": 24, "values": {"POS_X": 20}},
        {"op": "render.image", "camera": "cam", "frame": 0, "path": "a-first.png"},
        {"op": "render.image", "camera": "cam", "frame": 24, "path": "b.png"},
        {"op": "render.image", "camera": "cam", "frame": 0, "path": "a-again.png"},
    ]
    code, result = run(exe, root, "light-frame-order", steps)
    if not check("moving light job ok", code == agent.EXIT_OK, result.get("error")):
        return
    decoded = [check_png("moving light " + Path(a["path"]).name, a["path"], (320, 180)) for a in artifacts(result, "image")]
    if len(decoded) == 3 and all(decoded):
        check("light frame A is independent of previous capture", decoded[0] == decoded[2])
        check("moving light changes pixels", pngdecode.difference(decoded[0], decoded[1]) > 0.001)


def check_errors(exe, root):
    existing = root / "errors-exists"
    existing.mkdir(exist_ok=True)
    (existing / "taken.png").write_bytes(b"keep")
    corrupt = root / "corrupt.miproject"
    corrupt.write_text("{ not json", encoding="utf-8")

    cam = {"op": "create", "id": "cam", "kind": "camera"}
    cases = [
        ("errors-op", [{"op": "explode"}], None, "UNSUPPORTED"),
        ("errors-field", [{"op": "create", "id": "a", "kind": "camera", "color": 1}], None, "INVALID_REQUEST"),
        ("errors-frame", [cam, {"op": "keyframe.set", "target": "cam", "frame": 1.5, "values": {"POS_X": 1}}], None, "INVALID_REQUEST"),
        ("errors-nonnumber", [cam, {"op": "keyframe.set", "target": "cam", "frame": 1, "values": {"POS_X": "far"}}], None, "INVALID_REQUEST"),
        ("errors-duplicate", [cam, cam], None, "DUPLICATE_ID"),
        ("errors-asset", [{"op": "create", "id": "a", "kind": "character", "asset": "no_such_mob"}], None, "ASSET_NOT_FOUND"),
        ("errors-empty-part", [{"op": "create", "id": "actor", "kind": "cube"}, {"op": "save", "path": "before-error.miproject"}, {"op": "keyframe.set", "target": "actor/", "frame": 0, "values": {"POS_X": 1}}], None, "INVALID_REQUEST"),
        ("errors-control-character", [{"op": "create", "id": "a", "kind": "character", "asset": "missing\rasset"}], None, "ASSET_NOT_FOUND"),
        ("errors-part", [{"op": "create", "id": "a", "kind": "character", "asset": "human"}, {"op": "keyframe.set", "target": "a/tail", "frame": 0, "values": {"ROT_X": 1}}], None, "OBJECT_NOT_FOUND"),
        ("errors-forward", [{"op": "keyframe.set", "target": "cam", "frame": 0, "values": {"POS_X": 1}}, cam], None, "OBJECT_NOT_FOUND"),
        ("errors-value-kind", [cam, {"op": "keyframe.set", "target": "cam", "frame": 0, "values": {"SCA_X": 2}}], None, "UNSUPPORTED"),
        ("errors-camera", [{"op": "create", "id": "box", "kind": "cube"}, {"op": "render.image", "camera": "box", "frame": 0, "path": "a.png"}], None, "CAMERA_REQUIRED"),
        ("errors-traversal", [cam, {"op": "render.image", "camera": "cam", "frame": 0, "path": "../a.png"}], None, "INVALID_REQUEST"),
        ("errors-collision", [cam, {"op": "render.image", "camera": "cam", "frame": 0, "path": "a.png"}, {"op": "render.image", "camera": "cam", "frame": 1, "path": "A.png"}], None, "INVALID_REQUEST"),
        ("errors-exists", [cam, {"op": "render.image", "camera": "cam", "frame": 0, "path": "taken.png"}], None, "OUTPUT_EXISTS"),
        ("errors-quality", [cam, {"op": "render.image", "camera": "cam", "frame": 0, "path": "a.png", "quality": "high"}], None, "UNSUPPORTED"),
        ("errors-open-missing", [], {"mode": "open", "path": str(root / "missing.miproject")}, "PROJECT_LOAD_FAILED"),
        ("errors-open-corrupt", [], {"mode": "open", "path": str(corrupt)}, "PROJECT_LOAD_FAILED"),
    ]
    for name, steps, project, expected in cases:
        code, result = run(exe, root, name, steps, project=project, timeout=180)
        got = result.get("error", {}).get("code")
        check(f"{name} returns {expected}", code == agent.EXIT_JOB_ERROR and got == expected, f"exit {code}, {got}: {result.get('error', {}).get('message')}")
        check(f"{name} lists no artifacts", result.get("artifacts") == [], result.get("artifacts"))
    check("existing output is untouched", (existing / "taken.png").read_bytes() == b"keep")
    check("preflight failure publishes nothing", not (root / "errors-empty-part" / "before-error.miproject").exists())


def user_files(exe):
    data = Path(exe).parent / "Data" if os.name == "nt" else Path.home() / "Mine-imator"
    paths = [data / name for name in ("settings.midata", "recent.midata", "key.midata", "log.txt")]
    return {p: p.read_bytes() if p.is_file() else None for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--keep", help="write jobs, results and outputs here instead of a temporary directory")
    args = parser.parse_args()

    exe = str(Path(args.exe).resolve())
    before = user_files(exe)

    root = Path(args.keep).resolve() if args.keep else Path(tempfile.mkdtemp(prefix="mineimator-integration-"))
    root.mkdir(parents=True, exist_ok=True)
    print("Working in", root)

    scene = check_scene(exe, root)
    if scene:
        check_reopen(exe, root, scene)
        check_output_isolation(exe, root, scene)
    check_case_distinct_containment(exe, root)
    check_light_frame_order(exe, root)
    check_errors(exe, root)
    for path, content in user_files(exe).items():
        check(f"{path.name} in the user data folder is unchanged", content == before[path], "created or modified")

    print(f"{len(failures)} failed" if failures else "All checks passed")
    print("Jobs, results and PNGs retained in", root)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
