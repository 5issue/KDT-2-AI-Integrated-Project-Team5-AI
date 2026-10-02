"""Run fixed Locust stages and sample the locally capped Serving container."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_ID = os.environ.get("LOADTEST_RUN_ID", "stages")
RESULTS = Path(os.environ.get("LOADTEST_RESULTS_DIR", "/tmp/ai-local-loadtest")) / RUN_ID
APP_CONTAINER = os.environ.get("LOADTEST_APP_CONTAINER", "ai-loadtest-app")
LOCUST_IMAGE = os.environ.get("LOADTEST_LOCUST_IMAGE", "locustio/locust:2.46.6")
USER_ID = os.environ.get("LOADTEST_USER_ID", "")
STAGES = [int(part) for part in os.environ.get("LOADTEST_USERS", "1,5,10,20,40").split(",")]
STAGE_SECONDS = int(os.environ.get("LOADTEST_STAGE_SECONDS", "60"))


def run_text(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()


def cgroup_stats() -> dict[str, int]:
    raw = run_text(["docker", "exec", APP_CONTAINER, "cat", "/sys/fs/cgroup/cpu.stat"])
    cpu = {key: int(value) for key, value in (line.split() for line in raw.splitlines())}
    memory_current = int(run_text(["docker", "exec", APP_CONTAINER, "cat", "/sys/fs/cgroup/memory.current"]))
    memory_peak = int(run_text(["docker", "exec", APP_CONTAINER, "cat", "/sys/fs/cgroup/memory.peak"]))
    memory_events_raw = run_text(["docker", "exec", APP_CONTAINER, "cat", "/sys/fs/cgroup/memory.events"])
    events = {key: int(value) for key, value in (line.split() for line in memory_events_raw.splitlines())}
    return {
        "usage_usec": cpu.get("usage_usec", 0),
        "nr_periods": cpu.get("nr_periods", 0),
        "nr_throttled": cpu.get("nr_throttled", 0),
        "throttled_usec": cpu.get("throttled_usec", 0),
        "memory_current_bytes": memory_current,
        "memory_peak_bytes": memory_peak,
        "oom": events.get("oom", 0),
        "oom_kill": events.get("oom_kill", 0),
    }


def sample_docker_stats(stage: int, stop: threading.Event) -> None:
    with (RESULTS / "container_samples.csv").open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        while not stop.is_set():
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                line = run_text(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}",
                        APP_CONTAINER,
                    ]
                )
                writer.writerow([timestamp, RUN_ID, stage, *line.split(",", maxsplit=2)])
                file.flush()
            except subprocess.CalledProcessError as error:
                writer.writerow([timestamp, RUN_ID, stage, "stats_error", error.returncode, ""])
                file.flush()
            stop.wait(1.0)


def main() -> int:
    if not USER_ID:
        print("Set LOADTEST_USER_ID to the synthetic user id in the throwaway local DB.", file=sys.stderr)
        return 2
    RESULTS.mkdir(parents=True, exist_ok=True)
    sample_file = RESULTS / "container_samples.csv"
    with sample_file.open("w", newline="", encoding="utf-8") as file:
        csv.writer(file).writerow(
            ["timestamp_utc", "run_id", "users", "cpu_percent", "memory_usage_limit", "memory_percent"]
        )

    with (RESULTS / "stage_resources.csv").open("w", newline="", encoding="utf-8") as resource_file:
        fields = [
            "users",
            "exit_code",
            "cpu_usage_delta_usec",
            "nr_periods_delta",
            "nr_throttled_delta",
            "throttled_period_percent",
            "throttled_usec_delta",
            "memory_peak_mib_since_start",
            "memory_events_oom",
            "memory_events_oom_kill",
        ]
        resources = csv.DictWriter(resource_file, fieldnames=fields)
        resources.writeheader()

        for users in STAGES:
            before = cgroup_stats()
            stop = threading.Event()
            sampler = threading.Thread(target=sample_docker_stats, args=(users, stop), daemon=True)
            sampler.start()
            prefix = f"{users}users"
            command = [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--network",
                "ai-loadtest-net",
                "-v",
                f"{ROOT}:/work:ro",
                "-v",
                f"{RESULTS}:/output",
                "-e",
                f"LOADTEST_USER_ID={USER_ID}",
                LOCUST_IMAGE,
                "-f",
                "/work/locustfile.py",
                "--headless",
                "--host",
                "http://ai-loadtest-app:8000",
                "--users",
                str(users),
                "--spawn-rate",
                str(max(users, 1)),
                "--run-time",
                f"{STAGE_SECONDS}s",
                "--csv",
                f"/output/{prefix}",
                "--csv-full-history",
                "--only-summary",
            ]
            with (RESULTS / f"{prefix}.log").open("w", encoding="utf-8") as output:
                print(f"Running stage: {users} users for {STAGE_SECONDS}s", flush=True)
                completed = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False)
            stop.set()
            sampler.join(timeout=3)
            after = cgroup_stats()
            resources.writerow(
                {
                    "users": users,
                    "exit_code": completed.returncode,
                    "cpu_usage_delta_usec": after["usage_usec"] - before["usage_usec"],
                    "nr_periods_delta": after["nr_periods"] - before["nr_periods"],
                    "nr_throttled_delta": after["nr_throttled"] - before["nr_throttled"],
                    "throttled_period_percent": round(
                        100
                        * (after["nr_throttled"] - before["nr_throttled"])
                        / max(1, after["nr_periods"] - before["nr_periods"]),
                        1,
                    ),
                    "throttled_usec_delta": after["throttled_usec"] - before["throttled_usec"],
                    "memory_peak_mib_since_start": round(after["memory_peak_bytes"] / 1024**2, 1),
                    "memory_events_oom": after["oom"],
                    "memory_events_oom_kill": after["oom_kill"],
                }
            )
            resource_file.flush()
            print(f"Completed stage: {users} users (Locust exit {completed.returncode})", flush=True)
            if completed.returncode:
                return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
