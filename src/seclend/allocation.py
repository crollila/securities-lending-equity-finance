"""Allocating scarce lendable inventory across competing locate requests.

Four methods are implemented against the same problem object:

* ``fcfs``      - first come, first served: fill locates in the order they arrive,
                  the default behaviour of a locate queue.
* ``greedy``    - fill requests in descending bid order (ties: relationship), the
                  way a desk working a queue by price would. Respects every hard
                  constraint but decides one request at a time.
* ``pro_rata``  - split each name's available shares in proportion to requested
                  size, a common "fair" policy for scarce names.
* ``optimize``  - a linear programme solved with HiGHS that maximises
                  risk-adjusted daily revenue over all requests jointly.

Decision variable: x_i = shares granted to request i (one request per
borrower b(i) per security s(i) per day).

    maximise   sum_i  x_i * p_i * (conv_i * (bid_i - lambda * recall_cost_i) + tilt * rel_i) / 1e4 / 360

conv_i is the borrower's historical locate-to-borrow conversion rate: a locate
that is approved but never used earns nothing and leaves the shares idle that day.
    subject to sum_{i in s}  x_i            <= avail_s                       (inventory)
               sum_{i in b}  p_i x_i        <= limit_b - exposure_b          (counterparty limit)
               sum_{i in b}  p_i x_i        <= c * Book - exposure_b         (book-share concentration)
               0 <= x_i <= min(qty_i, name_cap_s - held_{s,b})               (request size, single-name cap)
               x_i >= min_i                       (optional relationship minimums)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

DAILY = 1e-4 / 360.0  # bps-annualised -> fraction per day


@dataclass
class AllocationProblem:
    sec: np.ndarray            # (n,) security index per request
    bor: np.ndarray            # (n,) borrower index per request
    qty: np.ndarray            # (n,) shares requested
    price: np.ndarray          # (n,)
    bid_bps: np.ndarray        # (n,) fee the borrower will pay
    sec_avail: np.ndarray      # (S,) shares available to lend today
    bor_headroom: np.ndarray   # (B,) $ remaining under counterparty limit
    bor_exposure: np.ndarray   # (B,) $ currently on loan to borrower
    book_base: float = 0.0     # start-of-day book size the share cap is measured against
    book_share_cap: float = 1.0
    pair_headroom: np.ndarray | None = None   # (n,) shares allowed by single-name cap
    penalty_bps: np.ndarray | None = None     # (n,) expected recall cost, bps annualised
    relationship: np.ndarray | None = None    # (n,)
    min_qty: np.ndarray | None = None         # (n,)
    conversion: np.ndarray | None = None      # (n,) P(approved locate becomes a borrow)
    info: dict = field(default_factory=dict)

    def __post_init__(self):
        n = len(self.sec)
        self.sec = np.asarray(self.sec, int)
        self.bor = np.asarray(self.bor, int)
        for name in ("qty", "price", "bid_bps"):
            setattr(self, name, np.asarray(getattr(self, name), float))
        self.sec_avail = np.floor(np.maximum(np.asarray(self.sec_avail, float), 0.0))
        self.bor_headroom = np.maximum(np.asarray(self.bor_headroom, float), 0.0)
        self.bor_exposure = np.asarray(self.bor_exposure, float)
        if self.pair_headroom is None:
            self.pair_headroom = np.full(n, np.inf)
        if self.penalty_bps is None:
            self.penalty_bps = np.zeros(n)
        if self.relationship is None:
            self.relationship = np.zeros(n)
        if self.min_qty is None:
            self.min_qty = np.zeros(n)
        if self.conversion is None:
            self.conversion = np.ones(n)
        for name in ("pair_headroom", "penalty_bps", "relationship", "min_qty", "conversion"):
            setattr(self, name, np.asarray(getattr(self, name), float))

    def bor_room(self):
        """$ each borrower may still take: min(counterparty limit room, book-share room)."""
        if self.book_share_cap >= 1.0:
            return self.bor_headroom.copy()
        share_room = np.maximum(self.book_share_cap * self.book_base - self.bor_exposure, 0.0)
        return np.minimum(self.bor_headroom, share_room)

    @property
    def n(self):
        return len(self.sec)

    def upper(self):
        return np.floor(np.maximum(np.minimum(self.qty, self.pair_headroom), 0.0))


# --------------------------------------------------------------------- scoring
def revenue(prob: AllocationProblem, x) -> float:
    """Gross daily fee revenue ($/day) of an allocation at the borrowers' bids."""
    return float(np.sum(np.asarray(x) * prob.price * prob.bid_bps) * DAILY)


def expected_revenue(prob: AllocationProblem, x) -> float:
    """Gross daily revenue weighted by each borrower's locate-to-borrow conversion."""
    return float(np.sum(np.asarray(x) * prob.price * prob.bid_bps * prob.conversion) * DAILY)


def objective_coef(prob: AllocationProblem, risk_aversion=1.0, tilt_bps=0.0, use_conversion=True):
    conv = prob.conversion if use_conversion else 1.0
    return prob.price * (conv * (prob.bid_bps - risk_aversion * prob.penalty_bps)
                         + tilt_bps * prob.relationship) * DAILY


def risk_adjusted_revenue(prob: AllocationProblem, x, risk_aversion=1.0, tilt_bps=0.0, use_conversion=True) -> float:
    """Expected daily revenue net of expected recall cost (the optimizer's objective)."""
    return float(np.asarray(x) @ objective_coef(prob, risk_aversion, tilt_bps, use_conversion))


def violations(prob: AllocationProblem, x, tol_shares=1e-6, tol_dollars=1.0) -> list[str]:
    """Every hard-constraint breach in an allocation (empty list = feasible)."""
    x = np.asarray(x, float)
    out = []
    if np.any(x < -tol_shares):
        out.append("negative allocation")
    if np.any(x > prob.qty + tol_shares):
        out.append("allocation exceeds request")
    if np.any(x > np.maximum(prob.pair_headroom, 0.0) + tol_shares):
        out.append("single-name concentration cap")
    by_sec = np.bincount(prob.sec, weights=x, minlength=len(prob.sec_avail))
    if np.any(by_sec > prob.sec_avail + tol_shares):
        out.append("inventory")
    val = x * prob.price
    by_bor = np.bincount(prob.bor, weights=val, minlength=len(prob.bor_headroom))
    if np.any(by_bor > prob.bor_headroom + tol_dollars):
        out.append("counterparty limit")
    if prob.book_share_cap < 1.0:
        bad = (by_bor > 0) & (prob.bor_exposure + by_bor > prob.book_share_cap * prob.book_base + tol_dollars)
        if bad.any():
            out.append("book-share concentration")
    return out


# --------------------------------------------------------------------- greedy
def greedy(prob: AllocationProblem, order=None) -> np.ndarray:
    """Fill requests one at a time in descending bid order."""
    x = np.zeros(prob.n)
    ub = prob.upper()
    avail = prob.sec_avail.copy()
    head = prob.bor_room()
    if order is None:
        order = np.lexsort((np.arange(prob.n), -prob.relationship, -prob.bid_bps))
    for i in order:
        s, b, px = prob.sec[i], prob.bor[i], prob.price[i]
        q = np.floor(min(ub[i], avail[s], head[b] / px) + 1e-9)
        if q <= 0:
            continue
        x[i] = q
        avail[s] -= q
        head[b] -= q * px
    return x


def fcfs(prob: AllocationProblem, arrival) -> np.ndarray:
    """First come, first served: greedy in arrival order instead of bid order."""
    return greedy(prob, order=np.argsort(np.asarray(arrival), kind="stable"))


# --------------------------------------------------------------------- pro rata
def pro_rata(prob: AllocationProblem, rounds=6) -> np.ndarray:
    """Split each name's inventory in proportion to request size.

    Names are processed in descending average-bid order (scarce names first),
    and shares a borrower cannot take (because of its own limits) are re-offered
    to the others for a few rounds.
    """
    x = np.zeros(prob.n)
    ub = prob.upper()
    head = prob.bor_room()
    secs = np.unique(prob.sec)
    avg_bid = np.array([prob.bid_bps[prob.sec == s].mean() for s in secs])
    for s in secs[np.argsort(-avg_bid, kind="stable")]:
        idx = np.flatnonzero(prob.sec == s)
        remaining = prob.sec_avail[s]
        for _ in range(rounds):
            want = ub[idx] - x[idx]
            live = want > 0
            if remaining < 1 or not live.any():
                break
            if want[live].sum() <= remaining:
                share = want
            else:
                share = np.where(live, remaining * want / want[live].sum(), 0.0)
            gave = 0.0
            for k, i in enumerate(idx):
                if share[k] <= 0:
                    continue
                b, px = prob.bor[i], prob.price[i]
                q = np.floor(min(share[k], want[k], head[b] / px) + 1e-9)
                if q <= 0:
                    ub[i] = x[i]   # borrower is capped out; stop offering it shares
                    continue
                x[i] += q
                head[b] -= q * px
                gave += q
            remaining -= gave
            if gave == 0:
                break
    return x


# --------------------------------------------------------------------- optimizer
def optimize(prob: AllocationProblem, risk_aversion=1.0, tilt_bps=0.0, use_min=False, use_conversion=True):
    """Solve the allocation LP. Returns (x, info).

    risk_aversion=0, tilt_bps=0, use_conversion=False gives the pure gross-revenue LP.
    """
    n = prob.n
    info = {"status": "empty", "min_alloc_dropped": False}
    if n == 0:
        return np.zeros(0), info
    coef = objective_coef(prob, risk_aversion, tilt_bps, use_conversion)
    ub = prob.upper()
    ub = np.where(coef > 0, ub, 0.0)  # never lend at a negative risk-adjusted fee...
    lb = np.zeros(n)
    if use_min:
        lb = np.minimum(np.ceil(prob.min_qty), prob.upper())
        ub = np.maximum(ub, lb)       # ...unless a relationship minimum requires it

    # One inventory row per security, one $-room row per borrower (the tighter of
    # the counterparty limit and the book-share cap, see AllocationProblem.bor_room).
    secs, sec_row = np.unique(prob.sec, return_inverse=True)
    bors, bor_row = np.unique(prob.bor, return_inverse=True)
    rows = np.concatenate([sec_row, len(secs) + bor_row])
    cols = np.concatenate([np.arange(n), np.arange(n)])
    vals = np.concatenate([np.ones(n), prob.price])
    A = sparse.csr_matrix((vals, (rows, cols)), shape=(len(secs) + len(bors), n))
    rhs = [prob.sec_avail[secs], prob.bor_room()[bors]]
    b_ub = np.concatenate(rhs)

    def _solve(lo):
        return linprog(-coef, A_ub=A, b_ub=b_ub, bounds=np.column_stack([lo, np.maximum(ub, lo)]), method="highs")

    res = _solve(lb)
    if res.status != 0 and use_min:
        info["min_alloc_dropped"] = True
        res = _solve(np.zeros(n))
    info["status"] = "optimal" if res.status == 0 else f"failed:{res.status}"
    x = np.floor(np.maximum(res.x, 0.0) + 1e-6) if res.status == 0 else np.zeros(n)
    x = np.minimum(x, ub)
    info["objective"] = float(coef @ x)
    return x, info


def rejection_reasons(prob: AllocationProblem, x, optimizer=False) -> np.ndarray:
    """Best-effort attribution of why each request was not filled in full."""
    x = np.asarray(x, float)
    ub = prob.upper()
    used_sec = np.bincount(prob.sec, weights=x, minlength=len(prob.sec_avail))
    left_sec = prob.sec_avail - used_sec
    used_bor = np.bincount(prob.bor, weights=x * prob.price, minlength=len(prob.bor_headroom))
    left_bor = prob.bor_room() - used_bor
    out = np.empty(prob.n, dtype=object)
    for i in range(prob.n):
        if x[i] >= prob.qty[i] - 1e-9:
            out[i] = "filled"
        elif ub[i] <= x[i] and prob.pair_headroom[i] < prob.qty[i]:
            out[i] = "single-name cap"
        elif left_sec[prob.sec[i]] < 1:
            out[i] = "no inventory"
        elif left_bor[prob.bor[i]] < prob.price[i]:
            hit_share = prob.book_share_cap < 1.0 and (
                prob.book_share_cap * prob.book_base - prob.bor_exposure[prob.bor[i]] < prob.bor_headroom[prob.bor[i]])
            out[i] = "book-share cap" if hit_share else "counterparty limit"
        elif optimizer and prob.bid_bps[i] - prob.penalty_bps[i] <= 0:
            out[i] = "recall risk > fee"
        else:
            out[i] = "optimizer preferred others" if optimizer else "lower priority"
    return out
