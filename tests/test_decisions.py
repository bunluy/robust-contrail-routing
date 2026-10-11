import pandas as pd

from contrail_routing.decisions import choose, pareto_with_marginal
from contrail_routing.evaluate import pareto_mask


def toy_routes():
    # A is cheapest; B trades fuel for exposure; C is dominated by B; D is cleanest
    return pd.DataFrame(
        {"fuel_kg": [100.0, 110.0, 120.0, 150.0], "pcr_km": [400.0, 100.0, 150.0, 0.0]},
        index=["A", "B", "C", "D"],
    )


def test_pareto_mask_drops_dominated_route():
    mask = pareto_mask(toy_routes())
    assert list(mask[mask].index) == ["A", "B", "D"]


def test_pareto_marginal_costs():
    f = pareto_with_marginal(toy_routes())
    assert list(f.index) == ["A", "B", "D"]
    assert abs(f.loc["B", "marginal_kg_per_km_avoided"] - 10 / 300) < 1e-12
    assert abs(f.loc["D", "marginal_kg_per_km_avoided"] - 40 / 100) < 1e-12


def test_choose_rules_differ_when_a_scenario_hurts_the_deterministic_choice():
    fuel = pd.Series({"A": 100.0, "B": 110.0})
    # A is best in base but very exposed in the "wet" scenario
    pcr = pd.DataFrame({"base": [0.0, 5.0], "wet": [100.0, 5.0]}, index=["A", "B"])
    out = choose(pcr, fuel, ["A", "B"], lam=1.0).set_index("rule")
    assert out.loc["deterministic", "route"] == "A"
    assert out.loc["worst_case", "route"] == "B"
    assert out.loc["worst_case", "extra_fuel_vs_det_kg"] == 10.0
