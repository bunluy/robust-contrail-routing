"""Route planner: the stage-by-stage search finds the true optimum, and the
geometry it builds on matches the fixed-route construction."""
import itertools

import numpy as np
import pandas as pd
import pytest

from contrail_routing import config
from contrail_routing.geometry import (displaced_points, flight_from_path, haversine_km)
from contrail_routing.planner import (CostModel, climb_kg_per_level, path_costs, path_delay_s,
                                      plan, plan_timed)


def random_model(seed, S=6, J=5, K=3, max_dj=1, max_dk=1, unflyable=()):
    """A small synthetic cost model with random lengths, contrail flags and fuel rates."""
    rng = np.random.default_rng(seed)
    offsets = (np.arange(J) - J // 2) * 25.0
    edge = rng.uniform(20.0, 40.0, (S - 1, J, 2 * max_dj + 1))
    for i, dj in enumerate(range(-max_dj, max_dj + 1)):
        for j in range(J):
            if not 0 <= j + dj < J:
                edge[:, j, i] = np.inf
    fuel = rng.uniform(2.5, 3.0, K)
    fuel[list(unflyable)] = np.inf
    pcr_frac = rng.integers(0, 4, (S - 1, J, 2 * max_dj + 1, K, 2 * max_dk + 1)) / 3.0
    return CostModel(lon=np.zeros((S, J)), lat=np.zeros((S, J)), offsets_km=offsets,
                     flight_levels=np.arange(K) * 10 + 340, times=np.zeros(S), edge_km=edge,
                     pcr_frac=pcr_frac, fuel_per_km=fuel, climb_kg=15.0, max_dj=max_dj, max_dk=max_dk)


def brute_force(cm, lam, max_dk):
    """Minimum J over every allowed path, by enumeration (level held on the final segment, as in plan)."""
    S, J, K = cm.shape
    best = np.inf
    for mid in itertools.product(range(J), repeat=S - 2):
        j_idx = (cm.j0, *mid, cm.j0)
        if np.abs(np.diff(j_idx)).max() > cm.max_dj:
            continue
        for k_idx in itertools.product(range(K), repeat=S):
            if (np.abs(np.diff(k_idx)).max() > max_dk or k_idx[-1] != k_idx[-2]
                    or not np.isfinite(cm.fuel_per_km[list(k_idx)]).all()):
                continue
            c = path_costs(cm, j_idx, k_idx)
            best = min(best, c["fuel_kg"] + lam * c["pcr_km"])
    return best


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("lam", [0.0, 1.0, 10.0])
@pytest.mark.parametrize("max_dk", [0, 1])
def test_plan_matches_brute_force(seed, lam, max_dk):
    cm = random_model(seed, unflyable=(2,) if seed == 3 else ())
    p = plan(cm, lam, max_dk=max_dk)
    assert p["J"] == pytest.approx(brute_force(cm, lam, max_dk), rel=1e-12)
    assert p["J"] == pytest.approx(p["J_search"], rel=1e-12)   # backtracked path costs what the search said
    assert p["offset_km"][0] == 0 and p["offset_km"][-1] == 0
    if seed == 3:
        assert (p["flight_level"] != 360).all()                 # never uses the unflyable level


def test_no_contrails_gives_straight_path_at_cheapest_level():
    cm = random_model(0)
    cm.pcr_frac[:] = 0.0
    cm.edge_km[:] = np.where(np.isfinite(cm.edge_km), 30.0, np.inf)
    cm.edge_km[:, :, cm.max_dj] = 29.0          # staying on the same offset is shortest
    p = plan(cm, lam=10.0)
    assert (p["offset_km"] == 0).all()
    assert (p["flight_level"] == cm.flight_levels[np.argmin(cm.fuel_per_km)]).all()


def test_ties_keep_the_path_level():
    # with no contrails the final level does not change the cost; the planner must not drop it for free
    cm = random_model(1)
    cm.pcr_frac[:] = 0.0
    cm.fuel_per_km[:] = [3.0, 2.9, 2.8]          # highest level cheapest, as for real aircraft
    p = plan(cm, lam=1.0)
    assert (p["flight_level"] == cm.flight_levels[-1]).all()


def test_climb_fuel_per_1000_ft():
    # 64 t raised 304.8 m at 30% efficiency with 43.13 MJ/kg: about 14.8 kg
    assert climb_kg_per_level(64000.0) == pytest.approx(14.78, abs=0.01)


def test_displaced_points_geometry():
    o, d = config.ORIGIN, config.DESTINATION
    gc = float(haversine_km(*o, *d))
    lon0, lat0 = displaced_points(o, d, 0.5, 0.0)
    assert haversine_km(*o, lon0, lat0) == pytest.approx(gc / 2, rel=1e-9)
    lon1, lat1 = displaced_points(o, d, 0.5, 150.0)
    assert haversine_km(lon0, lat0, lon1, lat1) == pytest.approx(150.0, rel=1e-6)
    assert lat1 > lat0                          # positive offset = left of travel = north-west here


def test_flight_from_path_constant_speed_and_altitude_ramp():
    lon, lat = displaced_points(config.ORIGIN, config.DESTINATION, np.linspace(0, 1, 7), 0.0)
    alt = np.array([10000.0] * 3 + [10300.0] * 4)
    fl = flight_from_path(lon, lat, alt, pd.Timestamp("2022-03-01"), 230.0)
    seg = haversine_km(fl["longitude"][:-1], fl["latitude"][:-1], fl["longitude"][1:], fl["latitude"][1:])
    dt = np.diff(fl["time"]).astype("timedelta64[ms]").astype(float) / 1000.0
    assert np.allclose(seg * 1000.0 / dt, 230.0, rtol=1e-3)
    assert fl["altitude"][0] == 10000.0 and fl["altitude"][-1] == 10300.0
    assert np.all(np.diff(fl["altitude"]) >= 0)


def test_path_delay_counts_extra_distance():
    cm = random_model(2)
    cm.edge_km[:] = np.where(np.isfinite(cm.edge_km), 39.0, np.inf)
    cm.edge_km[:, :, cm.max_dj] = 30.0                    # straight 30 km, sideways step 39 km
    j = [cm.j0, cm.j0 + 1, cm.j0 + 1, cm.j0, cm.j0, cm.j0]
    d = path_delay_s(cm, j, 230.0)
    assert d[0] == 0.0 and d[1] == pytest.approx(9000 / 230.0)
    assert d[-1] == pytest.approx(18000 / 230.0)          # two sideways steps, 9 km extra each


def test_plan_timed_stops_when_the_path_repeats():
    cm = random_model(0)
    calls = []
    def rebuild(delay):
        calls.append(delay)
        return cm                                          # weather independent of delay here
    p = plan_timed(rebuild, cm, lam=1.0, speed_ms=230.0)
    assert p["converged"] and p["iterations"] == 2 and len(calls) == 1
    assert p["J"] == pytest.approx(plan(cm, 1.0)["J"])