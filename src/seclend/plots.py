"""Figures for the README and ANALYSIS. Every plotted number comes from a run.

Style: one light surface, recessive grid, thin marks, direct labels where they
help, and a fixed categorical order (palette validated for colour-vision
deficiency; see README "Charts").
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from . import allocation as al  # noqa: E402
from .economics import cash_collateral_flows, fee_curve_bps, mark_to_market, tier_from_fee  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BAND = "#f0efec"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CRITICAL = "#d03b3b"

METHOD_COLOR = {"optimized": SERIES[0], "greedy": SERIES[1], "pro_rata": SERIES[2], "fcfs": SERIES[3],
                "lp_revenue": SERIES[4], "optimized_min": SERIES[6]}
METHOD_NAME = {"fcfs": "First come, first served", "greedy": "Greedy (highest bid)", "pro_rata": "Pro-rata",
               "lp_revenue": "LP (gross revenue)", "optimized": "LP (risk-adjusted)",
               "optimized_min": "LP + relationship minimums"}
TYPE_ORDER = ["Multi-strategy", "Fundamental L/S", "Quant", "Market maker", "Event-driven", "Activist short"]
TYPE_COLOR = dict(zip(TYPE_ORDER, SERIES[:6]))
TIER_ORDER = ["GC", "Warm", "Special", "HTB"]
TIER_COLOR = {"GC": "#86b6ef", "Warm": "#3987e5", "Special": "#1c5cab", "HTB": "#0d366b"}  # sequential blue


def _style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "axes.labelsize": 10, "legend.frameon": False, "legend.fontsize": 9,
        "lines.linewidth": 2.0, "axes.axisbelow": True, "text.parse_math": False,
    })


def _save(fig, path: Path, note="SIMULATED book - not real broker inventory or counterparties"):
    fig.text(0.995, 0.005, note, ha="right", va="bottom", fontsize=7, color=MUTED)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def _money(x, pos=None):
    if x < 0:
        return "-" + _money(-x)
    a = abs(x)
    if a >= 1e9:
        return f"${x / 1e9:.1f}bn"
    if a >= 1e6:
        return f"${x / 1e6:.0f}m" if a >= 1e7 else f"${x / 1e6:.1f}m"
    if a >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:.0f}"


# ============================================================ explainers
def explainer_how_it_works(path):
    fig, ax = plt.subplots(figsize=(11, 3.4))
    ax.set_xlim(0, 11)
    ax.set_ylim(0.2, 3.4)
    ax.axis("off")
    boxes = {
        "owner": (0.2, 1.6, "Beneficial owner", "pension fund, ETF,\ninsurer - owns the shares"),
        "agent": (3.1, 1.6, "Agent lender", "custodian lending desk\n(this simulator)"),
        "borrower": (6.0, 1.6, "Borrower", "hedge fund / dealer\nvia prime broker"),
        "market": (8.9, 1.6, "Market buyer", "buys the shares the\nborrower sold short"),
    }
    for k, (x, y, title, sub) in boxes.items():
        face = "#e6f0fc" if k == "agent" else "#ffffff"
        ax.add_patch(FancyBboxPatch((x, y), 1.9, 1.4, boxstyle="round,pad=0.04,rounding_size=0.12",
                                    linewidth=1.2, edgecolor=SERIES[0] if k == "agent" else AXIS, facecolor=face))
        ax.text(x + 0.95, y + 1.0, title, ha="center", va="center", fontsize=11, fontweight="bold", color=INK)
        ax.text(x + 0.95, y + 0.45, sub, ha="center", va="center", fontsize=8.2, color=INK2)

    def arrow(x0, y0, x1, y1, color, label, dy):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=14, linewidth=1.8, color=color))
        ax.text((x0 + x1) / 2, y0 + dy, label, ha="center", va="center", fontsize=8.5, color=INK)

    arrow(2.15, 2.75, 3.05, 2.75, SERIES[0], "lends shares", 0.25)
    arrow(5.05, 2.75, 5.95, 2.75, SERIES[0], "shares", 0.25)
    arrow(7.95, 2.75, 8.85, 2.75, SERIES[0], "sells short", 0.25)
    arrow(5.95, 1.85, 5.05, 1.85, SERIES[1], "collateral\n102-105%", -0.4)
    arrow(3.05, 1.85, 2.15, 1.85, SERIES[2], "most of\nthe revenue", -0.4)
    ax.text(5.5, 0.55, "Daily: shares are re-priced and collateral is topped up or returned (mark-to-market). "
            "The owner can recall the shares at any time;\nthe borrower can return them at any time. "
            "The borrower pays a fee - or, with cash collateral, receives a reduced interest 'rebate'.",
            ha="center", va="center", fontsize=8.8, color=INK2)
    ax.set_title("Who does what in a securities loan", pad=4)
    _save(fig, path)


def explainer_cash_collateral(path, p):
    """Worked example: $10m loan, one year, GC vs hard-to-borrow."""
    loan, days = 10_000_000, 360
    coll = loan * p.coll.margin_gc
    bench, spr = p.econ.benchmark_rate, p.econ.reinvest_spread
    cases = [("General collateral\nfee 25 bps", 25.0), ("Hard to borrow\nfee 800 bps", 800.0)]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    axes[0].set_ylim(-470_000, 1_000_000)
    for ax, (title, fee) in zip(axes, cases):
        f = cash_collateral_flows(coll, fee, bench, spr, days)
        vals = [f["reinvestment_income"], -f["rebate_paid"], f["lender_net"]]
        labels = ["Lender reinvests\ncash collateral", "Rebate leg\n(lender's view)", "Lender keeps"]
        colors = [SERIES[0], SERIES[1], SERIES[2]]
        bars = ax.bar(labels, vals, color=colors, width=0.62, edgecolor=SURFACE, linewidth=2)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + (12_000 if v >= 0 else -12_000), _money(v),
                    ha="center", va="bottom" if v >= 0 else "top", fontsize=10, color=INK, fontweight="bold")
        ax.axhline(0, color=AXIS, linewidth=1)
        rebate = (bench - fee * 1e-4) * 100
        ax.set_title(title.replace("\n", " - "))
        who = "lender pays borrower" if rebate > 0 else "borrower pays lender"
        ax.text(0.02, 0.97, f"rebate = {bench * 100:.2f}% - {fee / 100:.2f}% = {rebate:+.2f}%  ({who})",
                transform=ax.transAxes, va="top", fontsize=9, color=INK2)
        ax.yaxis.set_major_formatter(_money)
        ax.tick_params(axis="x", labelsize=8.5)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel(f"$ per year on a $10m loan (collateral {p.coll.margin_gc:.0%})")
    fig.suptitle(f"Where the money comes from: cash collateral at a {bench:.2%} benchmark (assumption)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save(fig, path)


def explainer_margin_call(path, p, paths, universe):
    """Daily mark-to-market on one squeeze name from the stress run."""
    s = int(paths.squeeze[np.argmax(paths.price[paths.burn_in + p.stress.peak, paths.squeeze]
                                    / paths.price[paths.burn_in + p.stress.start - 1, paths.squeeze])])
    t0 = paths.burn_in + p.stress.start - 5
    t1 = paths.burn_in + p.stress.end
    px = paths.price[t0:t1, s]
    shares = np.floor(5_000_000 / px[0] / 100) * 100
    margin = paths.margin[t0:t1, s]
    coll = np.empty(len(px))
    req = np.empty(len(px))
    calls = np.zeros(len(px))
    held = shares * px[0] * margin[0]
    for i in range(len(px)):
        r, c, ret = mark_to_market(shares, px[i], held, margin[i], 0.0, p.coll.call_threshold)
        req[i] = r
        coll[i] = held                          # collateral in hand when the day's mark is struck
        if max(c, ret) > p.coll.min_transfer:   # same minimum-transfer rule as the simulator
            calls[i] = c - ret
            held = r
    days = np.arange(len(px)) - 5
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.2), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    a1.plot(days, shares * px, color=INK2, linewidth=1.6, label="Loan value (shares x price)")
    a1.plot(days, req, color=SERIES[1], linewidth=2, label="Collateral required")
    a1.plot(days, coll, color=SERIES[0], linewidth=1.6, marker="o", markersize=3.5, label="Collateral held before the day's call")
    a1.yaxis.set_major_formatter(_money)
    a1.legend(loc="center left")
    jump = int(np.argmax(np.diff(px))) + 1
    a1.annotate(f"price gaps +{(px[jump] / px[jump - 1] - 1):.0%}\nlender is under-collateralised\nuntil the call is met",
                xy=(days[jump], (req[jump] + coll[jump]) / 2), xytext=(days[jump] + 6, req[jump] * 0.98),
                fontsize=8.5, color=INK, ha="left", va="top",
                arrowprops=dict(arrowstyle="-", color=MUTED))
    sq_m = p.coll.margin_squeeze
    a1.set_title(f"Mark-to-market on a squeezed name ({universe.at[s, 'security']}, SIMULATED): "
                 f"margin rises from {margin[0]:.0%} to {sq_m:.0%} on day 2")
    cols = np.where(calls > 0, SERIES[1], SERIES[2])
    a2.bar(days, calls, color=cols, width=0.7)
    a2.axhline(0, color=AXIS, linewidth=1)
    a2.yaxis.set_major_formatter(_money)
    a2.set_ylabel("Collateral moved")
    a2.set_xlabel("Business days from squeeze start")
    a2.text(0.01, 0.92, "orange = margin call to borrower, green = excess returned", transform=a2.transAxes,
            fontsize=8.5, color=INK2, va="top")
    for a in (a1, a2):
        a.axvspan(0, p.stress.end - p.stress.start, color=BAND, zorder=0)
    fig.tight_layout()
    _save(fig, path)


def toy_allocation():
    """Two names, two borrowers. Borrower A bids highest on both but has room for one loan."""
    prob = al.AllocationProblem(
        sec=[0, 1, 0], bor=[0, 0, 1], qty=[100_000, 100_000, 100_000], price=[50.0, 50.0, 50.0],
        bid_bps=[1000.0, 900.0, 950.0], sec_avail=[100_000, 100_000],
        bor_headroom=[5_000_000, 50_000_000], bor_exposure=[0.0, 0.0],
    )
    return prob, al.greedy(prob), al.optimize(prob, risk_aversion=0, tilt_bps=0, use_conversion=False)[0]


def explainer_allocation_toy(path):
    prob, xg, xo = toy_allocation()
    labels = ["A  borrows X\nbid 10.0%", "A  borrows Y\nbid 9.0%", "B  borrows X\nbid 9.5%"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
    for ax, x, name in zip(axes, (xg, xo), ("Greedy: highest bid first", "Optimizer: best combination")):
        rev = al.revenue(prob, x) * 360
        ax.barh(labels, x * 50, color=[SERIES[1] if name.startswith("Greedy") else SERIES[0]] * 3, height=0.55)
        for i, v in enumerate(x * 50):
            ax.text(v + 1e5, i, _money(v) if v else "not lent", va="center", fontsize=9.5, color=INK)
        ax.set_xlim(0, 7.5e6)
        ax.xaxis.set_major_formatter(_money)
        ax.invert_yaxis()
        ax.set_title(f"{name}\nannual revenue {_money(rev)}")
        ax.grid(axis="y", visible=False)
    fig.text(0.01, -0.06, "Borrower A's counterparty limit only fits one $5m loan. Greedy gives A name X (best bid), "
             "so A cannot take Y and B is shut out of X: name Y sits idle.\nThe optimizer gives X to B and Y to A: "
             "lower fee on X, but both names earn. (Built and solved with the project's own allocation code.)",
             fontsize=8.8, color=INK2)
    fig.tight_layout()
    _save(fig, path)


# ============================================================ results
def fee_vs_utilization(path, p, universe, paths_stress, results):
    fig, ax = plt.subplots(figsize=(11, 5.2))
    u = np.linspace(0, 0.998, 500)
    ax.plot(u * 100, fee_curve_bps(u, p.fee.gc_fee_bps, p.fee.knee, p.fee.scale_bps, p.fee.cap_bps),
            color=INK2, linewidth=1.6, label="Fee curve (model)", zorder=2)
    b0 = paths_stress.burn_in
    tiers = tier_from_fee(paths_stress.market_fee[b0])
    for tier in TIER_ORDER:
        m = tiers == tier
        if not m.any():
            continue
        ax.scatter(paths_stress.street_util[b0, m] * 100, paths_stress.market_fee[b0, m], s=22, color=TIER_COLOR[tier],
                   edgecolor=SURFACE, linewidth=0.6, label=f"{tier} ({m.sum()} names, day 0)", zorder=3)
    sq = paths_stress.squeeze
    s0, s1 = b0 + p.stress.start - 3, b0 + p.stress.peak + 1
    path_u = paths_stress.street_util[s0:s1, sq].mean(axis=1) * 100
    path_f = paths_stress.market_fee[s0:s1, sq].mean(axis=1)
    ax.plot(path_u, path_f, color=SERIES[1], linewidth=2, marker="o", markersize=4, zorder=4,
            label="Squeeze basket, average path to peak (stress)")
    ax.annotate("peak", xy=(path_u[-1], path_f[-1]), xytext=(path_u[-1] - 9, path_f[-1] * 1.25), fontsize=9,
                color=INK, arrowprops=dict(arrowstyle="-", color=MUTED))
    ax.set_yscale("log")
    ax.set_xlim(0, 100)
    ax.set_xlabel("Street utilization: shares on loan / shares available to lend, market-wide (%)")
    ax.set_ylabel("Annualised borrow fee (bps, log scale)")
    ax.set_yticks([25, 50, 100, 200, 500, 1000, 2000, 5000])
    ax.set_yticklabels(["25", "50", "100", "200", "500", "1,000", "2,000", "5,000"])
    for y, lab in [(50, "GC / warm"), (200, "warm / special"), (1000, "special / HTB")]:
        ax.axhline(y, color=GRID, linewidth=1, linestyle="--", zorder=1)
        ax.text(62, y * 1.06, lab, fontsize=8, color=MUTED)
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.93))
    ax.set_title("Borrow fees are flat until supply runs out, then explode")
    _save(fig, path)


def revenue_by_method(path, summ):
    meths = ["pro_rata", "greedy", "lp_revenue", "optimized"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9), sharey=True)
    lim = 0
    for scen in ("base", "stress"):
        d = summ[summ["scenario"] == scen].set_index("method")
        lim = max(lim, (d.loc[meths, "net_revenue"] - d.loc["fcfs", "net_revenue"]).abs().max())
    for ax, scen in zip(axes, ("base", "stress")):
        d = summ[summ["scenario"] == scen].set_index("method")
        ref = d.loc["fcfs", "net_revenue"]
        delta = d.loc[meths, "net_revenue"] - ref
        y = np.arange(len(meths))
        ax.barh(y, delta, color=[METHOD_COLOR[m] for m in meths], height=0.6)
        for i, v in enumerate(delta):
            ax.text(v + (0.03 * lim if v >= 0 else -0.03 * lim), i, f"{_money(v)}  ({100 * v / ref:+.2f}%)",
                    va="center", ha="left" if v >= 0 else "right", fontsize=9, color=INK)
        ax.axvline(0, color=AXIS, linewidth=1)
        ax.set_yticks(y, [METHOD_NAME[m] for m in meths])
        ax.invert_yaxis()
        ax.set_xlim(-lim * 1.7, lim * 1.7)
        ax.xaxis.set_major_formatter(_money)
        ax.set_title(f"{scen.capitalize()}: FCFS baseline {_money(ref)} net")
        ax.grid(axis="y", visible=False)
    fig.suptitle("Quarter net revenue vs first come, first served (one market path, identical draws)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.text(0.01, -0.03, "Net = fees + reinvestment spread - recall, fail and buy-in costs. A single path is noisy: "
             "see the 20-path confidence intervals for the robust comparison.", fontsize=8.5, color=INK2)
    fig.tight_layout()
    _save(fig, path)


def optimization_improvement(path, rob):
    if rob is None or len(rob) == 0:
        return
    from .experiments import paired_stats
    st = paired_stats(rob, "net_revenue")
    refs = ["fcfs", "pro_rata", "greedy"]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    yt, ylab = [], []
    for j, scen in enumerate(("base", "stress")):
        for i, ref in enumerate(refs):
            r = st[(st["scenario"] == scen) & (st["vs"] == ref)].iloc[0]
            y = j * (len(refs) + 1) + i
            ax.plot([r["ci95_low_pct"], r["ci95_high_pct"]], [y, y], color=METHOD_COLOR[ref], linewidth=3,
                    solid_capstyle="round")
            ax.scatter([r["mean_pct"]], [y], s=60, color=METHOD_COLOR[ref], edgecolor=SURFACE, linewidth=1.5, zorder=3)
            ax.text(max(r["ci95_high_pct"], 0) + 0.15, y,
                    f"{r['mean_pct']:+.2f}%  [{r['ci95_low_pct']:+.2f}, {r['ci95_high_pct']:+.2f}]  wins {r['win_rate']:.0%}",
                    va="center", fontsize=8.8, color=INK)
            yt.append(y)
            ylab.append(f"{scen}: vs {METHOD_NAME[ref]}")
    ax.axvline(0, color=AXIS, linewidth=1)
    ax.set_yticks(yt, ylab)
    ax.invert_yaxis()
    ax.set_xlabel("Risk-adjusted LP net revenue minus alternative, % of alternative")
    n = int(st["n_seeds"].iloc[0])
    ax.set_title(f"Optimizer improvement across {n} independent market paths (mean and 95% CI)")
    ax.grid(axis="y", visible=False)
    lo = min(st["ci95_low_pct"].min(), 0) - 0.5
    ax.set_xlim(lo, st["ci95_high_pct"].max() + 2.6)
    _save(fig, path)


def inventory_scarcity(path, p, results, paths_by, universe):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [1, 1.3]})
    r = results[("base", "optimized")]
    loc = r.locates
    pb = paths_by["base"]
    t_abs = loc["day"].to_numpy() + pb.burn_in
    loc = loc.assign(tier=tier_from_fee(pb.market_fee[t_abs, loc["sec"].to_numpy()]),
                     req_val=loc["requested"] * pb.price[t_abs, loc["sec"].to_numpy()],
                     app_val=loc["approved"] * pb.price[t_abs, loc["sec"].to_numpy()])
    g = loc.groupby("tier")[["req_val", "app_val"]].sum().reindex(TIER_ORDER).fillna(0)
    fill = (g["app_val"] / g["req_val"]).fillna(0)
    x = np.arange(len(TIER_ORDER))
    a1.bar(x, fill * 100, color=[TIER_COLOR[t] for t in TIER_ORDER], width=0.6)
    for i, v in enumerate(fill):
        a1.text(i, v * 100 + 2, f"{v:.0%}", ha="center", fontsize=10, color=INK, fontweight="bold")
    a1.set_xticks(x, TIER_ORDER)
    a1.set_ylim(0, 110)
    a1.set_ylabel("Share of requested value approved (%)")
    a1.set_title("Locate fill rate by tier (base)")
    a1.grid(axis="x", visible=False)

    rs = results[("stress", "optimized")].daily
    ps = paths_by["stress"]
    d = rs["t"].to_numpy()
    a2.axvspan(p.stress.start, p.stress.end, color=BAND, zorder=0)
    a2.text(p.stress.start + 0.5, 0.97, "squeeze window", transform=a2.get_xaxis_transform(), fontsize=8.5,
            color=INK2, va="top")
    sq = ps.squeeze
    req = ps.requests[ps.requests["sec"].isin(sq)]
    req = req.assign(val=req["shares"] * ps.price[req["t"].to_numpy(), req["sec"].to_numpy()])
    dem = req.groupby("t")["val"].sum().reindex(range(ps.n_days), fill_value=0).to_numpy()[ps.burn_in:]
    a2.plot(d, rs["squeeze_lendable_value"], color=SERIES[0], label="Our lendable inventory")
    a2.plot(d, rs["squeeze_on_loan_value"], color=SERIES[2], label="On loan (incl. recalled, not yet returned)")
    a2.plot(d, pd.Series(dem).rolling(3, min_periods=1).mean(), color=SERIES[1], label="New borrow requests / day (3d avg)")
    a2.yaxis.set_major_formatter(_money)
    a2.set_xlabel("Business day")
    a2.legend(loc="center right")
    a2.set_title(f"Squeeze basket ({len(sq)} names): demand vs supply (stress)")
    fig.tight_layout()
    _save(fig, path)


def counterparty_concentration(path, p, results, borrowers, summ):
    r = results[("base", "optimized")]
    by = r.positions.groupby("bor")["value"].sum().reindex(range(len(borrowers)), fill_value=0)
    use = by / borrowers["exposure_limit"]
    order = np.argsort(-by.to_numpy())
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [2.2, 1]})
    cols = [TYPE_COLOR[borrowers.at[b, "type"]] for b in order]
    a1.bar(np.arange(len(order)), by.to_numpy()[order], color=cols, width=0.75)
    a1.scatter(np.arange(len(order)), borrowers["exposure_limit"].to_numpy()[order], marker="_", s=90,
               color=INK, linewidth=1.6, zorder=3, label="Counterparty limit")
    a1.yaxis.set_major_formatter(_money)
    a1.set_xticks([])
    a1.set_xlabel("36 SIMULATED counterparties, largest first")
    handles = [plt.Rectangle((0, 0), 1, 1, color=TYPE_COLOR[t]) for t in TYPE_ORDER]
    a1.legend(handles + [a1.collections[0]], TYPE_ORDER + ["Counterparty limit"], ncol=2, loc="upper right", fontsize=8.5)
    at = (use >= 0.98).sum()
    a1.set_title(f"On-loan value by counterparty at quarter end (base, LP): {at} at their limit")
    a1.grid(axis="x", visible=False)

    meths = ["fcfs", "pro_rata", "greedy", "optimized"]
    dd = summ[summ["scenario"] == "base"].set_index("method").loc[meths]
    y = np.arange(len(meths))
    a2.barh(y, dd["top5_share_avg"] * 100, color=[METHOD_COLOR[m] for m in meths], height=0.6)
    for i, m in enumerate(meths):
        a2.text(dd.loc[m, "top5_share_avg"] * 100 + 0.5, i, f"{dd.loc[m, 'top5_share_avg']:.1%}  HHI {dd.loc[m, 'hhi_avg']:.3f}",
                va="center", fontsize=8.8, color=INK)
    a2.set_yticks(y, [METHOD_NAME[m] for m in meths])
    a2.invert_yaxis()
    a2.set_xlim(0, dd["top5_share_avg"].max() * 100 * 1.7)
    a2.set_xlabel("Top-5 counterparties' share of book (%)")
    a2.set_title("Concentration by method")
    a2.grid(axis="y", visible=False)
    fig.tight_layout()
    _save(fig, path)


def stress_timeline(path, p, results, paths_by):
    ps = paths_by["stress"]
    ro = results[("stress", "optimized")].daily
    rg = results[("stress", "greedy")].daily
    rb = results[("base", "optimized")].daily
    d = ro["t"].to_numpy()
    sq = ps.squeeze
    b0 = ps.burn_in
    fig, ax = plt.subplots(4, 1, figsize=(11, 10), sharex=True, gridspec_kw={"hspace": 0.35})
    for a in ax:
        a.axvspan(p.stress.start, p.stress.end, color=BAND, zorder=0)
    idx = ps.price[b0:, sq] / ps.price[b0 + p.stress.start - 1, sq]
    ax[0].plot(d, np.exp(np.log(idx).mean(axis=1)), color=SERIES[1], label="Squeeze basket (geometric mean)")
    ax[0].plot(d, ps.price[b0:].mean(axis=1) / ps.price[b0 + p.stress.start - 1].mean(), color=INK2, linewidth=1.4,
               label="Whole universe (mean)")
    ax[0].set_ylabel("Price / pre-squeeze")
    ax[0].legend(loc="upper left")
    ax[0].set_title("1  Prices gap up as shorts scramble to cover")
    ax[0].text(p.stress.start + 0.3, 0.92, "squeeze window", transform=ax[0].get_xaxis_transform(), fontsize=8.5, color=INK2)

    ax[1].plot(d, ps.market_fee[b0:, sq].mean(axis=1), color=SERIES[1], label="Squeeze basket market fee")
    ax[1].set_yscale("log")
    ax[1].set_yticks([250, 500, 1000, 2000, 4000], ["250", "500", "1,000", "2,000", "4,000"])
    ax[1].minorticks_off()
    ax[1].set_ylabel("Fee (bps, log)")
    u_pk = ps.street_util[b0 + p.stress.peak, sq].mean()
    ax[1].set_title(f"2  Street utilization hits {u_pk:.1%}; borrow fees go from "
                    f"{ps.market_fee[b0 + p.stress.start - 1, sq].mean():,.0f} to "
                    f"{ps.market_fee[b0 + p.stress.peak, sq].mean():,.0f} bps")

    w = 0.4
    ax[2].bar(d - w / 2, ro["recalls_n"], width=w, color=SERIES[0], label="Recall notices")
    ax[2].bar(d + w / 2, ro["recall_fails_n"], width=w, color=CRITICAL, label="Recalls not returned on time")
    ax[2].plot(d, rb["recalls_n"].rolling(5, min_periods=1).mean(), color=INK2, linewidth=1.3, label="Base-case recalls (5d avg)")
    ax[2].set_ylabel("Count per day")
    ax[2].legend(loc="upper right", fontsize=8.5)
    ax[2].set_title("3  Owners sell into the rally: recalls and settlement fails spike")

    ax[3].plot(d, ro["squeeze_on_loan_value"], color=INK2, linewidth=1.4, label="Loan value")
    ax[3].plot(d, ro["squeeze_required_collateral"], color=SERIES[1], label="Collateral required")
    ax[3].plot(d, ro["squeeze_collateral_value"], color=SERIES[0], linestyle="--", label="Collateral held")
    ax[3].yaxis.set_major_formatter(_money)
    ax[3].set_xlabel("Business day")
    ax[3].legend(loc="upper right", fontsize=8.5)
    tot_calls = ro.loc[(ro["t"] >= p.stress.start) & (ro["t"] < p.stress.end), "calls_value"].sum()
    ax[3].set_title(f"4  Squeeze names: margin raised to {p.coll.margin_squeeze:.0%}; "
                    f"{_money(tot_calls)} called book-wide in the window")
    del rg
    _save(fig, path)


def limit_sweep_chart(path, lsw):
    if lsw is None or len(lsw) == 0:
        return
    piv = lsw.groupby(["limit_scale", "method"])["net_revenue"].mean().unstack()
    fig, ax = plt.subplots(figsize=(11, 4.4))
    xs = piv.index.to_numpy()
    for m in ["fcfs", "pro_rata", "greedy"]:
        rel = 100 * (piv["optimized"] / piv[m] - 1)
        ax.plot(xs, rel, color=METHOD_COLOR[m], marker="o", markersize=7, label=f"vs {METHOD_NAME[m]}")
        if m == "fcfs":
            for x, v in zip(xs, rel):
                ax.text(x, v + 0.8, f"{v:+.1f}%", ha="center", va="bottom", fontsize=9, color=INK)
    ax.axhline(0, color=AXIS, linewidth=1)
    top = max(100 * (piv["optimized"] / piv[m] - 1).max() for m in ["fcfs", "pro_rata", "greedy"])
    ax.set_ylim(top=top * 1.2)
    ax.set_xscale("log")
    ax.minorticks_off()
    ax.set_xticks(xs, [f"{x:g}x" for x in xs])
    ax.set_xlim(xs[0] * 0.7, xs[-1] * 1.2)
    ax.set_xlabel("Counterparty limits relative to the default (log scale)")
    ax.set_ylabel("LP net revenue advantage (%)")
    ax.legend(loc="upper right")
    n = lsw["seed"].nunique()
    ax.set_title(f"Allocation matters most when counterparty limits bind (base scenario, mean of {n} paths)")
    _save(fig, path)


def risk_frontier_chart(path, rsw):
    if rsw is None or len(rsw) == 0:
        return
    m = rsw.groupby(["method", "lambda"], dropna=False)[["revenue", "recall_cost", "net_revenue", "buyins_n"]].mean().reset_index()
    opt = m[m["method"] == "optimized"].sort_values("lambda")
    g = m[m["method"] == "greedy"].iloc[0]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    lam = opt["lambda"].to_numpy()
    xs = np.arange(len(lam))
    for a, col, ttl in ((a1, "recall_cost", "Realised recall + fail + buy-in cost"), (a2, "revenue", "Gross lending revenue")):
        rel = 100 * (opt[col].to_numpy() / g[col] - 1)
        a.plot(xs, rel, color=SERIES[0], marker="o", markersize=7, label="LP (risk-adjusted)")
        a.axhline(0, color=METHOD_COLOR["greedy"], linestyle="--", linewidth=1.5, label="Greedy (highest bid) = 0")
        span = max(abs(rel).max(), 0.05)
        for x, v in zip(xs, rel):
            a.text(x, v + 0.06 * span, f"{v:+.2f}%", ha="center", va="bottom", fontsize=8.5, color=INK)
        a.set_ylim(min(rel.min(), 0) - 0.4 * span, max(rel.max(), 0) + 0.4 * span)
        a.set_xticks(xs, [f"{v:g}" for v in lam])
        a.set_xlabel("Recall-risk aversion lambda (1 = expected cost at face value)")
        a.set_ylabel("% vs greedy")
        a.set_title(f"{ttl}  (greedy: {_money(g[col])})", fontsize=10.5)
        a.legend(loc="lower left", fontsize=8.5)
    n = rsw["seed"].nunique()
    fig.suptitle(f"The price of safety in a squeeze (stress scenario, mean of {n} paths)", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    _save(fig, path)


def make_all(fig_dir: Path, p, universe, borrowers, results, paths_by, summ, dsum, rob, lsw=None, rsw=None):
    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    _style()
    explainer_how_it_works(fig_dir / "explainer_how_it_works.png")
    explainer_cash_collateral(fig_dir / "explainer_cash_collateral.png", p)
    explainer_margin_call(fig_dir / "explainer_margin_call.png", p, paths_by["stress"], universe)
    explainer_allocation_toy(fig_dir / "explainer_allocation_toy.png")
    fee_vs_utilization(fig_dir / "fee_vs_utilization.png", p, universe, paths_by["stress"], results)
    revenue_by_method(fig_dir / "revenue_by_method.png", summ)
    optimization_improvement(fig_dir / "optimization_improvement.png", rob)
    inventory_scarcity(fig_dir / "inventory_scarcity.png", p, results, paths_by, universe)
    counterparty_concentration(fig_dir / "counterparty_concentration.png", p, results, borrowers, summ)
    stress_timeline(fig_dir / "stress_timeline.png", p, results, paths_by)
    limit_sweep_chart(fig_dir / "limit_sweep.png", lsw)
    risk_frontier_chart(fig_dir / "risk_frontier.png", rsw)
