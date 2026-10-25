"""Robust route planning over weather scenarios.

Each scenario (e.g. the weather field shifted up or down) gives its own cost
model on the same grid: the same geometry and fuel, a different contrail field.
A path's cost in scenario i is J_i = fuel + lam * pcr_km_i. Two rules:
    worst_case: minimise max_i J_i
    min_regret: minimise max_i (J_i - J*_i), J*_i = best J_i of any path
Neither splits into a sum over stages, so plan() cannot be used; the search
keeps, at each grid point, every partial path not dominated on fuel and on the
PCR km of every scenario, and prunes partial paths that cannot beat the best
complete path known, using lower bounds on the cost still to come. Exact on the
grid. The search runs in the C++ module contrail_core (built from cpp/) when it
is installed; robust_plan_reference() is the same algorithm in pure Python,
used for testing and benchmarking.
"""
import math

import numpy as np

from .planner import _moves, plan

try:
    import contrail_core
except ImportError:          # C++ core not built: the Python reference is used
    contrail_core = None

INF = math.inf


def stack_scenarios(models):
    """Arrays for the search from one CostModel per scenario (same grid and fuel)."""
    m0 = models[0]
    for m in models[1:]:
        if (m.edge_km.shape != m0.edge_km.shape or not np.array_equal(m.edge_km, m0.edge_km)
                or not np.array_equal(m.fuel_per_km, m0.fuel_per_km) or m.climb_kg != m0.climb_kg):
            raise ValueError("scenario cost models must share the grid, fuel rates and climb cost")
    pcr = np.stack([m.pcr_frac for m in models])
    return m0.edge_km, pcr, m0.fuel_per_km, m0.climb_kg, m0.j0


def robust_plan_reference(edge_km, pcr_frac, fuel_per_km, climb_kg, lam, scenario_offset, j0,
                          max_dk_used, upper_bound=INF, max_labels=50_000_000):
    """Pure-Python version of contrail_core.robust_plan: same algorithm, same tie-breaking."""
    Sm1, J, DJ = edge_km.shape
    n, K, DK = pcr_frac.shape[0], pcr_frac.shape[4], pcr_frac.shape[5]
    S, mdj, mdk = Sm1 + 1, (DJ - 1) // 2, (DK - 1) // 2
    edge = edge_km.tolist()
    pcr = pcr_frac.tolist()
    fuel = [float(f) for f in fuel_per_km]
    c = [float(x) for x in scenario_offset]
    flyable = [math.isfinite(f) for f in fuel]
    dj_moves = _moves(mdj)

    def level_moves(s):
        return _moves(max_dk_used) if s < S - 2 else [0]

    def valid(s, j, dj, k, dk):
        jt, kt = j + dj, k + dk
        return (0 <= jt < J and 0 <= kt < K and flyable[k] and flyable[kt]
                and math.isfinite(edge[s][j][dj + mdj]))

    def edge_fuel(s, j, dj, k, dk):
        return fuel[k] * edge[s][j][dj + mdj] + climb_kg * max(dk, 0)

    def robust_value(F, P):
        return F + max(lam * P[i] + c[i] for i in range(n))

    def cost_to_go(edge_cost):
        out = [[[INF] * K for _ in range(J)] for _ in range(S)]
        for k in range(K):
            if flyable[k]:
                out[S - 1][j0][k] = 0.0
        for s in range(S - 2, -1, -1):
            for j in range(J):
                for k in range(K):
                    best = INF
                    for dj in dj_moves:
                        for dk in level_moves(s):
                            if not valid(s, j, dj, k, dk):
                                continue
                            rest = out[s + 1][j + dj][k + dk]
                            if math.isfinite(rest):
                                best = min(best, edge_cost(s, j, dj, k, dk) + rest)
                    out[s][j][k] = best
        return out

    fuel_lb = cost_to_go(edge_fuel)
    pcr_lb = [cost_to_go(lambda s, j, dj, k, dk, i=i: edge[s][j][dj + mdj] * pcr[i][s][j][dj + mdj][k][dk + mdk])
              for i in range(n)]

    def mean_cost(s, j, dj, k, dk):
        p = sum(pcr[i][s][j][dj + mdj][k][dk + mdk] for i in range(n))
        return edge_fuel(s, j, dj, k, dk) + lam * edge[s][j][dj + mdj] * p / n

    mean_to_go = cost_to_go(mean_cost)
    k_best = -1
    for k in range(K):
        if mean_to_go[0][j0][k] < (INF if k_best < 0 else mean_to_go[0][j0][k_best]):
            k_best = k
    if k_best < 0:
        raise RuntimeError("no feasible path")
    j, k, F, P = j0, k_best, 0.0, [0.0] * n
    for s in range(S - 1):
        best, bj, bk = INF, 0, 0
        for dj in dj_moves:
            for dk in level_moves(s):
                if not valid(s, j, dj, k, dk):
                    continue
                v = mean_cost(s, j, dj, k, dk) + mean_to_go[s + 1][j + dj][k + dk]
                if v < best:
                    best, bj, bk = v, dj, dk
        L = edge[s][j][bj + mdj]
        F += edge_fuel(s, j, bj, k, bk)
        P = [P[i] + L * pcr[i][s][j][bj + mdj][k][bk + mdk] for i in range(n)]
        j, k = j + bj, k + bk
    UB = min(upper_bound, robust_value(F, P))
    tol = 1e-9 * max(1.0, abs(UB))

    # stage[s] = (F list, P list of lists, parent, j, k, alive, at_node dict)
    def new_stage():
        return {"F": [], "P": [], "parent": [], "j": [], "k": [], "alive": [], "at": {}}

    stages = [new_stage() for _ in range(S)]
    st0 = stages[0]
    for k in range(K):
        if flyable[k]:
            st0["F"].append(0.0); st0["P"].append([0.0] * n); st0["parent"].append(-1)
            st0["j"].append(j0); st0["k"].append(k); st0["alive"].append(True)
            st0["at"].setdefault((j0, k), []).append(len(st0["F"]) - 1)
    created, pruned, dominated, max_at = len(st0["F"]), 0, 0, 1
    for s in range(S - 1):
        cur, nxt = stages[s], stages[s + 1]
        for jj in range(J):                      # nodes in the same order as the C++ core
            for kk in range(K):
                for a in cur["at"].get((jj, kk), []):
                    j, k = cur["j"][a], cur["k"][a]
                    for dj in dj_moves:
                        for dk in level_moves(s):
                            if not valid(s, j, dj, k, dk):
                                continue
                            jt, kt = j + dj, k + dk
                            if not math.isfinite(fuel_lb[s + 1][jt][kt]):
                                continue
                            L = edge[s][j][dj + mdj]
                            Fn = cur["F"][a] + edge_fuel(s, j, dj, k, dk)
                            Pn = [cur["P"][a][i] + L * pcr[i][s][j][dj + mdj][k][dk + mdk] for i in range(n)]
                            worst_lb = max(lam * (Pn[i] + pcr_lb[i][s + 1][jt][kt]) + c[i] for i in range(n))
                            if Fn + fuel_lb[s + 1][jt][kt] + worst_lb > UB + tol:
                                pruned += 1
                                continue
                            here = nxt["at"].setdefault((jt, kt), [])
                            beaten = False
                            for b in here:
                                Pb = nxt["P"][b]
                                b_le = nxt["F"][b] <= Fn and all(Pb[i] <= Pn[i] for i in range(n))
                                if b_le:
                                    beaten = True
                                    break
                                if Fn <= nxt["F"][b] and all(Pn[i] <= Pb[i] for i in range(n)):
                                    nxt["alive"][b] = False
                                    dominated += 1
                            if beaten:
                                dominated += 1
                                continue
                            here[:] = [b for b in here if nxt["alive"][b]]
                            nxt["F"].append(Fn); nxt["P"].append(Pn); nxt["parent"].append(a)
                            nxt["j"].append(jt); nxt["k"].append(kt); nxt["alive"].append(True)
                            here.append(len(nxt["F"]) - 1)
                            max_at = max(max_at, len(here))
                            created += 1
                            if created > max_labels:
                                raise RuntimeError("label limit exceeded")
    last = stages[S - 1]
    best, best_val = -1, INF
    for k in range(K):
        for a in last["at"].get((j0, k), []):
            v = robust_value(last["F"][a], last["P"][a])
            if v < best_val:
                best, best_val = a, v
    if best < 0:
        raise RuntimeError("no path found")
    j_idx, k_idx, a = [0] * S, [0] * S, best
    for s in range(S - 1, -1, -1):
        j_idx[s], k_idx[s] = stages[s]["j"][a], stages[s]["k"][a]
        a = stages[s]["parent"][a]
    return {"j_idx": j_idx, "k_idx": k_idx, "objective": best_val, "fuel_kg": last["F"][best],
            "pcr_km": list(last["P"][best]), "upper_bound_start": UB, "labels_created": created,
            "labels_pruned_by_bound": pruned, "labels_dominated": dominated, "max_labels_at_a_point": max_at}


def robust_plan(models, lam, rule="worst_case", max_dk=None, engine="auto"):
    """
    Robust path over the scenario cost models (a list, one per scenario).
    rule: "worst_case" or "min_regret". engine: "cpp", "python" or "auto"
    (C++ if built). Returns the path as plan() does, plus each scenario's PCR
    km and J, the objective and search statistics.
    """
    m0 = models[0]
    max_dk = m0.max_dk if max_dk is None else max_dk
    edge, pcr, fuel, climb, j0 = stack_scenarios(models)
    if rule == "worst_case":
        offset = np.zeros(len(models))
        best_single = None
    elif rule == "min_regret":
        best_single = np.array([plan(m, lam, max_dk=max_dk)["J"] for m in models])
        offset = -best_single
    else:
        raise ValueError("rule must be 'worst_case' or 'min_regret'")
    if engine == "auto":
        engine = "cpp" if contrail_core is not None else "python"
    if engine == "cpp":
        if contrail_core is None:
            raise ImportError("contrail_core is not built; run: python -m pip install --no-build-isolation ./cpp")
        r = contrail_core.robust_plan(np.ascontiguousarray(edge), np.ascontiguousarray(pcr),
                                      np.ascontiguousarray(fuel), float(climb), float(lam),
                                      np.ascontiguousarray(offset, dtype=float), int(j0), int(max_dk))
    elif engine == "python":
        r = robust_plan_reference(edge, pcr, fuel, climb, lam, offset, j0, max_dk)
    else:
        raise ValueError("engine must be 'cpp', 'python' or 'auto'")
    S = len(r["j_idx"])
    j_idx, k_idx = np.array(r["j_idx"]), np.array(r["k_idx"])
    pcr_km = np.array(r["pcr_km"])
    out = dict(r)
    out.update({"engine": engine, "rule": rule, "j_idx": j_idx, "k_idx": k_idx, "pcr_km": pcr_km,
                "J_by_scenario": r["fuel_kg"] + lam * pcr_km, "best_single_J": best_single,
                "offset_km": m0.offsets_km[j_idx], "flight_level": m0.flight_levels[k_idx],
                "lon": m0.lon[np.arange(S), j_idx], "lat": m0.lat[np.arange(S), j_idx],
                "level_changes": int(np.count_nonzero(np.diff(k_idx)))})
    return out