"""Robust route planning over weather scenarios.

Each scenario (e.g. the weather field shifted up or down) gives its own cost
model on the same grid: the same geometry and fuel, a different contrail field.
A path's cost in scenario i is J_i = fuel + lam * pcr_km_i. Two rules:
    worst_case: minimise max_i J_i
    min_regret: minimise max_i (J_i - J*_i), J*_i = best J_i of any path
Neither splits into a sum over stages, so plan() cannot be used. The search:
  1. Bounds. Per scenario, the least (fuel + lam * PCR km) still to come from
     every grid point. A Lagrangian bound: for weights w on the scenarios
     (summing to 1), max_i(J_i + c_i) >= sum_i w_i (J_i + c_i) for every path,
     so the least weighted cost is a lower bound on the answer; the weights are
     improved by projected subgradient steps, and every weighted-optimal path
     is an upper-bound candidate. If the bounds meet, that path is optimal.
  2. Otherwise, a label-setting search keeps, at each grid point, every partial
     path not dominated on fuel and on the PCR km of every scenario, and drops
     partial paths whose lower bound exceeds the best complete path known.
Exact on the grid. If a limit on partial paths or time is reached, the best
path found by the bounds is returned with a certified gap.

The search runs in the C++ module contrail_core (built from cpp/) when it is
installed; robust_plan_reference() is the same algorithm, step for step, in
pure Python, used for testing and benchmarking.
"""
import math
import time

import numpy as np

from .planner import _moves, path_delay_s, plan

try:
    import contrail_core
except ImportError:          # C++ core not built: the Python reference is used
    contrail_core = None

INF = math.inf
SUBGRADIENT_STEPS = 60


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
                          max_dk_used, upper_bound=INF, max_labels=10_000_000, max_seconds=INF):
    """Pure-Python version of contrail_core.robust_plan: same algorithm, same tie-breaking."""
    t_start = time.monotonic()
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

    def weighted_cost(w):
        def cost(s, j, dj, k, dk):
            p = 0.0
            for i in range(n):
                p += w[i] * pcr[i][s][j][dj + mdj][k][dk + mdk]
            return edge_fuel(s, j, dj, k, dk) + lam * edge[s][j][dj + mdj] * p
        return cost

    def weighted_offset(w):
        o = 0.0
        for i in range(n):
            o += w[i] * c[i]
        return o

    def follow(to_go, edge_cost):
        """Cheapest path through a cost-to-go table: (robust value, F, P, j path, k path)."""
        k_best = -1
        for k in range(K):
            if to_go[0][j0][k] < (INF if k_best < 0 else to_go[0][j0][k_best]):
                k_best = k
        if k_best < 0:
            return INF, 0.0, [], [], []
        j, k, F, P = j0, k_best, 0.0, [0.0] * n
        js, ks = [j], [k]
        for s in range(S - 1):
            best, bj, bk = INF, 0, 0
            for dj in dj_moves:
                for dk in level_moves(s):
                    if not valid(s, j, dj, k, dk):
                        continue
                    v = edge_cost(s, j, dj, k, dk) + to_go[s + 1][j + dj][k + dk]
                    if v < best:
                        best, bj, bk = v, dj, dk
            L = edge[s][j][bj + mdj]
            F += edge_fuel(s, j, bj, k, bk)
            P = [P[i] + L * pcr[i][s][j][bj + mdj][k][bk + mdk] for i in range(n)]
            j, k = j + bj, k + bk
            js.append(j)
            ks.append(k)
        return robust_value(F, P), F, P, js, ks

    # 1. Bounds: per-scenario joint lower bounds (their optimal paths are upper-bound candidates)
    joint_lb, ub_path = [], (INF, 0.0, [], [], [])
    for i in range(n):
        w = [0.0] * n
        w[i] = 1.0
        joint_lb.append(cost_to_go(weighted_cost(w)))
        cand = follow(joint_lb[i], weighted_cost(w))
        if cand[0] < ub_path[0]:
            ub_path = cand
    # Lagrangian bound, weights improved by projected subgradient steps
    w = [1.0 / n] * n
    best_w, best_L, best_to_go = list(w), -INF, None
    for t in range(1, SUBGRADIENT_STEPS + 1):
        to_go = cost_to_go(weighted_cost(w))
        start = INF
        for k in range(K):
            start = min(start, to_go[0][j0][k])
        if not math.isfinite(start):
            break
        Lw = start + weighted_offset(w)
        cand = follow(to_go, weighted_cost(w))
        if cand[0] < ub_path[0]:
            ub_path = cand
        if Lw > best_L:
            best_L, best_w, best_to_go = Lw, list(w), to_go
        if ub_path[0] - best_L <= 1e-9 * max(1.0, abs(ub_path[0])):
            break
        _, pF, pP, _, _ = cand
        a = [pF + lam * pP[i] + c[i] for i in range(n)]
        lo, hi, mean_a = INF, -INF, 0.0
        for i in range(n):
            lo, hi = min(lo, a[i]), max(hi, a[i])
            mean_a += w[i] * a[i]
        if hi - lo <= 0.0:
            break
        step = 0.5 / math.sqrt(float(t))
        total = 0.0
        for i in range(n):
            w[i] = max(0.0, w[i] + step * (a[i] - mean_a) / (hi - lo))
            total += w[i]
        w = [w[i] / total for i in range(n)]
    if not math.isfinite(ub_path[0]):
        raise RuntimeError("no feasible path")
    best_w_offset = weighted_offset(best_w)
    ub_heuristic = ub_path[0]
    UB = min(upper_bound, ub_heuristic)
    tol = 1e-9 * max(1.0, abs(UB))

    def from_bounds(proved, stopped, created=0, pruned=0, dominated=0, max_at=0):
        return {"j_idx": ub_path[3], "k_idx": ub_path[4], "objective": ub_path[0], "fuel_kg": ub_path[1],
                "pcr_km": list(ub_path[2]), "upper_bound_start": UB, "lower_bound_lagrangian": best_L,
                "proved_by_bounds": proved, "stopped": stopped,
                "gap": ub_path[0] - best_L if stopped else 0.0,
                "labels_created": created, "labels_pruned_by_bound": pruned,
                "labels_dominated": dominated, "max_labels_at_a_point": max_at}

    if ub_heuristic <= upper_bound and ub_heuristic - best_L <= tol:      # bounds meet: proven optimal
        return from_bounds(True, "")

    # 2. Label-setting search, one stage at a time
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
    stopped = ""
    for s in range(S - 1):
        if stopped:
            break
        cur, nxt = stages[s], stages[s + 1]
        for jj in range(J):                      # nodes in the same order as the C++ core
            if stopped:
                break
            for kk in range(K):
                if stopped:
                    break
                for a in cur["at"].get((jj, kk), []):
                    if stopped:
                        break
                    j, k = cur["j"][a], cur["k"][a]
                    for dj in dj_moves:
                        if stopped:
                            break
                        for dk in level_moves(s):
                            if not valid(s, j, dj, k, dk):
                                continue
                            jt, kt = j + dj, k + dk
                            if not math.isfinite(best_to_go[s + 1][jt][kt]):
                                continue
                            L = edge[s][j][dj + mdj]
                            Fn = cur["F"][a] + edge_fuel(s, j, dj, k, dk)
                            Pn = [cur["P"][a][i] + L * pcr[i][s][j][dj + mdj][k][dk + mdk] for i in range(n)]
                            lb, weighted = -INF, 0.0
                            for i in range(n):
                                lb = max(lb, Fn + lam * Pn[i] + c[i] + joint_lb[i][s + 1][jt][kt])
                                weighted += best_w[i] * Pn[i]
                            lb = max(lb, Fn + lam * weighted + best_w_offset + best_to_go[s + 1][jt][kt])
                            if lb > UB + tol:
                                pruned += 1
                                continue
                            here = nxt["at"].setdefault((jt, kt), [])
                            beaten = False
                            for b in here:
                                Pb = nxt["P"][b]
                                if nxt["F"][b] <= Fn and all(Pb[i] <= Pn[i] for i in range(n)):
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
                                stopped = "label limit"
                                break
                            if created % 1024 == 0 and time.monotonic() - t_start > max_seconds:
                                stopped = "time limit"
                                break
    if stopped:          # best path found by the bounds, with its certified gap
        return from_bounds(False, stopped, created, pruned, dominated, max_at)

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
            "pcr_km": list(last["P"][best]), "upper_bound_start": UB, "lower_bound_lagrangian": best_L,
            "proved_by_bounds": False, "stopped": "", "gap": 0.0, "labels_created": created,
            "labels_pruned_by_bound": pruned, "labels_dominated": dominated, "max_labels_at_a_point": max_at}


def robust_plan(models, lam, rule="worst_case", max_dk=None, engine="auto", warm_start=False,
                max_labels=10_000_000, max_seconds=600.0, upper_bound=INF):
    """
    Robust path over the scenario cost models (a list, one per scenario).
    rule: "worst_case" or "min_regret". engine: "cpp", "python" or "auto"
    (C++ if built).
    warm_start: when level changes are allowed, first solve with the level held
    constant and use that path's objective as the bound to beat; it is a valid
    bound because a constant-level path is also a free-level path, so the answer
    is unchanged. Off by default: with the joint and Lagrangian bounds it rarely helps.
    max_labels, max_seconds: if the exact search would keep more partial paths
    than this (about 80 bytes each with 7 scenarios: 10 million ~ 0.8 GB) or
    run longer than this, it stops and returns the best path found by the
    bounds, with 'stopped' giving the reason and 'gap' a certified bound: no
    path is better than objective - gap. Exact results have stopped == ''.
    Returns the path as plan() does, plus each scenario's PCR km and J, the
    objective and search statistics.
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
    if engine == "cpp" and contrail_core is None:
        raise ImportError("contrail_core is not built; run: python -m pip install --no-build-isolation ./cpp")
    if engine not in ("cpp", "python"):
        raise ValueError("engine must be 'cpp', 'python' or 'auto'")

    def search(max_dk_used, ub):
        if engine == "cpp":
            return contrail_core.robust_plan(np.ascontiguousarray(edge), np.ascontiguousarray(pcr),
                                             np.ascontiguousarray(fuel), float(climb), float(lam),
                                             np.ascontiguousarray(offset, dtype=float), int(j0),
                                             int(max_dk_used), float(ub), int(max_labels), float(max_seconds))
        return robust_plan_reference(edge, pcr, fuel, climb, lam, offset, j0, max_dk_used, ub, max_labels,
                                     max_seconds)

    warm_objective, warm_labels = None, 0
    if warm_start and max_dk > 0:
        w = search(0, upper_bound)
        warm_objective, warm_labels = w["objective"], w["labels_created"]
        upper_bound = min(upper_bound, warm_objective)
    r = search(max_dk, upper_bound)
    S = len(r["j_idx"])
    j_idx, k_idx = np.array(r["j_idx"]), np.array(r["k_idx"])
    pcr_km = np.array(r["pcr_km"])
    out = dict(r)
    out.update({"engine": engine, "rule": rule, "j_idx": j_idx, "k_idx": k_idx, "pcr_km": pcr_km,
                "J_by_scenario": r["fuel_kg"] + lam * pcr_km, "best_single_J": best_single,
                "warm_start_objective": warm_objective, "warm_start_labels": warm_labels,
                "offset_km": m0.offsets_km[j_idx], "flight_level": m0.flight_levels[k_idx],
                "lon": m0.lon[np.arange(S), j_idx], "lat": m0.lat[np.arange(S), j_idx],
                "level_changes": int(np.count_nonzero(np.diff(k_idx)))})
    return out


def robust_plan_timed(rebuild, models, lam, speed_ms, rule="worst_case", max_dk=None, max_iter=5, **kwargs):
    """
    As planner.plan_timed, for the robust search: plan, re-evaluate every
    scenario's contrail field with the planned path's delay, and re-plan until
    the path repeats or max_iter plans were made. rebuild(delay_s) must return
    the list of scenario CostModels for those delays. A search stopped by a
    limit is not re-timed (repeating it would only spend the limit again).
    """
    r = robust_plan(models, lam, rule=rule, max_dk=max_dk, **kwargs)
    if r["stopped"]:
        r.update(iterations=1, converged=False)
        return r
    for it in range(2, max_iter + 1):
        models = rebuild(path_delay_s(models[0], r["j_idx"], speed_ms))
        q = robust_plan(models, lam, rule=rule, max_dk=max_dk, **kwargs)
        if q["stopped"]:
            q.update(iterations=it, converged=False)
            return q
        if np.array_equal(q["j_idx"], r["j_idx"]) and np.array_equal(q["k_idx"], r["k_idx"]):
            q.update(iterations=it, converged=True)
            return q
        r = q
    r.update(iterations=max_iter, converged=False)
    return r
