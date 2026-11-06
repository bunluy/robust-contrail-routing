"""Reading IAGOS-CORE L2 time series and comparing them with ERA5.

IAGOS (In-service Aircraft for a Global Observing System) measures humidity
and temperature on board passenger airliners every 4 seconds. Each L2 file is
one flight. Relative humidity over ice (RHI_P1) and temperature (air_temp_P1)
come from the same sensor package (P1); pressure and position come from the
aircraft. Values are used only where IAGOS marks them as good and final.

The ERA5 side goes through the same steps as the route evaluation: the same
pressure levels, pycontrails' PCR model and the same vertical shifts.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from pycontrails import GeoVectorDataset
from pycontrails.models.pcr import PCR
from pycontrails.physics import thermo, units

GOOD = 0      # validity flag: good
FINAL = 2     # process flag: final data

# Vertical scenarios of Sessions 7-11: the field moved up (+) or down (-) by this many metres
VERTICAL_M = {"base": 0.0, "up150": 150.0, "up300": 300.0, "up600": 600.0,
              "down150": -150.0, "down300": -300.0, "down600": -600.0}


def read_flight(path):
    """
    One IAGOS L2 file -> (DataFrame, meta). Columns: time, lon, lat, p_hPa,
    T_K, rhi (fraction, 1 = saturation over ice), rhi_err, and the flags
    needed by good_mask. meta holds the flight id, airports, aircraft and
    the times at which the climb ends and the descent starts.
    """
    with xr.open_dataset(path, decode_timedelta=False) as ds:
        a = ds.attrs

        def col(name):
            return ds[name].values.astype(float) if name in ds else np.full(ds.sizes["UTC_time"], np.nan)

        df = pd.DataFrame({
            "time": pd.to_datetime(ds["UTC_time"].values),
            "lon": col("lon"), "lat": col("lat"),
            "p_hPa": col("air_press_AC") / 100.0,
            "T_K": col("air_temp_P1"),
            "rhi": col("RHI_P1"),
            "rhi_err": col("RHI_P1_error"),
            "p_flag": col("air_press_AC_validity_flag"),
            "T_flag": col("air_temp_P1_validity_flag"),
            "rhi_flag": col("RHI_P1_validity_flag"),
            "rhi_process": col("RHI_P1_process_flag"),
        })

    def when(attr):
        txt = str(a.get(attr, "")).split()
        return pd.Timestamp(txt[-1]).tz_localize(None) if len(txt) == 2 else pd.NaT

    meta = {
        "flight_id": str(a.get("flight_name", Path(path).stem)),
        "departure": str(a.get("departure_airport", "")).split(",")[0],
        "arrival": str(a.get("arrival_airport", "")).split(",")[0],
        "platform": str(a.get("platform", "")),
        "file_version": str(a.get("file_version", "")),
        "ascent_end": when("index_ascent"),
        "descent_start": when("index_descent"),
    }
    df["flight_id"] = meta["flight_id"]
    return df, meta


def good_mask(df):
    """True where pressure, temperature and RHI are all flagged good and RHI is final."""
    return ((df["p_flag"] == GOOD) & (df["T_flag"] == GOOD) & (df["rhi_flag"] == GOOD)
            & (df["rhi_process"] == FINAL)
            & df[["lon", "lat", "p_hPa", "T_K", "rhi"]].notna().all(axis=1))


def flight_phase(df, meta):
    """'ascent' before the end of the climb, 'descent' after the start of the descent, else 'cruise'."""
    phase = np.full(len(df), "cruise", dtype=object)
    if pd.notna(meta["ascent_end"]):
        phase[(df["time"] < meta["ascent_end"]).to_numpy()] = "ascent"
    if pd.notna(meta["descent_start"]):
        phase[(df["time"] >= meta["descent_start"]).to_numpy()] = "descent"
    return phase


def load_points(folder, lon_range, lat_range, p_range_hPa, verbose=True):
    """
    Good IAGOS points inside the box and pressure range from every *.nc4 file
    in folder. Returns (points, flights): one row per point, one per file.
    point_id is a stable key (flight id + index within the file).
    """
    files = sorted(Path(folder).glob("*.nc4"))
    if not files:
        raise FileNotFoundError(f"No .nc4 files in {folder}")
    pts, rows = [], []
    for i, f in enumerate(files, 1):
        df, meta = read_flight(f)
        df["phase"] = flight_phase(df, meta)
        df["point_id"] = meta["flight_id"] + "_" + df.index.astype(str)
        good = good_mask(df)
        inside = (df["lon"].between(*lon_range) & df["lat"].between(*lat_range)
                  & df["p_hPa"].between(*p_range_hPa))
        keep = df[good & inside]
        rows.append({**meta, "file": f.name, "n_points": len(df), "n_good": int(good.sum()),
                     "n_used": len(keep)})
        pts.append(keep)
        if verbose and (i % 100 == 0 or i == len(files)):
            print(f"  read {i}/{len(files)} files", flush=True)
    points = pd.concat(pts, ignore_index=True)
    cols = ["point_id", "flight_id", "time", "lon", "lat", "p_hPa", "T_K", "rhi", "rhi_err", "phase"]
    return points[cols], pd.DataFrame(rows)


def _vector(points):
    """GeoVectorDataset at the measured positions; altitude is the ISA altitude of the measured pressure."""
    return GeoVectorDataset(longitude=points["lon"].to_numpy(float),
                            latitude=points["lat"].to_numpy(float),
                            altitude=units.pl_to_m(points["p_hPa"].to_numpy(float)),
                            time=points["time"].to_numpy("datetime64[ns]"))


def observed_pcr(points):
    """
    SAC, ISSR and PCR from the measured temperature and humidity, with the
    same pycontrails model and parameters as the route evaluation.
    Returns a DataFrame aligned with points.
    """
    v = _vector(points)
    T = points["T_K"].to_numpy(float)
    v["air_temperature"] = T
    v["specific_humidity"] = points["rhi"].to_numpy(float) * thermo.q_sat_ice(T, v.air_pressure)
    out = PCR(met=None).eval(v)
    return pd.DataFrame({"sac_obs": np.asarray(out["sac"], float),
                         "issr_obs": np.asarray(out["issr"], float),
                         "pcr_obs": np.asarray(out["pcr"], float)}, index=points.index)


def era5_at_points(points, fields):
    """
    ERA5 temperature, RHi, SAC, ISSR and PCR at the points, for each field in
    fields = {name: MetDataset} (e.g. the shifted scenarios). Interpolated in
    longitude, latitude, pressure and time by pycontrails, as for routes;
    NaN where a point lies outside a field.
    """
    out = pd.DataFrame({"point_id": points["point_id"].to_numpy()})
    for name, met in fields.items():
        r = PCR(met=met).eval(_vector(points))
        q, T = np.asarray(r["specific_humidity"], float), np.asarray(r["air_temperature"], float)
        out[f"T_{name}"] = T
        out[f"rhi_{name}"] = thermo.rhi(q, T, np.asarray(r.air_pressure, float))
        for k in ("sac", "issr", "pcr"):
            out[f"{k}_{name}"] = np.asarray(r[k], float)
    return out


def hour_windows(times):
    """Sorted start hours H such that some point lies in [H, H + 1 h)."""
    return sorted(pd.DatetimeIndex(times).floor("h").unique())


def scores(obs, model):
    """
    Agreement of two yes/no series (e.g. measured vs ERA5 ice supersaturation).
    hit rate = share of observed events that the model also has;
    false alarm ratio = share of model events that were not observed;
    ETS = equitable threat score (0 = no skill beyond chance, 1 = perfect).
    """
    obs, model = np.asarray(obs, bool), np.asarray(model, bool)
    a = np.sum(obs & model)            # hits
    b = np.sum(~obs & model)           # false alarms
    c = np.sum(obs & ~model)           # misses
    n = obs.size
    a_random = (a + b) * (a + c) / n if n else np.nan
    denom = a + b + c - a_random
    return {"n": int(n), "observed_share": (a + c) / n if n else np.nan,
            "model_share": (a + b) / n if n else np.nan,
            "hit_rate": a / (a + c) if a + c else np.nan,
            "false_alarm_ratio": b / (a + b) if a + b else np.nan,
            "ets": (a - a_random) / denom if denom else np.nan}


def _scores_from_counts(a, b, c, n):
    """Vectorised scores from hit, false-alarm and miss counts (arrays allowed)."""
    a, b, c, n = (np.asarray(x, float) for x in (a, b, c, n))
    with np.errstate(invalid="ignore", divide="ignore"):
        a_random = (a + b) * (a + c) / n
        return {"observed_share": (a + c) / n, "model_share": (a + b) / n,
                "hit_rate": a / (a + c), "false_alarm_ratio": b / (a + b),
                "ets": (a - a_random) / (a + b + c - a_random)}


def bootstrap_scores(df, obs_col, model_col, unit="flight_id", n_boot=1000, seed=0):
    """
    Scores with 95% intervals from resampling whole flights (points within a
    flight are not independent). Rows where either column is NaN are dropped.
    Returns {score: (estimate, low, high)} plus n_points and n_units.
    """
    d = df[[unit, obs_col, model_col]].dropna()
    o, m = d[obs_col].to_numpy() > 0.5, d[model_col].to_numpy() > 0.5
    g = pd.DataFrame({unit: d[unit].to_numpy(), "a": o & m, "b": ~o & m, "c": o & ~m, "n": 1}
                     ).groupby(unit).sum()
    counts = g[["a", "b", "c", "n"]].to_numpy(float)
    est = _scores_from_counts(*counts.sum(axis=0))
    idx = np.random.default_rng(seed).integers(0, len(g), size=(n_boot, len(g)))
    boot = _scores_from_counts(*counts[idx].sum(axis=1).T)
    out = {k: (float(est[k]), float(np.nanpercentile(boot[k], 2.5)), float(np.nanpercentile(boot[k], 97.5)))
           for k in est}
    out["n_points"], out["n_units"] = int(counts[:, 3].sum()), int(len(g))
    return out


# Tie-break order for the best shift: no shift first, then the smallest shifts
SHIFT_ORDER = ["base", "up150", "down150", "up300", "down300", "up600", "down600"]


def best_shift(df, shifts=SHIFT_ORDER, block_minutes=10):
    """
    For each block (one flight, block_minutes of flying), the vertical shift
    whose ERA5 ice supersaturation agrees best with the measurement (share of
    points that agree). Only blocks with ice supersaturation in the
    measurement or in at least one shifted field are kept; ties go to the
    earlier entry of shifts (the smaller shift). Blocks where all fields agree
    equally are marked informative = False. Rows with any NaN are dropped.
    """
    cols = [f"issr_{s}" for s in shifts]
    d = df.dropna(subset=cols + ["issr_obs"]).copy()
    d["block"] = d["flight_id"] + "_" + d["time"].dt.floor(f"{block_minutes}min").astype(str)
    obs = d["issr_obs"].to_numpy() > 0.5
    for s in shifts:
        d[f"agree_{s}"] = (d[f"issr_{s}"].to_numpy() > 0.5) == obs
    d["any_model"] = (d[cols].to_numpy() > 0.5).any(axis=1)
    g = d.groupby("block").agg(flight_id=("flight_id", "first"), n=("issr_obs", "size"),
                               obs_share=("issr_obs", "mean"), any_model=("any_model", "max"),
                               **{f"agree_{s}": (f"agree_{s}", "mean") for s in shifts})
    g = g[(g["obs_share"] > 0) | g["any_model"]].copy()
    agree = g[[f"agree_{s}" for s in shifts]].to_numpy()
    g["best"] = np.asarray(shifts)[np.argmax(agree, axis=1)]      # argmax keeps the first of equal values
    g["best_agreement"] = agree.max(axis=1)
    # A block where every field agrees equally well (e.g. none has any ice supersaturation)
    # says nothing about the height of the layer; it is kept but marked.
    g["informative"] = agree.max(axis=1) > agree.min(axis=1)
    g["missed_by_all"] = (g["obs_share"] > 0) & ~g["any_model"]
    return g.drop(columns="any_model")
