"""Drive a real Mine-imator window through the live session and check edits, undo and conflicts.

Usage: python Tools/agent/tests/live_check.py --exe path/to/Mine-imator.exe [--keep DIR]

Copies the built app to a temporary folder so its settings stay untouched, builds a test project
with a batch job, opens it in a normal editor window with agent editing on, then sends live
requests together with real keyboard and mouse input. Windows only. Needs an unlocked desktop,
and the mouse and keyboard must stay idle while it runs (about two minutes).
"""

import argparse
import ctypes
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import mineimator_agent as agent  # noqa: E402
from integration_check import check, failures, run  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x8, 0x10
VK_CONTROL, VK_MENU, VK_RETURN = 0x11, 0x12, 0x0D


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def send(*inputs):
    for item in inputs:
        user32.SendInput(1, (INPUT * 1)(item), ctypes.sizeof(INPUT))
        time.sleep(0.08)


def key(vk, up=False):
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


def chord(letter):
    vk = ord(letter.upper())
    send(key(VK_CONTROL), key(vk), key(vk, True), key(VK_CONTROL, True))


def type_text(text):
    for ch in text:
        send(INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, 0))),
             INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))
    send(key(VK_RETURN), key(VK_RETURN, True))


def drag(down):
    """Hold or release the right button over the viewport, which flies the work camera until release."""
    send(INPUT(INPUT_MOUSE, _INPUTUNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP, 0, 0))))
    time.sleep(0.5)


def main_window(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            found.append((rect.right - rect.left) * (rect.bottom - rect.top) and (hwnd, rect))
        return True

    user32.EnumWindows(visit, 0)
    windows = [w for w in found if w]
    return max(windows, key=lambda w: (w[1].right - w[1].left) * (w[1].bottom - w[1].top)) if windows else None


def focus(pid):
    window = main_window(pid)
    if not window:
        return False
    hwnd, rect = window
    send(key(VK_MENU), key(VK_MENU, True))
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    user32.SetCursorPos((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    return user32.GetForegroundWindow() == hwnd


class Live:
    def __init__(self, session):
        self.session = session

    def call(self, request, timeout=30):
        return agent.live_request(self.session, request, timeout)

    def ok(self, request, timeout=30):
        code, response = self.call(request, timeout)
        if code != agent.EXIT_OK:
            raise RuntimeError(f"{request.get('op')} failed: {response}")
        return response

    def inspect(self, **extra):
        return self.ok({"op": "inspect", **extra})

    def apply(self, state, operations, **extra):
        request = {"op": "apply", "documentGeneration": state["documentGeneration"], "expectedRevision": state["revision"], "operations": operations, **extra}
        return self.call(request)

    def background(self, request, timeout=30):
        box = {}
        thread = threading.Thread(target=lambda: box.update(result=self.call(request, timeout)), daemon=True)
        thread.start()
        return thread, box


def by_name(scene, name, part=None):
    for obj in scene["objects"]:
        if part is None and obj["name"] == name and obj["partOf"] is None:
            return obj
    root = by_name(scene, name) if part else None
    for obj in scene["objects"]:
        if root and obj["partOf"] == root["ref"] and obj["part"] == part:
            return obj
    raise KeyError((name, part))


def keyframe(obj, frame):
    return next((k for k in obj["keyframes"] if k["frame"] == frame), None)


def build_project(exe, root):
    steps = [
        {"op": "create", "id": "actor", "kind": "character", "asset": "human"},
        {"op": "create", "id": "box", "kind": "cube"},
        {"op": "create", "id": "locked", "kind": "cube"},
        {"op": "create", "id": "cam", "kind": "camera"},
        {"op": "keyframe.set", "target": "box", "frame": 0, "values": {"POS_X": 0, "POS_Z": 8}, "transition": "linear"},
        {"op": "keyframe.set", "target": "box", "frame": 24, "values": {"POS_X": 48}, "transition": "linear"},
        {"op": "keyframe.set", "target": "locked", "frame": 0, "values": {"POS_Z": 8}, "transition": "linear"},
        {"op": "save", "path": "scene.miproject"},
    ]
    code, result = run(exe, root, "live-scene", steps)
    if not check("batch job builds the test project", code == agent.EXIT_OK, result.get("error")):
        return None
    scene_file = root / "live-scene" / "scene.miproject"
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    next(t for t in scene["timelines"] if t["name"] == "locked")["lock"] = True
    scene_file.write_text(json.dumps(scene), encoding="utf-8")
    other = run(exe, root, "live-other", [{"op": "create", "id": "solo", "kind": "cube"}, {"op": "save", "path": "other.miproject"}])
    check("batch job builds a second project", other[0] == agent.EXIT_OK, other[1].get("error"))
    return root / "live-scene" / "scene.miproject", root / "live-other" / "other.miproject"


def check_review_regressions(live, pid):
    if not check("editor takes focus for keyboard regressions", focus(pid)):
        return

    before = live.inspect()
    box = by_name(before, "box")
    send(key(0x27), key(0x27, True))
    time.sleep(0.3)
    moved = live.inspect()
    check("arrow key moves the playhead", moved["project"]["frame"] != before["project"]["frame"])
    check("arrow key advances the revision", moved["revision"] > before["revision"], (before["revision"], moved["revision"]))
    code, stale = live.apply(before, [{"op": "keyframe.set", "target": box["ref"], "frame": 70, "values": {"POS_Y": 1}}])
    check("patch planned before arrow input is rejected", code == agent.EXIT_JOB_ERROR and stale.get("error", {}).get("code") == "REVISION_CONFLICT", stale)
    check("arrow-invalidated patch changes nothing", keyframe(by_name(live.inspect(), "box"), 70) is None)

    before = live.inspect()
    code, baseline = live.apply(before, [{"op": "keyframe.set", "target": box["ref"], "frame": 71, "values": {"POS_Y": 2}}])
    if check("undo race has a prior edit", code == agent.EXIT_OK, baseline):
        before = live.inspect()
        send(key(VK_CONTROL))
        try:
            pending = live.background({"op": "apply", "documentGeneration": before["documentGeneration"], "expectedRevision": before["revision"],
                                       "operations": [{"op": "keyframe.set", "target": box["ref"], "frame": 72, "values": {"POS_Y": 3}}]})
            time.sleep(0.3)
            check("patch waits while a keyboard shortcut is pending", pending[0].is_alive())
            send(key(ord("Z")), key(ord("Z"), True))
        finally:
            send(key(VK_CONTROL, True))
        pending[0].join(15)
        code, rejected = pending[1].get("result", (None, {}))
        check("Undo input invalidates the queued patch", code == agent.EXIT_JOB_ERROR and rejected.get("error", {}).get("code") == "REVISION_CONFLICT", rejected)
        after = live.inspect()
        check("Undo reverses the prior edit rather than the agent's pending patch", keyframe(by_name(after, "box"), 71) is None)
        check("patch queued alongside Undo creates no keyframe", keyframe(by_name(after, "box"), 72) is None)

    for frame in (0, 12):
        before = live.inspect()
        locked = by_name(before, "locked")
        check("transition test target is locked", locked["locked"])
        code, rejected = live.apply(before, [
            {"op": "keyframe.set", "target": box["ref"], "frame": 73 + frame, "values": {"POS_Y": 4}},
            {"op": "keyframe.set", "target": locked["ref"], "frame": frame, "values": {}, "transition": "easeinoutquad"},
        ])
        check(f"locked transition at frame {frame} rejects the entire patch", code == agent.EXIT_JOB_ERROR and rejected.get("error", {}).get("code") == "UNSUPPORTED", rejected)
        after = live.inspect()
        check(f"locked transition at frame {frame} preserves keys and revision", by_name(after, "locked")["keyframes"] == locked["keyframes"] and after["revision"] == before["revision"])
        check(f"locked transition at frame {frame} does not apply an earlier operation", keyframe(by_name(after, "box"), 73 + frame) is None)


def wait_for_session(live_dir, project_name, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sessions = agent.live_sessions()
        if sessions:
            code, hello = agent.live_request(sessions[0], {"op": "hello"}, 5)
            if code == agent.EXIT_OK and hello["project"]["file"] and Path(hello["project"]["file"]).name == project_name:
                return Live(sessions[0])
        time.sleep(1)
    return None


def check_live(live, pid, scene_file, other_file):
    hello = live.ok({"op": "hello"})
    check("hello reports generation, revision and keyframe.set", isinstance(hello["documentGeneration"], int) and isinstance(hello["revision"], int) and "keyframe.set" in hello["operations"], hello)

    scene = live.inspect()
    actor, box, arm = by_name(scene, "actor"), by_name(scene, "box"), by_name(scene, "actor", "right_arm")
    check("inspect lists objects by display name and ref", all(o["ref"] for o in (actor, box)) and box["type"] == "cube", box)
    check("inspect lists parts without keyframes", arm["keyframes"] == [] and "ROT_X" in arm["editable"], arm)
    check("camera values are editable only on cameras", "CAM_FOV" in by_name(scene, "cam")["editable"] and "CAM_FOV" not in box["editable"])

    marker = scene["project"]["frame"]
    mid = live.inspect(frame=12)
    check("inspect evaluates another frame", abs(by_name(mid, "box")["values"]["POS_X"] - 24) < 0.01, by_name(mid, "box")["values"])
    check("inspect leaves the playhead where it was", live.inspect()["project"]["frame"] == marker)

    code, stale = live.apply({**scene, "revision": scene["revision"] + 7}, [{"op": "keyframe.set", "target": box["ref"], "frame": 6, "values": {"POS_Y": 4}}])
    check("stale revision is rejected", code == agent.EXIT_JOB_ERROR and stale["error"]["code"] == "REVISION_CONFLICT", stale)
    code, other_doc = live.apply({**scene, "documentGeneration": scene["documentGeneration"] + 1}, [{"op": "keyframe.set", "target": box["ref"], "frame": 6, "values": {"POS_Y": 4}}])
    check("wrong document generation is rejected", code == agent.EXIT_JOB_ERROR and other_doc["error"]["code"] == "DOCUMENT_CHANGED", other_doc)

    code, half = live.apply(scene, [
        {"op": "keyframe.set", "target": box["ref"], "frame": 30, "values": {"POS_Y": 4}},
        {"op": "keyframe.set", "target": box["ref"], "frame": 31, "values": {"CAM_FOV": 40}},
    ])
    after_half = live.inspect()
    check("a patch with one invalid operation is rejected", code == agent.EXIT_JOB_ERROR and half["error"]["code"] == "UNSUPPORTED", half)
    check("a rejected patch changes nothing", keyframe(by_name(after_half, "box"), 30) is None and after_half["revision"] == scene["revision"], after_half["revision"])

    patch = [
        {"op": "keyframe.set", "target": arm["ref"], "frame": 20, "values": {"ROT_X": -30}},
        {"op": "keyframe.set", "target": box["ref"], "frame": 0, "values": {"POS_Z": 16}},
        {"op": "keyframe.set", "target": box["ref"], "frame": 24, "values": {"POS_X": 64}, "transition": "easeinoutquad"},
    ]
    request = {"op": "apply", "requestId": "live-check-1", "documentGeneration": scene["documentGeneration"], "expectedRevision": scene["revision"], "label": "raise arm", "operations": patch}
    code, applied = live.call(request)
    check("patch applies", code == agent.EXIT_OK and [a["created"] for a in applied["applied"]] == [True, False, False], applied)
    check("patch advances the revision by one", applied.get("revision") == scene["revision"] + 1, applied.get("revision"))
    edited = live.inspect()
    arm_kf, box0, box24 = keyframe(by_name(edited, "actor", "right_arm"), 20), keyframe(by_name(edited, "box"), 0), keyframe(by_name(edited, "box"), 24)
    check("new part keyframe has the value", arm_kf and arm_kf["values"]["ROT_X"] == -30, arm_kf)
    check("existing keyframes are updated", box0["values"]["POS_Z"] == 16 and box0["values"]["POS_X"] == 0 and box24["values"]["POS_X"] == 64 and box24["transition"] == "easeinoutquad", (box0, box24))

    code, retried = live.call(request)
    check("retrying a requestId returns the first reply", code == agent.EXIT_OK and retried == applied, retried)
    check("retrying a requestId does not apply again", live.ok({"op": "hello"})["revision"] == applied["revision"])

    dropped = {"op": "apply", "requestId": "live-check-dropped", "documentGeneration": edited["documentGeneration"], "expectedRevision": edited["revision"],
               "operations": [{"op": "keyframe.set", "target": box["ref"], "frame": 40, "values": {"POS_Y": 2}}]}
    with socket.create_connection((live.session["host"], live.session["port"]), timeout=5) as conn:
        conn.sendall(json.dumps(dict(dropped, token=live.session["token"])).encode() + b"\n")
    time.sleep(1)
    code, redelivered = live.call(dropped)
    check("a request whose connection dropped applied once and its retry returns that result", code == agent.EXIT_OK and redelivered["revision"] == edited["revision"] + 1, redelivered)
    count = sum(1 for k in by_name(live.inspect(), "box")["keyframes"] if k["frame"] == 40)
    check("the dropped request made one keyframe", count == 1, count)

    if not check("editor window takes keyboard focus", focus(pid)):
        return

    chord("z")
    time.sleep(1)
    chord("z")
    time.sleep(1)
    undone = live.inspect()
    box_undone = by_name(undone, "box")
    check("one undo reverses the whole agent patch", keyframe(by_name(undone, "actor", "right_arm"), 20) is None and keyframe(box_undone, 0)["values"]["POS_Z"] == 8 and
          keyframe(box_undone, 24)["values"]["POS_X"] == 48 and keyframe(box_undone, 24)["transition"] == "linear", box_undone["keyframes"])
    check("undo advances the revision", undone["revision"] > redelivered["revision"], undone["revision"])

    chord("y")
    time.sleep(1)
    redone = live.inspect()
    check("redo restores the agent patch", keyframe(by_name(redone, "actor", "right_arm"), 20) and keyframe(by_name(redone, "box"), 24)["values"]["POS_X"] == 64)
    check("redo advances the revision", redone["revision"] > undone["revision"])

    drag(True)
    thread, box_reply = live.background({"op": "apply", "documentGeneration": redone["documentGeneration"], "expectedRevision": redone["revision"],
                                         "operations": [{"op": "keyframe.set", "target": box["ref"], "frame": 50, "values": {"POS_Y": 1}}]})
    time.sleep(2)
    waiting = thread.is_alive()
    drag(False)
    thread.join(15)
    code, held = box_reply.get("result", (None, {}))
    check("a request waits while the user drags in the viewport", waiting)
    check("a request that waited for a drag is revalidated", code == agent.EXIT_JOB_ERROR and held["error"]["code"] == "REVISION_CONFLICT", held)
    check("the revalidated request changed nothing", keyframe(by_name(live.inspect(), "box"), 50) is None)

    drag(True)
    code, timed_out = live.call({"op": "inspect"}, timeout=1)
    check("a client timeout returns TIMEOUT", code == agent.EXIT_TIMEOUT and timed_out["error"]["code"] == "TIMEOUT", timed_out)
    state = live.background({"op": "hello"}, timeout=20)
    time.sleep(11)
    drag(False)
    state[0].join(15)
    code, busy = state[1].get("result", (None, {}))
    check("a request that waits more than 10 seconds gets EDITOR_BUSY", code == agent.EXIT_JOB_ERROR and busy["error"]["code"] == "EDITOR_BUSY", busy)
    check("the editor keeps running after a client timeout", live.ok({"op": "hello"})["status"] == "ok")

    check_review_regressions(live, pid)

    current = live.inspect()
    chord("s")
    time.sleep(2)
    chord("o")
    time.sleep(2)
    queued = live.background({"op": "apply", "documentGeneration": current["documentGeneration"], "expectedRevision": current["revision"],
                              "operations": [{"op": "keyframe.set", "target": box["ref"], "frame": 60, "values": {"POS_Y": 1}}]})
    type_text(str(other_file))
    queued[0].join(20)
    code, switched = queued[1].get("result", (None, {}))
    check("a request queued before another project opened is rejected", code == agent.EXIT_JOB_ERROR and switched["error"]["code"] == "DOCUMENT_CHANGED", switched)
    reopened = live.inspect()
    check("the other project is open", Path(reopened["project"]["file"]).name == other_file.name, reopened["project"])
    check("the document generation changed", reopened["documentGeneration"] != current["documentGeneration"])
    saved = json.loads(scene_file.read_text(encoding="utf-8"))
    check("the saved scene file is still a project", isinstance(saved, dict))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--keep", help="keep the app copy and projects here instead of a temporary directory")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("live_check.py drives Windows input and runs on Windows only")
        return 1

    root = Path(args.keep).resolve() if args.keep else Path(tempfile.mkdtemp(prefix="mineimator-live-"))
    root.mkdir(parents=True, exist_ok=True)
    print("Working in", root)
    app = root / "app"
    if not app.exists():
        shutil.copytree(Path(args.exe).resolve().parent, app)
    exe = str(app / Path(args.exe).name)
    if not (app / "Data" / "settings.midata").exists():
        print("The app folder has no Data/settings.midata. Start Mine-imator once and close it, then rerun")
        return 1

    projects = build_project(exe, root)
    if not projects:
        return 1
    scene_file, other_file = projects

    live_dir = root / "live"
    agent.os.environ["MINEIMATOR_LIVE_DIR"] = str(live_dir)
    env = dict(agent.os.environ, MINEIMATOR_LIVE_SESSION="1")
    proc = subprocess.Popen([exe, str(scene_file)], cwd=str(app), env=env)
    try:
        live = wait_for_session(live_dir, scene_file.name)
        if check("an editor window with the project allows agent editing", live is not None):
            check("the session file points at loopback with a token", "token" in live.session and live.session["host"] == "127.0.0.1", live.session)
            check_live(live, proc.pid, scene_file, other_file)
        check("the editor is still running", proc.poll() is None, proc.poll())
    finally:
        proc.kill()
        proc.wait()

    check("the session file is gone or unreachable after the editor exits", agent.live_sessions() == [])
    print(f"{len(failures)} failed" if failures else "All checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
