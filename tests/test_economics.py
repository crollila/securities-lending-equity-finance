"""Hand-checked securities-lending arithmetic."""

import numpy as np
import pytest

from seclend import economics as ec


def test_rebate_is_benchmark_minus_fee():
    assert ec.rebate_rate(0.04, 25) == pytest.approx(0.0375)
    # Hard-to-borrow: the fee exceeds the benchmark, the rebate turns negative.
    assert ec.rebate_rate(0.04, 800) == pytest.approx(-0.04)


def test_act360_accrual():
    # $10m at 25 bps for 30 days, ACT/360.
    assert ec.accrual(10_000_000, 0.0025, 30) == pytest.approx(10_000_000 * 0.0025 * 30 / 360)
    assert ec.accrual(10_000_000, 0.0025, 30) == pytest.approx(2083.333333)


def test_cash_collateral_flows_net_equals_fee_plus_spread():
    coll, fee, bench, spr = 10_200_000, 25.0, 0.04, 0.001
    f = ec.cash_collateral_flows(coll, fee, bench, spr, 360)
    assert f["reinvestment_income"] == pytest.approx(coll * 0.041)
    assert f["rebate_paid"] == pytest.approx(coll * 0.0375)
    # Lender keeps fee + reinvestment spread on the collateral balance.
    assert f["lender_net"] == pytest.approx(coll * (0.0025 + 0.001))


def test_negative_rebate_means_borrower_pays():
    f = ec.cash_collateral_flows(10_000_000, 800.0, 0.04, 0.0, 360)
    assert f["rebate_paid"] < 0
    assert f["lender_net"] == pytest.approx(10_000_000 * 0.08)


def test_lender_daily_income_cash_vs_noncash():
    fee_c, re_c = ec.lender_daily_income(1_000_000, 1_020_000, 100, True, 0.04, 0.001, days=1)
    assert fee_c == pytest.approx(1_020_000 * 0.01 / 360)
    assert re_c == pytest.approx(1_020_000 * 0.001 / 360)
    fee_n, re_n = ec.lender_daily_income(1_000_000, 1_040_000, 100, False, 0.04, 0.001, days=3)
    assert fee_n == pytest.approx(1_000_000 * 0.01 * 3 / 360)   # fee on loan value, not collateral
    assert re_n == 0.0


def test_required_collateral_with_haircut():
    # $1m loan at 102% margin, cash: $1.02m.
    assert ec.required_collateral(1_000_000, 1.02) == pytest.approx(1_020_000)
    # Bonds with a 2% haircut: need 1.02m / 0.98 of market value.
    assert ec.required_collateral(1_000_000, 1.02, 0.02) == pytest.approx(1_020_000 / 0.98)
    with pytest.raises(ValueError):
        ec.required_collateral(1, 1.02, 1.0)


def test_mark_to_market_call_after_price_shock():
    shares, coll = 100_000, 100_000 * 50 * 1.02   # posted at $50
    req, call, ret = ec.mark_to_market(shares, 60.0, coll, 1.02)
    assert req == pytest.approx(6_120_000)
    assert call == pytest.approx(6_120_000 - 5_100_000)
    assert ret == 0.0


def test_mark_to_market_return_after_price_drop_and_threshold():
    shares, coll = 100_000, 5_100_000
    _, call, ret = ec.mark_to_market(shares, 40.0, coll, 1.02)
    assert call == 0.0 and ret == pytest.approx(5_100_000 - 4_080_000)
    # A tiny move inside the threshold triggers nothing.
    _, call, ret = ec.mark_to_market(shares, 50.10, coll, 1.02, threshold=0.005)
    assert call == 0.0 and ret == 0.0


def test_fee_curve_shape():
    u = np.linspace(0, 0.999, 200)
    f = ec.fee_curve_bps(u)
    assert np.all(np.diff(f) >= -1e-12)          # monotone
    assert f[0] == pytest.approx(25.0)           # GC floor
    assert ec.fee_curve_bps(0.5) == pytest.approx(25.0)
    assert ec.fee_curve_bps(0.99) > 1000         # specials explode near 100%
    assert ec.fee_curve_bps(1.0) <= 12_000


def test_tiers():
    assert list(ec.tier_from_fee([25, 100, 500, 3000])) == ["GC", "Warm", "Special", "HTB"]


def test_recall_fail_probability_scales_with_scarcity_and_is_capped():
    lo = ec.recall_fail_probability(0.03, 1.0, 0.5)
    hi = ec.recall_fail_probability(0.03, 1.0, 1.0)
    assert lo == pytest.approx(0.03)
    assert hi == pytest.approx(0.27)
    assert ec.recall_fail_probability(0.5, 5.0, 1.0) == pytest.approx(0.90)


def test_buyin_loss():
    cost, shortfall, loss = ec.buyin_loss(10_000, 100.0, 0.15, 1_050_000, 0.6)
    assert cost == pytest.approx(1_150_000)
    assert shortfall == pytest.approx(100_000)
    assert loss == pytest.approx(40_000)
    # Fully collateralised buy-in costs the lender nothing.
    assert ec.buyin_loss(10_000, 100.0, 0.01, 1_050_000, 0.6)[2] == 0.0


def test_split_preserves_value_and_manufactured_dividend():
    sh, px = ec.split_adjust(1000, 300.0, 3.0)
    assert sh == 3000 and px == pytest.approx(100.0)
    sh, px = ec.split_adjust(1000, 5.0, 0.1)      # 1-for-10 reverse split
    assert sh == pytest.approx(100) and px == pytest.approx(50.0)
    assert ec.manufactured_dividend(25_000, 0.42) == pytest.approx(10_500)


def test_hhi():
    assert ec.hhi([1, 1, 1, 1]) == pytest.approx(0.25)
    assert ec.hhi([5, 0, 0]) == pytest.approx(1.0)
    assert ec.hhi([0, 0]) == 0.0
