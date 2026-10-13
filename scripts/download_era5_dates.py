"""
Download and check ERA5 for the Session 8 multi-date study.

Date rule, fixed before any results are seen: the 1st and 15th of every
month in 2022, departure 00:00 UTC. That gives 24 dates, including the
Sessions 2-7 case (2022-03-01). For each date: hours 00:00-06:00 UTC, the
10 fine pressure levels, checked for missing values over the study region
and repaired if needed.

Each date runs in its own Python process, so memory is released between
dates and one failure does not stop the rest. A date that takes longer
than TIMEOUT_MIN is stopped and logged; re-running is safe, because hours
already in the cache load in seconds. The run stops if free disk space
falls below MIN_FREE_GB (each new date adds about 0.6 GB of cache).

Run from the repository root with the project's Python:
    python scripts/download_era5_dates.py
"""
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATES = [f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 15)]
HOURS = ("T00", "T06")
LOG = ROOT / "results" / "session8" / "era5_download_log.csv"
MIN_FREE_GB = 3.0
TIMEOUT_MIN = 45


def one_date(date):
    """Load and check one date (runs in a child process)."""
    from contrail_routing import config
    from contrail_routing.met import load_era5_checked

    _, report = load_era5_checked((date + HOURS[0], date + HOURS[1]), config.LEVELS_FINE,
                                  config.REGION_LON, config.REGION_LAT)
    print(f"RESULT repairs={len(report) - 1}", flush=True)
    # Exit immediately: the cloud-storage library's background threads can
    # stop a normal Python shutdown from finishing on Windows.
    os._exit(0)


def run_child(date):
    """Run one date in a child process; return (status, repairs, stderr tail)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "--one", date]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_MIN * 60)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = "timeout"
    repairs = next((line.split("=")[1] for line in out.splitlines() if line.startswith("RESULT")), "")
    if repairs != "":
        status = "ok"                       # data checked; a hang after that does not matter
    else:
        status = "TIMEOUT" if code == "timeout" else "FAILED"
    return status, repairs, err[-1500:]


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text("date,status,repairs,minutes,finished\n")
    start = time.time()
    for i, date in enumerate(DATES, 1):
        free_gb = shutil.disk_usage(ROOT.anchor).free / 1e9
        if free_gb < MIN_FREE_GB:
            print(f"Stopping: only {free_gb:.1f} GB free on {ROOT.anchor}", flush=True)
            break
        t0 = time.time()
        print(f"[{i}/{len(DATES)}] {date} ({free_gb:.1f} GB free, started {datetime.now():%H:%M}) ...", flush=True)
        status, repairs, err = run_child(date)
        minutes = (time.time() - t0) / 60
        print(f"    {status} in {minutes:.1f} min (total {(time.time() - start) / 3600:.1f} h)", flush=True)
        if status != "ok":
            print(err, flush=True)
        with LOG.open("a") as f:
            f.write(f"{date},{status},{repairs},{minutes:.1f},{datetime.now():%Y-%m-%d %H:%M}\n")
    print(f"Done. Log: {LOG}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--one":
        one_date(sys.argv[2])
    else:
        main()