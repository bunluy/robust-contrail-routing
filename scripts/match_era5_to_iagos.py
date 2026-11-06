"""
Match ERA5 to IAGOS measurements (Session 12).

For every hour H in which IAGOS measured inside the study region at
175-300 hPa, load ERA5 for H and H + 1 exactly as the route evaluation does
(same loader and checks, same 10 pressure levels), interpolate it to the
measured positions and times, and evaluate pycontrails' PCR model in the
field as given and in the six vertically shifted fields of Sessions 7-11.
One CSV per hour is written to OUT/era5/; nothing else is computed here.

Runs for many hours (about 1,500 hourly windows), so it is built to be
stopped and resumed:
- hours already written are skipped;
- each day runs in its own Python process (memory is released, one failure
  does not stop the rest, a day that hangs is stopped after TIMEOUT_MIN);
- after an hour is matched its ERA5 cache file is deleted, so disk use stays
  small. The 24 study nights (00-06 UTC) are never deleted. --keep-cache
  keeps everything;
- the run stops if free disk space falls below MIN_FREE_GB;
- days are processed in a fixed random order, so if the run is stopped
  early the hours already matched are still spread over the whole year.

IAGOS data stay outside the repository (their data policy restricts
redistribution). Run from the repository root with the project's Python:
    python scripts/match_era5_to_iagos.py C:/Users/you/iagos_data
"""
import gc
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MIN_FREE_GB = 3.0
TIMEOUT_MIN = 60
P_RANGE_HPA = (175.0, 300.0)
DAY_ORDER_SEED = 2022


def paths(data_dir):
    out = Path(data_dir) / "matched"
    return {"out": out, "era5": out / "era5", "points": out / "iagos_points.pkl",
            "flights": out / "iagos_flights.csv", "log": out / "match_log.csv"}


def protected_hours():
    """ERA5 hours of the 24 study nights: their cache is shared with Sessions 8-11 and never deleted."""
    from contrail_routing import config
    return {pd.Timestamp(f"{d}T{h:02d}") for d in config.STUDY_DATES for h in range(7)}


def prepare_points(data_dir):
    """Read every IAGOS file once and save the good points in the region (about 1 minute)."""
    from contrail_routing import config
    from contrail_routing.iagos import load_points
    p = paths(data_dir)
    if p["points"].exists():
        return pd.read_pickle(p["points"])
    p["era5"].mkdir(parents=True, exist_ok=True)
    print("Reading IAGOS files ...", flush=True)
    points, flights = load_points(data_dir, config.REGION_LON, config.REGION_LAT, P_RANGE_HPA)
    points.to_pickle(p["points"])
    flights.to_csv(p["flights"], index=False)
    print(f"{len(points):,} good points from {points['flight_id'].nunique()} flights saved.", flush=True)
    return points


def window_file(era5_dir, hour):
    return era5_dir / f"{hour:%Y-%m-%dT%H}.csv"


def load_window_region(hour):
    """ERA5 for hour and hour + 1, checked and repaired as in Sessions 8-11, cut to the region in memory."""
    from contrail_routing import config
    from contrail_routing.met import load_era5_checked, load_region
    t0, t1 = hour, hour + pd.Timedelta(hours=1)
    met, _ = load_era5_checked((f"{t0:%Y-%m-%dT%H}", f"{t1:%Y-%m-%dT%H}"), config.LEVELS_FINE,
                               config.REGION_LON, config.REGION_LAT)
    region = load_region(met, config.REGION_LON, config.REGION_LAT)
    met.data.close()            # release the cache files so they can be deleted on Windows
    del met
    gc.collect()
    return region


def delete_cache(hours):
    """Delete the ERA5 cache files of these hours (never the protected study nights)."""
    from contrail_routing import config
    from pycontrails.datalib.ecmwf import ERA5ARCO
    from contrail_routing.met import VARIABLES
    keep = protected_hours()
    for h in hours:
        if h in keep:
            continue
        era5 = ERA5ARCO(time=(f"{h:%Y-%m-%dT%H}", f"{h:%Y-%m-%dT%H}"), variables=list(VARIABLES),
                        pressure_levels=list(config.LEVELS_FINE))
        Path(era5.create_cachepath(h.to_pydatetime())).unlink(missing_ok=True)


def match_hours(points, hours, all_windows, era5_dir, keep_cache=False, loader=load_window_region):
    """Match and save each window in hours; returns the number written. loader is replaceable in tests."""
    from contrail_routing.iagos import VERTICAL_M, era5_at_points
    from contrail_routing.scenarios import shift_field
    order = {h: i for i, h in enumerate(all_windows)}
    written = 0
    for hour in hours:
        path = window_file(era5_dir, hour)
        if path.exists():
            continue
        w = points[(points["time"] >= hour) & (points["time"] < hour + pd.Timedelta(hours=1))]
        region = loader(hour)
        fields = {name: shift_field(region, up_m=dz) for name, dz in VERTICAL_M.items()}
        res = era5_at_points(w, fields)
        tmp = path.with_suffix(".tmp")
        res.to_csv(tmp, index=False, float_format="%.5g")
        tmp.replace(path)                    # a file only appears once it is complete
        written += 1
        del region, fields
        gc.collect()
        if not keep_cache:
            nxt = all_windows[order[hour] + 1] if order[hour] + 1 < len(all_windows) else None
            done = [hour] + ([] if nxt == hour + pd.Timedelta(hours=1) else [hour + pd.Timedelta(hours=1)])
            delete_cache(done)
    return written


def one_day(data_dir, day, keep_cache):
    """Child process: match every window of one day."""
    from contrail_routing.iagos import hour_windows
    p = paths(data_dir)
    points = pd.read_pickle(p["points"])
    windows = hour_windows(points["time"])
    todays = [h for h in windows if f"{h:%Y-%m-%d}" == day]
    n = match_hours(points, todays, windows, p["era5"], keep_cache=keep_cache)
    print(f"RESULT written={n}", flush=True)
    os._exit(0)          # gcsfs background threads can stop a normal exit on Windows


def main(data_dir, keep_cache):
    from contrail_routing.iagos import hour_windows
    p = paths(data_dir)
    points = prepare_points(data_dir)
    windows = hour_windows(points["time"])
    days = sorted({f"{h:%Y-%m-%d}" for h in windows})
    done = {f.stem for f in p["era5"].glob("*.csv")}
    # Days run in a fixed random order (seed fixed in advance), so a run stopped early still
    # leaves a sample spread over the whole year rather than only its first months.
    order = np.random.default_rng(DAY_ORDER_SEED).permutation(len(days))
    todo_days = [days[i] for i in order
                 if any(f"{h:%Y-%m-%dT%H}" not in done for h in windows if f"{h:%Y-%m-%d}" == days[i])]
    print(f"{len(windows)} hourly windows on {len(days)} days; {len(done)} already matched; "
          f"{len(todo_days)} days to do.", flush=True)
    if not p["log"].exists():
        p["log"].write_text("day,status,written,minutes,finished\n")
    start = time.time()
    for i, day in enumerate(todo_days, 1):
        free_gb = shutil.disk_usage(Path(data_dir).anchor or ROOT.anchor).free / 1e9
        if free_gb < MIN_FREE_GB:
            print(f"Stopping: only {free_gb:.1f} GB free.", flush=True)
            break
        t0 = time.time()
        cmd = [sys.executable, str(Path(__file__).resolve()), "--one", str(data_dir), day]
        if keep_cache:
            cmd.append("--keep-cache")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_MIN * 60)
            out, err = proc.stdout, proc.stderr
            status = "ok" if "RESULT" in out else "FAILED"
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            status = "TIMEOUT"
        written = next((ln.split("=")[1] for ln in out.splitlines() if ln.startswith("RESULT")), "")
        minutes = (time.time() - t0) / 60
        elapsed_h = (time.time() - start) / 3600
        eta_h = elapsed_h / i * (len(todo_days) - i)
        print(f"[{i}/{len(todo_days)}] {day} {status}: {written or 0} hours in {minutes:.1f} min | "
              f"{free_gb:.1f} GB free | elapsed {elapsed_h:.1f} h, about {eta_h:.1f} h left", flush=True)
        if status != "ok":
            print(err[-1500:], flush=True)
        with p["log"].open("a") as f:
            f.write(f"{day},{status},{written},{minutes:.1f},{datetime.now():%Y-%m-%d %H:%M}\n")
    left = len(windows) - len(list(p["era5"].glob("*.csv")))
    print(f"Finished this run. Hours still to match: {left}" + (" (re-run to retry them)." if left else "."))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--keep-cache"]
    keep = "--keep-cache" in sys.argv
    if len(args) == 3 and args[0] == "--one":
        one_day(args[1], args[2], keep)
    elif len(args) == 1:
        main(Path(args[0]), keep)
    else:
        print(__doc__)
        sys.exit(1)
