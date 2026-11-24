import _thread
import contextlib
import io
import threading
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import mineimator_agent as agent  # noqa: E402
import pngdecode  # noqa: E402

FAKE_APP = [sys.executable, str(HERE / "fake_app.py")]


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def job(self, mode, **extra):
        path = Path(self.dir.name) / f"{mode}.json"
        path.write_text(json.dumps({"schema": "mineimator.job/1", "id": mode, **extra}), encoding="utf-8")
        return path

    def run_mode(self, mode, timeout=30, **extra):
        return agent.run_job(self.job(mode, **extra), FAKE_APP, timeout)

    def test_ok_returns_app_result_from_executable_directory(self):
        code, result = self.run_mode("ok")
        self.assertEqual(code, agent.EXIT_OK)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("launcher", result)
        self.assertEqual(Path(result["cwd"]).resolve(), Path(sys.executable).resolve().parent)

    def test_environment_is_set_only_for_the_child(self):
        self.run_mode("ok")
        self.assertNotIn("MINEIMATOR_AUTOMATION_JOB", os.environ)
        self.assertNotIn("MINEIMATOR_AUTOMATION_RESULT", os.environ)

    def test_job_error_keeps_work_directory_and_log_tail(self):
        code, result = self.run_mode("job-error")
        self.assertEqual(code, agent.EXIT_JOB_ERROR)
        self.assertEqual(result["error"]["code"], "ASSET_NOT_FOUND")
        self.assertIn("mode job-error", result["launcher"]["logTail"])
        self.assertTrue(Path(result["launcher"]["workDirectory"]).is_dir())

    def test_fatal_result_is_an_app_failure(self):
        code, result = self.run_mode("fatal")
        self.assertEqual(code, agent.EXIT_APP_FAILED)
        self.assertEqual(result["error"]["code"], "FATAL")

    def test_missing_result_after_crash(self):
        code, result = self.run_mode("crash")
        self.assertEqual(code, agent.EXIT_APP_FAILED)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "NO_RESULT")
        self.assertEqual(result["id"], "crash")
        self.assertEqual(result["launcher"]["exitCode"], 1)

    def test_refusal_is_reported_with_stderr(self):
        code, result = self.run_mode("refuse")
        self.assertEqual(code, agent.EXIT_APP_FAILED)
        self.assertIn("refused", result["error"]["message"])
        self.assertTrue(any("refused" in line for line in result["launcher"]["stderrTail"]))

    def test_unreadable_or_foreign_result_is_never_success(self):
        for mode in ("garbage", "wrong-schema"):
            code, result = self.run_mode(mode)
            self.assertEqual(code, agent.EXIT_APP_FAILED, mode)
            self.assertEqual(result["error"]["code"], "INVALID_RESULT", mode)

    def test_timeout_stops_the_app(self):
        start = time.monotonic()
        code, result = self.run_mode("hang", timeout=2)
        self.assertLess(time.monotonic() - start, 30)
        self.assertEqual(code, agent.EXIT_TIMEOUT)
        self.assertEqual(result["error"]["code"], "TIMEOUT")

    def test_timeout_stops_child_processes(self):
        pid_file = Path(self.dir.name) / "child.pid"
        code, _ = self.run_mode("hang-with-child", timeout=3, childPidFile=str(pid_file))
        self.assertEqual(code, agent.EXIT_TIMEOUT)
        child = int(pid_file.read_text())
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))

    def test_interrupt_stops_the_app_and_its_children(self):
        child_file = Path(self.dir.name) / "child.pid"
        app_file = Path(self.dir.name) / "app.pid"
        job = self.job("hang-with-child", childPidFile=str(child_file), appPidFile=str(app_file))

        def interrupt_when_started():
            for _ in range(100):
                if child_file.exists() and app_file.exists():
                    break
                time.sleep(0.1)
            _thread.interrupt_main()

        threading.Thread(target=interrupt_when_started, daemon=True).start()
        start = time.monotonic()
        code, result = agent.run_job(job, FAKE_APP, 60)
        self.assertLess(time.monotonic() - start, 30)
        self.assertEqual(code, agent.EXIT_CANCELLED)
        self.assertEqual(result["error"]["code"], "CANCELLED")
        self.assertIn("mode hang-with-child", result["launcher"]["logTail"])
        self.assertTrue(Path(result["launcher"]["workDirectory"]).is_dir())
        for pid in (int(app_file.read_text()), int(child_file.read_text())):
            self.assertFalse(pid_alive(pid), pid)

    def test_malformed_error_object_is_an_invalid_result(self):
        for shape in ("null", "list", "number", "string", "no-code"):
            with self.subTest(shape=shape):
                code, result = self.run_mode("bad-error-" + shape)
                self.assertEqual(code, agent.EXIT_APP_FAILED)
                self.assertEqual(result["error"]["code"], "INVALID_RESULT")
                self.assertIn("logTail", result["launcher"])

    def test_result_written_before_a_hang_is_kept(self):
        code, result = self.run_mode("hang-after-result", timeout=3)
        self.assertEqual(code, agent.EXIT_OK)
        self.assertEqual(result["status"], "ok")

    def test_missing_job_file(self):
        code, result = agent.run_job(Path(self.dir.name) / "missing.json", FAKE_APP, 5)
        self.assertEqual(code, agent.EXIT_USAGE)
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")

    def test_invalid_timeout_returns_usage_error_before_starting(self):
        for timeout in (float("nan"), float("inf"), float("-inf"), 0, -1):
            with self.subTest(timeout=timeout):
                code, result = agent.run_job(self.job("ok"), FAKE_APP, timeout)
                self.assertEqual(code, agent.EXIT_USAGE)
                self.assertEqual(result["error"]["code"], "INVALID_REQUEST")

    def test_unstartable_executable(self):
        code, result = agent.run_job(self.job("ok"), [str(Path(self.dir.name) / "missing.exe")], 5)
        self.assertEqual(code, agent.EXIT_USAGE)
        self.assertEqual(result["error"]["code"], "STARTUP_FAILED")


class CliTest(unittest.TestCase):
    def test_example_is_a_job(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(agent.main(["example"]), agent.EXIT_OK)
        job = json.loads(out.getvalue())
        self.assertEqual(job["schema"], "mineimator.job/1")
        self.assertTrue(job["steps"])

    def test_run_requires_an_existing_executable(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            agent.main(["run", "job.json", "--exe", "definitely-missing.exe"])
        self.assertEqual(raised.exception.code, agent.EXIT_USAGE)

    def test_help_mentions_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            agent.main(["--help"])
        self.assertIn("Exit codes", out.getvalue())

    def test_nonfinite_timeout_is_rejected_by_cli(self):
        for timeout in ("nan", "inf"):
            with self.subTest(timeout=timeout):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    agent.main(["run", "job.json", "--exe", sys.executable, "--timeout", timeout])
                self.assertEqual(raised.exception.code, agent.EXIT_USAGE)

    def test_missing_arguments_are_usage_errors(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            agent.main(["run"])
        self.assertEqual(raised.exception.code, agent.EXIT_USAGE)


class FakeEditor:
    def __init__(self, reply):
        self.reply = reply
        self.received = []
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    continue
                self.received.append(json.loads(data))
                if self.reply is not None:
                    conn.sendall(self.reply(self.received[-1]))
                else:
                    time.sleep(3)

    def close(self):
        self.server.close()

    def session(self, session_id="s1"):
        return {"schema": agent.LIVE_SESSION_SCHEMA, "sessionId": session_id, "host": "127.0.0.1", "port": self.port, "token": "secret", "started": session_id}


def ok_reply(request):
    return json.dumps({"schema": agent.LIVE_SCHEMA, "status": "ok", "requestId": request.get("requestId")}).encode() + b"\n"


class LiveClientTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        os.environ["MINEIMATOR_LIVE_DIR"] = self.dir.name
        self.addCleanup(os.environ.pop, "MINEIMATOR_LIVE_DIR")

    def editor(self, reply=ok_reply, session_id="s1"):
        editor = FakeEditor(reply)
        self.addCleanup(editor.close)
        Path(self.dir.name, f"{session_id}.json").write_text(json.dumps(editor.session(session_id)), encoding="utf-8")
        return editor

    def live(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = agent.main(["live", *argv])
        return code, json.loads(out.getvalue())

    def test_request_carries_token_and_apply_gets_a_request_id(self):
        editor = self.editor()
        patch = Path(self.dir.name, "patch.txt")
        patch.write_text(json.dumps({"documentGeneration": 1, "expectedRevision": 2, "operations": []}), encoding="utf-8")
        code, reply = self.live("apply", str(patch))
        self.assertEqual(code, agent.EXIT_OK)
        sent = editor.received[-1]
        self.assertEqual((sent["op"], sent["token"]), ("apply", "secret"))
        self.assertTrue(sent["requestId"])
        self.assertEqual(reply["requestId"], sent["requestId"])

    def test_sessions_skip_stale_files_and_hide_tokens(self):
        self.editor()
        closed = socket.create_server(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        stale = {"schema": agent.LIVE_SESSION_SCHEMA, "sessionId": "stale", "host": "127.0.0.1", "port": port, "token": "x"}
        Path(self.dir.name, "stale.json").write_text(json.dumps(stale), encoding="utf-8")
        code, reply = self.live("sessions")
        self.assertEqual(code, agent.EXIT_OK)
        self.assertEqual([s["sessionId"] for s in reply["sessions"]], ["s1"])
        self.assertNotIn("token", reply["sessions"][0])

    def test_no_session_and_several_sessions_are_usage_errors(self):
        code, reply = self.live("hello")
        self.assertEqual((code, reply["error"]["code"]), (agent.EXIT_USAGE, "NO_SESSION"))
        self.editor(session_id="a")
        self.editor(session_id="b")
        code, reply = self.live("hello")
        self.assertEqual((code, reply["error"]["code"]), (agent.EXIT_USAGE, "SESSION_AMBIGUOUS"))
        code, reply = self.live("hello", "--session", "b")
        self.assertEqual(code, agent.EXIT_OK)

    def test_editor_error_is_a_request_error(self):
        self.editor(lambda r: json.dumps({"schema": agent.LIVE_SCHEMA, "status": "error", "error": {"code": "REVISION_CONFLICT", "message": "x"}}).encode() + b"\n")
        code, reply = self.live("inspect", "--frame", "3")
        self.assertEqual((code, reply["error"]["code"]), (agent.EXIT_JOB_ERROR, "REVISION_CONFLICT"))

    def test_timeout_stops_waiting_without_touching_the_editor(self):
        editor = self.editor(reply=None)
        start = time.monotonic()
        code, reply = agent.live_request(editor.session(), {"op": "apply", "requestId": "r1"}, 0.5)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual((code, reply["error"]["code"]), (agent.EXIT_TIMEOUT, "TIMEOUT"))
        self.assertIn("r1", reply["error"]["message"])

    def test_foreign_reply_is_invalid(self):
        editor = self.editor(lambda r: b"{\"hello\": 1}\n")
        code, reply = agent.live_request(editor.session(), {"op": "hello"}, 5)
        self.assertEqual((code, reply["error"]["code"]), (agent.EXIT_APP_FAILED, "INVALID_RESULT"))


class PngDecodeTest(unittest.TestCase):
    def write_png(self, path, width, height, pixel_at, filter_type):
        rows = b""
        for y in range(height):
            row = b"".join(bytes(pixel_at(x, y)) for x in range(width))
            if filter_type == 1:
                row = bytes((row[i] - (row[i - 4] if i >= 4 else 0)) & 0xFF for i in range(len(row)))
            rows += bytes([filter_type]) + row

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        path.write_bytes(pngdecode.SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    def test_decodes_filtered_rgba(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.png"
            self.write_png(path, 3, 2, lambda x, y: (x * 10, y * 20, 7, 255), 1)
            width, height, channels, rows = pngdecode.decode(path)
            self.assertEqual((width, height, channels), (3, 2, 4))
            self.assertEqual(rows[1][4:8], bytes((10, 20, 7, 255)))
            self.assertFalse(pngdecode.is_flat(width, channels, rows))

            flat = Path(d) / "flat.png"
            self.write_png(flat, 3, 2, lambda x, y: (1, 2, 3, 255), 0)
            _, _, _, flat_rows = pngdecode.decode(flat)
            self.assertTrue(pngdecode.is_flat(3, 4, flat_rows))
            self.assertGreater(pngdecode.difference(rows, flat_rows), 0.5)


if __name__ == "__main__":
    unittest.main()
