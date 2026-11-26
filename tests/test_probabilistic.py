"""Probability-based contrail scores, the planner hook, and per-flight distances."""
import warnings

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pycontrails import MetDataset
from pycontrails.physics import thermo

from contrail_routing.geometry import flight_level_to_m
from contrail_routing.iagos import VERTICAL_M
from contrail_routing.planner import build_cost_model, pcr_at_points
from contrail_routing.probabilistic import (FEATURES, features_at_points, flight_scores, km_by_flight,
                                            pcr_scores, segment_km)
from contrail_routing.scenarios import shift_field

warnings.filterwarnings("ignore", message=r"\s*Met data appears")
LEVELS = np.array([175.0, 190.0, 200.0, 210.0, 220.0, 230.0, 240.0, 250.0, 275.0, 300.0])
T0 = pd.Timestamp("2022-03-01 00:00")


def uniform_met(rhi_value, temp_K=220.0):
    lon, lat = np.arange(-15, 15.01, 1.0), np.arange(40, 60.01, 1.0)
    time = pd.date_range(T0, periods=4, freq="h").values
    shape = (lon.size, lat.size, LEVELS.size, time.size)
    temp = np.full(shape, temp_K)
    q = rhi_value * thermo.q_sat_ice(temp, np.broadcast_to(LEVELS[None, None, :, None] * 100.0, shape))
    dims = ("longitude", "latitude", "level", "time")
    return MetDataset(xr.Dataset({"air_temperature": (dims, temp), "specific_humidity": (dims, q)},
                                 coords={"longitude": lon, "latitude": lat, "level": LEVELS, "time": time}))


def fields_of(met):
    return {name: shift_field(met, up_m=dz) for name, dz in VERTICAL_M.items()}


def known_model(intercept, coef_rhi_base):
    """A logistic model on raw features: only rhi_base matters (mean 0, scale 1 so beta is raw)."""
    beta = np.zeros(len(FEATURES) + 1)
    beta[0], beta[1] = intercept, coef_rhi_base
    return {"mean": np.zeros(len(FEATURES)), "scale": np.ones(len(FEATURES)), "beta": beta}


@pytest.mark.parametrize("rhi", [0.93, 1.05])
def test_the_three_scores_on_a_uniform_atmosphere(rhi):
    fields = fields_of(uniform_met(rhi))
    alt = flight_level_to_m(np.array([350.0, 370.0]))
    feat = features_at_points(fields, np.array([0.0, 2.0]), np.array([50.0, 51.0]), alt,
                              np.array([T0, T0 + pd.Timedelta(hours=1)], dtype="datetime64[ns]"))
    # linear interpolation of humidity between pressure levels moves RHi by a few 1e-4
    assert np.allclose(feat["rhi_base"], rhi, atol=1e-3) and np.allclose(feat["rhi_up600"], rhi, atol=0.02)
    model = known_model(-10.0, 10.0)                      # p = 1 / (1 + exp(-(10 rhi - 10)))
    s = pcr_scores(feat, model, threshold=0.91)
    assert (s["plain"] == float(rhi > 1)).all() and (s["threshold"] == 1.0).all()
    assert np.allclose(s["expected"], 1 / (1 + np.exp(-(10 * feat["rhi_base"] - 10))), atol=1e-9)


def test_points_outside_a_shifted_field_raise():
    fields = fields_of(uniform_met(0.8))
    with pytest.raises(ValueError):
        features_at_points(fields, 0.0, 50.0, flight_level_to_m(310.0), np.datetime64(T0))   # 287 hPa: above 'up600' data


def test_planner_hook_reproduces_the_default_and_accepts_probabilities():
    met = uniform_met(1.05)
    args = (met, (-10.0, 45.0), (10.0, 55.0), T0, 230.0, 6, np.array([-25.0, 0.0, 25.0]),
            np.array([350, 370]), np.array([2.8, 2.7]), 15.0)
    a = build_cost_model(*args)
    b = build_cost_model(*args, point_fn=lambda lo, la, al, t: pcr_at_points(met, lo, la, al, t))
    assert np.array_equal(a.pcr_frac, b.pcr_frac)
    c = build_cost_model(*args, point_fn=lambda lo, la, al, t: np.full(np.shape(lo), 0.25))
    assert np.allclose(c.pcr_frac, 0.25)


def test_segment_km_and_per_flight_sums():
    t = pd.date_range("2022-03-01", periods=4, freq="4s")
    df = pd.DataFrame({"flight_id": ["A", "A", "A", "B"], "time": list(t[:2]) + [t[1] + pd.Timedelta(seconds=60), t[3]],
                       "lon": [0.0, 0.01, 0.02, 5.0], "lat": [50.0, 50.0, 50.0, 50.0],
                       "measured": [1.0, 0.0, 1.0, 1.0], "plain": [1.0, 1.0, 0.0, 0.0]})
    km = segment_km(df)
    assert km[0] == pytest.approx(0.01 * 111.19 * np.cos(np.radians(50)), rel=1e-3)
    assert km[1] == 0.0 and km[2] == 0.0 and km[3] == 0.0       # gap of 60 s; next flight; last point
    df["km"] = km
    per = km_by_flight(df, ["measured", "plain"])
    assert per.loc["A", "measured"] == pytest.approx(km[0]) and per.loc["B", "plain"] == 0.0


def test_flight_scores_perfect_and_biased():
    rng = np.random.default_rng(0)
    m = rng.exponential(50.0, 200) * (rng.random(200) < 0.5)
    per = pd.DataFrame({"measured": m, "plain": m, "threshold": 2 * m, "expected": rng.permutation(m)})
    s = flight_scores(per, n_boot=100).set_index("method")
    assert s.loc["plain", "total_ratio"] == pytest.approx(1.0) and s.loc["plain", "mae_km"] == 0.0
    assert s.loc["plain", "spearman"] == pytest.approx(1.0) and s.loc["plain", "detection"] == 1.0
    assert s.loc["threshold", "total_ratio"] == pytest.approx(2.0)
    assert s.loc["expected", "total_ratio"] == pytest.approx(1.0)       # right on average, no skill per flight
