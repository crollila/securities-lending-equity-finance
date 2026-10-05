"""Daily simulation of a SIMULATED agency securities-lending book.

One business day, in order:

1. corporate actions   - splits rescale shares; dividends create manufactured payments
2. inventory           - beneficial owners buy/sell, changing lendable quantity
3. failing returns     - recalled loans that missed their date cure, keep failing, or get bought in
4. due recalls         - recalls issued yesterday settle (T+1) or fail
5. voluntary returns   - borrowers cover shorts and hand shares back
6. recalls             - if on-loan > lendable, recall the cheapest loans first
7. mark-to-market      - re-price every loan, call or return collateral
8. accrual             - fee + reinvestment income on every settled loan
9. new business        - locate requests -> allocation -> borrows -> collateral in
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import allocation as al
from .config import Params
from .economics import hhi, lender_daily_income, recall_fail_probability, required_collateral
from .market import MarketPaths

ACTIVE, RECALLED, FAILING, CLOSED = 0, 1, 2, 3
METHODS = ("fcfs", "greedy", "pro_rata", "lp_revenue", "optimized", "optimized_min")
METHOD_LABELS = {
    "fcfs": "Greedy, first come first served",
    "greedy": "Greedy, highest bid first",
    "pro_rata": "Pro-rata",
    "lp_revenue": "LP, gross revenue only",
    "optimized": "LP, risk-adjusted (expected)",
    "optimized_min": "LP, risk-adjusted + minimums",
}


class Ledger:
    """Column-oriented loan ledger (one row per loan contract)."""

    FIELDS = {
        "sec": int, "bor": int, "shares": float, "fee": float, "coll": float,
        "cash": bool, "haircut": float, "status": int, "due": int, "fail_days": int,
        "start": int, "opened": int,
    }

    def __init__(self, capacity=4096):
        self.n = 0
        self.cap = capacity
        for f, dt in self.FIELDS.items():
            setattr(self, f, np.zeros(capacity, dtype=dt))

    def _grow(self, need):
        while self.n + need > self.cap:
            self.cap *= 2
        for f, dt in self.FIELDS.items():
            old = getattr(self, f)
            new = np.zeros(self.cap, dtype=dt)
            new[: len(old)] = old
            setattr(self, f, new)

    def add(self, **cols) -> np.ndarray:
        k = len(cols["sec"])
        if self.n + k > self.cap:
            self._grow(k)
        sl = slice(self.n, self.n + k)
        for f in self.FIELDS:
            getattr(self, f)[sl] = cols.get(f, 0)
        self.n += k
        return np.arange(sl.start, sl.stop)

    def view(self, f):
        return getattr(self, f)[: self.n]

    def open_mask(self):
        return self.view("status") != CLOSED

    def split(self, i, keep_shares):
        """Split loan i into (i: keep_shares) and a new loan holding the rest; returns new index."""
        total = self.shares[i]
        frac = (total - keep_shares) / total
        cols = {f: np.array([getattr(self, f)[i]]) for f in self.FIELDS}
        cols["shares"] = np.array([total - keep_shares])
        cols["coll"] = np.array([self.coll[i] * frac])
        j = self.add(**cols)[0]
        self.shares[i] = keep_shares
        self.coll[i] *= 1 - frac
        return j


@dataclass
class RunResult:
    scenario: str
    method: str
    daily: pd.DataFrame
    events: pd.DataFrame
    locates: pd.DataFrame
    positions: pd.DataFrame
    summary: dict


class Book:
    def __init__(self, p: Params, universe: pd.DataFrame, borrowers: pd.DataFrame, paths: MarketPaths, method: str,
                 record_problems: bool = False):
        if method not in METHODS:
            raise ValueError(method)
        self.p, self.u, self.b, self.m, self.method = p, universe, borrowers, paths, method
        self.S, self.B = len(universe), len(borrowers)
        self.lendable = universe["lendable_qty"].to_numpy(float).copy()
        self.L = Ledger()
        b = borrowers
        self.limit = b["exposure_limit"].to_numpy(float)
        self.rel = b["relationship"].to_numpy(float)
        self.sens = b["recall_sensitivity"].to_numpy(float)
        self.conv = b["conversion"].to_numpy(float)
        self.hazard = b["return_hazard"].to_numpy(float)
        self.cash_b = (b["collateral"] == "cash").to_numpy()
        self.addon = b["rating"].map(p.coll.rating_addon).to_numpy(float)
        self.miss_mult = b["rating"].map({"A": 1.0, "BBB": 2.0, "BB": 4.0}).to_numpy(float)
        self.sale_seen = np.zeros((paths.n_days, self.S), bool)
        self.daily, self.events, self.locates = [], [], []
        self.record_problems = record_problems
        self.problems = []   # (day, AllocationProblem, arrival) when record_problems

    # ------------------------------------------------------------ helpers
    def _on_loan_by_sec(self, statuses=(ACTIVE, RECALLED, FAILING)):
        L = self.L
        m = np.isin(L.view("status"), statuses)
        return np.bincount(L.view("sec")[m], weights=L.view("shares")[m], minlength=self.S)

    def _exposure_by_bor(self, price):
        L = self.L
        m = L.open_mask()
        val = L.view("shares")[m] * price[L.view("sec")[m]]
        return np.bincount(L.view("bor")[m], weights=val, minlength=self.B)

    def _margin(self, t, sec, bor):
        return self.m.margin[t, sec] + self.addon[bor]

    def _event(self, t, kind, i, amount=0.0, cost=0.0):
        L = self.L
        self.events.append((t - self.m.burn_in, kind, int(L.sec[i]), int(L.bor[i]),
                            float(L.shares[i]), float(amount), float(cost), int(L.opened[i]) - self.m.burn_in))

    # ------------------------------------------------------------ main loop
    def run(self) -> RunResult:
        for t in range(self.m.n_days):
            self._step(t)
        return self._result()

    def _step(self, t):
        p, m, L = self.p, self.m, self.L
        P = m.price[t]
        dc = m.daycount[t]
        rec = dict(t=t - m.burn_in, manufactured_div=0.0, recall_cost=0.0, fail_charges=0.0,
                   buyin_loss=0.0, recalls_n=0, recalled_value=0.0, recall_fails_n=0, buyins_n=0,
                   returns_n=0, returned_value=0.0, delivery_fails_n=0, calls_n=0, calls_value=0.0,
                   calls_missed_n=0, uncovered_exposure=0.0, collateral_returned=0.0)

        # 1. corporate actions -------------------------------------------------
        r = m.split_ratio[t]
        if np.any(r != 1.0):
            o = L.open_mask()
            idx = np.flatnonzero(o)
            L.shares[idx] *= r[L.sec[idx]]
            self.lendable *= r
            for s in np.flatnonzero(r != 1.0):
                self.events.append((t - m.burn_in, "split", int(s), -1, float(r[s]), 0.0, 0.0, t - m.burn_in))
        d = m.dividend[t]
        if np.any(d > 0):
            idx = np.flatnonzero(L.open_mask())
            pay = L.shares[idx] * d[L.sec[idx]]
            rec["manufactured_div"] = float(pay.sum())

        # 2. inventory ---------------------------------------------------------
        self.lendable *= m.owner_factor[t]
        self.lendable = np.floor(self.lendable)
        self.sale_seen[t] = m.owner_factor[t] < 1.0

        # 3. loans already failing a return -----------------------------------
        util = m.street_util[t]
        for i in np.flatnonzero(L.view("status") == FAILING):
            s, b = L.sec[i], L.bor[i]
            pf = recall_fail_probability(p.ops.base_recall_fail, self.sens[b], util[s])
            val = L.shares[i] * P[s]
            if m.unif["fail"][t, s, b] >= pf:
                L.status[i] = CLOSED
                self._event(t, "return_cured", i, val)
                continue
            L.fail_days[i] += 1
            fc = p.ops.fail_charge_daily * val
            rec["fail_charges"] += fc
            if L.fail_days[i] >= p.ops.buyin_after_fail_days:
                cost = L.shares[i] * P[s] * (1 + m.buyin_slip[t, s])
                cover = L.coll[i] * (1 - L.haircut[i])
                loss = max(cost - cover, 0.0) * (1 - p.ops.buyin_recovery) + p.ops.buyin_ops_cost
                rec["buyin_loss"] += loss
                rec["buyins_n"] += 1
                L.status[i] = CLOSED
                self._event(t, "buy_in", i, cost, loss + fc)
            else:
                self._event(t, "fail_day", i, val, fc)

        # 4. recalls due today --------------------------------------------------
        for i in np.flatnonzero((L.view("status") == RECALLED) & (L.view("due") <= t)):
            s, b = L.sec[i], L.bor[i]
            pf = recall_fail_probability(p.ops.base_recall_fail, self.sens[b], util[s])
            val = L.shares[i] * P[s]
            if m.unif["fail"][t, s, b] >= pf:
                L.status[i] = CLOSED
                self._event(t, "recall_returned", i, val)
            else:
                L.status[i] = FAILING
                L.fail_days[i] = 1
                fc = p.ops.fail_charge_daily * val
                rec["fail_charges"] += fc
                rec["recall_fails_n"] += 1
                self._event(t, "recall_fail", i, val, fc)

        # 5. voluntary returns --------------------------------------------------
        act = np.flatnonzero(L.view("status") == ACTIVE)
        if len(act):
            s, b = L.sec[act], L.bor[act]
            h = self.hazard[b] * m.hazard_mult[t, s]
            go = act[m.unif["ret"][t, s, b] < h]
            L.status[go] = CLOSED
            rec["returns_n"] = len(go)
            rec["returned_value"] = float(np.sum(L.shares[go] * P[L.sec[go]]))

        # 6. recalls ------------------------------------------------------------
        on_loan = self._on_loan_by_sec()
        out_recall = self._on_loan_by_sec((RECALLED, FAILING))
        need = on_loan - self.lendable - out_recall
        for s in np.flatnonzero(need > 0.5):
            cand = np.flatnonzero((L.view("status") == ACTIVE) & (L.view("sec") == s))
            cand = cand[np.lexsort((self.rel[L.bor[cand]], L.fee[cand]))]  # cheapest, weakest first
            left = need[s]
            for i in cand:
                if left <= 0.5:
                    break
                if L.shares[i] > left + 0.5:
                    keep = L.shares[i] - np.ceil(left)
                    j = L.split(i, keep)   # j carries the recalled part
                    i = j
                L.status[i] = RECALLED
                L.due[i] = t + p.ops.settlement_days
                left -= L.shares[i]
                rec["recalls_n"] += 1
                rec["recalled_value"] += float(L.shares[i] * P[s])
                rec["recall_cost"] += p.ops.recall_ops_cost
                self._event(t, "recall", i, float(L.shares[i] * P[s]), p.ops.recall_ops_cost)

        # 7. mark-to-market -------------------------------------------------------
        # Loans are marked individually, but collateral moves at counterparty level:
        # each borrower's deficits and excesses are netted, and a call is only made
        # when the net exceeds max(threshold x loan value, minimum transfer amount).
        o = np.flatnonzero(L.open_mask())
        if len(o):
            s, b = L.sec[o], L.bor[o]
            val = L.shares[o] * P[s]
            req = required_collateral(val, self._margin(t, s, b), L.haircut[o])
            diff = req - L.coll[o]
            net = np.bincount(b, weights=diff, minlength=self.B)
            bval = np.bincount(b, weights=val, minlength=self.B)
            thr = np.maximum(p.coll.call_threshold * bval, p.coll.min_transfer)
            call_b = net > thr
            ret_b = -net > thr
            miss_p = p.coll.call_miss_prob * self.miss_mult * (3.0 if m.in_squeeze[t] else 1.0)
            missed_b = call_b & (m.unif["call"][t, 0, :] < miss_p)
            settle_b = (call_b & ~missed_b) | ret_b
            mv = settle_b[b]
            L.coll[o[mv]] = req[mv]          # rebalance every loan of a settling borrower to required
            rec["calls_n"] = int(call_b.sum())
            rec["calls_value"] = float(net[call_b].sum())
            rec["calls_missed_n"] = int(missed_b.sum())
            rec["uncovered_exposure"] = float(net[missed_b].sum())
            rec["collateral_returned"] = float(-net[ret_b].sum())

        # 8. accrual --------------------------------------------------------------
        acc = np.flatnonzero(L.open_mask() & (L.view("start") <= t))
        fee_part = reinv = np.zeros(0)
        if len(acc):
            s = L.sec[acc]
            val = L.shares[acc] * P[s]
            fee_part, reinv = lender_daily_income(val, L.coll[acc], L.fee[acc], L.cash[acc],
                                                  p.econ.benchmark_rate, p.econ.reinvest_spread, dc)
        rec["revenue_fee"] = float(fee_part.sum())
        rec["revenue_reinvest"] = float(reinv.sum())

        # 9. new business -----------------------------------------------------------
        self._new_business(t, rec)

        # ---- daily book statistics (after new business) ----
        o = np.flatnonzero(L.open_mask())
        s, b = L.sec[o], L.bor[o]
        val = L.shares[o] * P[s]
        lend_val = float(np.sum(self.lendable * P))
        by_bor = np.bincount(b, weights=val, minlength=self.B)
        tot = float(val.sum())
        rec.update(
            lendable_value=lend_val, on_loan_value=tot,
            utilization=tot / lend_val if lend_val else 0.0,
            fee_x_value=float(np.sum(val * L.fee[o])),
            n_loans=len(o),
            hhi=hhi(by_bor),
            top5_share=float(np.sort(by_bor)[-5:].sum() / tot) if tot else 0.0,
            max_limit_usage=float(np.max(by_bor / self.limit)),
            limit_excess=float(np.sum(np.maximum(by_bor - self.limit, 0.0))),
            collateral_value=float(L.coll[o].sum()),
            collateral_shortfall=float(np.sum(np.maximum(
                required_collateral(val, self._margin(t, s, b), L.haircut[o]) - L.coll[o], 0.0))),
        )
        sq = m.squeeze
        if len(sq):
            inb = np.isin(s, sq)
            rec["squeeze_on_loan_value"] = float(val[inb].sum())
            rec["squeeze_fee_x_value"] = float(np.sum(val[inb] * L.fee[o][inb]))
            rec["squeeze_lendable_value"] = float(np.sum(self.lendable[sq] * P[sq]))
            rec["squeeze_collateral_value"] = float(L.coll[o][inb].sum())
            rec["squeeze_required_collateral"] = float(np.sum(required_collateral(
                val[inb], self._margin(t, s[inb], b[inb]), L.haircut[o][inb])))
        rec["recall_cost"] += rec["fail_charges"] + rec["buyin_loss"]
        rec["revenue"] = rec["revenue_fee"] + rec["revenue_reinvest"]
        if t >= m.burn_in:
            self.daily.append(rec)
        if t == m.n_days - 1:
            self._final_by_bor = by_bor

    # ------------------------------------------------------------ allocation
    def _penalty_bps(self, t, sec, bor, book_util):
        """Ex-ante expected recall cost per loan-day, annualised bps.

        Uses only information available to the desk on day t: the long-run owner
        sale rate, owner sales seen over the last 10 days, yesterday's street
        utilization and trailing realised volatility.
        """
        p, m = self.p, self.m
        lo = max(t - 10, 0)
        seen = self.sale_seen[lo:t + 1, sec].mean(axis=0) if t > lo else 0.0
        prior = self.u["owner_sale_prob"].to_numpy()[sec]
        sale_hat = 0.5 * prior + 0.5 * seen
        pi = sale_hat * p.ops.recall_mean_fraction * np.minimum(book_util[sec] / 0.75, 1.0)
        u_prev = m.street_util[max(t - 1, 0), sec]
        pf = recall_fail_probability(p.ops.base_recall_fail, self.sens[bor], u_prev)
        r = np.diff(np.log(m.price[max(t - 10, 0):t + 1, :]), axis=0)
        vol = r.std(axis=0)[sec] if r.shape[0] > 2 else self.u["daily_vol"].to_numpy()[sec]
        gap = 2.0 * vol * np.sqrt(p.ops.buyin_after_fail_days)
        margin = m.margin[t, sec] + self.addon[bor]
        shortfall = np.maximum(m.buyin_slip[t, sec] + gap - (margin - 1.0), 0.0) * (1 - p.ops.buyin_recovery)
        cost = p.ops.fail_charge_daily * (1 + pf + pf**2) + pf**2 * shortfall
        return 360e4 * pi * pf * cost

    def _new_business(self, t, rec):
        p, m, L = self.p, self.m, self.L
        P = m.price[t]
        req = m.requests[m.requests["t"] == t]
        n_req = len(req)
        rec.update(n_requests=n_req, requested_value=0.0, approved_value=0.0, n_filled_any=0,
                   borrowed_value=0.0, n_new_loans=0, rejected_reserve=0)
        if n_req == 0:
            return
        sec = req["sec"].to_numpy()
        bor = req["bor"].to_numpy()
        qty = req["shares"].to_numpy(float)
        bid = req["bid_bps"].to_numpy(float)
        px = P[sec]
        rec["requested_value"] = float(np.sum(qty * px))

        reserve = p.alloc.reserve_fraction * m.market_fee[t, sec]
        ok = bid >= reserve
        rec["rejected_reserve"] = int((~ok).sum())

        on_loan = self._on_loan_by_sec()
        avail = np.maximum(self.lendable - on_loan, 0.0)
        expo = self._exposure_by_bor(P)
        o = L.open_mask()
        held = np.zeros((self.S, self.B))
        np.add.at(held, (L.view("sec")[o], L.view("bor")[o]), L.view("shares")[o])
        pair_head = p.alloc.name_cap * self.lendable[sec] - held[sec, bor]
        book_util = np.where(self.lendable > 0, on_loan / np.maximum(self.lendable, 1.0), 1.0)
        pen = self._penalty_bps(t, sec, bor, book_util)
        min_q = np.where(self.rel[bor] >= p.alloc.min_alloc_relationship, p.alloc.min_alloc_fraction * qty, 0.0)

        k = np.flatnonzero(ok)
        prob = al.AllocationProblem(
            sec=sec[k], bor=bor[k], qty=qty[k], price=px[k], bid_bps=bid[k],
            sec_avail=avail, bor_headroom=self.limit - expo, bor_exposure=expo,
            book_base=max(float(expo.sum()), 0.05 * float(np.sum(self.lendable * P))), book_share_cap=p.alloc.book_share_cap,
            pair_headroom=pair_head[k], penalty_bps=pen[k], relationship=self.rel[bor[k]],
            min_qty=min_q[k], conversion=self.conv[bor[k]],
        )
        if self.record_problems and t >= m.burn_in:
            self.problems.append((t - m.burn_in, prob, req["arrival"].to_numpy()[k]))
        meth = self.method
        if meth == "fcfs":
            xk = al.fcfs(prob, req["arrival"].to_numpy()[k])
        elif meth == "greedy":
            xk = al.greedy(prob)
        elif meth == "pro_rata":
            xk = al.pro_rata(prob)
        elif meth == "lp_revenue":
            xk, _ = al.optimize(prob, risk_aversion=0.0, tilt_bps=0.0, use_conversion=False)
        else:
            xk, _ = al.optimize(prob, risk_aversion=p.alloc.recall_risk_aversion,
                                tilt_bps=p.alloc.relationship_tilt_bps, use_min=(meth == "optimized_min"))
        bad = al.violations(prob, xk)
        if bad:
            raise AssertionError(f"{meth} breached constraints on day {t}: {bad}")

        x = np.zeros(n_req)
        x[k] = xk
        reason = np.full(n_req, "bid below reserve", dtype=object)
        reason[k] = al.rejection_reasons(prob, xk, optimizer=meth.startswith("lp") or meth.startswith("opt"))
        rec["approved_value"] = float(np.sum(x * px))
        rec["n_filled_any"] = int((x > 0).sum())
        if t >= m.burn_in:
            self.locates.append(pd.DataFrame({
                "day": t - m.burn_in, "sec": sec, "bor": bor, "requested": qty, "bid_bps": bid,
                "reserve_bps": reserve, "penalty_bps": pen, "approved": x, "reason": reason,
            }))

        # Locates convert to borrows with a borrower-specific probability.
        conv = (x > 0) & (m.unif["conv"][t, sec, bor] < self.conv[bor])
        idx = np.flatnonzero(conv)
        if len(idx) == 0:
            return
        s, b = sec[idx], bor[idx]
        sh = x[idx]
        cash = self.cash_b[b]
        hc = np.where(cash, 0.0, p.coll.govt_haircut)
        coll = required_collateral(sh * P[s], self._margin(t, s, b), hc)
        late = m.unif["deliv"][t, s, b] < m.delivery_fail_prob[t]
        start = t + 1 + late.astype(int)
        new = L.add(sec=s, bor=b, shares=sh, fee=bid[idx], coll=coll, cash=cash, haircut=hc,
                    status=np.full(len(idx), ACTIVE), due=np.zeros(len(idx), int),
                    fail_days=np.zeros(len(idx), int), start=start, opened=np.full(len(idx), t))
        rec["borrowed_value"] = float(np.sum(sh * P[s]))
        rec["n_new_loans"] = len(idx)
        rec["delivery_fails_n"] = int(late.sum())
        fc = p.ops.fail_charge_daily * float(np.sum(sh[late] * P[s[late]]))
        rec["fail_charges"] += fc
        for i in new[late]:
            v = float(L.shares[i] * P[L.sec[i]])
            self._event(t, "delivery_fail", i, v, p.ops.fail_charge_daily * v)

    # ------------------------------------------------------------ results
    def _result(self) -> RunResult:
        daily = pd.DataFrame(self.daily)
        daily["avg_fee_bps"] = np.where(daily["on_loan_value"] > 0, daily["fee_x_value"] / daily["on_loan_value"], 0.0)
        daily["net_revenue"] = daily["revenue"] - daily["recall_cost"]
        if "squeeze_on_loan_value" in daily:
            daily["squeeze_avg_fee_bps"] = np.where(daily["squeeze_on_loan_value"] > 0,
                                                    daily["squeeze_fee_x_value"] / daily["squeeze_on_loan_value"], 0.0)
        events = pd.DataFrame(self.events, columns=["day", "event", "sec", "bor", "shares", "value", "cost",
                                                   "loan_opened_day"])
        events = events[events["day"] >= 0].reset_index(drop=True)
        locates = pd.concat(self.locates, ignore_index=True) if self.locates else pd.DataFrame()
        L = self.L
        o = L.open_mask()
        P = self.m.price[-1]
        positions = pd.DataFrame({
            "sec": L.view("sec")[o], "bor": L.view("bor")[o], "shares": L.view("shares")[o],
            "fee_bps": L.view("fee")[o], "collateral": L.view("coll")[o], "status": L.view("status")[o],
        })
        positions["value"] = positions["shares"] * P[positions["sec"]]
        return RunResult(self.m.scenario, self.method, daily, events, locates, positions,
                         summarize(daily, locates, events, self._final_by_bor))


def summarize(daily: pd.DataFrame, locates: pd.DataFrame, events: pd.DataFrame, final_by_bor) -> dict:
    d = daily
    loan_days = d["on_loan_value"].sum()
    out = {
        "revenue": d["revenue"].sum(),
        "revenue_fee": d["revenue_fee"].sum(),
        "revenue_reinvest": d["revenue_reinvest"].sum(),
        "recall_cost": d["recall_cost"].sum(),
        "net_revenue": d["net_revenue"].sum(),
        "avg_fee_bps": d["fee_x_value"].sum() / loan_days if loan_days else 0.0,
        "avg_utilization": d["on_loan_value"].sum() / d["lendable_value"].sum(),
        "avg_on_loan_value": d["on_loan_value"].mean(),
        "avg_lendable_value": d["lendable_value"].mean(),
        "locate_fill_rate_value": d["approved_value"].sum() / d["requested_value"].sum(),
        "locate_fill_rate_count": d["n_filled_any"].sum() / d["n_requests"].sum(),
        "hhi_avg": d["hhi"].mean(),
        "top5_share_avg": d["top5_share"].mean(),
        "hhi_end": hhi(final_by_bor),
        "recalls_n": int(d["recalls_n"].sum()),
        "recalled_value": d["recalled_value"].sum(),
        "recall_fails_n": int(d["recall_fails_n"].sum()),
        "buyins_n": int(d["buyins_n"].sum()),
        "fail_charges": d["fail_charges"].sum(),
        "buyin_loss": d["buyin_loss"].sum(),
        "delivery_fails_n": int(d["delivery_fails_n"].sum()),
        "settlement_fails_n": int(d["recall_fails_n"].sum() + d["delivery_fails_n"].sum()),
        "fail_days_n": int((events["event"].isin(["recall_fail", "fail_day", "buy_in"])).sum()) if len(events) else 0,
        "calls_n": int(d["calls_n"].sum()),
        "calls_value": d["calls_value"].sum(),
        "calls_missed_n": int(d["calls_missed_n"].sum()),
        "max_uncovered_exposure": d["uncovered_exposure"].max(),
        "max_limit_usage": d["max_limit_usage"].max(),
        "limit_excess_max": d["limit_excess"].max(),
        "manufactured_div": d["manufactured_div"].sum(),
        "returns_n": int(d["returns_n"].sum()),
    }
    if len(locates):
        rs = locates["reason"].value_counts(normalize=True)
        for k, v in rs.items():
            out[f"reason_{k.replace(' ', '_').replace('>', 'gt').replace('-', '_')}"] = v
    return out


def run(p: Params, universe, borrowers, paths, method, record_problems=False):
    book = Book(p, universe, borrowers, paths, method, record_problems)
    res = book.run()
    if record_problems:
        res.problems = book.problems
    return res
