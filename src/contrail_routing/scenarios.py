"""Perturbed meteorology for uncertainty scenarios, and cached evaluation."""
from pathlib import Path

import numpy as np
import pandas as pd
from pycontrails import MetDataset
from pycontrails.physics import units

from .evaluate import evaluate_routes, route_metrics

KM_PER_DEG_LAT = 110.57
# Pressure scale height of the ISA lower stratosphere (216.65 K): ~6,342 m.
# Multiplying pressure by exp(-dz / H) corresponds to moving up by dz there.
SCALE_HEIGHT_M = 287.05 * 216.65 / 9.80665


def km_per_deg_lon(lat_deg):
    return 111.32 * np.cos(np.radians(lat_deg))


def shift_field(met, east_km=0.0, north_km=0.0, hours=0, up_m=0.0, ref_lat=50.0):
    """
    Move the whole temperature/humidity field and return a new MetDataset.

    east_km / north_km > 0 move features east / north (converted to degrees
    at ref_lat, so the true distance varies slightly with latitude).
    hours = -1 means the weather arrives one hour early.
    up_m > 0 moves the field up (e.g. a higher tropopause and moist layer);
    temperature and relative humidity move with the air.
    """
    ds = met.data if isinstance(met, MetDataset) else met
    derived = [c for c in ("air_pressure", "altitude") if c in ds.coords]
    factor = np.exp(-up_m / SCALE_HEIGHT_M)    # new pressure / old pressure
    if up_m != 0.0 and "specific_humidity" in ds:
        # Relabelling levels moves each air parcel to a new pressure. Vapour
        # pressure is proportional to q * p, so q is divided by the same
        # factor to keep relative humidity unchanged (a pure displacement).
        ds = ds.assign(specific_humidity=ds["specific_humidity"] / factor)
    out = ds.drop_vars(derived).assign_coords(
        longitude=ds["longitude"].values + east_km / km_per_deg_lon(ref_lat),
        latitude=ds["latitude"].values + north_km / KM_PER_DEG_LAT,
        time=ds["time"].values + np.timedelta64(int(hours), "h"),
        level=ds["level"].values * factor,
    )
    return MetDataset(out)


def coverage(met, routes):
    """
    For each route, check it lies inside the met data in longitude, latitude,
    time and pressure. Routes outside would get NaN (no extrapolation).
    """
    ds = met.data
    lon, lat = ds["longitude"].values, ds["latitude"].values
    lev, t = ds["level"].values, ds["time"].values
    rows = []
    for name, fl in routes.items():
        p = units.m_to_pl(np.asarray(fl["altitude"], dtype=float))
        rows.append({
            "route": name,
            "lon_ok": lon.min() <= fl["longitude"].min() and fl["longitude"].max() <= lon.max(),
            "lat_ok": lat.min() <= fl["latitude"].min() and fl["latitude"].max() <= lat.max(),
            "time_ok": t.min() <= fl["time"].min() and fl["time"].max() <= t.max(),
            "level_ok": lev.min() <= p.min() and p.max() <= lev.max(),
        })
    df = pd.DataFrame(rows).set_index("route")
    df["ok"] = df.all(axis=1)
    return df


def run_scenarios(scenarios, routes, cache_dir, prefix, **eval_kwargs):
    """
    Evaluate every route under every scenario {name: MetDataset}.
    Each scenario's along-track results are saved to
    cache_dir / f"{prefix}_{name}.csv" and reloaded on later runs.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, met in scenarios.items():
        path = cache_dir / f"{prefix}_{name}.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["time"])
            if set(df["route"].unique()) == set(routes):
                print(f"{name:16s} loaded from disk")
                out[name] = {r: g.reset_index(drop=True)
                             for r, g in df.groupby("route", sort=False)}
                continue
            print(f"{name:16s} saved routes differ from requested routes; re-evaluating")
        print(f"{name:16s} evaluating {len(routes)} routes ...")
        tables = evaluate_routes(routes, met, **eval_kwargs)
        pd.concat(tables.values(), ignore_index=True).to_csv(path, index=False)
        out[name] = tables
    return out


def pcr_by_scenario(results, specs):
    """DataFrame of PCR km: one row per route, one column per scenario."""
    return pd.DataFrame({
        name: route_metrics(tables, {r: specs[r] for r in tables}).set_index("route")["pcr_km"]
        for name, tables in results.items()
    })
