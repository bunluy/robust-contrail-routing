"""Robust route search: the C++ core and its pure-Python reference find the true
optimum (checked by brute force), agree with each other path for path, and
reduce to the ordinary planner when there is only one scenario."""
import itertools
from dataclasses import replace

import numpy as np
import pytest

from contrail_routing.planner import CostModel, path_costs, plan
from contrail_routing.robust import contrail_core, robust_plan

needs_cpp = pytest.mark.skipif(contrail_core is None, reason="C++ core not built (pip install ./cpp)")
ENGINES = ["python", pytest.param("cpp", marks=needs_cpp)]


def scenario_models(seed, n=3, S=5, J=3, K=3, unflyable=()):
    """n cost models on one random grid: same lengths and fuel, different contrail fields."""
    rng = np.random.default_rng(seed)
    offsets = (np.arange(J) - J // 2) * 25.0
    edge = rng.uniform(20.0, 40.0, (S - 1, J, 3))
    for i, dj in enumerate((-1, 0, 1)):
        for j in range(J):
            if not 0 <= j + dj < J:
                edge[:, j, i] = np.inf
    fuel = rng.uniform(2.5, 3.0, K)
    fuel[list(unflyable)] = np.inf
    base = CostModel(lon=np.zeros((S, J)), lat=np.zeros((S, J)), offsets_km=offsets,
                     flight_levels=np.arange(K) * 10 + 340, times=np.zeros(S), edge_km=edge,
                     pcr_frac=np.zeros((S - 1, J, 3, K, 3)), fuel_per_km=fuel, climb_kg=15.0)
    return [replace(base, pcr_frac=rng.integers(0, 4, (S - 1, J, 3, K, 3)) / 3.0) for _ in range(n)]


def brute_force(models, lam, rule, max_dk):
    """Best robust objective over every allowed path (level held on the final segment)."""
    m0 = models[0]
    S, J, K = m0.shape
    best_single = np.array([plan(m, lam, max_dk=max_dk)["J"] for m in models])
    offset = -best_single if rule == "min_regret" else np.zeros(len(models))
    best = np.inf
    for mid in itertools.product(range(J), repeat=S - 2):
        j_idx = (m0.j0, *mid, m0.j0)
        if np.abs(np.diff(j_idx)).max() > 1:
            continue
        for k_idx in itertools.product(range(K), repeat=S):
            if (np.abs(np.diff(k_idx)).max() > max_dk or k_idx[-1] != k_idx[-2]
                    or not np.isfinite(m0.fuel_per_km[list(k_idx)]).all()):
                continue
            J_i = [path_costs(m, j_idx, k_idx) for m in models]
            best = min(best, max(c["fuel_kg"] + lam * c["pcr_km"] + o for c, o in zip(J_i, offset)))
    return best


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("rule", ["worst_case", "min_regret"])
@pytest.mark.parametrize("seed,lam,max_dk", [(0, 1.0, 1), (1, 10.0, 1), (2, 10.0, 0), (3, 0.1, 1)])
def test_robust_matches_brute_force(engine, rule, seed, lam, max_dk):
    models = scenario_models(seed, unflyable=(1,) if seed == 3 else ())
    r = robust_plan(models, lam, rule=rule, max_dk=max_dk, engine=engine)
    assert r["objective"] == pytest.approx(brute_force(models, lam, rule, max_dk), rel=1e-12)
    # the returned path really has the costs reported
    for m, p in zip(models, r["pcr_km"]):
        c = path_costs(m, r["j_idx"], r["k_idx"])
        assert c["pcr_km"] == pytest.approx(p, rel=1e-12)
        assert c["fuel_kg"] == pytest.approx(r["fuel_kg"], rel=1e-12)


@needs_cpp
@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("rule", ["worst_case", "min_regret"])
def test_cpp_and_python_return_the_same_path(seed, rule):
    models = scenario_models(seed, n=3, S=10, J=5, K=3)
    py_r = robust_plan(models, 3.0, rule=rule, engine="python")
    cpp_r = robust_plan(models, 3.0, rule=rule, engine="cpp")
    assert np.array_equal(py_r["j_idx"], cpp_r["j_idx"]) and np.array_equal(py_r["k_idx"], cpp_r["k_idx"])
    assert cpp_r["objective"] == pytest.approx(py_r["objective"], rel=1e-12)
    for key in ("labels_created", "labels_pruned_by_bound", "labels_dominated"):
        assert cpp_r[key] == py_r[key]                     # same algorithm, step for step


@pytest.mark.parametrize("engine", ENGINES)
def test_one_scenario_equals_the_ordinary_planner(engine):
    models = scenario_models(5, n=1, S=12, J=7, K=5)
    for lam in (0.1, 1.0, 10.0):
        r = robust_plan(models, lam, engine=engine)
        assert r["objective"] == pytest.approx(plan(models[0], lam)["J"], rel=1e-12)


@pytest.mark.parametrize("engine", ENGINES)
def test_identical_scenarios_give_the_same_answer_as_one(engine):
    one = scenario_models(6, n=1, S=10, J=5, K=4)
    many = one * 4
    assert robust_plan(many, 2.0, engine=engine)["objective"] == pytest.approx(
        robust_plan(one, 2.0, engine=engine)["objective"], rel=1e-12)
    assert robust_plan(many, 2.0, rule="min_regret", engine=engine)["objective"] == pytest.approx(0.0, abs=1e-9)


def test_mismatched_scenarios_are_refused():
    a, b = scenario_models(7, n=2)
    b = replace(b, fuel_per_km=b.fuel_per_km * 1.01)
    with pytest.raises(ValueError):
        robust_plan([a, b], 1.0)