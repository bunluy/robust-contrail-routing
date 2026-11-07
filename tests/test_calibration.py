"""Calibration tools: logistic fit, thresholds, month-wise testing, reliability."""
import numpy as np
import pandas as pd
import pytest

from contrail_routing.calibration import (best_threshold, brier, ets, fit_logistic, leave_one_month_out,
                                          predict_proba, reliability)


def test_logistic_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200_000, 2))
    p = 1 / (1 + np.exp(-(-1.0 + 2.0 * X[:, 0] - 0.5 * X[:, 1])))
    y = rng.random(len(p)) < p
    m = fit_logistic(X, y, l2=0.0)
    # back from standardised to raw coefficients
    raw = m["beta"][1:] / m["scale"]
    intercept = m["beta"][0] - np.sum(raw * m["mean"])
    assert raw == pytest.approx([2.0, -0.5], abs=0.03) and intercept == pytest.approx(-1.0, abs=0.03)
    assert np.allclose(predict_proba(m, X[:5]), p[:5], atol=0.02)


def test_best_threshold_finds_the_separating_value():
    score = np.linspace(0.5, 1.2, 701)
    obs = score > 0.93
    t, e = best_threshold(score, obs)
    assert t == pytest.approx(0.93) and e == pytest.approx(1.0)
    assert ets(obs, obs) == pytest.approx(1.0)


def _months(n_per=2000, shift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(1, 13):
        rhi = rng.uniform(0.3, 1.3, n_per)
        truth = rhi + rng.normal(0, 0.05, n_per) > 0.92 + shift
        rows.append(pd.DataFrame({"month": m, "rhi_base": rhi, "issr_obs": truth.astype(float)}))
    return pd.concat(rows, ignore_index=True)


def test_month_cv_finds_the_threshold_and_never_sees_the_test_month():
    df = _months()
    out, folds = leave_one_month_out(df, ["rhi_base"])
    assert folds["thr_fit"].between(0.90, 0.94).all()
    assert out[["thr_fit", "prob", "pred_thr", "pred_prob"]].notna().all().all()
    # Changing one month's answers must not change that month's predictions
    df2 = df.copy()
    df2.loc[df2["month"] == 5, "issr_obs"] = 1 - df2.loc[df2["month"] == 5, "issr_obs"]
    out2, _ = leave_one_month_out(df2, ["rhi_base"])
    sel = df["month"] == 5
    assert np.allclose(out.loc[sel, "prob"].astype(float), out2.loc[sel, "prob"].astype(float))
    assert (out.loc[sel, "thr_fit"] == out2.loc[sel, "thr_fit"]).all()


def test_reliability_and_brier_on_calibrated_forecasts():
    rng = np.random.default_rng(3)
    p = rng.uniform(0, 1, 200_000)
    y = rng.random(len(p)) < p
    r = reliability(p, y)
    assert np.allclose(r["forecast"], r["observed"], atol=0.02)
    assert brier(p, y) == pytest.approx(1 / 6, abs=0.005)        # E[p(1-p)] for uniform p
    assert brier(y.astype(float), y) == 0.0


def test_paired_difference_is_zero_for_identical_methods_and_positive_for_a_better_one():
    from contrail_routing.calibration import paired_ets_difference
    rng = np.random.default_rng(5)
    rows = []
    for f in range(40):
        obs = rng.random(200) < 0.2
        good = np.where(rng.random(200) < 0.9, obs, ~obs)
        bad = np.where(rng.random(200) < 0.6, obs, ~obs)
        rows.append(pd.DataFrame({"flight_id": f"F{f}", "obs": obs, "good": good, "bad": bad}))
    d = pd.concat(rows, ignore_index=True).astype({"obs": float, "good": float, "bad": float})
    est, lo, hi = paired_ets_difference(d, "obs", "good", "good", n_boot=200)
    assert est == 0.0 and lo == 0.0 and hi == 0.0
    est, lo, hi = paired_ets_difference(d, "obs", "bad", "good", n_boot=200)
    assert lo > 0 and lo <= est <= hi
    assert est == pytest.approx(ets(d["obs"] > 0.5, d["good"] > 0.5) - ets(d["obs"] > 0.5, d["bad"] > 0.5))
