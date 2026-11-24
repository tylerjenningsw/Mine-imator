#!/usr/bin/env python3
"""Run one Mine-imator automation job in an isolated app process and print its result JSON."""

import argparse
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

RESULT_SCHEMA = "mineimator.result/1"
LIVE_SCHEMA = "mineimator.live/1"
LIVE_SESSION_SCHEMA = "mineimator.live-session/1"
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


def live_directory():
    override = os.environ.get("MINEIMATOR_LIVE_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "Mine-imator" / "live"


def _live_error(code, message, **extra):
    return {"schema": LIVE_SCHEMA, "status": "error", "error": {"code": code, "message": message}, **extra}


def _reachable(session):
    try:
        with socket.create_connection((session["host"], session["port"]), timeout=0.5):
            return True
    except (OSError, KeyError, TypeError):
        return False


def live_sessions():
    """Return the live sessions whose editor is still listening, newest first."""
    sessions = []
    for path in sorted(live_directory().glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                session = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(session, dict) and session.get("schema") == LIVE_SESSION_SCHEMA and _reachable(session):
            sessions.append(session)
    return sorted(sessions, key=lambda s: str(s.get("started", "")), reverse=True)


def _pick_session(session_id):
    sessions = live_sessions()
    if session_id:
        sessions = [s for s in sessions if s.get("sessionId") == session_id]
    if len(sessions) == 1:
        return sessions[0], None
    if not sessions:
        return None, _live_error("NO_SESSION", "No open Mine-imator window allows agent editing. Choose Edit > Allow agent editing")
    ids = ", ".join(str(s.get("sessionId")) for s in sessions)
    return None, _live_error("SESSION_AMBIGUOUS", f"Several windows allow agent editing ({ids}). Pass --session")


def live_request(session, request, timeout):
    """Send one request to a live session and return (exit_code, response). Never stops the editor."""
    payload = dict(request, token=session["token"])
    request_id = request.get("requestId")
    try:
        with socket.create_connection((session["host"], session["port"]), timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
            data = b""
            deadline = time.monotonic() + timeout
            while not data.endswith(b"\n"):
                conn.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
    except socket.timeout:
        message = f"No reply within {timeout} seconds. The editor keeps running"
        if request_id:
            message += f"; the request may still apply, so retry with requestId {request_id}"
        return EXIT_TIMEOUT, _live_error("TIMEOUT", message, requestId=request_id)
    except OSError as e:
        return EXIT_APP_FAILED, _live_error("CONNECTION_FAILED", f"Could not reach session {session.get('sessionId')}: {e}", requestId=request_id)

    try:
        response = json.loads(data.decode("utf-8"))
    except ValueError:
        return EXIT_APP_FAILED, _live_error("INVALID_RESULT", "The editor closed the connection without a valid reply", requestId=request_id)
    if not isinstance(response, dict) or response.get("schema") != LIVE_SCHEMA:
        return EXIT_APP_FAILED, _live_error("INVALID_RESULT", "The reply is not a mineimator.live/1 object", requestId=request_id)
    return (EXIT_OK if response.get("status") == "ok" else EXIT_JOB_ERROR), response


def _live_main(args):
    if args.live_command == "sessions":
        sessions = [{k: v for k, v in s.items() if k != "token"} for s in live_sessions()]
        return EXIT_OK, {"schema": LIVE_SCHEMA, "status": "ok", "sessions": sessions}

    if args.live_command == "apply":
        try:
            with open(args.patch, encoding="utf-8") as f:
                request = json.load(f)
        except (OSError, ValueError) as e:
            return EXIT_USAGE, _live_error("INVALID_REQUEST", f"Could not read patch {args.patch}: {e}")
        if not isinstance(request, dict):
            return EXIT_USAGE, _live_error("INVALID_REQUEST", "The patch must be a JSON object")
        request["op"] = "apply"
        request.setdefault("requestId", str(uuid.uuid4()))
    elif args.live_command == "inspect":
        request = {"op": "inspect"}
        if args.frame is not None:
            request["frame"] = args.frame
        if args.selected:
            request["selectedOnly"] = True
    else:
        request = {"op": "hello"}

    session, error = _pick_session(args.session)
    if error:
        return EXIT_USAGE, error
    return live_request(session, request, args.timeout)


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

    live = sub.add_parser("live", help="read and edit the scene in an open Mine-imator window")
    live_sub = live.add_subparsers(dest="live_command", required=True)
    live_sub.add_parser("sessions", help="list open windows that allow agent editing")
    for name, text in (("hello", "show the session state and supported values"), ("inspect", "print the open scene"), ("apply", "apply a patch as one undo step")):
        cmd = live_sub.add_parser(name, help=text)
        cmd.add_argument("--session", help="session id from `live sessions` (needed when several windows allow editing)")
        cmd.add_argument("--timeout", type=float, default=30, help="seconds to wait for a reply (default: 30)")
        if name == "inspect":
            cmd.add_argument("--frame", type=float, help="evaluate values at this frame without moving the playhead")
            cmd.add_argument("--selected", action="store_true", help="only list selected objects")
        if name == "apply":
            cmd.add_argument("patch", help="path to a JSON patch with documentGeneration, expectedRevision and operations")

    args = parser.parse_args(argv)

    if args.command == "example":
        sys.stdout.write(EXAMPLE_JOB.read_text(encoding="utf-8"))
        return EXIT_OK

    if args.command == "live":
        if args.live_command != "sessions" and (not math.isfinite(args.timeout) or args.timeout <= 0):
            parser.error("--timeout must be finite and positive")
        code, result = _live_main(args)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return code

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
