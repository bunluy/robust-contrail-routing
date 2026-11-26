"""Contrail conditions as a probability, for checking against flights and for planning (Session 14).

Three ways to score a point for persistent-contrail (PCR) conditions from ERA5:
- plain:     1 if SAC is met and RHi > 1 (pycontrails PCR, what Sessions 1-11 use);
- threshold: 1 if SAC is met and RHi > t (the Session 13 fitted threshold);
- expected:  SAC (0/1) times the Session 13 probability of ice supersaturation,
             from ERA5 RHi at the point and 150-600 m above and below it,
             temperature and pressure.
All three come from one evaluation of the seven vertically shifted fields.
"""
import numpy as np
import pandas as pd
from pycontrails import GeoVectorDataset
from pycontrails.models.pcr import PCR
from pycontrails.physics import thermo, units

from .calibration import predict_proba
from .iagos import SHIFT_ORDER

FEATURES = [f"rhi_{s}" for s in SHIFT_ORDER] + ["T_base", "p_hPa"]
METHODS = ("plain", "threshold", "expected")


def features_at_points(fields, lon, lat, altitude_m, time):
    """
    ERA5 features at points (arrays of one shape): RHi in each field of
    fields = {name: MetDataset} (names as in SHIFT_ORDER), temperature and
    SAC in the unshifted field, and pressure. Returns a dict of arrays with
    the input shape. Raises if any field has no value at a point.
    """
    lon, lat, alt, t = np.broadcast_arrays(np.asarray(lon, float), np.asarray(lat, float),
                                           np.asarray(altitude_m, float), np.asarray(time))
    shape = lon.shape
    out = {"p_hPa": units.m_to_pl(alt.ravel()).reshape(shape)}
    for name in SHIFT_ORDER:
        pts = GeoVectorDataset(longitude=lon.ravel(), latitude=lat.ravel(), altitude=alt.ravel(), time=t.ravel())
        r = PCR(met=fields[name], humidity_scaling=None).eval(source=pts)
        q, T = np.asarray(r["specific_humidity"], float), np.asarray(r["air_temperature"], float)
        rhi = thermo.rhi(q, T, np.asarray(r.air_pressure, float))
        if np.isnan(rhi).any():
            raise ValueError(f"field {name}: {int(np.isnan(rhi).sum())} points outside the data")
        out[f"rhi_{name}"] = rhi.reshape(shape)
        if name == "base":
            out["T_base"] = T.reshape(shape)
            out["sac_base"] = np.asarray(r["sac"], float).reshape(shape)
            out["pcr_base"] = np.asarray(r["pcr"], float).reshape(shape)
    return out


def pcr_scores(feat, model, threshold):
    """The three PCR scores (plain, threshold, expected) from features_at_points output."""
    shape = feat["rhi_base"].shape
    X = np.column_stack([np.clip(feat[c].ravel(), 0.0, 2.0) if c.startswith("rhi_") else feat[c].ravel()
                         for c in FEATURES])
    prob = predict_proba(model, X).reshape(shape)
    sac = feat["sac_base"]
    return {"plain": feat["pcr_base"],
            "threshold": sac * (feat["rhi_base"] > threshold),
            "expected": sac * prob}


def segment_km(df, max_gap_s=8.0):
    """
    Distance from each measured point to the next point of the same flight
    (great circle, km); 0 where the next point is more than max_gap_s later
    or belongs to another flight. df must be sorted by flight and time.
    """
    lon, lat = np.radians(df["lon"].to_numpy(float)), np.radians(df["lat"].to_numpy(float))
    same = df["flight_id"].to_numpy()[1:] == df["flight_id"].to_numpy()[:-1]
    gap = np.diff(df["time"].to_numpy("datetime64[ns]")).astype("timedelta64[ms]").astype(float) / 1000.0
    a = (np.sin(np.diff(lat) / 2) ** 2
         + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2)
    d = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    d = np.where(same & (gap <= max_gap_s), d, 0.0)
    return np.append(d, 0.0)


def km_by_flight(df, cols, km_col="km"):
    """Kilometres in each condition per flight: sum over points of km x column value."""
    return df[cols].multiply(df[km_col], axis=0).groupby(df["flight_id"]).sum()


def flight_scores(per_flight, measured="measured", predicted=METHODS, n_boot=1000, seed=0, min_km=10.0):
    """
    Per-flight comparison of predicted with measured km in PCR conditions,
    with 95% intervals from resampling flights:
    - total ratio: all predicted km / all measured km (1 = right on average);
    - mean absolute error per flight (km);
    - rank correlation (Spearman) across flights;
    - detection: share of flights with at least min_km measured that also have
      at least min_km predicted.
    """
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(per_flight), size=(n_boot, len(per_flight)))
    m = per_flight[measured].to_numpy(float)
    rows = []
    for p in predicted:
        x = per_flight[p].to_numpy(float)

        def stats(i):
            mi, xi = m[i], x[i]
            ranks = pd.DataFrame({"m": mi, "x": xi}).rank()
            corr = ranks["m"].corr(ranks["x"])
            hit = xi[mi >= min_km] >= min_km
            return (xi.sum() / mi.sum(), np.mean(np.abs(xi - mi)), corr,
                    hit.mean() if hit.size else np.nan)

        est = stats(np.arange(len(m)))
        boot = np.array([stats(i) for i in idx])
        row = {"method": p}
        for n, (name, e) in enumerate(zip(("total_ratio", "mae_km", "spearman", "detection"), est)):
            row[name] = e
            row[name + "_lo"], row[name + "_hi"] = np.nanpercentile(boot[:, n], [2.5, 97.5])
        rows.append(row)
    return pd.DataFrame(rows)
