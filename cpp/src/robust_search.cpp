// Exact robust route search over the planner grid (see contrail_routing/planner.py
// and contrail_routing/robust.py, which holds a pure-Python copy of this algorithm).
//
// A path's cost in scenario i is  fuel + lam * pcr_km_i + c_i  (c_i = 0 for the
// worst-case rule, c_i = -(best cost in scenario i) for minimum regret). The
// search minimises the largest of these over the scenarios. Because the maximum
// of sums is not the sum of maxima, a plain stage-by-stage search cannot do this.
//  1. Bounds: per scenario, the least (fuel + lam * PCR km) still to come from
//     every grid point; and a Lagrangian bound (a weighted average of the
//     scenarios never exceeds their maximum), with the weights improved by
//     projected subgradient steps. Each weighted-optimal path is an upper-bound
//     candidate; if the bounds meet, that path is optimal.
//  2. Otherwise each grid point keeps every partial path ("label") not
//     dominated on fuel and on PCR km in every scenario, and labels whose lower
//     bound exceeds the best complete path known are pruned. Exact on the grid.
//  3. If a limit on labels or time is reached, the best upper-bound path is
//     returned with its certified gap to the Lagrangian bound.
//
// Conventions match planner.plan(): moves tried in the order 0, -1, +1, ...;
// the flight level is held on the final segment; a new label replaces nothing
// it only equals, so ties keep the first path found.

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <utility>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;
using Array = py::array_t<double, py::array::c_style | py::array::forcecast>;

namespace {

constexpr double INF = std::numeric_limits<double>::infinity();

std::vector<int> moves(int m) {
    std::vector<int> out{0};
    for (int a = 1; a <= m; ++a) {
        out.push_back(-a);
        out.push_back(a);
    }
    return out;
}

struct Grid {
    int S, J, K, DJ, DK, n, mdj, mdk, j0, mdk_used;
    const double* edge;  // (S-1, J, DJ)
    const double* pcr;   // (n, S-1, J, DJ, K, DK)
    const double* fuel;  // (K)
    double climb;

    double length(int s, int j, int dj) const {
        return edge[(static_cast<std::size_t>(s) * J + j) * DJ + (dj + mdj)];
    }
    double frac(int i, int s, int j, int dj, int k, int dk) const {
        std::size_t idx = static_cast<std::size_t>(i);
        idx = idx * (S - 1) + s;
        idx = idx * J + j;
        idx = idx * DJ + (dj + mdj);
        idx = idx * K + k;
        idx = idx * DK + (dk + mdk);
        return pcr[idx];
    }
    bool flyable(int k) const { return std::isfinite(fuel[k]); }
    std::size_t node(int j, int k) const { return static_cast<std::size_t>(j) * K + k; }
    // level changes allowed when leaving stage s (held on the final segment)
    std::vector<int> level_moves(int s) const { return s < S - 2 ? moves(mdk_used) : std::vector<int>{0}; }
    bool valid(int s, int j, int dj, int k, int dk) const {
        int jt = j + dj, kt = k + dk;
        return jt >= 0 && jt < J && kt >= 0 && kt < K && flyable(k) && flyable(kt) &&
               std::isfinite(length(s, j, dj));
    }
    double edge_fuel(int s, int j, int dj, int k, int dk) const {
        return fuel[k] * length(s, j, dj) + climb * std::max(dk, 0);
    }
};

// Backward pass: least cost-to-go from every (s, j, k) to the destination for
// one additive edge cost. cost_to_go[s][node], INF where the destination is unreachable.
template <class EdgeCost>
std::vector<std::vector<double>> cost_to_go(const Grid& g, EdgeCost edge_cost) {
    std::vector<std::vector<double>> c(g.S, std::vector<double>(static_cast<std::size_t>(g.J) * g.K, INF));
    for (int k = 0; k < g.K; ++k)
        if (g.flyable(k)) c[g.S - 1][g.node(g.j0, k)] = 0.0;
    const auto dj_moves = moves(g.mdj);
    for (int s = g.S - 2; s >= 0; --s) {
        const auto dk_moves = g.level_moves(s);
        for (int j = 0; j < g.J; ++j)
            for (int k = 0; k < g.K; ++k) {
                double best = INF;
                for (int dj : dj_moves)
                    for (int dk : dk_moves) {
                        if (!g.valid(s, j, dj, k, dk)) continue;
                        double rest = c[s + 1][g.node(j + dj, k + dk)];
                        if (!std::isfinite(rest)) continue;
                        best = std::min(best, edge_cost(s, j, dj, k, dk) + rest);
                    }
                c[s][g.node(j, k)] = best;
            }
    }
    return c;
}

struct Labels {  // all labels created at one stage
    std::vector<double> F;      // fuel so far
    std::vector<double> P;      // PCR km so far, n per label
    std::vector<int> parent;    // label index at the previous stage
    std::vector<int> j, k;
    std::vector<char> alive;
    std::vector<std::vector<int>> at_node;  // label indices per node
};

}  // namespace

py::dict robust_plan(Array edge_km, Array pcr_frac, Array fuel_per_km, double climb_kg, double lam,
                     Array scenario_offset, int j0, int max_dk_used, double upper_bound,
                     std::int64_t max_labels, double max_seconds) {
    const auto t_start = std::chrono::steady_clock::now();
    if (edge_km.ndim() != 3 || pcr_frac.ndim() != 6 || fuel_per_km.ndim() != 1 || scenario_offset.ndim() != 1)
        throw std::invalid_argument("expected edge_km (S-1,J,DJ), pcr_frac (n,S-1,J,DJ,K,DK), fuel_per_km (K), scenario_offset (n)");
    Grid g{};
    g.S = static_cast<int>(edge_km.shape(0)) + 1;
    g.J = static_cast<int>(edge_km.shape(1));
    g.DJ = static_cast<int>(edge_km.shape(2));
    g.n = static_cast<int>(pcr_frac.shape(0));
    g.K = static_cast<int>(pcr_frac.shape(4));
    g.DK = static_cast<int>(pcr_frac.shape(5));
    g.mdj = (g.DJ - 1) / 2;
    g.mdk = (g.DK - 1) / 2;
    g.j0 = j0;
    g.mdk_used = max_dk_used;
    if (pcr_frac.shape(1) != g.S - 1 || pcr_frac.shape(2) != g.J || pcr_frac.shape(3) != g.DJ ||
        fuel_per_km.shape(0) != g.K || scenario_offset.shape(0) != g.n || g.n < 1)
        throw std::invalid_argument("array shapes do not match");
    if (j0 < 0 || j0 >= g.J || max_dk_used < 0 || max_dk_used > g.mdk)
        throw std::invalid_argument("j0 or max_dk_used out of range");
    g.edge = edge_km.data();
    g.pcr = pcr_frac.data();
    g.fuel = fuel_per_km.data();
    g.climb = climb_kg;
    const double* c = scenario_offset.data();
    const int n = g.n;

    auto robust_value = [&](double F, const double* P) {
        double worst = -INF;
        for (int i = 0; i < n; ++i) worst = std::max(worst, lam * P[i] + c[i]);
        return F + worst;
    };

    // Edge cost for a weighting w of the scenarios: fuel + lam * L * sum_i w_i * frac_i.
    auto weighted_cost = [&](const std::vector<double>& w) {
        return [&g, lam, n, w](int s, int j, int dj, int k, int dk) {
            double p = 0.0;
            for (int i = 0; i < n; ++i) p += w[i] * g.frac(i, s, j, dj, k, dk);
            return g.edge_fuel(s, j, dj, k, dk) + lam * g.length(s, j, dj) * p;
        };
    };
    auto weighted_offset = [&](const std::vector<double>& w) {
        double o = 0.0;
        for (int i = 0; i < n; ++i) o += w[i] * c[i];
        return o;
    };
    struct Path { double value = INF; double F = 0.0; std::vector<double> P; std::vector<int> j, k; };
    const auto dj_moves_ub = moves(g.mdj);
    // Follow the cheapest path through a cost-to-go table and evaluate it robustly.
    auto follow = [&](const std::vector<std::vector<double>>& to_go, auto edge_cost) {
        Path out;
        int k_best = -1;
        for (int k = 0; k < g.K; ++k)
            if (to_go[0][g.node(j0, k)] < (k_best < 0 ? INF : to_go[0][g.node(j0, k_best)])) k_best = k;
        if (k_best < 0) return out;
        int j = j0, k = k_best;
        out.P.assign(n, 0.0);
        out.j.push_back(j);
        out.k.push_back(k);
        for (int s = 0; s < g.S - 1; ++s) {
            int bj = 0, bk = 0;
            double best = INF;
            for (int dj : dj_moves_ub)
                for (int dk : g.level_moves(s)) {
                    if (!g.valid(s, j, dj, k, dk)) continue;
                    double v = edge_cost(s, j, dj, k, dk) + to_go[s + 1][g.node(j + dj, k + dk)];
                    if (v < best) { best = v; bj = dj; bk = dk; }
                }
            double L = g.length(s, j, bj);
            out.F += g.edge_fuel(s, j, bj, k, bk);
            for (int i = 0; i < n; ++i) out.P[i] += L * g.frac(i, s, j, bj, k, bk);
            j += bj;
            k += bk;
            out.j.push_back(j);
            out.k.push_back(k);
        }
        out.value = robust_value(out.F, out.P.data());
        return out;
    };

    // Lower bounds on the cost still to come, per scenario i: the least
    // (fuel + lam * PCR km in scenario i) along any remaining path.
    std::vector<std::vector<std::vector<double>>> joint_lb(n);
    Path ub_path;
    for (int i = 0; i < n; ++i) {
        std::vector<double> w(n, 0.0);
        w[i] = 1.0;
        joint_lb[i] = cost_to_go(g, weighted_cost(w));
        Path p = follow(joint_lb[i], weighted_cost(w));
        if (p.value < ub_path.value) ub_path = p;
    }
    // Lagrangian bound: for weights w on the scenarios (summing to 1), every path
    // has max_i(J_i + c_i) >= sum_i w_i (J_i + c_i), so the least weighted cost
    // L(w) is a lower bound on the answer, and the weighted-optimal path an upper
    // bound. Weights are improved by projected subgradient steps.
    std::vector<double> w(n, 1.0 / n), best_w = w;
    double best_L = -INF;
    std::vector<std::vector<double>> best_to_go;
    for (int t = 1; t <= 60; ++t) {
        auto to_go = cost_to_go(g, weighted_cost(w));
        double start = INF;
        for (int k = 0; k < g.K; ++k) start = std::min(start, to_go[0][g.node(j0, k)]);
        if (!std::isfinite(start)) break;
        const double L = start + weighted_offset(w);
        Path p = follow(to_go, weighted_cost(w));
        if (p.value < ub_path.value) ub_path = p;
        if (L > best_L) { best_L = L; best_w = w; best_to_go = std::move(to_go); }
        if (ub_path.value - best_L <= 1e-9 * std::max(1.0, std::abs(ub_path.value))) break;
        std::vector<double> a(n);
        double lo = INF, hi = -INF, mean_a = 0.0;
        for (int i = 0; i < n; ++i) {
            a[i] = p.F + lam * p.P[i] + c[i];
            lo = std::min(lo, a[i]);
            hi = std::max(hi, a[i]);
            mean_a += w[i] * a[i];
        }
        if (hi - lo <= 0.0) break;
        const double step = 0.5 / std::sqrt(static_cast<double>(t));
        double total = 0.0;
        for (int i = 0; i < n; ++i) {
            w[i] = std::max(0.0, w[i] + step * (a[i] - mean_a) / (hi - lo));
            total += w[i];
        }
        for (int i = 0; i < n; ++i) w[i] /= total;
    }
    if (!std::isfinite(ub_path.value)) throw std::runtime_error("no feasible path");
    const double best_w_offset = weighted_offset(best_w);
    double ub_heuristic = ub_path.value;
    double UB = std::min(upper_bound, ub_heuristic);
    const double tol = 1e-9 * std::max(1.0, std::abs(UB));

    auto result = [&](const std::vector<int>& j_idx, const std::vector<int>& k_idx, double value, double F,
                      const std::vector<double>& P, std::int64_t created, std::int64_t pruned,
                      std::int64_t dominated, std::int64_t max_at_node, bool proved,
                      const std::string& stopped) {
        py::dict out;
        out["stopped"] = stopped;                           // "" = exact; otherwise why the search stopped
        out["gap"] = stopped.empty() ? 0.0 : value - best_L;  // certified: optimum >= objective - gap
        out["j_idx"] = j_idx;
        out["k_idx"] = k_idx;
        out["objective"] = value;
        out["fuel_kg"] = F;
        out["pcr_km"] = P;
        out["upper_bound_start"] = UB;
        out["lower_bound_lagrangian"] = best_L;
        out["proved_by_bounds"] = proved;
        out["labels_created"] = created;
        out["labels_pruned_by_bound"] = pruned;
        out["labels_dominated"] = dominated;
        out["max_labels_at_a_point"] = max_at_node;
        return out;
    };
    // The bounds meet: the upper-bound path is optimal and no search is needed.
    if (ub_heuristic <= upper_bound && ub_heuristic - best_L <= tol)
        return result(ub_path.j, ub_path.k, ub_path.value, ub_path.F, ub_path.P, 0, 0, 0, 0, true, "");

    // Forward label setting, one stage at a time.
    const std::size_t nodes = static_cast<std::size_t>(g.J) * g.K;
    std::vector<Labels> stage(g.S);
    for (auto& L : stage) L.at_node.assign(nodes, {});
    for (int k = 0; k < g.K; ++k) {
        if (!g.flyable(k)) continue;
        auto& L = stage[0];
        L.F.push_back(0.0);
        L.P.insert(L.P.end(), n, 0.0);
        L.parent.push_back(-1);
        L.j.push_back(j0);
        L.k.push_back(k);
        L.alive.push_back(1);
        L.at_node[g.node(j0, k)].push_back(static_cast<int>(L.F.size()) - 1);
    }
    std::int64_t created = static_cast<std::int64_t>(stage[0].F.size()), pruned = 0, dominated = 0;
    std::size_t max_at_node = 1;
    const auto dj_moves = moves(g.mdj);
    std::vector<double> Pn(n);
    std::string stopped;

    for (int s = 0; s < g.S - 1; ++s) {
        const Labels& cur = stage[s];
        Labels& nxt = stage[s + 1];
        const auto dk_moves = g.level_moves(s);
        for (std::size_t nd = 0; nd < nodes; ++nd) {
            for (int a : cur.at_node[nd]) {
                const int j = cur.j[a], k = cur.k[a];
                for (int dj : dj_moves)
                    for (int dk : dk_moves) {
                        if (!g.valid(s, j, dj, k, dk)) continue;
                        const int jt = j + dj, kt = k + dk;
                        const std::size_t tn = g.node(jt, kt);
                        if (!std::isfinite(best_to_go[s + 1][tn])) continue;
                        const double L = g.length(s, j, dj);
                        const double Fn = cur.F[a] + g.edge_fuel(s, j, dj, k, dk);
                        double lb = -INF, weighted = 0.0;
                        for (int i = 0; i < n; ++i) {
                            Pn[i] = cur.P[static_cast<std::size_t>(a) * n + i] + L * g.frac(i, s, j, dj, k, dk);
                            lb = std::max(lb, Fn + lam * Pn[i] + c[i] + joint_lb[i][s + 1][tn]);
                            weighted += best_w[i] * Pn[i];
                        }
                        lb = std::max(lb, Fn + lam * weighted + best_w_offset + best_to_go[s + 1][tn]);
                        if (lb > UB + tol) { ++pruned; continue; }

                        // dominance against the labels already at the target node
                        bool beaten = false;
                        auto& here = nxt.at_node[tn];
                        for (int b : here) {
                            const double* Pb = &nxt.P[static_cast<std::size_t>(b) * n];
                            bool b_le = nxt.F[b] <= Fn, new_le = Fn <= nxt.F[b];
                            for (int i = 0; i < n; ++i) {
                                b_le = b_le && Pb[i] <= Pn[i];
                                new_le = new_le && Pn[i] <= Pb[i];
                            }
                            if (b_le) { beaten = true; break; }        // existing equal or better: keep it
                            if (new_le) { nxt.alive[b] = 0; ++dominated; }  // new strictly better
                        }
                        if (beaten) { ++dominated; continue; }
                        here.erase(std::remove_if(here.begin(), here.end(), [&](int b) { return !nxt.alive[b]; }),
                                   here.end());
                        nxt.F.push_back(Fn);
                        nxt.P.insert(nxt.P.end(), Pn.begin(), Pn.end());
                        nxt.parent.push_back(a);
                        nxt.j.push_back(jt);
                        nxt.k.push_back(kt);
                        nxt.alive.push_back(1);
                        here.push_back(static_cast<int>(nxt.F.size()) - 1);
                        max_at_node = std::max(max_at_node, here.size());
                        if (++created > max_labels) { stopped = "label limit"; goto finished; }
                        if ((created & 1023) == 0 &&
                            std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count() > max_seconds) {
                            stopped = "time limit";
                            goto finished;
                        }
                    }
            }
        }
    }

finished:
    // Search stopped by a limit: return the best path found by the bounds, with its certified gap.
    if (!stopped.empty())
        return result(ub_path.j, ub_path.k, ub_path.value, ub_path.F, ub_path.P, created, pruned, dominated,
                      static_cast<std::int64_t>(max_at_node), false, stopped);

    // Best complete path.
    const Labels& last = stage[g.S - 1];
    int best = -1;
    double best_val = INF;
    for (int k = 0; k < g.K; ++k)
        for (int a : last.at_node[g.node(j0, k)]) {
            double v = robust_value(last.F[a], &last.P[static_cast<std::size_t>(a) * n]);
            if (v < best_val) { best_val = v; best = a; }
        }
    if (best < 0) throw std::runtime_error("no path found (upper bound too low?)");

    std::vector<int> j_idx(g.S), k_idx(g.S);
    for (int s = g.S - 1, a = best; s >= 0; --s) {
        j_idx[s] = stage[s].j[a];
        k_idx[s] = stage[s].k[a];
        a = stage[s].parent[a];
    }
    std::vector<double> pcr_km(last.P.begin() + static_cast<std::ptrdiff_t>(best) * n,
                               last.P.begin() + static_cast<std::ptrdiff_t>(best + 1) * n);
    return result(j_idx, k_idx, best_val, last.F[best], pcr_km, created, pruned, dominated,
                  static_cast<std::int64_t>(max_at_node), false, "");
}

PYBIND11_MODULE(contrail_core, m) {
    m.doc() = "Exact robust route search (C++ core of contrail_routing.robust)";
    m.def("robust_plan", &robust_plan, py::arg("edge_km"), py::arg("pcr_frac"), py::arg("fuel_per_km"),
          py::arg("climb_kg"), py::arg("lam"), py::arg("scenario_offset"), py::arg("j0"),
          py::arg("max_dk_used"), py::arg("upper_bound") = INF, py::arg("max_labels") = 10000000,
          py::arg("max_seconds") = INF,
          "Minimise fuel + max_i(lam * pcr_km_i + scenario_offset_i) over all grid paths.");
}