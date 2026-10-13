"""Case definition: other dates give the same routes, shifted in time only."""
import numpy as np
import pandas as pd

from contrail_routing import config


def test_build_routes_departure_shifts_time_only():
    specs = config.route_specs(flight_levels=[360], offsets_km=[0, 150])
    a = config.build_routes(specs)
    b = config.build_routes(specs, departure=pd.Timestamp("2022-07-15 00:00"))
    shift = (pd.Timestamp("2022-07-15 00:00") - config.DEPARTURE).to_timedelta64()
    for name in specs:
        assert np.array_equal(a[name]["longitude"], b[name]["longitude"])
        assert np.array_equal(a[name]["latitude"], b[name]["latitude"])
        assert np.all(np.asarray(b[name]["time"]) - np.asarray(a[name]["time"]) == shift)


def test_study_dates_rule():
    assert len(config.STUDY_DATES) == 24
    assert config.STUDY_DATES[0] == "2022-01-01" and config.STUDY_DATES[-1] == "2022-12-15"
    assert "2022-03-01" in config.STUDY_DATES