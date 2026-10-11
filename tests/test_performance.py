"""The PS fuel calculation must not be affected by attributes left by PCR."""
import warnings

import numpy as np
import pandas as pd

from contrail_routing.evaluate import evaluate_route
from contrail_routing.geometry import make_route
from contrail_routing.performance import fuel_burn
from test_scenarios_and_pcr import synthetic_met

warnings.filterwarnings("ignore", message=r"\s*Met data appears")


def test_fuel_same_with_and_without_pcr_attributes():
    met = synthetic_met(rhi_value=0.8)
    fl = make_route((-10.0, 45.0), (10.0, 55.0), 0, 11582.4,
                    pd.Timestamp("2022-03-01 00:00"), 230.0, name="r")
    track = evaluate_route(fl, met, aircraft_type="A320", true_airspeed_ms=230.0)
    t = (track["time"] - track["time"].iloc[0]).dt.total_seconds().to_numpy()
    fuel_via_pcr_output = float(np.sum(track["fuel_flow_kg_s"].to_numpy()[:-1] * np.diff(t)))
    fuel_clean = fuel_burn(track, "A320", 230.0)["fuel_kg"]
    assert abs(fuel_via_pcr_output - fuel_clean) < 1e-6