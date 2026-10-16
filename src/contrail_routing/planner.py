"""Route planner: a stage-by-stage search over lateral offset and flight level.

The great circle from origin to destination is divided into n_stages equal
steps. At each stage boundary the aircraft can be at one of a grid of lateral
offsets (km, > 0 = left of travel, as in make_route) and one of a set of flight
levels. Between consecutive stages the offset may change by at most max_dj grid
steps and the flight level by at most max_dk levels. The path starts and ends
at zero offset; its flight level at the start and end is free (cruise only).

Cost of the edge from (s, j, k) to (s+1, j+dj, k+dk), with L its length in km:
    fuel   = fuel_per_km[k] * L + climb_kg_per_level * max(dk, 0)
    pcr_km = L * pcr_frac[s, j, dj, k, dk]
    J      = fuel + lam * pcr_km
pcr_frac is the share of n_sub points spread along the edge (from its start,
altitude changing linearly through a level change) that are in persistent-
contrail (PCR) conditions. Descents cost nothing extra.

Timing: the time at stage s is the nominal great-circle time plus a delay per
stage (delay_s), the same for every offset and level. plan_timed() takes the
delay from the previously planned path and re-plans until the path repeats,
because sideways steps lengthen the path (a 25 km step over a 30 km stage
flies ~39 km) and the weather changes during the delay.

Search: dynamic programming over stages. Ties are broken deterministically:
moves are tried smallest first (dj = 0, -1, +1, ...; then dk the same way),
and a later move replaces an earlier one only if strictly cheaper, so equal-cost
paths stay straight and level. The flight level is held on the final segment:
a change there would cost nothing in this model, but the full fuel model would
credit a final descent.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from pycontrails import GeoVectorDataset
from pycontrails.models.pcr import PCR

from .geometry import displaced_points, flight_level_to_m, haversine_km

G = 9.80665                  # m/s^2
LHV_J_PER_KG = 43.13e6       # lower heating value of Jet A-1
CLIMB_EFFICIENCY = 0.3       # assumed overall efficiency of turning fuel into height


def climb_kg_per_level(mass_kg, level_step_ft=1000.0):
    """Fuel to raise mass_kg by one flight-level step: m g dh / (efficiency * LHV)."""
    return mass_kg * G * level_step_ft * 0.3048 / (CLIMB_EFFICIENCY * LHV_J_PER_KG)


def pcr_at_points(met, lon, lat, altitude_m, time):
    """PCR (0/1) from the pycontrails PCR model at arbitrary points (arrays of one
    shape, broadcast together). No humidity scaling. Raises on missing values."""
    lon, lat, alt, t = np.broadcast_arrays(np.asarray(lon, float), np.asarray(lat, float),
                                           np.asarray(altitude_m, float), np.asarray(time))
    pts = GeoVectorDataset(longitude=lon.ravel(), latitude=lat.ravel(),
                           altitude=alt.ravel(), time=t.ravel())
    out = PCR(met=met, humidity_scaling=None).eval(source=pts)
    pcr = np.asarray(out["pcr"], dtype=float).reshape(lon.shape)
    if np.isnan(pcr).any():
        raise ValueError(f"{int(np.isnan(pcr).sum())} points outside the met data")
    return pcr


def stage_times(departure, gc_km, n_stages, speed_ms, delay_s=None):
    """Time at each stage boundary: great-circle progress at constant speed, plus a delay per stage."""
    t_s = np.linspace(0.0, 1.0, n_stages + 1) * gc_km * 1000.0 / speed_ms
    if delay_s is not None:
        t_s = t_s + np.asarray(delay_s, dtype=float)
    return (pd.Timestamp(departure) + pd.to_timedelta(t_s, unit="s")).to_numpy()


@dataclass
class CostModel:
    """Everything the search needs. Shapes: S stage boundaries, J offsets, K levels."""
    lon: np.ndarray            # (S, J) degrees
    lat: np.ndarray            # (S, J)
    offsets_km: np.ndarray     # (J,)
    flight_levels: np.ndarray  # (K,)
    times: np.ndarray          # (S,) datetime64
    edge_km: np.ndarray        # (S-1, J, 2*max_dj+1): length from (s, j) to (s+1, j+dj); inf if off-grid
    pcr_frac: np.ndarray       # (S-1, J, 2*max_dj+1, K, 2*max_dk+1): share of the edge in PCR
    fuel_per_km: np.ndarray    # (K,) kg/km; inf where the level is not flyable
    climb_kg: float            # fuel per flight-level step climbed
    max_dj: int = 1
    max_dk: int = 1

    @property
    def shape(self):
        return self.lon.shape[0], self.lon.shape[1], len(self.flight_levels)

    @property
    def j0(self):
        return int(np.flatnonzero(self.offsets_km == 0)[0])


def edge_pcr_fraction(met, origin, destination, offsets_km, flight_levels, times, max_dj, max_dk, n_sub=3):
    """Share of n_sub points along every edge that are in PCR, shape (S-1, J, 2*max_dj+1, K, 2*max_dk+1).
    Edges leaving the grid get 0 (they are never used: their length is inf)."""
    S, J, K = len(times), len(offsets_km), len(flight_levels)
    step = float(offsets_km[1] - offsets_km[0]) if J > 1 else 0.0
    alt = flight_level_to_m(np.asarray(flight_levels, dtype=float))
    t_rel = (times - times[0]).astype("timedelta64[ns]").astype(float)
    u = np.arange(n_sub) / n_sub
    dj = np.arange(-max_dj, max_dj + 1)
    dk = np.arange(-max_dk, max_dk + 1)
    # broadcast shapes: (S-1, J, DJ, K, DK, n_sub)
    s_ = np.arange(S - 1)[:, None, None, None, None, None]
    j_ = np.arange(J)[None, :, None, None, None, None]
    dj_ = dj[None, None, :, None, None, None]
    k_ = np.arange(K)[None, None, None, :, None, None]
    dk_ = dk[None, None, None, None, :, None]
    u_ = u[None, None, None, None, None, :]
    frac = (s_ + u_) / (S - 1)
    offset = offsets_km[j_] + u_ * dj_ * step
    k_end = np.clip(k_ + dk_, 0, K - 1)
    altitude = alt[k_] + u_ * (alt[k_end] - alt[k_])
    t_ns = t_rel[s_] + u_ * (t_rel[np.minimum(s_ + 1, S - 1)] - t_rel[s_])
    shape = np.broadcast_shapes(frac.shape, offset.shape, altitude.shape, t_ns.shape)
    frac, offset, altitude, t_ns = (np.broadcast_to(a, shape) for a in (frac, offset, altitude, t_ns))
    lon, lat = displaced_points(origin, destination, frac, offset)
    time = times[0] + t_ns.astype("timedelta64[ns]")
    return pcr_at_points(met, lon, lat, altitude, time).mean(axis=-1)


def build_cost_model(met, origin, destination, departure, speed_ms, n_stages,
                     offsets_km, flight_levels, fuel_per_km, climb_kg,
                     max_dj=1, max_dk=1, delay_s=None, n_sub=3):
    offsets_km = np.asarray(offsets_km, dtype=float)
    flight_levels = np.asarray(flight_levels)
    if not np.any(offsets_km == 0):
        raise ValueError("offsets_km must include 0 (the endpoints)")
    frac = np.linspace(0.0, 1.0, n_stages + 1)
    lon, lat = displaced_points(origin, destination, frac[:, None], offsets_km[None, :])
    gc_km = float(haversine_km(*origin, *destination))
    times = stage_times(departure, gc_km, n_stages, speed_ms, delay_s)

    J = len(offsets_km)
    edge = np.full((n_stages, J, 2 * max_dj + 1), np.inf)
    for i, dj in enumerate(range(-max_dj, max_dj + 1)):
        js = np.arange(max(0, -dj), min(J, J - dj))
        edge[:, js, i] = haversine_km(lon[:-1, js], lat[:-1, js], lon[1:, js + dj], lat[1:, js + dj])
    pcr_frac = edge_pcr_fraction(met, origin, destination, offsets_km, flight_levels, times,
                                 max_dj, max_dk, n_sub)
    return CostModel(lon, lat, offsets_km, flight_levels, times, edge, pcr_frac,
                     np.asarray(fuel_per_km, dtype=float), float(climb_kg), max_dj, max_dk)


def path_costs(cm, j_idx, k_idx):
    """Fuel, PCR km and length of a path given its offset and level index per stage."""
    j_idx, k_idx = np.asarray(j_idx), np.asarray(k_idx)
    dj, dk = np.diff(j_idx), np.diff(k_idx)
    if np.abs(dj).max(initial=0) > cm.max_dj or np.abs(dk).max(initial=0) > cm.max_dk:
        raise ValueError("path makes a move larger than the model allows")
    s = np.arange(len(j_idx) - 1)
    L = cm.edge_km[s, j_idx[:-1], dj + cm.max_dj]
    frac = cm.pcr_frac[s, j_idx[:-1], dj + cm.max_dj, k_idx[:-1], dk + cm.max_dk]
    fuel = float(np.sum(cm.fuel_per_km[k_idx[:-1]] * L) + cm.climb_kg * np.clip(dk, 0, None).sum())
    return {"fuel_kg": fuel, "pcr_km": float(np.sum(L * frac)), "length_km": float(L.sum()),
            "level_changes": int(np.count_nonzero(dk))}


def _moves(m):
    """Moves in tie-break order: 0, -1, +1, -2, +2, ..."""
    return [0] + [d for a in range(1, m + 1) for d in (-a, a)]


def plan(cm, lam, max_dk=None):
    """
    Minimum-J path from (0, j0) to (S-1, j0). max_dk=0 holds the flight level
    constant (still chosen freely). Returns a dict with the path and its costs.
    """
    max_dk = cm.max_dk if max_dk is None else max_dk
    if max_dk > cm.max_dk:
        raise ValueError("max_dk larger than the cost model was built for")
    S, J, K = cm.shape
    flyable = np.isfinite(cm.fuel_per_km)
    cost = np.full((J, K), np.inf)
    cost[cm.j0, flyable] = 0.0
    prev_j = np.zeros((S, J, K), dtype=np.int64)
    prev_k = np.zeros((S, J, K), dtype=np.int64)
    for s in range(S - 1):
        new = np.full((J, K), np.inf)
        for dj in _moves(cm.max_dj):
            L = cm.edge_km[s, :, dj + cm.max_dj][:, None]                           # (J, 1)
            js = np.arange(max(0, -dj), min(J, J - dj))
            for dk in (_moves(max_dk) if s < S - 2 else [0]):    # level held on the final segment
                with np.errstate(invalid="ignore"):
                    leave = cost + L * (cm.fuel_per_km[None, :]
                                        + lam * cm.pcr_frac[s, :, dj + cm.max_dj, :, dk + cm.max_dk])
                ks = np.arange(max(0, -dk), min(K, K - dk))
                cand = leave[np.ix_(js, ks)] + cm.climb_kg * max(dk, 0)
                tj, tk = np.ix_(js + dj, ks + dk)
                better = cand < new[tj, tk]
                new[tj, tk] = np.where(better, cand, new[tj, tk])
                prev_j[s + 1][tj, tk] = np.where(better, np.broadcast_to(js[:, None], better.shape), prev_j[s + 1][tj, tk])
                prev_k[s + 1][tj, tk] = np.where(better, np.broadcast_to(ks[None, :], better.shape), prev_k[s + 1][tj, tk])
        new[:, ~flyable] = np.inf
        cost = new
    k_end = int(np.argmin(cost[cm.j0]))
    if not np.isfinite(cost[cm.j0, k_end]):
        raise ValueError("no feasible path")
    j_idx, k_idx = [cm.j0], [k_end]
    for s in range(S - 1, 0, -1):
        j, k = j_idx[-1], k_idx[-1]
        j_idx.append(int(prev_j[s, j, k]))
        k_idx.append(int(prev_k[s, j, k]))
    j_idx, k_idx = np.array(j_idx[::-1]), np.array(k_idx[::-1])
    out = path_costs(cm, j_idx, k_idx)
    out.update({"J": out["fuel_kg"] + lam * out["pcr_km"], "J_search": float(cost[cm.j0, k_end]),
                "j_idx": j_idx, "k_idx": k_idx,
                "offset_km": cm.offsets_km[j_idx], "flight_level": cm.flight_levels[k_idx],
                "lon": cm.lon[np.arange(S), j_idx], "lat": cm.lat[np.arange(S), j_idx]})
    return out


def path_delay_s(cm, j_idx, speed_ms):
    """Delay at each stage boundary of a path, relative to great-circle progress."""
    j_idx = np.asarray(j_idx)
    S = len(j_idx)
    L = cm.edge_km[np.arange(S - 1), j_idx[:-1], np.diff(j_idx) + cm.max_dj]
    cum = np.concatenate([[0.0], np.cumsum(L)])
    gc_cum = np.linspace(0.0, 1.0, S) * cm.edge_km[:, cm.j0, cm.max_dj].sum()
    return (cum - gc_cum) * 1000.0 / speed_ms


def plan_timed(rebuild, cm, lam, speed_ms, max_dk=None, max_iter=5):
    """
    Plan, then re-evaluate the contrail field with the planned path's delay and
    re-plan, until the path repeats (converged) or max_iter plans were made.
    rebuild(delay_s) must return a CostModel for those delays (same grid).
    Returns the final plan with 'iterations' and 'converged' added.
    """
    p = plan(cm, lam, max_dk)
    for it in range(2, max_iter + 1):
        cm = rebuild(path_delay_s(cm, p["j_idx"], speed_ms))
        q = plan(cm, lam, max_dk)
        if np.array_equal(q["j_idx"], p["j_idx"]) and np.array_equal(q["k_idx"], p["k_idx"]):
            q.update(iterations=it, converged=True)
            return q
        p = q
    p.update(iterations=max_iter, converged=False)
    return p


def fixed_route_estimate(met, origin, destination, departure, speed_ms, n_stages,
                         amplitude_km, flight_level, fuel_per_km_at_level, n_sub=3):
    """The planner's cost model applied to a fixed route of the Session 3-8
    family (offset amplitude * sin(pi * s), constant level): the same stage
    boundaries, n_sub points per stage, timed along the route's own length."""
    frac_nodes = np.linspace(0.0, 1.0, n_stages + 1)
    lon_n, lat_n = displaced_points(origin, destination, frac_nodes, amplitude_km * np.sin(np.pi * frac_nodes))
    L = haversine_km(lon_n[:-1], lat_n[:-1], lon_n[1:], lat_n[1:])
    cum = np.concatenate([[0.0], np.cumsum(L)])
    frac = (np.arange(n_stages)[:, None] + np.arange(n_sub)[None, :] / n_sub) / n_stages
    lon, lat = displaced_points(origin, destination, frac, amplitude_km * np.sin(np.pi * frac))
    d_km = cum[:-1, None] + (np.arange(n_sub)[None, :] / n_sub) * L[:, None]
    t = (pd.Timestamp(departure) + pd.to_timedelta(d_km.ravel() * 1000.0 / speed_ms, unit="s")).to_numpy().reshape(d_km.shape)
    pcr = pcr_at_points(met, lon, lat, flight_level_to_m(float(flight_level)), t)
    return {"fuel_kg": float(fuel_per_km_at_level * L.sum()), "pcr_km": float(np.sum(L * pcr.mean(axis=1))),
            "length_km": float(L.sum())}