"""Allocation methods: hard constraints, arithmetic, and optimality versus greedy."""

import numpy as np
import pytest

from seclend import allocation as al


def random_problem(seed, n_sec=12, n_bor=8, n_req=60, limits=True, caps=True, risk=True):
    rng = np.random.default_rng(seed)
    pairs = rng.choice(n_sec * n_bor, size=n_req, replace=False)
    sec, bor = pairs // n_bor, pairs % n_bor
    price = rng.uniform(5, 200, n_sec)[sec]
    qty = np.floor(rng.uniform(1_000, 50_000, n_req))
    expo = rng.uniform(0, 5e6, n_bor)
    return al.AllocationProblem(
        sec=sec, bor=bor, qty=qty, price=price, bid_bps=rng.lognormal(np.log(200), 1.0, n_req),
        sec_avail=np.floor(rng.uniform(5_000, 80_000, n_sec)),
        bor_headroom=rng.uniform(1e5, 4e6, n_bor) if limits else np.full(n_bor, 1e15),
        bor_exposure=expo, book_base=8e7 if caps else 0.0, book_share_cap=0.10 if caps else 1.0,
        pair_headroom=np.floor(rng.uniform(5_000, 60_000, n_req)) if caps else None,
        penalty_bps=rng.uniform(0, 150, n_req) if risk else None,
        relationship=rng.uniform(0, 1, n_bor)[bor], conversion=rng.uniform(0.6, 1.0, n_bor)[bor],
    )


def all_methods(prob):
    rng = np.random.default_rng(0)
    return {
        "fcfs": al.fcfs(prob, rng.random(prob.n)),
        "greedy": al.greedy(prob),
        "pro_rata": al.pro_rata(prob),
        "lp_revenue": al.optimize(prob, 0.0, 0.0, use_conversion=False)[0],
        "optimized": al.optimize(prob, 1.0, 5.0)[0],
    }


@pytest.mark.parametrize("seed", range(25))
def test_every_method_respects_every_hard_constraint(seed):
    prob = random_problem(seed)
    for name, x in all_methods(prob).items():
        assert al.violations(prob, x) == [], name


@pytest.mark.parametrize("seed", range(10))
def test_shares_lent_never_exceed_inventory(seed):
    prob = random_problem(seed, limits=False, caps=False)
    for name, x in all_methods(prob).items():
        used = np.bincount(prob.sec, weights=x, minlength=len(prob.sec_avail))
        assert np.all(used <= prob.sec_avail + 1e-9), name


@pytest.mark.parametrize("seed", range(10))
def test_counterparty_limits_cannot_be_breached(seed):
    prob = random_problem(seed)
    room = prob.bor_room()
    for name, x in all_methods(prob).items():
        used = np.bincount(prob.bor, weights=x * prob.price, minlength=len(room))
        assert np.all(used <= room + 1.0), name
        assert np.all(used <= prob.bor_headroom + 1.0), name


def test_allocations_are_whole_shares_and_sum_correctly():
    prob = random_problem(3)
    for name, x in all_methods(prob).items():
        assert np.all(x == np.floor(x)), name
        assert np.all((x >= 0) & (x <= prob.qty)), name
        by_sec = np.bincount(prob.sec, weights=x, minlength=len(prob.sec_avail))
        assert by_sec.sum() == pytest.approx(x.sum())
        assert al.revenue(prob, x) == pytest.approx(np.sum(x * prob.price * prob.bid_bps) / 1e4 / 360)


@pytest.mark.parametrize("seed", range(20))
def test_lp_never_worse_than_greedy_when_unconstrained(seed):
    """Only inventory binds and there is no risk term: the LP must match or beat greedy."""
    prob = random_problem(seed, limits=False, caps=False, risk=False)
    xg = al.greedy(prob)
    xo, info = al.optimize(prob, 0.0, 0.0, use_conversion=False)
    assert info["status"] == "optimal"
    # Flooring an LP vertex can cost at most one share per request.
    tol = np.sum(prob.price * prob.bid_bps) / 1e4 / 360
    assert al.revenue(prob, xo) >= al.revenue(prob, xg) - tol


@pytest.mark.parametrize("seed", range(20))
def test_lp_never_worse_than_any_feasible_method_on_its_objective(seed):
    """Every method obeys the same hard constraints, so each is feasible for the LP."""
    prob = random_problem(seed)
    xs = all_methods(prob)
    tol = np.sum(prob.price * (prob.bid_bps + 5.0)) / 1e4 / 360
    obj_lp = al.risk_adjusted_revenue(prob, xs["optimized"], 1.0, 5.0)
    # (The LP zeroes requests with a negative coefficient; dropping such an allocation from
    # any feasible solution keeps it feasible and never lowers the objective.)
    for name, x in xs.items():
        assert obj_lp >= al.risk_adjusted_revenue(prob, x, 1.0, 5.0) - tol, name
    # Gross-revenue LP vs every heuristic on gross revenue.
    obj_rev = al.revenue(prob, xs["lp_revenue"])
    for name in ("fcfs", "greedy", "pro_rata"):
        assert obj_rev >= al.revenue(prob, xs[name]) - tol, name


def test_toy_example_optimizer_beats_greedy_because_of_a_counterparty_limit():
    prob = al.AllocationProblem(
        sec=[0, 1, 0], bor=[0, 0, 1], qty=[100_000] * 3, price=[50.0] * 3,
        bid_bps=[1000.0, 900.0, 950.0], sec_avail=[100_000, 100_000],
        bor_headroom=[5_000_000, 50_000_000], bor_exposure=[0.0, 0.0],
    )
    xg = al.greedy(prob)
    xo, _ = al.optimize(prob, 0.0, 0.0, use_conversion=False)
    assert list(xg) == [100_000, 0, 0]
    assert list(xo) == [0, 100_000, 100_000]
    assert al.revenue(prob, xg) * 360 == pytest.approx(500_000)
    assert al.revenue(prob, xo) * 360 == pytest.approx(925_000)


def test_risk_penalty_declines_loans_whose_expected_cost_exceeds_the_fee():
    prob = al.AllocationProblem(
        sec=[0, 0], bor=[0, 1], qty=[1000, 1000], price=[10.0, 10.0], bid_bps=[900.0, 600.0],
        sec_avail=[1000], bor_headroom=[1e9, 1e9], bor_exposure=[0, 0], penalty_bps=[1200.0, 50.0],
    )
    assert list(al.greedy(prob)) == [1000, 0]            # greedy takes the highest bid
    x, _ = al.optimize(prob, risk_aversion=1.0)
    assert list(x) == [0, 1000]                          # LP takes the safer borrower
    assert al.rejection_reasons(prob, x, optimizer=True)[0] == "no inventory"


def test_conversion_rate_shifts_allocation_to_borrowers_who_use_locates():
    prob = al.AllocationProblem(
        sec=[0, 0], bor=[0, 1], qty=[1000, 1000], price=[10.0, 10.0], bid_bps=[1000.0, 900.0],
        sec_avail=[1000], bor_headroom=[1e9, 1e9], bor_exposure=[0, 0], conversion=[0.5, 0.95],
    )
    x, _ = al.optimize(prob, 0.0, 0.0, use_conversion=True)
    assert list(x) == [0, 1000]   # 0.95 x 900 > 0.5 x 1000


def test_minimum_allocations_are_honoured_when_feasible_and_dropped_when_not():
    base = dict(sec=[0, 0], bor=[0, 1], qty=[1000, 1000], price=[10.0, 10.0], bid_bps=[1000.0, 100.0],
                bor_headroom=[1e9, 1e9], bor_exposure=[0, 0])
    prob = al.AllocationProblem(**base, sec_avail=[1000], min_qty=[0, 300])
    x, info = al.optimize(prob, 0.0, 0.0, use_min=True)
    assert list(x) == [700, 300] and not info["min_alloc_dropped"]
    prob = al.AllocationProblem(**base, sec_avail=[200], min_qty=[0, 300])
    x, info = al.optimize(prob, 0.0, 0.0, use_min=True)
    assert info["min_alloc_dropped"] and al.violations(prob, x) == []


def test_book_share_cap_applies_against_start_of_day_book():
    prob = al.AllocationProblem(
        sec=[0], bor=[0], qty=[10_000], price=[100.0], bid_bps=[500.0], sec_avail=[10_000],
        bor_headroom=[1e9], bor_exposure=[900_000], book_base=10_000_000, book_share_cap=0.15,
    )
    for x in (al.greedy(prob), al.optimize(prob, 0, 0)[0], al.pro_rata(prob)):
        assert x[0] == 6000          # (0.15 x 10m - 0.9m) / $100
        assert al.violations(prob, x) == []


def test_borrower_already_over_cap_gets_nothing_and_does_not_force_others():
    prob = al.AllocationProblem(
        sec=[0, 0], bor=[0, 1], qty=[100, 100], price=[10.0, 10.0], bid_bps=[100.0, -5.0],
        sec_avail=[1000], bor_headroom=[1e9, 1e9], bor_exposure=[5e6, 0], book_base=1e7, book_share_cap=0.15,
    )
    x, info = al.optimize(prob, 0.0, 0.0)
    assert info["status"] == "optimal"
    assert list(x) == [0, 0]
