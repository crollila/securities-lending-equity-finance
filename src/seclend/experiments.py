"""Experiments behind every number in README.md and ANALYSIS.md.

1. Scenario runs     - base and short-squeeze stress, every allocation method,
                       identical market paths and behavioural draws.
2. Decision study    - the exact allocation problems the desk faced each day,
                       re-solved by every method from the *same* state, which
                       isolates the quality of the allocation decision itself.
3. Seed robustness   - the dynamic comparison repeated over many market paths,
                       reported as paired differences with confidence intervals.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import allocation as al
from .config import Params
from .economics import fee_curve_bps, tier_from_fee
from .market import build_paths
from .simulation import METHOD_LABELS, METHODS, run
from .universe import build_borrowers, build_universe

MAIN_METHODS = ("fcfs", "greedy", "pro_rata", "lp_revenue", "optimized")


def scenario_runs(p: Params, universe, borrowers, methods=METHODS, log=print):
    results, paths_by = {}, {}
    for scen in ("base", "stress"):
        paths = build_paths(p, universe, borrowers, scen)
        paths_by[scen] = paths
        for meth in methods:
            t0 = time.time()
            results[(scen, meth)] = run(p, universe, borrowers, paths, meth, record_problems=(meth == "optimized"))
            log(f"  {scen:6s} {meth:14s} {time.time() - t0:5.1f}s")
    return results, paths_by


def summary_table(results) -> pd.DataFrame:
    rows = []
    for (scen, meth), r in results.items():
        rows.append({"scenario": scen, "method": meth, "method_label": METHOD_LABELS[meth], **r.summary})
    df = pd.DataFrame(rows)
    for scen in df["scenario"].unique():
        m = df["scenario"] == scen
        for ref in ("fcfs", "greedy"):
            base = df.loc[m & (df["method"] == ref), "net_revenue"].iloc[0]
            df.loc[m, f"net_vs_{ref}_pct"] = 100 * (df.loc[m, "net_revenue"] / base - 1)
    return df


def decision_study(p: Params, problems) -> pd.DataFrame:
    """Re-solve each recorded daily problem with every method from the same state."""
    ra, tilt = p.alloc.recall_risk_aversion, p.alloc.relationship_tilt_bps
    rows = []
    for day, prob, arrival in problems:
        if prob.n == 0:
            continue
        xs = {
            "fcfs": al.fcfs(prob, arrival),
            "greedy": al.greedy(prob),
            "pro_rata": al.pro_rata(prob),
            "lp_revenue": al.optimize(prob, risk_aversion=0.0, tilt_bps=0.0, use_conversion=False)[0],
            "optimized": al.optimize(prob, risk_aversion=ra, tilt_bps=tilt)[0],
        }
        for meth, x in xs.items():
            assert not al.violations(prob, x), (day, meth)
            rows.append({
                "day": day, "method": meth, "n_requests": prob.n,
                "gross_revenue_per_day": al.revenue(prob, x),
                "expected_revenue_per_day": al.expected_revenue(prob, x),
                "risk_adj_revenue_per_day": al.risk_adjusted_revenue(prob, x, ra, 0.0),
                "objective": al.risk_adjusted_revenue(prob, x, ra, tilt),
                "allocated_value": float(np.sum(x * prob.price)),
                "expected_recall_cost_per_day": float(np.sum(x * prob.price * prob.penalty_bps * prob.conversion) * al.DAILY),
            })
    return pd.DataFrame(rows)


def decision_summary(dec: pd.DataFrame) -> pd.DataFrame:
    g = dec.groupby("method")[["gross_revenue_per_day", "expected_revenue_per_day", "risk_adj_revenue_per_day", "objective",
                               "allocated_value", "expected_recall_cost_per_day"]].sum()
    piv = dec.pivot_table(index="day", columns="method", values="objective")
    for ref in ("fcfs", "greedy", "pro_rata"):
        g.loc[:, f"objective_vs_{ref}_pct"] = 100 * (g["objective"] / g.loc[ref, "objective"] - 1)
    # The LP solution is floored to whole shares, which can cost up to one share per
    # request; within 0.01% of the objective counts as "not worse".
    tol = 1e-4 * piv["optimized"].abs()
    g["days_lp_objective_ge"] = [(piv["optimized"] >= piv[mth] - tol).mean() for mth in g.index]
    g["worst_day_lp_shortfall_pct"] = [100 * ((piv[mth] - piv["optimized"]) / piv["optimized"].abs()).max()
                                       for mth in g.index]
    return g.reindex([m for m in MAIN_METHODS if m in g.index])


def seed_robustness(p: Params, universe, borrowers, seeds, methods=("fcfs", "greedy", "pro_rata", "optimized"), log=print):
    rows = []
    for seed in seeds:
        q = replace(p, path_seed=int(seed))
        for scen in ("base", "stress"):
            paths = build_paths(q, universe, borrowers, scen)
            for meth in methods:
                s = run(q, universe, borrowers, paths, meth).summary
                rows.append({"seed": seed, "scenario": scen, "method": meth, **s})
        log(f"  seed {seed} done")
    return pd.DataFrame(rows)


def paired_stats(rob: pd.DataFrame, metric="net_revenue", target="optimized") -> pd.DataFrame:
    out = []
    for scen in rob["scenario"].unique():
        d = rob[rob["scenario"] == scen].pivot(index="seed", columns="method", values=metric)
        for ref in [c for c in d.columns if c != target]:
            diff = d[target] - d[ref]
            pct = 100 * diff / d[ref]
            n = len(diff)
            se = pct.std(ddof=1) / np.sqrt(n)
            tcrit = stats.t.ppf(0.975, n - 1)
            out.append({
                "scenario": scen, "metric": metric, "vs": ref, "n_seeds": n,
                "mean_diff": diff.mean(), "mean_pct": pct.mean(),
                "ci95_low_pct": pct.mean() - tcrit * se, "ci95_high_pct": pct.mean() + tcrit * se,
                "win_rate": float((diff > 0).mean()),
            })
    return pd.DataFrame(out)


def limit_sweep(p: Params, scales=(0.25, 0.5, 1.0, 2.0), seeds=range(1, 6),
                methods=("fcfs", "greedy", "pro_rata", "optimized"), log=print) -> pd.DataFrame:
    """How much does the allocation method matter as counterparty limits tighten?"""
    rows = []
    for sc in scales:
        q0 = replace(p, limit_scale=sc)
        uni, bors = build_universe(q0), build_borrowers(q0)
        for seed in seeds:
            q = replace(q0, path_seed=int(seed))
            paths = build_paths(q, uni, bors, "base")
            for meth in methods:
                s = run(q, uni, bors, paths, meth).summary
                rows.append({"limit_scale": sc, "seed": seed, "method": meth, "net_revenue": s["net_revenue"],
                             "revenue": s["revenue"], "locate_fill_rate_value": s["locate_fill_rate_value"]})
        log(f"  limit scale {sc} done")
    return pd.DataFrame(rows)


def risk_sweep(p: Params, universe, borrowers, lambdas=(0.0, 1.0, 3.0, 10.0, 30.0), seeds=range(1, 6), log=print):
    """Stress-scenario trade-off between revenue and realised recall cost as risk aversion rises."""
    rows = []
    for seed in seeds:
        q = replace(p, path_seed=int(seed))
        paths = build_paths(q, universe, borrowers, "stress")
        g = run(q, universe, borrowers, paths, "greedy").summary
        rows.append({"seed": seed, "lambda": np.nan, "method": "greedy", **{k: g[k] for k in (
            "revenue", "recall_cost", "net_revenue", "recall_fails_n", "buyins_n", "fail_charges", "buyin_loss")}})
        for lam in lambdas:
            q2 = replace(q, alloc=replace(q.alloc, recall_risk_aversion=lam))
            s = run(q2, universe, borrowers, paths, "optimized").summary
            rows.append({"seed": seed, "lambda": lam, "method": "optimized", **{k: s[k] for k in (
                "revenue", "recall_cost", "net_revenue", "recall_fails_n", "buyins_n", "fail_charges", "buyin_loss")}})
        log(f"  risk sweep seed {seed} done")
    return pd.DataFrame(rows)


def run_everything(out_dir: Path, fig_dir: Path, p: Params | None = None, n_seeds=20, log=print, make_figures=True):
    p = p or Params()
    out_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    universe = build_universe(p)
    borrowers = build_borrowers(p)
    log(f"universe: {len(universe)} SIMULATED securities, {len(borrowers)} SIMULATED counterparties")

    results, paths_by = scenario_runs(p, universe, borrowers, log=log)
    summ = summary_table(results)

    # ---- tables -------------------------------------------------------------
    u_out = universe.copy()
    u_out["tier0"] = tier_from_fee(u_out["fee0_bps"])
    u_out.round(6).to_csv(out_dir / "universe_simulated.csv", index=False)
    borrowers.round(4).to_csv(out_dir / "borrowers_simulated.csv", index=False)
    summ.round(6).to_csv(out_dir / "summary_by_method.csv", index=False)
    for (scen, meth), r in results.items():
        if meth in ("fcfs", "greedy", "optimized"):
            r.daily.round(4).to_csv(out_dir / f"daily_{scen}_{meth}.csv", index=False)
    ev = results[("stress", "optimized")].events.copy()
    ev["security"] = universe["security"].to_numpy()[ev["sec"]]
    ev["counterparty"] = np.where(ev["bor"] >= 0, borrowers["counterparty"].to_numpy()[ev["bor"].clip(0)], "")
    ev.round(2).to_csv(out_dir / "events_stress_optimized.csv", index=False)
    loc = results[("base", "optimized")].locates
    sample = loc[loc["day"] == loc["day"].max()].copy()
    sample["security"] = universe["security"].to_numpy()[sample["sec"]]
    sample["counterparty"] = borrowers["counterparty"].to_numpy()[sample["bor"]]
    sample.round(2).to_csv(out_dir / "locate_log_sample.csv", index=False)
    paths_by["base"].corporate_actions.to_csv(out_dir / "corporate_actions.csv", index=False)

    # Concentration by counterparty at quarter end.
    conc = []
    for (scen, meth), r in results.items():
        if meth in MAIN_METHODS:
            by = r.positions.groupby("bor")["value"].sum().reindex(range(len(borrowers)), fill_value=0)
            for b, v in by.items():
                conc.append({"scenario": scen, "method": meth, "counterparty": borrowers.at[b, "counterparty"],
                             "type": borrowers.at[b, "type"], "on_loan_value": v,
                             "limit": borrowers.at[b, "exposure_limit"], "limit_usage": v / borrowers.at[b, "exposure_limit"]})
    pd.DataFrame(conc).round(4).to_csv(out_dir / "counterparty_concentration.csv", index=False)

    # Fee curve and tier economics.
    u = np.linspace(0, 0.999, 400)
    pd.DataFrame({"street_utilization": u, "fee_bps": fee_curve_bps(u, p.fee.gc_fee_bps, p.fee.knee, p.fee.scale_bps, p.fee.cap_bps)}
                 ).round(4).to_csv(out_dir / "fee_curve.csv", index=False)

    # ---- decision study ------------------------------------------------------
    dec_rows = []
    for scen in ("base", "stress"):
        d = decision_study(p, results[(scen, "optimized")].problems)
        d.insert(0, "scenario", scen)
        dec_rows.append(d)
    dec = pd.concat(dec_rows, ignore_index=True)
    dec.round(4).to_csv(out_dir / "decision_study_daily.csv", index=False)
    dsum = pd.concat({s: decision_summary(dec[dec["scenario"] == s]) for s in ("base", "stress")}, names=["scenario"])
    dsum.round(4).to_csv(out_dir / "decision_study_summary.csv")
    log("decision study done")

    # ---- seed robustness -----------------------------------------------------
    rob = seed_robustness(p, universe, borrowers, range(1, n_seeds + 1), log=log) if n_seeds else pd.DataFrame()
    if len(rob):
        rob.round(4).to_csv(out_dir / "robustness_by_seed.csv", index=False)
        pst = pd.concat([paired_stats(rob, "net_revenue"), paired_stats(rob, "revenue"),
                         paired_stats(rob, "recall_cost")], ignore_index=True)
        pst.round(4).to_csv(out_dir / "robustness_paired.csv", index=False)

    sweep_seeds = range(1, min(n_seeds, 5) + 1)
    lsw = limit_sweep(p, seeds=sweep_seeds, log=log) if n_seeds else pd.DataFrame()
    rsw = risk_sweep(p, universe, borrowers, seeds=sweep_seeds, log=log) if n_seeds else pd.DataFrame()
    if len(lsw):
        lsw.round(4).to_csv(out_dir / "sweep_counterparty_limits.csv", index=False)
        rsw.round(4).to_csv(out_dir / "sweep_recall_risk_aversion.csv", index=False)

    headline = build_headline(p, universe, borrowers, summ, dsum, rob, paths_by, results)
    if len(lsw):
        piv = lsw.groupby(["limit_scale", "method"])["net_revenue"].mean().unstack()
        h_l = {}
        for sc in piv.index:
            h_l[str(sc)] = {f"optimized_vs_{m}_pct": float(100 * (piv.loc[sc, "optimized"] / piv.loc[sc, m] - 1))
                            for m in piv.columns if m != "optimized"}
        headline["limit_sweep_mean_over_seeds"] = h_l
        rs = rsw.groupby(["method", "lambda"], dropna=False)[["revenue", "recall_cost", "net_revenue", "buyins_n"]].mean()
        headline["risk_sweep_mean_over_seeds"] = {f"{m}|{lam}": v for (m, lam), v in rs.to_dict(orient="index").items()}
    headline["runtime_seconds"] = round(time.time() - t_start, 1)
    (out_dir / "headline.json").write_text(json.dumps(headline, indent=2, default=float))

    if make_figures:
        from . import plots
        plots.make_all(fig_dir, p, universe, borrowers, results, paths_by, summ, dsum, rob, lsw, rsw)
        log(f"figures written to {fig_dir}")
    log(f"done in {time.time() - t_start:.0f}s")
    return headline


def build_headline(p, universe, borrowers, summ, dsum, rob, paths_by, results):
    def row(scen, meth):
        return summ[(summ["scenario"] == scen) & (summ["method"] == meth)].iloc[0].to_dict()

    h = {
        "label": "All counterparties, positions, balances and AUM are SIMULATED.",
        "n_securities": len(universe), "n_borrowers": len(borrowers), "n_days": p.n_days,
        "lendable_value_start": float(universe["lendable_value"].sum()),
        "tiers_start": universe["tier0"].value_counts().to_dict(),
        "squeeze_basket": universe["security"].to_numpy()[paths_by["stress"].squeeze].tolist(),
        "scenarios": {},
    }
    for scen in ("base", "stress"):
        h["scenarios"][scen] = {m: {k: row(scen, m)[k] for k in (
            "revenue", "net_revenue", "recall_cost", "avg_fee_bps", "avg_utilization", "avg_on_loan_value",
            "locate_fill_rate_value", "locate_fill_rate_count", "hhi_avg", "top5_share_avg", "recalls_n",
            "recall_fails_n", "buyins_n", "settlement_fails_n", "calls_n", "calls_value", "max_uncovered_exposure",
            "net_vs_fcfs_pct", "net_vs_greedy_pct")} for m in METHODS}
        h["scenarios"][scen]["decision_study"] = dsum.loc[scen].to_dict(orient="index")
    if len(rob):
        h["robustness"] = paired_stats(rob, "net_revenue").to_dict(orient="records")
    d = results[("stress", "optimized")].daily
    sq = paths_by["stress"]
    s0, pk = sq.burn_in + p.stress.start, sq.burn_in + p.stress.peak
    basket = sq.squeeze
    h["stress_path"] = {
        "basket_price_multiple_at_peak": float(np.mean(sq.price[pk, basket] / sq.price[s0 - 1, basket])),
        "basket_fee_bps_before": float(np.mean(sq.market_fee[s0 - 1, basket])),
        "basket_fee_bps_peak": float(np.mean(sq.market_fee[pk, basket])),
        "basket_street_util_peak": float(np.mean(sq.street_util[pk, basket])),
        "peak_daily_recalls": int(d["recalls_n"].max()),
        "peak_daily_calls_value": float(d["calls_value"].max()),
    }
    return h
