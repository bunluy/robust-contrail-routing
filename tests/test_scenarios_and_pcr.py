"""PCR and scenario tests on a small artificial atmosphere (no ERA5 needed)."""
import warnings

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pycontrails import MetDataset
from pycontrails.physics import thermo

from contrail_routing.evaluate import evaluate_routes, route_metrics
from contrail_routing.geometry import make_route
from contrail_routing.scenarios import SCALE_HEIGHT_M, coverage, shift_field

warnings.filterwarnings("ignore", message=r"\s*Met data appears")

LEVELS = np.array([175.0, 200.0, 225.0, 250.0, 275.0, 300.0])


def synthetic_met(rhi_value):
    """Uniform atmosphere: ISA temperature, the same RHi everywhere."""
    lon = np.arange(-15, 15.01, 1.0)
    lat = np.arange(40, 60.01, 1.0)
    time = pd.date_range("2022-03-01", periods=4, freq="h").values
    temp_1d = np.maximum(288.15 * (LEVELS / 1013.25) ** 0.190263, 216.65)
    shape = (lon.size, lat.size, LEVELS.size, time.size)
    temp = np.broadcast_to(temp_1d[None, None, :, None], shape).copy()
    pres = np.broadcast_to(LEVELS[None, None, :, None] * 100.0, shape)
    q = rhi_value * thermo.q_sat_ice(temp, pres)
    dims = ("longitude", "latitude", "level", "time")
    ds = xr.Dataset(
        {"air_temperature": (dims, temp), "specific_humidity": (dims, q)},
        coords={"longitude": lon, "latitude": lat, "level": LEVELS, "time": time},
    )
    return MetDataset(ds)


@pytest.fixture(scope="module")
def route():
    return {"test": make_route((-10.0, 45.0), (10.0, 55.0), 0, 11000.0,
                               pd.Timestamp("2022-03-01 00:00"), 230.0, name="test")}


def test_dry_atmosphere_gives_no_persistent_contrails(route):
    tables = evaluate_routes(route, synthetic_met(rhi_value=0.5))
    m = route_metrics(tables, {"test": {}})
    assert m.loc[0, "pcr_km"] == 0.0


def test_ice_supersaturated_atmosphere_gives_pcr_everywhere(route):
    tables = evaluate_routes(route, synthetic_met(rhi_value=1.2))
    df = tables["test"]
    assert (df["ISSR"] > 0).all() and (df["SAC"] > 0).all() and (df["PCR"] > 0).all()


def test_zero_shift_leaves_results_unchanged(route):
    met = synthetic_met(rhi_value=1.2)
    a = route_metrics(evaluate_routes(route, met), {"test": {}})
    b = route_metrics(evaluate_routes(route, shift_field(met)), {"test": {}})
    assert a.loc[0, "pcr_km"] == b.loc[0, "pcr_km"]


def test_vertical_shift_scales_pressure_levels():
    met = synthetic_met(rhi_value=0.5)
    up = shift_field(met, up_m=SCALE_HEIGHT_M * np.log(2.0))   # halves every pressure
    assert np.allclose(up.data["level"].values, LEVELS / 2.0)


def test_coverage_flags_route_outside_shifted_levels(route):
    met = synthetic_met(rhi_value=0.5)
    assert coverage(met, route)["ok"].all()
    far_up = shift_field(met, up_m=3000.0)   # 11 km now below the lowest level
    assert not coverage(far_up, route).loc["test", "level_ok"]


def test_vertical_shift_keeps_relative_humidity(route):
    """A pure displacement must move the air with its relative humidity unchanged."""
    # Linear interpolation between levels alone gives ~0.5% error, so allow 1%;
    # keeping specific humidity fixed instead would be off by ~9% at 600 m.
    met = synthetic_met(rhi_value=0.8)
    for dz in (600.0, -600.0):
        df = evaluate_routes(route, shift_field(met, up_m=dz))["test"]
        assert np.allclose(df["rhi"], 0.8, rtol=0.01), (dz, df["rhi"].min(), df["rhi"].max())
