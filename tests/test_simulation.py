"""End-to-end book invariants on a reduced SIMULATED universe."""

from dataclasses import replace

import numpy as np
import pytest

from seclend.config import Params
from seclend.market import build_paths
from seclend.simulation import ACTIVE, CLOSED, FAILING, RECALLED, Book, Ledger, run
from seclend.universe import build_borrowers, build_universe

P = Params(n_securities=90)


@pytest.fixture(scope="module")
def world():
    u, b = build_universe(P), build_borrowers(P)
    return u, b, {s: build_paths(P, u, b, s) for s in ("base", "stress")}


class CheckedBook(Book):
    """Book that asserts its invariants after every simulated day."""

    def _step(self, t):
        L = self.L
        super()._step(t)
        st = L.view("status")
        sec = L.view("sec")
        sh = L.view("shares")
        # Shares not under recall never exceed what owners still make available.
        active = np.bincount(sec[st == ACTIVE], weights=sh[st == ACTIVE], minlength=self.S)
        assert np.all(active <= self.lendable + 1e-6), t
        # Total on loan never exceeds lendable except for recalled shares still in transit.
        total = self._on_loan_by_sec()
        transit = self._on_loan_by_sec((RECALLED, FAILING))
        assert np.all(total - transit <= self.lendable + 1e-6), t
        # Loan records stay well-formed.
        assert np.all(sh[st != CLOSED] > 0)
        assert np.all(L.view("coll")[st != CLOSED] > 0)
        self._checks = getattr(self, "_checks", 0) + 1


@pytest.mark.parametrize("scenario", ["base", "stress"])
@pytest.mark.parametrize("method", ["fcfs", "greedy", "pro_rata", "optimized", "optimized_min"])
def test_inventory_invariants_hold_every_day(world, scenario, method):
    u, b, paths = world
    book = CheckedBook(P, u, b, paths[scenario], method)
    res = book.run()
    assert book._checks == paths[scenario].n_days
    assert res.summary["revenue"] > 0
    # Counterparty limits are checked at allocation; any excess is passive (price moves only).
    assert res.summary["max_limit_usage"] < 1.25


def test_runs_are_deterministic(world):
    u, b, paths = world
    a = run(P, u, b, paths["base"], "optimized").summary
    c = run(P, u, b, paths["base"], "optimized").summary
    assert a == c


def test_daily_revenue_reconciles(world):
    u, b, paths = world
    r = run(P, u, b, paths["base"], "greedy")
    d = r.daily
    assert np.allclose(d["revenue"], d["revenue_fee"] + d["revenue_reinvest"])
    assert np.allclose(d["net_revenue"], d["revenue"] - d["recall_cost"])
    assert r.summary["revenue"] == pytest.approx(d["revenue"].sum())
    assert 0 < r.summary["avg_utilization"] < 1
    assert 0 < r.summary["locate_fill_rate_value"] <= 1


def test_recall_accounting(world):
    u, b, paths = world
    r = run(P, u, b, paths["stress"], "greedy")
    ev = r.events
    recalls = ev[ev["event"] == "recall"]
    assert len(recalls) == r.summary["recalls_n"] > 0
    outcomes = ev[ev["event"].isin(["recall_returned", "recall_fail"])]
    # Every recall issued before the last day settles or fails the next business day.
    assert len(outcomes) >= len(recalls[recalls["day"] < r.daily["t"].max()])
    assert (ev["event"] == "recall_fail").sum() == r.summary["recall_fails_n"]
    assert (ev["event"] == "buy_in").sum() == r.summary["buyins_n"]
    # Costs reconcile: recall cost = ops per notice + fail charges + buy-in losses.
    ops = P.ops.recall_ops_cost * r.summary["recalls_n"]
    assert r.summary["recall_cost"] == pytest.approx(ops + r.summary["fail_charges"] + r.summary["buyin_loss"])


def test_returns_restore_inventory(world):
    u, b, paths = world
    book = Book(P, u, b, paths["base"], "greedy")
    for t in range(paths["base"].burn_in):
        book._step(t)
    L = book.L
    i = int(np.flatnonzero(L.view("status") == ACTIVE)[0])
    s = L.sec[i]
    before = book.lendable[s] - book._on_loan_by_sec()[s]
    L.status[i] = CLOSED          # borrower hands the shares back
    after = book.lendable[s] - book._on_loan_by_sec()[s]
    assert after - before == pytest.approx(L.shares[i])


def test_split_adjusts_loans_and_preserves_value(world):
    u, b, paths = world
    pb = paths["base"]
    splits = np.argwhere(pb.split_ratio != 1.0)
    assert len(splits)
    t, s = (int(v) for v in splits[0])
    book = Book(P, u, b, pb, "greedy")
    for k in range(t):
        book._step(k)
    L = book.L
    n0 = L.n
    o = np.flatnonzero(L.open_mask() & (L.view("sec") == s))
    lend0 = book.lendable[s]
    sh0 = L.shares[o].sum()
    book._step(t)
    r = pb.split_ratio[t, s]
    # Loans open before the split, plus any pieces split off them by a recall today.
    split_offs = np.arange(n0, L.n)[(L.sec[n0:L.n] == s) & (L.opened[n0:L.n] < t)]
    assert L.shares[np.concatenate([o, split_offs])].sum() == pytest.approx(sh0 * r)
    assert book.lendable[s] == pytest.approx(np.floor(lend0 * r * pb.owner_factor[t, s]), abs=1)
    # The traded price is divided by the ratio, so loan value is continuous through the split.
    assert abs(pb.price[t, s] * r / pb.price[t - 1, s] - 1) < 0.25


def test_manufactured_dividends_are_paid(world):
    u, b, paths = world
    r = run(P, u, b, paths["base"], "greedy")
    assert r.summary["manufactured_div"] > 0


def test_margin_calls_after_price_shocks(world):
    u, b, paths = world
    rs = run(P, u, b, paths["stress"], "greedy").daily
    assert rs["calls_n"].sum() > 0
    w = (rs["t"] >= P.stress.start) & (rs["t"] < P.stress.peak)
    # Once margin is raised and prices gap, squeeze-name collateral is called up to the requirement.
    sq_short = rs.loc[w, "squeeze_required_collateral"] - rs.loc[w, "squeeze_collateral_value"]
    assert (sq_short / rs.loc[w, "squeeze_required_collateral"]).max() < 0.25


def test_stress_raises_recalls_and_fees(world):
    u, b, paths = world
    base = run(P, u, b, paths["base"], "greedy").summary
    stress = run(P, u, b, paths["stress"], "greedy").summary
    assert stress["recalls_n"] > base["recalls_n"]
    pb, ps = paths["base"], paths["stress"]
    pk = ps.burn_in + P.stress.peak
    assert ps.market_fee[pk, ps.squeeze].mean() > 2 * pb.market_fee[pk, ps.squeeze].mean()
    assert ps.street_util[pk, ps.squeeze].mean() > 0.98


def test_ledger_split_conserves_shares_and_collateral():
    L = Ledger(capacity=2)
    L.add(sec=np.array([0]), bor=np.array([0]), shares=np.array([1000.0]), fee=np.array([50.0]),
          coll=np.array([10_200.0]), cash=np.array([True]), haircut=np.array([0.0]), status=np.array([ACTIVE]),
          due=np.array([0]), fail_days=np.array([0]), start=np.array([1]), opened=np.array([0]))
    j = L.split(0, 600.0)
    assert L.shares[0] + L.shares[j] == pytest.approx(1000.0)
    assert L.coll[0] + L.coll[j] == pytest.approx(10_200.0)
    assert L.shares[j] == pytest.approx(400.0)


def test_universe_is_deterministic_and_labelled():
    a, b = build_universe(P), build_universe(P)
    assert a.equals(b)
    assert (a["label"] == "SIMULATED").all()
    assert (build_borrowers(P)["label"] == "SIMULATED").all()
    assert a["security"].str.startswith("SIM").all()
    c = build_universe(replace(P, universe_seed=1))
    assert not a["lendable_qty"].equals(c["lendable_qty"])
