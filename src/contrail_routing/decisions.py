"""Route choice: Pareto fronts and deterministic vs robust decision rules.

All rules use the objective J = fuel_kg + lam * pcr_km, where lam is the fuel
(kg) an operator is willing to burn to avoid 1 km of flight in persistent-
contrail (PCR) conditions.
"""
import pandas as pd

from .evaluate import pareto_mask


def pareto_with_marginal(m, cost="fuel_kg", harm="pcr_km"):
    """Pareto front of the routes in m (cheapest first), with the marginal
    fuel cost of each step along the front."""
    f = m[pareto_mask(m, cost=cost, harm=harm)].sort_values(cost).copy()
    f["extra_fuel_vs_min_kg"] = f[cost] - f[cost].iloc[0]
    f["pcr_avoided_vs_min_km"] = f[harm].iloc[0] - f[harm]
    f["marginal_kg_per_km_avoided"] = f[cost].diff() / (-f[harm].diff())
    return f


def objective(pcr, fuel, candidates, lam):
    """J for each candidate route (rows) in each scenario (columns)."""
    return pcr.loc[candidates].mul(lam).add(fuel.loc[candidates], axis=0)


def best_per_scenario(pcr, fuel, candidates, lam):
    """The route that minimises J in each scenario, if that scenario were known."""
    return objective(pcr, fuel, candidates, lam).idxmin()


def choose(pcr, fuel, candidates, lam, base="base"):
    """
    Compare three rules:
      deterministic - minimise J in the base scenario
      worst_case    - minimise the largest J across scenarios
      min_regret    - minimise the largest gap to the best route in each scenario
    """
    J = objective(pcr, fuel, candidates, lam)
    regret = J - J.min(axis=0)
    picks = {
        "deterministic": J[base].idxmin(),
        "worst_case": J.max(axis=1).idxmin(),
        "min_regret": regret.max(axis=1).idxmin(),
    }
    rows = []
    for rule, r in picks.items():
        rows.append({
            "rule": rule, "route": r, "fuel_kg": fuel[r],
            "pcr_base_km": pcr.at[r, base],
            "pcr_worst_km": pcr.loc[r].max(),
            "worst_scenario": pcr.loc[r].idxmax(),
            "max_regret_kg": regret.loc[r].max(),
        })
    out = pd.DataFrame(rows)
    det_fuel = out.loc[out["rule"] == "deterministic", "fuel_kg"].iloc[0]
    out["extra_fuel_vs_det_kg"] = out["fuel_kg"] - det_fuel
    return out
