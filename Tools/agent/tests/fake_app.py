"""Stand-in for the Mine-imator executable. The job id selects the behavior."""

import json
import os
import subprocess
import sys
import time

job_path = os.environ["MINEIMATOR_AUTOMATION_JOB"]
result_path = os.environ["MINEIMATOR_AUTOMATION_RESULT"]
with open(job_path, encoding="utf-8") as f:
    job = json.load(f)
mode = job["id"]

work = result_path + ".work"
os.makedirs(work, exist_ok=True)
with open(os.path.join(work, "log.txt"), "w", encoding="utf-8") as f:
    f.write("fake app started\nmode " + mode + "\n")


def write_result(status, extra=None):
    result = {"schema": "mineimator.result/1", "id": mode, "status": status, "steps": [], "artifacts": [], "cwd": os.getcwd()}
    result.update(extra or {})
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f)


if mode == "ok":
    write_result("ok")
elif mode == "job-error":
    write_result("error", {"error": {"code": "ASSET_NOT_FOUND", "message": "Unknown asset", "step": 1}})
elif mode.startswith("bad-error-"):
    shapes = {"null": None, "list": [], "number": 42, "string": "oops", "no-code": {"message": "x"}}
    write_result("error", {"error": shapes[mode[len("bad-error-"):]]})
elif mode == "fatal":
    write_result("error", {"error": {"code": "FATAL", "message": "boom", "step": None}})
elif mode == "garbage":
    with open(result_path, "w", encoding="utf-8") as f:
        f.write("not json")
elif mode == "wrong-schema":
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({"schema": "other", "status": "ok"}, f)
elif mode == "crash":
    sys.exit(1)
elif mode == "refuse":
    sys.stderr.write("Mine-imator automation refused: result file already exists\n")
    sys.exit(3)
elif mode == "hang":
    time.sleep(60)
elif mode == "hang-after-result":
    write_result("ok")
    time.sleep(60)
elif mode == "hang-with-child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(job["childPidFile"], "w", encoding="utf-8") as f:
        f.write(str(child.pid))
    if "appPidFile" in job:
        with open(job["appPidFile"], "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    time.sleep(60)
