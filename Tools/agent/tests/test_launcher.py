import contextlib
import io
import json
import os
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

    def test_result_written_before_a_hang_is_kept(self):
        code, result = self.run_mode("hang-after-result", timeout=3)
        self.assertEqual(code, agent.EXIT_OK)
        self.assertEqual(result["status"], "ok")

    def test_missing_job_file(self):
        code, result = agent.run_job(Path(self.dir.name) / "missing.json", FAKE_APP, 5)
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
        self.assertEqual(raised.exception.code, 2)

    def test_help_mentions_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            agent.main(["--help"])
        self.assertIn("Exit codes", out.getvalue())


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
