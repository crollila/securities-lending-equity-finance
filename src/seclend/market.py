"""Exogenous SIMULATED market paths: prices, street utilization, fees, owner
sales, corporate actions and borrower demand.

Everything in this module is independent of the allocation method. The
simulator runs every method against *the same* paths and the same behavioural
random numbers (common random numbers), so differences between methods come
from allocation decisions, not from luck.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Params
from .economics import fee_curve_bps

BURN_IN = 45  # days simulated before the reporting window so the book is seasoned


def _logit(u):
    u = np.clip(u, 1e-4, 1 - 1e-4)
    return np.log(u / (1 - u))


def _expit(x):
    return 1.0 / (1.0 + np.exp(-x))


@dataclass
class MarketPaths:
    scenario: str
    burn_in: int
    price: np.ndarray            # (T, S) traded price, after corporate actions
    street_util: np.ndarray      # (T, S)
    market_fee: np.ndarray       # (T, S) bps
    owner_factor: np.ndarray     # (T, S) multiplicative change to lendable (ex-split)
    owner_sale_prob: np.ndarray  # (T, S)
    split_ratio: np.ndarray      # (T, S) 1.0 except on split ex-dates
    dividend: np.ndarray         # (T, S) $/share on ex-date
    margin: np.ndarray           # (T, S) base collateral margin before rating add-on
    buyin_slip: np.ndarray       # (T, S)
    hazard_mult: np.ndarray      # (T, S) multiplier on borrower return hazard
    delivery_fail_prob: np.ndarray  # (T,)
    daycount: np.ndarray         # (T,) calendar days accrued on each business day
    squeeze: np.ndarray          # indices of squeeze-basket securities (empty in base)
    in_squeeze: np.ndarray       # (T,) bool, True inside the stress window
    unif: dict                   # tag -> (T, S, B) float32 uniforms
    requests: pd.DataFrame       # t, sec, bor, shares, bid_bps, arrival
    corporate_actions: pd.DataFrame

    @property
    def n_days(self):
        return self.price.shape[0]


def build_paths(p: Params, universe: pd.DataFrame, borrowers: pd.DataFrame, scenario: str = "base") -> MarketPaths:
    if scenario not in ("base", "stress"):
        raise ValueError(scenario)
    S, B = len(universe), len(borrowers)
    T = BURN_IN + p.n_days
    rng = np.random.default_rng(p.path_seed)
    st = p.stress
    s0, pk, s1 = BURN_IN + st.start, BURN_IN + st.peak, BURN_IN + st.end
    stressed = scenario == "stress"

    # ---------------- squeeze basket (chosen identically in both scenarios) ---
    cand = universe.index[(universe["size"] != "Large") & (universe["street_util0"] > 0.80)]
    order = universe.loc[cand].sort_values("street_util0", ascending=False).index.to_numpy()
    basket = np.sort(order[: st.n_names]) if stressed else np.array([], dtype=int)
    in_squeeze = np.zeros(T, bool)
    if stressed:
        in_squeeze[s0:s1] = True
    is_basket = np.zeros(S, bool)
    is_basket[basket] = True

    # ---------------- prices -------------------------------------------------
    vol = universe["daily_vol"].to_numpy()
    beta = universe["beta"].to_numpy()
    sectors, sec_idx = np.unique(universe["sector"].to_numpy(), return_inverse=True)
    mkt = rng.normal(0, 0.010, T)
    secf = rng.normal(0, 0.006, (T, len(sectors)))
    idio_vol = np.sqrt(np.maximum(vol**2 - (beta * 0.010) ** 2 - 0.006**2, (0.3 * vol) ** 2))
    eps = rng.normal(0, 1, (T, S))
    logret = beta * mkt[:, None] + secf[:, sec_idx] + idio_vol * eps
    logret[0] = 0.0

    # Squeeze: jumpy run-up to a peak multiple, then a partial collapse.
    peak_mult = rng.uniform(*st.peak_multiple, S)
    up_w = rng.dirichlet(np.full(pk - s0, 0.6), S)
    dn_w = rng.dirichlet(np.full(s1 - pk, 0.8), S)
    if stressed:
        for s in basket:
            logret[s0:pk, s] += np.log(peak_mult[s]) * up_w[s]
            logret[pk:s1, s] -= 0.6 * np.log(peak_mult[s]) * dn_w[s]

    price = universe["price"].to_numpy() * np.exp(np.cumsum(logret, axis=0))

    # ---------------- corporate actions ---------------------------------------
    ca_rng = np.random.default_rng(p.universe_seed + 99)
    split = np.ones((T, S))
    div = np.zeros((T, S))
    ca_rows = []
    payers = np.flatnonzero(ca_rng.random(S) < 0.30)
    for s in payers:
        t = int(ca_rng.integers(BURN_IN + 3, T - 3))
        yld = ca_rng.uniform(0.005, 0.04)
        d = round(float(price[t - 1, s]) * yld / 4, 2)
        div[t, s] = d
        ca_rows.append((t - BURN_IN, s, "cash_dividend", d))
    p0 = universe["price"].to_numpy()
    fwd = np.flatnonzero(p0 > 250)[:4]
    rev = np.flatnonzero(p0 < 8)[:2]
    for s in fwd:
        t = int(ca_rng.integers(BURN_IN + 5, T - 5))
        r = 2.0 if p0[s] < 500 else 3.0
        split[t, s] = r
        ca_rows.append((t - BURN_IN, s, "forward_split", r))
    for s in rev:
        t = int(ca_rng.integers(BURN_IN + 5, T - 5))
        split[t, s] = 0.1
        ca_rows.append((t - BURN_IN, s, "reverse_split", 0.1))
    # Apply to the traded price path: split divides, dividend drops the price.
    adj = np.cumprod(1.0 / split, axis=0)
    drop = np.ones((T, S))
    for t, s in zip(*np.nonzero(div)):
        drop[t:, s] *= 1.0 - div[t, s] / (price[t - 1, s] * adj[t, s])
    price = price * adj * drop
    ca = pd.DataFrame(ca_rows, columns=["day", "sec", "action", "value"])
    ca["security"] = universe["security"].to_numpy()[ca["sec"]] if len(ca) else []

    # ---------------- street utilization and fees ------------------------------
    u0 = universe["street_util0"].to_numpy()
    x = np.empty((T, S))
    x[0] = _logit(u0)
    shocks = rng.normal(0, 0.08, (T, S))
    for t in range(1, T):
        x[t] = _logit(u0) + 0.95 * (x[t - 1] - _logit(u0)) + shocks[t]
    if stressed:
        lp, le = _logit(st.peak_utilization), _logit(0.92)
        for s in basket:
            ls = x[s0, s]
            ramp = np.linspace(0, 1, pk - s0 + 1)
            x[s0:pk + 1, s] = ls + (lp - ls) * np.sqrt(ramp)
            x[pk:s1, s] = lp + (le - lp) * np.linspace(0, 1, s1 - pk)
            x[s1:, s] = le + 0.3 * shocks[s1:, s]
        other_sp = (u0 > 0.7) & ~is_basket
        x[s0:s1, other_sp] += 0.5  # crowded shorts get tighter everywhere
    street_util = _expit(x)
    fee = fee_curve_bps(street_util, p.fee.gc_fee_bps, p.fee.knee, p.fee.scale_bps, p.fee.cap_bps)

    # ---------------- beneficial-owner sales (drive recalls) ------------------
    q = np.tile(universe["owner_sale_prob"].to_numpy(), (T, 1))
    if stressed:
        q[s0:s1][:, other_sp] *= st.special_recall_multiplier
        q[s0:s1, basket] = st.owner_sale_prob
    ev = rng.random((T, S))
    frac = rng.uniform(0.10, 0.40, (T, S))
    add = rng.uniform(0.05, 0.20, (T, S))
    owner_factor = np.where(ev < q, 1.0 - frac, np.where(ev > 1 - 0.01, 1.0 + add, 1.0))
    owner_factor[:1] = 1.0

    # ---------------- collateral & operational parameters ---------------------
    special = fee > 200
    margin = np.where(special, p.coll.margin_special, p.coll.margin_gc)
    slip = np.where(special, p.ops.buyin_slippage_special, p.ops.buyin_slippage_gc)
    hazard_mult = np.ones((T, S))
    dfp = np.full(T, p.ops.delivery_fail_prob)
    if stressed:
        margin[s0 + 2:, basket] = p.coll.margin_squeeze
        slip[s0:s1, basket] = p.ops.buyin_slippage_squeeze
        tri = np.interp(np.arange(T), [s0, pk, s1, s1 + 5], [1.0, st.cover_hazard / 0.035, 2.0, 1.0])
        hazard_mult[:, basket] = np.where((np.arange(T) >= s0) & (np.arange(T) < s1 + 5), tri, 1.0)[:, None]
        dfp[s0:s1] = st.delivery_fail_prob

    daycount = np.array([3 if (t % 5) == 4 else 1 for t in range(T)], float)

    # ---------------- common random numbers for borrower behaviour ------------
    urng = np.random.default_rng(p.path_seed + 1000)
    unif = {tag: urng.random((T, S, B), dtype=np.float32) for tag in ("ret", "fail", "conv", "call", "deliv")}

    # ---------------- borrower demand (locate requests) -----------------------
    requests = _demand(p, universe, borrowers, street_util, fee, price, split, T, s0, s1, basket, stressed)

    return MarketPaths(
        scenario=scenario, burn_in=BURN_IN, price=price, street_util=street_util, market_fee=fee,
        owner_factor=owner_factor, owner_sale_prob=q, split_ratio=split, dividend=div,
        margin=margin, buyin_slip=slip, hazard_mult=hazard_mult, delivery_fail_prob=dfp,
        daycount=daycount, squeeze=basket, in_squeeze=in_squeeze, unif=unif,
        requests=requests, corporate_actions=ca,
    )


def _demand(p, universe, borrowers, util, fee, price, split, T, s0, s1, basket, stressed):
    """Locate requests. Notionals use the split-unadjusted price so they stay continuous through splits."""
    cum_split = np.cumprod(split, axis=0)
    value_px = price * cum_split
    rng = np.random.default_rng(p.path_seed + 2000)
    B = len(borrowers)
    lend_val0 = universe["lendable_value"].to_numpy()
    pref = borrowers["special_pref"].to_numpy()
    prem = borrowers["bid_premium"].to_numpy()
    disp = borrowers["bid_dispersion"].to_numpy()
    size = borrowers["size_mult"].to_numpy()
    p0 = universe["price"].to_numpy()

    out = []
    for t in range(T):
        lam = 0.55 * (0.6 + 2.5 * util[t])
        if stressed and s0 <= t < s1:
            lam[basket] *= p.stress.demand_multiplier
        n_req = np.minimum(rng.poisson(lam), B)
        for s in np.flatnonzero(n_req):
            sp = fee[t, s] > 200
            w = pref if sp else pref ** -0.5
            bors = rng.choice(B, size=n_req[s], replace=False, p=w / w.sum())
            base_frac = 0.045 if sp else 0.009
            # Notional is sized off the holding's *original* value, scaled to today's price,
            # so demand does not mechanically track our own inventory changes.
            px_today = value_px[t, s] / cum_split[t, s]
            notional = lend_val0[s] * (value_px[t, s] / p0[s]) * base_frac * size[bors] * np.exp(rng.normal(0, 0.7, len(bors)))
            notional = np.minimum(notional, 0.6 * lend_val0[s] * value_px[t, s] / p0[s])
            shares = np.maximum(np.floor(notional / px_today / 100) * 100, 100)
            sig = 0.30 if sp else 0.10
            bid = fee[t, s] * np.exp(prem[bors] + disp[bors] * sig * rng.normal(0, 1, len(bors)))
            for b, q, f in zip(bors, shares, bid):
                out.append((t, s, b, q, f))
    df = pd.DataFrame(out, columns=["t", "sec", "bor", "shares", "bid_bps"])
    # Intraday arrival order of each locate (used by first-come-first-served).
    df["arrival"] = rng.random(len(df))
    return df
