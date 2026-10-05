"""Model parameters.

Every number in this file is a *modelling assumption* chosen to produce a
plausible, internally consistent SIMULATED book. None of them is a measured
broker statistic. They are collected here so the assumptions are reviewable in
one place and so experiments can override them explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class FeeCurve:
    """Borrow fee as a function of street utilization (annualised bps).

    Below the knee the name trades at the general-collateral (GC) fee. Above
    it the fee rises convexly and explodes as utilization approaches 100%,
    which is the qualitative shape documented for specials and squeezes.
    """

    gc_fee_bps: float = 25.0
    knee: float = 0.60
    scale_bps: float = 30.0
    cap_bps: float = 12_000.0


@dataclass(frozen=True)
class Economics:
    # Synthetic overnight cash benchmark used to set rebates. Not a quote.
    benchmark_rate: float = 0.0400
    # Extra yield the lender earns reinvesting cash collateral over the benchmark.
    reinvest_spread: float = 0.0010
    # Agent-lender share of gross revenue (the remainder goes to beneficial owners).
    agent_share: float = 0.20


@dataclass(frozen=True)
class Collateral:
    # Required collateral as a multiple of loan value.
    margin_gc: float = 1.02
    margin_special: float = 1.05
    # Add-on by borrower rating bucket.
    rating_addon: dict = field(default_factory=lambda: {"A": 0.00, "BBB": 0.01, "BB": 0.03})
    # Haircut applied to non-cash (government bond) collateral.
    govt_haircut: float = 0.02
    # Marks smaller than this fraction of loan value do not trigger a call.
    call_threshold: float = 0.005
    # Minimum transfer amount ($): smaller net moves are not called.
    min_transfer: float = 250_000.0
    # Daily probability a borrower misses a collateral call (scaled by rating).
    call_miss_prob: float = 0.01
    # Stress: margin applied to squeeze names once the squeeze is recognised.
    margin_squeeze: float = 1.25


@dataclass(frozen=True)
class Operations:
    settlement_days: int = 1             # US equities settle T+1
    base_recall_fail: float = 0.03       # P(borrower fails to return on time), before scaling
    fail_charge_daily: float = 0.0005    # lender's cost per fail-day, fraction of notional
    buyin_after_fail_days: int = 3
    buyin_slippage_gc: float = 0.01
    buyin_slippage_special: float = 0.04
    buyin_slippage_squeeze: float = 0.15
    buyin_recovery: float = 0.60         # recovery on any shortfall above collateral
    buyin_ops_cost: float = 2_500.0      # fixed operational cost per buy-in ($)
    recall_ops_cost: float = 150.0       # fixed operational cost per recall notice ($)
    delivery_fail_prob: float = 0.005    # lender fails to deliver a new loan on time
    recall_mean_fraction: float = 0.25   # mean fraction of a holding an owner sells


@dataclass(frozen=True)
class Allocation:
    # Hard constraints (applied by every method).
    name_cap: float = 0.35          # max share of a name's lendable to one borrower
    book_share_cap: float = 0.15    # max share of total on-loan value to one borrower
    # Lender will not lend a special below this fraction of the market fee.
    reserve_fraction: float = 0.80
    # Optimizer-only soft terms.
    recall_risk_aversion: float = 1.0     # 1.0 = charge expected recall cost at face value
    relationship_tilt_bps: float = 5.0    # bonus per unit relationship score
    # Optional policy: protected borrowers get at least this fraction of their request.
    min_alloc_fraction: float = 0.25
    min_alloc_relationship: float = 0.85


@dataclass(frozen=True)
class Stress:
    start: int = 20         # first squeeze day (business-day index)
    peak: int = 34
    end: int = 50           # squeeze has unwound by this day
    n_names: int = 8        # size of the squeeze basket
    peak_multiple: tuple = (2.0, 4.0)   # price multiple at the peak (uniform range)
    peak_utilization: float = 0.995
    owner_sale_prob: float = 0.10       # daily owner-sale probability in squeeze names
    special_recall_multiplier: float = 2.0  # owner-sale prob multiplier for other specials
    demand_multiplier: float = 1.8
    cover_hazard: float = 0.10          # daily voluntary-return hazard at the peak
    delivery_fail_prob: float = 0.03


@dataclass(frozen=True)
class Params:
    n_securities: int = 300
    n_borrowers: int = 36
    n_days: int = 63                 # one quarter of business days
    universe_seed: int = 2026        # universe + borrower population (fixed)
    path_seed: int = 7               # market paths, demand, behaviour draws
    limit_scale: float = 1.0         # multiplies every counterparty exposure limit
    fee: FeeCurve = field(default_factory=FeeCurve)
    econ: Economics = field(default_factory=Economics)
    coll: Collateral = field(default_factory=Collateral)
    ops: Operations = field(default_factory=Operations)
    alloc: Allocation = field(default_factory=Allocation)
    stress: Stress = field(default_factory=Stress)

    def with_(self, **kw) -> "Params":
        return replace(self, **kw)


DEFAULT = Params()
