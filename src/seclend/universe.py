"""Deterministic SIMULATED securities universe and borrower population.

Nothing here represents actual broker inventory, real tickers or real
counterparties. Tickers are `SIMxxx`, counterparties are `SIM-CPTY-xx`.
The generators are seeded, so the same seed always produces the same book.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Params
from .economics import fee_curve_bps, tier_from_fee

SECTORS = np.array([
    "Technology", "Health Care", "Financials", "Consumer Discretionary",
    "Communication", "Industrials", "Consumer Staples", "Energy",
    "Utilities", "Materials", "Real Estate",
])
SECTOR_WEIGHTS = np.array([0.18, 0.13, 0.12, 0.11, 0.08, 0.10, 0.06, 0.06, 0.04, 0.06, 0.06])
SECTOR_VOL = dict(zip(SECTORS, [1.25, 1.20, 1.00, 1.15, 1.15, 1.00, 0.75, 1.30, 0.70, 1.05, 0.95]))


def build_universe(p: Params) -> pd.DataFrame:
    rng = np.random.default_rng(p.universe_seed)
    n = p.n_securities

    sector = rng.choice(SECTORS, size=n, p=SECTOR_WEIGHTS / SECTOR_WEIGHTS.sum())
    mcap = np.clip(np.exp(rng.normal(np.log(8e9), 1.3, n)), 3e8, 1.5e12)
    size = np.where(mcap < 2e9, "Small", np.where(mcap < 1e10, "Mid", "Large"))
    price = np.round(np.clip(np.exp(rng.normal(np.log(60), 0.8, n)), 3, 900), 2)
    float_frac = rng.uniform(0.70, 0.95, n)
    float_shares = mcap / price * float_frac

    # Street utilization: most names are GC, some are crowded shorts.
    # Large caps are effectively always GC: their float dwarfs any short base.
    p_special = np.select([size == "Small", size == "Mid"], [0.32, 0.15], 0.0)
    is_special = rng.random(n) < p_special
    u_gc = np.clip(rng.beta(2.0, 7.0, n), 0.02, 0.58)
    u_sp = rng.uniform(0.80, 0.985, n)
    street_util0 = np.where(is_special, u_sp, u_gc)
    fee0 = fee_curve_bps(street_util0, p.fee.gc_fee_bps, p.fee.knee, p.fee.scale_bps, p.fee.cap_bps)

    # Our beneficial owners' lendable holding, as a fraction of float.
    # Crowded shorts are typically thinly held by lending programmes.
    own_frac = rng.uniform(0.002, 0.015, n) * np.where(is_special, 0.5, 1.0)
    lendable = np.floor(float_shares * own_frac / 100) * 100

    base_vol = np.select([size == "Small", size == "Mid"], [0.030, 0.020], 0.014)
    daily_vol = base_vol * np.array([SECTOR_VOL[s] for s in sector]) * np.where(is_special, 1.3, 1.0)

    # Daily probability that a beneficial owner sells part of the holding (-> recalls).
    owner_sale_prob = rng.uniform(0.005, 0.025, n)
    beta = np.clip(rng.normal(1.0, 0.25, n), 0.4, 1.8)

    df = pd.DataFrame({
        "security": [f"SIM{i:03d}" for i in range(n)],
        "sector": sector,
        "size": size,
        "market_cap": mcap,
        "price": price,
        "float_shares": np.floor(float_shares),
        "lendable_qty": lendable,
        "lendable_value": lendable * price,
        "ownership_pct_float": own_frac,
        "street_util0": street_util0,
        "fee0_bps": fee0,
        "tier0": tier_from_fee(fee0),
        "daily_vol": daily_vol,
        "beta": beta,
        "owner_sale_prob": owner_sale_prob,
    })
    df["concentration"] = df["lendable_value"] / df["lendable_value"].sum()
    df["label"] = "SIMULATED"
    return df


# type: count, limit $, relationship range, recall sensitivity range, bid premium (log),
#       bid dispersion multiplier, size multiplier, locate->borrow conversion,
#       special preference, return hazard, collateral, rating
BORROWER_TYPES = {
    "Multi-strategy":   (6, 0.55e9, (0.75, 0.95), (0.6, 0.9), 0.00, 1.0, 1.6, 0.85, 1.0, 0.035, "cash", "A"),
    "Fundamental L/S":  (10, 0.28e9, (0.50, 0.85), (0.8, 1.3), 0.05, 1.0, 1.0, 0.75, 1.2, 0.030, "cash", "A"),
    "Quant":            (6, 0.35e9, (0.40, 0.70), (0.5, 0.8), -0.05, 0.6, 0.8, 0.90, 0.5, 0.060, "cash", "A"),
    "Activist short":   (5, 0.11e9, (0.20, 0.50), (1.6, 2.5), 0.25, 1.3, 0.8, 0.70, 4.0, 0.020, "cash", "BB"),
    "Market maker":     (5, 0.22e9, (0.50, 0.80), (0.4, 0.7), -0.02, 0.8, 1.2, 0.95, 0.8, 0.080, "noncash", "A"),
    "Event-driven":     (4, 0.14e9, (0.30, 0.60), (1.2, 1.8), 0.12, 1.2, 0.9, 0.70, 2.5, 0.030, "cash", "BBB"),
}


def build_borrowers(p: Params) -> pd.DataFrame:
    rng = np.random.default_rng(p.universe_seed + 1)
    rows = []
    for btype, (cnt, limit, rel, sens, prem, disp, size, conv, sp_pref, hazard, coll, rating) in BORROWER_TYPES.items():
        for _ in range(cnt):
            rows.append({
                "type": btype,
                "exposure_limit": float(np.round(limit * p.limit_scale * rng.uniform(0.7, 1.3), -6)),
                "relationship": float(rng.uniform(*rel)),
                "recall_sensitivity": float(rng.uniform(*sens)),
                "bid_premium": prem,
                "bid_dispersion": disp,
                "size_mult": size * float(rng.uniform(0.8, 1.2)),
                "conversion": conv,
                "special_pref": sp_pref,
                "return_hazard": hazard,
                "collateral": coll,
                "rating": rating,
            })
    df = pd.DataFrame(rows)
    df = df.iloc[: p.n_borrowers].reset_index(drop=True)
    df.insert(0, "counterparty", [f"SIM-CPTY-{i:02d}" for i in range(len(df))])
    df["label"] = "SIMULATED"
    return df
