"""Correcting ERA5's ice supersaturation against IAGOS measurements (Session 13).

Four ways of saying "ice-supersaturated" from ERA5 at a point are compared:
plain ERA5 (RHi > 1), a fixed published threshold, a threshold fitted to the
measurements, and a calibrated probability from a small logistic model. Every
fitted method is tested on months it was not fitted to (leave-one-month-out),
so the scores are what to expect on new data, not on the data used to fit.

Only numpy is used, so the method is fully visible and testable.
"""
import numpy as np
import pandas as pd

from .iagos import _scores_from_counts

THRESHOLD_GRID = np.round(np.arange(0.80, 1.0001, 0.01), 2)


def ets(obs, pred):
    """Equitable threat score of two yes/no arrays."""
    obs, pred = np.asarray(obs, bool), np.asarray(pred, bool)
    a, b, c = np.sum(obs & pred), np.sum(~obs & pred), np.sum(obs & ~pred)
    return float(_scores_from_counts(a, b, c, obs.size)["ets"])


def best_threshold(score, obs, grid=THRESHOLD_GRID):
    """The value t in grid for which 'score > t' has the highest ETS (ties: the larger t)."""
    score, obs = np.asarray(score, float), np.asarray(obs, bool)
    vals = [ets(obs, score > t) for t in grid]
    best = max(range(len(grid)), key=lambda i: (np.nan_to_num(vals[i], nan=-1.0), grid[i]))
    return float(grid[best]), float(vals[best])


def fit_logistic(X, y, l2=1e-4, max_iter=100, tol=1e-10):
    """
    Logistic regression by iteratively reweighted least squares (Newton's
    method), on standardised features with a small ridge penalty for
    stability. Returns a dict usable by predict_proba.
    """
    X, y = np.asarray(X, float), np.asarray(y, float)
    mean, scale = X.mean(axis=0), X.std(axis=0)
    scale[scale == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mean) / scale])
    beta = np.zeros(Z.shape[1])
    penalty = l2 * len(X) * np.eye(Z.shape[1])
    penalty[0, 0] = 0.0                       # no penalty on the intercept
    for _ in range(max_iter):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ beta, -35, 35)))
        w = p * (1 - p)
        grad = Z.T @ (y - p) - penalty @ beta
        hess = (Z * w[:, None]).T @ Z + penalty
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < tol:
            break
    return {"mean": mean, "scale": scale, "beta": beta}


def predict_proba(model, X):
    Z = (np.asarray(X, float) - model["mean"]) / model["scale"]
    return 1.0 / (1.0 + np.exp(-np.clip(model["beta"][0] + Z @ model["beta"][1:], -35, 35)))


def leave_one_month_out(df, features, obs_col="issr_obs", score_col="rhi_base", month_col="month"):
    """
    For each month, fit on the other months and predict that month:
    - thr_fit: the RHi threshold with the best ETS on the training months;
      pred_thr = score > thr_fit;
    - prob: probability from a logistic model on features; prob_cut: the
      probability cut with the best ETS on the training months (fitted
      in-sample there); pred_prob = prob > prob_cut.
    Returns a DataFrame aligned with df, and one row per month of fitted values.
    """
    out = pd.DataFrame(index=df.index, columns=["thr_fit", "pred_thr", "prob", "prob_cut", "pred_prob"],
                       dtype=float)
    folds = []
    y_all = df[obs_col].to_numpy() > 0.5
    X_all = df[features].to_numpy(float)
    s_all = df[score_col].to_numpy(float)
    months = df[month_col].to_numpy()
    for m in sorted(np.unique(months)):
        test, train = months == m, months != m
        thr, thr_ets = best_threshold(s_all[train], y_all[train])
        model = fit_logistic(X_all[train], y_all[train])
        p_train = predict_proba(model, X_all[train])
        cut, cut_ets = best_threshold(p_train, y_all[train], grid=np.round(np.arange(0.05, 0.951, 0.01), 2))
        p_test = predict_proba(model, X_all[test])
        idx = df.index[test]
        out.loc[idx, "thr_fit"] = thr
        out.loc[idx, "pred_thr"] = (s_all[test] > thr).astype(float)
        out.loc[idx, "prob"] = p_test
        out.loc[idx, "prob_cut"] = cut
        out.loc[idx, "pred_prob"] = (p_test > cut).astype(float)
        folds.append({"held_out_month": m, "test_points": int(test.sum()), "thr_fit": thr,
                      "train_ets_thr": thr_ets, "prob_cut": cut, "train_ets_prob": cut_ets})
    return out, pd.DataFrame(folds)


def brier(prob, obs):
    """Mean squared error of a probability against 0/1 outcomes (0 = perfect)."""
    return float(np.mean((np.asarray(prob, float) - np.asarray(obs, float)) ** 2))


def reliability(prob, obs, bins=np.linspace(0, 1, 11)):
    """Per probability bin: number of points, mean forecast probability, observed frequency."""
    d = pd.DataFrame({"prob": np.asarray(prob, float), "obs": np.asarray(obs, float)})
    d["bin"] = pd.cut(d["prob"], bins, include_lowest=True)
    return d.groupby("bin", observed=True).agg(points=("obs", "size"), forecast=("prob", "mean"),
                                               observed=("obs", "mean"))


def paired_ets_difference(df, obs_col, a_col, b_col, unit="flight_id", n_boot=1000, seed=0):
    """
    ETS of method b minus ETS of method a on the same points, with a 95%
    interval from resampling whole flights (both methods resampled together,
    so their shared variation cancels). Returns (difference, low, high).
    """
    keep = df[list(dict.fromkeys([obs_col, a_col, b_col]))].notna().all(axis=1)
    d = df.loc[keep]
    o = d[obs_col].to_numpy() > 0.5
    cols = {}
    for name, c in (("a", a_col), ("b", b_col)):
        m = d[c].to_numpy() > 0.5
        cols.update({f"{name}_a": o & m, f"{name}_b": ~o & m, f"{name}_c": o & ~m})
    g = pd.DataFrame({unit: d[unit].to_numpy(), **cols, "n": 1}).groupby(unit).sum()

    def diff(counts):
        sa = _scores_from_counts(counts["a_a"], counts["a_b"], counts["a_c"], counts["n"])["ets"]
        sb = _scores_from_counts(counts["b_a"], counts["b_b"], counts["b_c"], counts["n"])["ets"]
        return sb - sa

    est = float(diff(g.sum()))
    arr = g.to_numpy(float)
    idx = np.random.default_rng(seed).integers(0, len(g), size=(n_boot, len(g)))
    tot = arr[idx].sum(axis=1)
    boot = diff({c: tot[:, i] for i, c in enumerate(g.columns)})
    return est, float(np.nanpercentile(boot, 2.5)), float(np.nanpercentile(boot, 97.5))
