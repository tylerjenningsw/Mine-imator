#!/usr/bin/env python3
"""Run one Mine-imator automation job in an isolated app process and print its result JSON."""

import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RESULT_SCHEMA = "mineimator.result/1"
EXAMPLE_JOB = Path(__file__).with_name("example-job.json")

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_JOB_ERROR = 2
EXIT_TIMEOUT = 3
EXIT_APP_FAILED = 4
EXIT_CANCELLED = 130

APP_REFUSED = 3
LOG_TAIL_LINES = 40
POLL_SECONDS = 0.25


def _error_result(code, message, job_id=None):
    return {
        "schema": RESULT_SCHEMA,
        "id": job_id,
        "status": "error",
        "error": {"code": code, "message": message, "step": None},
        "steps": [],
        "artifacts": [],
    }


def _job_id(job_path):
    try:
        with open(job_path, encoding="utf-8") as f:
            job_id = json.load(f).get("id")
        return job_id if isinstance(job_id, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def _tail(path, lines=LOG_TAIL_LINES):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-lines:]]
    except OSError:
        return []


def _valid_result(result):
    if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA:
        return False
    if result.get("status") == "ok":
        return True
    error = result.get("error")
    return result.get("status") == "error" and isinstance(error, dict) and isinstance(error.get("code"), str)


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def run_job(job_path, command, timeout, keep_work=False):
    """Run one job with `command` (the app executable plus arguments).

    Returns (exit_code, result_dict). A result is always returned, even when the
    app hangs, crashes, is cancelled with Ctrl+C or exits without writing one.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        return EXIT_USAGE, _error_result("INVALID_REQUEST", "Timeout must be finite and positive")

    job_path = Path(job_path).resolve()
    if not job_path.is_file():
        return EXIT_USAGE, _error_result("INVALID_REQUEST", f"Job file not found: {job_path}")

    work = Path(tempfile.mkdtemp(prefix="mineimator-job-"))
    result_path = work / "result.json"
    log_path = work / "result.json.work" / "log.txt"
    stderr_path = work / "stderr.txt"

    env = dict(os.environ)
    env["MINEIMATOR_AUTOMATION_JOB"] = str(job_path)
    env["MINEIMATOR_AUTOMATION_RESULT"] = str(result_path)

    exe = Path(command[0])
    timed_out = False
    cancelled = False
    with open(stderr_path, "wb") as stderr:
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(exe.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=(os.name != "nt"),
            )
        except OSError as e:
            shutil.rmtree(work, ignore_errors=True)
            return EXIT_USAGE, _error_result("STARTUP_FAILED", f"Could not start {command[0]}: {e}", _job_id(job_path))

        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    returncode = proc.wait(timeout=max(0.0, min(POLL_SECONDS, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        _kill_tree(proc)
                        returncode = proc.returncode
                        timed_out = True
                        break
        except KeyboardInterrupt:
            _kill_tree(proc)
            returncode = proc.returncode
            cancelled = True
        except BaseException:
            _kill_tree(proc)
            raise

    result = None
    if result_path.is_file():
        try:
            with open(result_path, encoding="utf-8") as f:
                result = json.load(f)
            if not _valid_result(result):
                result = _error_result("INVALID_RESULT", "The app wrote a result that is not a valid mineimator.result/1 object", _job_id(job_path))
        except (OSError, ValueError) as e:
            result = _error_result("INVALID_RESULT", f"Could not read result: {e}", _job_id(job_path))

    if cancelled:
        code = EXIT_CANCELLED
        result = _error_result("CANCELLED", "Cancelled; the app was stopped", _job_id(job_path))
    elif result is None and timed_out:
        code = EXIT_TIMEOUT
        result = _error_result("TIMEOUT", f"Job did not finish within {timeout} seconds; the app was stopped", _job_id(job_path))
    elif result is None:
        code = EXIT_APP_FAILED
        if returncode == APP_REFUSED:
            message = "The app refused the automation request"
        else:
            message = f"The app exited with code {returncode} without writing a result"
        result = _error_result("NO_RESULT", message, _job_id(job_path))
    elif result["status"] == "ok":
        code = EXIT_OK
    elif result["error"]["code"] in ("INVALID_RESULT", "FATAL"):
        code = EXIT_APP_FAILED
    else:
        code = EXIT_JOB_ERROR

    if code != EXIT_OK:
        stderr_tail = _tail(stderr_path)
        result["launcher"] = {
            "exitCode": returncode,
            "workDirectory": str(work),
            "logTail": _tail(log_path),
        }
        if stderr_tail:
            result["launcher"]["stderrTail"] = stderr_tail
    elif not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    else:
        result["launcher"] = {"exitCode": returncode, "workDirectory": str(work)}

    return code, result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def main(argv=None):
    parser = _Parser(
        prog="mineimator_agent.py",
        description="Build, animate, save and render Mine-imator scenes from a JSON job.",
        epilog=(
            "Exit codes: 0 ok, 1 usage, 2 job error (see result.error), 3 timeout, 4 app failed, 130 cancelled. "
            "See Tools/agent/README.md for the job format."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one job and print the result JSON")
    run.add_argument("job", help="path to a mineimator.job/1 JSON file")
    run.add_argument("--exe", default=os.environ.get("MINEIMATOR_EXE"), help="Mine-imator executable (default: $MINEIMATOR_EXE)")
    run.add_argument("--timeout", type=float, default=600, help="seconds before the app is stopped (default: 600)")
    run.add_argument("--keep-work", action="store_true", help="keep the temporary work directory and log after success")

    sub.add_parser("example", help="print an example job")

    args = parser.parse_args(argv)

    if args.command == "example":
        sys.stdout.write(EXAMPLE_JOB.read_text(encoding="utf-8"))
        return EXIT_OK

    if not args.exe:
        parser.error("--exe is required when MINEIMATOR_EXE is not set")
    if not Path(args.exe).is_file():
        parser.error(f"executable not found: {args.exe}")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")

    code, result = run_job(args.job, [str(Path(args.exe).resolve())], args.timeout, args.keep_work)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
