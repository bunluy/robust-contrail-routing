import numpy as np
import pandas as pd

from contrail_routing.geometry import flight_level_to_m, haversine_km, make_route

ORIGIN, DEST = (-10.0, 45.0), (10.0, 55.0)
DEP = pd.Timestamp("2022-03-01 00:00")


def route_length_km(fl):
    lon, lat = np.asarray(fl["longitude"]), np.asarray(fl["latitude"])
    return haversine_km(lon[:-1], lat[:-1], lon[1:], lat[1:]).sum()


def test_haversine_known_value():
    # One degree of latitude is about 111.2 km on a 6,371 km sphere
    assert abs(haversine_km(0.0, 0.0, 0.0, 1.0) - 111.19) < 0.05


def test_flight_level_conversion():
    assert abs(flight_level_to_m(370) - 11277.6) < 1e-6


def test_great_circle_route_matches_great_circle_distance():
    fl = make_route(ORIGIN, DEST, 0, 11000, DEP, 230.0)
    assert abs(route_length_km(fl) - haversine_km(*ORIGIN, *DEST)) < 0.5


def test_route_starts_and_ends_at_endpoints():
    fl = make_route(ORIGIN, DEST, 150, 11000, DEP, 230.0)
    assert np.allclose([fl["longitude"][0], fl["latitude"][0]], ORIGIN, atol=1e-6)
    assert np.allclose([fl["longitude"][-1], fl["latitude"][-1]], DEST, atol=1e-6)


def test_left_and_right_deviations_are_symmetric():
    nw = route_length_km(make_route(ORIGIN, DEST, 300, 11000, DEP, 230.0))
    se = route_length_km(make_route(ORIGIN, DEST, -300, 11000, DEP, 230.0))
    assert abs(nw - se) < 0.1
    assert nw > haversine_km(*ORIGIN, *DEST)


def test_positive_offset_bends_north_west():
    gc = make_route(ORIGIN, DEST, 0, 11000, DEP, 230.0)
    nw = make_route(ORIGIN, DEST, 150, 11000, DEP, 230.0)
    mid_gc, mid_nw = len(gc) // 2, len(nw) // 2
    assert nw["latitude"][mid_nw] > gc["latitude"][mid_gc]
    assert nw["longitude"][mid_nw] < gc["longitude"][mid_gc]


def test_constant_speed_and_altitude():
    fl = make_route(ORIGIN, DEST, 0, 11000, DEP, 230.0, time_step_s=60)
    duration_s = (fl["time"][-1] - fl["time"][0]) / np.timedelta64(1, "s")
    assert abs(route_length_km(fl) * 1000 / duration_s - 230.0) < 0.5
    assert np.all(np.asarray(fl["altitude"]) == 11000.0)
