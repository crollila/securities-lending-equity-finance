"""Pure securities-lending arithmetic.

Conventions
-----------
* Fees and rates are annualised. Fees are in basis points, rates are decimals.
* Accruals use ACT/360, the US money-market convention for equity loans.
* "Loan value" is shares x price. "Collateral value" is what the lender holds.

These functions are deliberately free of simulation state so they can be
tested by hand against worked examples.
"""

from __future__ import annotations

import numpy as np

BP = 1e-4
DAYS_IN_YEAR = 360.0


def fee_curve_bps(utilization, gc_fee_bps=25.0, knee=0.60, scale_bps=30.0, cap_bps=12_000.0):
    """Market borrow fee implied by street utilization.

    fee = gc                                   for u <= knee
    fee = gc + scale * x^2 / (1 - u + 0.005)   for u >  knee, x = (u-knee)/(1-knee)

    Around 90% utilization this gives ~1.9%, around 99% ~19%, capped at 120%.
    """
    u = np.clip(np.asarray(utilization, dtype=float), 0.0, 1.0)
    x = np.clip((u - knee) / (1.0 - knee), 0.0, None)
    fee = gc_fee_bps + scale_bps * x**2 / (1.0 - u + 0.005)
    return np.minimum(fee, cap_bps)


def tier_from_fee(fee_bps):
    """Industry-style labels. Thresholds are a convention, not a standard."""
    f = np.asarray(fee_bps, dtype=float)
    return np.select(
        [f <= 50, f <= 200, f <= 1000], ["GC", "Warm", "Special"], default="HTB"
    )


def rebate_rate(benchmark_rate, fee_bps):
    """Rebate paid by the lender to the borrower on cash collateral.

    rebate = benchmark - fee. When the fee exceeds the benchmark the rebate is
    negative: the borrower pays interest on its own cash (typical for HTB names).
    """
    return benchmark_rate - np.asarray(fee_bps, dtype=float) * BP


def accrual(amount, rate, days):
    """Simple ACT/360 accrual of `rate` on `amount` for `days` calendar days."""
    return np.asarray(amount, dtype=float) * rate * days / DAYS_IN_YEAR


def lender_daily_income(loan_value, collateral_value, fee_bps, cash_collateral,
                        benchmark_rate, reinvest_spread, days=1):
    """Lender income for `days` days, split into its two economic sources.

    Cash collateral: the lender reinvests the cash at benchmark + spread and pays
    the borrower the rebate (benchmark - fee). Net = (fee + spread) x collateral.
      - spread component    = fee x collateral    (what the borrower gives up)
      - reinvest component  = spread x collateral (lender's cash-management pickup)

    Non-cash collateral: the borrower pays the fee directly on loan value.
      - spread component    = fee x loan value
      - reinvest component  = 0

    Returns (fee_component, reinvest_component) arrays.
    """
    loan_value = np.asarray(loan_value, dtype=float)
    collateral_value = np.asarray(collateral_value, dtype=float)
    fee = np.asarray(fee_bps, dtype=float) * BP
    cash = np.asarray(cash_collateral, dtype=bool)
    fee_part = np.where(cash, accrual(collateral_value, fee, days), accrual(loan_value, fee, days))
    reinvest_part = np.where(cash, accrual(collateral_value, reinvest_spread, days), 0.0)
    return fee_part, reinvest_part


def cash_collateral_flows(collateral_value, fee_bps, benchmark_rate, reinvest_spread, days):
    """Gross legs of a cash-collateralised loan (used for worked examples).

    Returns dict with reinvestment income, rebate paid and lender net.
    """
    reinvest = accrual(collateral_value, benchmark_rate + reinvest_spread, days)
    rebate = accrual(collateral_value, rebate_rate(benchmark_rate, fee_bps), days)
    return {
        "reinvestment_income": float(reinvest),
        "rebate_paid": float(rebate),
        "lender_net": float(reinvest - rebate),
    }


def required_collateral(loan_value, margin, haircut=0.0):
    """Collateral market value required so that value x (1 - haircut) >= margin x loan."""
    if np.any(np.asarray(haircut) >= 1.0):
        raise ValueError("haircut must be < 1")
    return np.asarray(loan_value, dtype=float) * np.asarray(margin) / (1.0 - np.asarray(haircut))


def mark_to_market(shares, price, collateral_value, margin, haircut=0.0, threshold=0.005):
    """Daily mark. Returns (required, call_amount, return_amount).

    call_amount > 0 when the lender is under-collateralised by more than the
    threshold (fraction of loan value); return_amount > 0 when the borrower is
    over-collateralised by more than the threshold and may ask for cash back.
    """
    loan_value = np.asarray(shares, dtype=float) * np.asarray(price, dtype=float)
    required = required_collateral(loan_value, margin, haircut)
    diff = required - np.asarray(collateral_value, dtype=float)
    thr = threshold * loan_value
    call = np.where(diff > thr, diff, 0.0)
    ret = np.where(-diff > thr, -diff, 0.0)
    return required, call, ret


def scarcity_multiplier(street_utilization):
    """How much harder it is to source shares to return a recall.

    1 at or below 80% street utilization, 9x at 100%.
    """
    u = np.clip(np.asarray(street_utilization, dtype=float), 0.0, 1.0)
    return 1.0 + 8.0 * (np.clip(u - 0.8, 0.0, None) / 0.2) ** 2


def recall_fail_probability(base, sensitivity, street_utilization, cap=0.90):
    """P(a recalled borrower fails to return on the due date)."""
    return np.minimum(base * np.asarray(sensitivity) * scarcity_multiplier(street_utilization), cap)


def buyin_loss(shares, price, slippage, collateral_cash_equiv, recovery):
    """Lender loss from buying in a failed return.

    The lender buys the shares at price x (1 + slippage), applies collateral,
    and has an unsecured claim on any shortfall, recovered at `recovery`.
    Returns (buyin_cost, shortfall, loss).
    """
    cost = np.asarray(shares, dtype=float) * np.asarray(price, dtype=float) * (1.0 + np.asarray(slippage))
    shortfall = np.maximum(cost - np.asarray(collateral_cash_equiv, dtype=float), 0.0)
    return cost, shortfall, shortfall * (1.0 - recovery)


def hhi(values):
    """Herfindahl-Hirschman index of exposures (0..1, 1 = single counterparty)."""
    v = np.asarray(values, dtype=float)
    tot = v.sum()
    if tot <= 0:
        return 0.0
    w = v / tot
    return float(np.sum(w * w))


def split_adjust(shares, price, ratio):
    """Apply an N-for-1 split (ratio=N; reverse splits use ratio<1). Value is preserved."""
    return np.asarray(shares, dtype=float) * ratio, np.asarray(price, dtype=float) / ratio


def manufactured_dividend(shares_on_loan, dividend_per_share, rate=1.0):
    """Payment the borrower owes the lender in lieu of a dividend it did not receive."""
    return np.asarray(shares_on_loan, dtype=float) * dividend_per_share * rate
