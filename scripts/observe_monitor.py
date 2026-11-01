"""只读观察真实监测年龄与 Linux I/O 等待，不刷新监测、不控制任务。"""
import argparse
import json
import re
from pathlib import Path
import time


def observe(var, worker_pid=None, job_id=None):
    started = time.monotonic()
    row = {"observed_at": time.time()}
    try:
        state = json.loads((var / "monitor.json").read_text())
        row.update(sample_age_seconds=round(time.time() - state["updated_at"], 3),
                   available=state.get("available"), error=state.get("error"))
    except Exception as error:
        row["monitor_error"] = type(error).__name__
    row["monitor_read_seconds"] = round(time.monotonic() - started, 4)
    if job_id:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("任务ID必须为32位小写十六进制")
        try:
            path = var / "jobs" / (job_id + ".progress.json")
            progress = json.loads(path.read_text())
            row["progress"] = {k: progress.get(k) for k in
                               ("frames_done", "yield_count", "stage", "reason", "elapsed_seconds")}
            row["progress_mtime"] = path.stat().st_mtime
        except (OSError, ValueError) as error:
            row["progress_error"] = type(error).__name__
    try:
        row["io_pressure"] = Path("/proc/pressure/io").read_text().strip()
    except OSError:
        pass
    if worker_pid:
        proc = Path("/proc") / str(worker_pid)
        try:
            # 不保存命令行、环境、片源名、凭据；进程退出明确报告而非猜测。
            row["worker"] = {"pid": worker_pid, "wchan": (proc / "wchan").read_text().strip(),
                             "io": (proc / "io").read_text().strip()}
        except FileNotFoundError:
            row["worker"] = {"pid": worker_pid, "exited": True}
        except OSError as error:
            row["worker"] = {"pid": worker_pid, "error": type(error).__name__}
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--var", type=Path, required=True)
    parser.add_argument("--worker-pid", type=int)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--job-id")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error("观察时长必须为1至3600秒")
    if not .1 <= args.interval <= 10:
        parser.error("采样间隔必须为0.1至10秒")
    if args.job_id and not re.fullmatch(r"[0-9a-f]{32}", args.job_id):
        parser.error("任务ID必须为32位小写十六进制")
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        started = time.monotonic()
        print(json.dumps(observe(args.var, args.worker_pid, args.job_id), ensure_ascii=False), flush=True)
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
