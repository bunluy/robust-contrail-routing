"""IAGOS reading and ERA5 matching, on artificial files and an artificial atmosphere."""
import importlib.util
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pycontrails import MetDataset
from pycontrails.physics import thermo

from contrail_routing.iagos import (VERTICAL_M, best_shift, bootstrap_scores, era5_at_points, good_mask, hour_windows,
                                    load_points, observed_pcr, read_flight, scores)
from contrail_routing.scenarios import shift_field

warnings.filterwarnings("ignore", message=r"\s*Met data appears")

LEVELS = np.array([175.0, 190.0, 200.0, 210.0, 220.0, 230.0, 240.0, 250.0, 275.0, 300.0])
T0 = pd.Timestamp("2022-03-01 00:00")


def fake_iagos_file(path, n=60, rhi=1.1, T=220.0, p_hPa=240.0, lon0=-5.0, start=T0 + pd.Timedelta(minutes=20)):
    """A small file with the IAGOS L2 layout: n points 4 s apart, all flagged good unless changed later."""
    t = pd.date_range(start, periods=n, freq="4s")
    good = np.zeros(n)
    data = {
        "lon": np.linspace(lon0, lon0 + 1.0, n), "lat": np.full(n, 50.0),
        "air_press_AC": np.full(n, p_hPa * 100.0), "air_temp_P1": np.full(n, T),
        "RHI_P1": np.full(n, rhi), "RHI_P1_error": np.full(n, 0.12),
        "air_press_AC_validity_flag": good, "air_temp_P1_validity_flag": good.copy(),
        "RHI_P1_validity_flag": good.copy(), "RHI_P1_process_flag": np.full(n, 2.0),
    }
    ds = xr.Dataset({k: ("UTC_time", v) for k, v in data.items()}, coords={"UTC_time": t.values})
    ds.attrs = {"flight_name": path.stem, "departure_airport": "FRA, Frankfurt, Germany",
                "arrival_airport": "CDG, Paris, France", "platform": "Aircraft, test",
                "file_version": "3.1.1",
                "index_ascent": f"10 {t[10]:%Y-%m-%dT%H:%M:%SZ}",
                "index_descent": f"50 {t[50]:%Y-%m-%dT%H:%M:%SZ}"}
    ds.to_netcdf(path)
    return path


def uniform_met(rhi_value, temp_K=220.0):
    """Atmosphere with one temperature and one RHi everywhere, 4 hours from T0."""
    lon, lat = np.arange(-15, 15.01, 1.0), np.arange(40, 60.01, 1.0)
    time = pd.date_range(T0, periods=4, freq="h").values
    shape = (lon.size, lat.size, LEVELS.size, time.size)
    temp = np.full(shape, temp_K)
    q = rhi_value * thermo.q_sat_ice(temp, np.broadcast_to(LEVELS[None, None, :, None] * 100.0, shape))
    dims = ("longitude", "latitude", "level", "time")
    return MetDataset(xr.Dataset({"air_temperature": (dims, temp), "specific_humidity": (dims, q)},
                                 coords={"longitude": lon, "latitude": lat, "level": LEVELS, "time": time}))


def layered_met(layer_hPa=(220.0, 250.0), temp_K=220.0):
    """Ice-supersaturated (RHi 1.2) between the two pressures, dry (RHi 0.5) elsewhere."""
    met = uniform_met(1.0, temp_K)
    ds = met.data
    rhi = np.where((ds["level"] >= layer_hPa[0]) & (ds["level"] <= layer_hPa[1]), 1.2, 0.5)
    q = rhi[None, None, :, None] * ds["specific_humidity"].values
    return MetDataset(ds.assign(specific_humidity=(ds["specific_humidity"].dims, q)))


def test_read_flight_parses_layout_and_meta(tmp_path):
    df, meta = read_flight(fake_iagos_file(tmp_path / "2022030100201516.nc4"))
    assert len(df) == 60 and df["p_hPa"].iloc[0] == pytest.approx(240.0)
    assert meta["departure"] == "FRA" and meta["arrival"] == "CDG"
    assert meta["ascent_end"] < meta["descent_start"]
    assert good_mask(df).all()


def test_flagged_and_out_of_box_points_are_dropped(tmp_path):
    f = fake_iagos_file(tmp_path / "a.nc4")
    with xr.open_dataset(f) as ds:
        ds = ds.load()
    ds["RHI_P1_validity_flag"][:5] = 3          # erroneous
    ds["air_temp_P1_validity_flag"][5:8] = 2    # limited
    ds["RHI_P1_process_flag"][8:10] = 1         # preliminary
    ds["air_press_AC"][10:12] = 350.0 * 100     # below the pressure range
    ds.to_netcdf(tmp_path / "b.nc4")
    f.unlink()
    pts, flights = load_points(tmp_path, (-15, 15), (40, 60), (175, 300), verbose=False)
    assert len(pts) == 60 - 12 and flights.loc[0, "n_used"] == 48
    assert set(pts["phase"]) == {"cruise", "descent"}           # the first 12 points (ascent) were dropped


@pytest.mark.parametrize("rhi, issr", [(1.1, 1.0), (0.8, 0.0)])
def test_observed_pcr_follows_measured_humidity(tmp_path, rhi, issr):
    fake_iagos_file(tmp_path / "a.nc4", rhi=rhi)
    pts, _ = load_points(tmp_path, (-15, 15), (40, 60), (175, 300), verbose=False)
    obs = observed_pcr(pts)
    assert (obs["issr_obs"] == issr).all() and (obs["sac_obs"] == 1.0).all()   # 220 K at 240 hPa: SAC met
    assert (obs["pcr_obs"] == issr).all()


def test_era5_matches_measurement_when_the_field_is_the_same(tmp_path):
    """If the 'ERA5' field equals what was measured, both sides must agree exactly."""
    fake_iagos_file(tmp_path / "a.nc4", rhi=1.1, T=220.0)
    pts, _ = load_points(tmp_path, (-15, 15), (40, 60), (175, 300), verbose=False)
    res = era5_at_points(pts, {"base": uniform_met(1.1, 220.0)})
    assert np.allclose(res["rhi_base"], 1.1, atol=1e-6) and np.allclose(res["T_base"], 220.0)
    assert (res["issr_base"].to_numpy() == observed_pcr(pts)["issr_obs"].to_numpy()).all()


def test_vertical_shift_moves_the_layer_past_the_measurement(tmp_path):
    """A layer at 220-250 hPa covers a point at 225 hPa; moved down by 600 m (to ~242-275 hPa) it no longer does."""
    fake_iagos_file(tmp_path / "a.nc4", rhi=1.1, p_hPa=225.0)
    pts, _ = load_points(tmp_path, (-15, 15), (40, 60), (175, 300), verbose=False)
    met = layered_met()
    fields = {name: shift_field(met, up_m=dz) for name, dz in VERTICAL_M.items()}
    res = era5_at_points(pts, fields)
    assert (res["issr_base"] == 1).all() and (res["issr_up150"] == 1).all()
    assert (res["issr_down600"] == 0).all()


def test_points_outside_the_field_get_nan(tmp_path):
    fake_iagos_file(tmp_path / "a.nc4", p_hPa=180.0)        # inside 175-300, but above the field moved down 600 m
    pts, _ = load_points(tmp_path, (-15, 15), (40, 60), (175, 300), verbose=False)
    res = era5_at_points(pts, {"down600": shift_field(uniform_met(0.5), up_m=-600.0)})
    assert res["rhi_down600"].isna().all()


def test_scores_on_a_known_table():
    obs = np.array([1, 1, 1, 0, 0, 0, 0, 0], bool)     # 3 observed events
    mod = np.array([1, 1, 0, 1, 0, 0, 0, 0], bool)     # 2 hits, 1 miss, 1 false alarm
    s = scores(obs, mod)
    assert s["hit_rate"] == pytest.approx(2 / 3) and s["false_alarm_ratio"] == pytest.approx(1 / 3)
    a_rand = 3 * 3 / 8
    assert s["ets"] == pytest.approx((2 - a_rand) / (2 + 1 + 1 - a_rand))
    assert scores(obs, obs)["ets"] == pytest.approx(1.0)


def test_hour_windows():
    t = pd.to_datetime(["2022-03-01 00:10", "2022-03-01 00:50", "2022-03-01 02:00"])
    assert hour_windows(t) == [pd.Timestamp("2022-03-01 00:00"), pd.Timestamp("2022-03-01 02:00")]


def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "match_era5_to_iagos.py"
    spec = importlib.util.spec_from_file_location("match_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_matching_script_writes_resumes_and_protects_study_nights(tmp_path, monkeypatch):
    script = _script()
    fake_iagos_file(tmp_path / "a.nc4", start=T0 + pd.Timedelta(minutes=20))
    fake_iagos_file(tmp_path / "b.nc4", start=T0 + pd.Timedelta(hours=2, minutes=5))
    pts, _ = load_points(tmp_path, (-15, 15), (40, 60), (175, 300), verbose=False)
    windows = hour_windows(pts["time"])
    deleted = []
    monkeypatch.setattr(script, "delete_cache", lambda hours: deleted.extend(
        h for h in hours if h not in script.protected_hours()))
    calls = []

    def loader(hour):
        calls.append(hour)
        return uniform_met(1.1).data.pipe(MetDataset)

    out = tmp_path / "era5"
    out.mkdir()
    n = script.match_hours(pts, windows, windows, out, loader=loader)
    assert n == 2 and len(calls) == 2
    assert sorted(f.stem for f in out.glob("*.csv")) == ["2022-03-01T00", "2022-03-01T02"]
    res = pd.read_csv(out / "2022-03-01T00.csv")
    assert len(res) == 60 and {"rhi_base", "issr_down600", "pcr_up600"} <= set(res.columns)
    assert deleted == []                                   # 2022-03-01 00-06 UTC is a study night
    assert script.match_hours(pts, windows, windows, out, loader=loader) == 0   # resumes: nothing redone
    assert len(calls) == 2


def _toy(n_flights=20, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for f in range(n_flights):
        t = pd.date_range("2022-03-01", periods=150, freq="4s")
        obs = rng.random(150) < 0.2
        rows.append(pd.DataFrame({"flight_id": f"F{f}", "time": t, "issr_obs": obs.astype(float),
                                  "model": np.where(rng.random(150) < 0.7, obs, ~obs).astype(float)}))
    return pd.concat(rows, ignore_index=True)


def test_bootstrap_interval_contains_the_estimate_and_is_reproducible():
    d = _toy()
    s = bootstrap_scores(d, "issr_obs", "model", n_boot=300, seed=0)
    for k in ("hit_rate", "false_alarm_ratio", "ets"):
        est, lo, hi = s[k]
        assert lo <= est <= hi
        assert est == pytest.approx(scores(d["issr_obs"] > 0.5, d["model"] > 0.5)[k])
    assert s == bootstrap_scores(d, "issr_obs", "model", n_boot=300, seed=0)
    assert s["n_units"] == 20 and s["n_points"] == 3000


def test_best_shift_picks_the_agreeing_field_and_breaks_ties_to_no_shift():
    d = _toy(n_flights=3)
    obs = d["issr_obs"]
    for s in ["base", "up150", "down150", "up300", "down300", "up600", "down600"]:
        d[f"issr_{s}"] = 0.0
    d["issr_up300"] = obs                                  # only this field matches exactly
    b = best_shift(d)
    assert (b["best"] == "up300").all() and (b["best_agreement"] == 1.0).all() and b["informative"].all()
    for s in ["base", "up150", "down150", "up300", "down300", "up600", "down600"]:
        d[f"issr_{s}"] = obs                               # all fields equal -> no shift wins
    b = best_shift(d)
    assert (b["best"] == "base").all() and not b["informative"].any()
    d["issr_obs"] = 1.0
    for s in ["base", "up150", "down150", "up300", "down300", "up600", "down600"]:
        d[f"issr_{s}"] = 0.0
    assert best_shift(d)["missed_by_all"].all()
