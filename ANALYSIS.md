# Analysis

This is the technical write-up behind the README. Every number comes from `seclend run` with the
default `Params()` (universe seed 2026, path seed 7, 20 robustness paths) and can be traced to a
file in [`results/`](results/). **All counterparties, positions, balances and AUM are SIMULATED.**

## 1. Questions

1. Does a constrained optimizer allocate scarce lendable inventory better than the rules desks
   actually use: first come, first served (FCFS), pro-rata, and greedy by highest bid?
2. How do the answers change in a short squeeze?
3. What do the book's economics and risk metrics look like under each regime, and which ones
   does allocation actually move?

## 2. Experimental design

**Book.** 300 SIMULATED securities: 276 GC, 11 warm and 13 special on day 0 of the reporting window.
Specials are drawn only from small and mid caps. Lendable inventory is $49.6bn at generation and
averages $36.8bn over the window. There are 36 SIMULATED counterparties of six types, with exposure
limits summing to $10.7bn. Each run simulates 45 burn-in days (so the book is seasoned) followed by
a 63-business-day reporting quarter.

**Common random numbers.** For a given scenario and seed, every method sees identical price,
utilization, demand, owner-sale, corporate-action and behavioural draws. Behavioural uniforms are
indexed by (day, security, borrower), so the same borrower makes the same return, fail and
conversion decision on the same day in every run. Paired differences between methods therefore
measure decisions, not noise in the environment. The noise that remains comes from path dependence:
a different allocation today changes which loans exist tomorrow.

**Three levels of evidence.**

| Level | What varies | What it isolates | File |
|---|---|---|---|
| Decision study | nothing: each recorded daily problem is re-solved by every method from the identical state | quality of the allocation decision | `decision_study_*.csv` |
| Scenario run | method, one path | full quarter: decision plus its consequences | `summary_by_method.csv` |
| Robustness | method × 20 paths × 2 scenarios | sampling uncertainty of the quarter-level comparison | `robustness_*.csv` |

Two sweeps add sensitivity analysis: counterparty-limit scale (0.25x to 2x, 5 paths) and
recall-risk aversion λ (0 to 30, 5 paths, stress).

## 3. Base case

| Metric | FCFS | Pro-rata | Greedy | LP gross | **LP risk-adj.** |
|---|---:|---:|---:|---:|---:|
| Gross revenue | $6.429m | $6.426m | $6.447m | $6.447m | $6.443m |
| Fail/recall/buy-in costs | $21k | $25k | $24k | $24k | $23k |
| Net revenue | $6.408m | $6.402m | $6.423m | $6.423m | $6.420m |
| Average fee (bps) | 34.35 | 34.41 | 34.57 | 34.57 | 34.54 |
| Locate fill, value / count | 87.2% / 89.9% | 87.3% / 91.7% | 87.2% / 90.2% | 87.3% / 89.9% | 87.2% / 89.8% |
| HHI / top-5 share | 0.037 / 29.3% | 0.037 / 29.3% | 0.037 / 29.3% | 0.037 / 29.3% | 0.037 / 29.3% |
| Recalls / late / buy-ins | 57 / 8 / 0 | 72 / 12 / 0 | 65 / 11 / 0 | 65 / 11 / 0 | 63 / 11 / 0 |

Economics of the risk-adjusted LP run:

- Revenue splits into $5.12m of fee and $1.32m of reinvestment spread.
- Utilization is 16.8% of an average $36.8bn lendable.
- Warm and special names are 2.3% of on-loan value but 26% of fee income. The top 10% of names
  earn 66%.
- Fill rate by tier: GC 89%, warm 93%, special 30%, and 16% on days a name trades as HTB.
- 667 counterparty-level margin calls move $1.26bn. Peak uncovered exposure from missed calls
  is $5.6m.
- Four counterparties end at their limit. Passive excess from price moves peaks at 1.02x of limit.
- Manufactured dividends of $5.96m pass through and are not counted as revenue.

On one path the methods sit within 0.3% of each other. The 20-path paired comparison:

| Optimizer minus | mean | 95% CI | wins |
|---|---:|---:|---:|
| FCFS | +0.82% ($61k) | [+0.65, +0.99] | 20/20 |
| Pro-rata | +0.51% | [+0.38, +0.64] | 18/20 |
| Greedy | −0.06% | [−0.12, −0.00] | 7/20 |

**Decision study (base).** The LP's objective (expected, conversion-weighted revenue minus expected
recall cost plus relationship tilt) is +1.08% over FCFS, +0.45% over pro-rata and +0.05% over
greedy. The LP is never beaten on any of the 63 days, within the one-share-per-request rounding
tolerance. Most of the value in pricing-aware allocation is captured by sorting by bid.

## 4. Short-squeeze stress

From day 20, eight crowded small and mid caps (`SIM044, 051, 127, 185, 200, 213, 265, 266`) go
through the following:

- **Utilization and fees.** Street utilization ramps to 99.5% by day 34. The basket fee goes from
  650 to 2,950 bps.
- **Prices.** Prices run up 2.97x (geometric mean), with single-day gaps of up to +57%, then
  partially collapse.
- **Owners.** Owners sell on 10% of days, and other specials see twice the normal owner-sale rate.
  Squeeze-name lendable falls from about $56m to $18m.
- **Margin.** Squeeze-name margin rises to 125% on day 22. Missed-call probability triples.
- **Demand and covering.** Borrow demand rises 1.8x, and voluntary returns peak at 10% a day.
- **Settlement.** Lender delivery fails rise from 0.5% to 3% of new loans.

| Metric (risk-adjusted LP) | Base | Stress |
|---|---:|---:|
| Gross revenue | $6.44m | $7.62m (+18%) |
| Average fee | 34.5 bps | 39.9 bps |
| Recalls / late returns / buy-ins | 63 / 11 / 0 | 147 / 40 / 6 |
| Settlement fails (recall + delivery) | 44 | 199 |
| Fail, recall and buy-in costs | $23k | $236k |
| Peak uncovered exposure | $5.6m | $11.7m |
| Worst day of margin calls | — | $146m |

Breaking down the $236k: $118k is the lender's own delivery fails, $79k is six buy-ins (one alone
cost $66k), and $39k is recall notices and borrower fail-day charges. Delivery fails have the same
probability under every method, so roughly half of stress costs cannot be allocated away.

| Optimizer minus (stress, 20 paths) | mean | 95% CI | wins |
|---|---:|---:|---:|
| FCFS | +2.45% ($230k) | [+1.62, +3.28] | 20/20 |
| Pro-rata | +1.60% | [+0.99, +2.21] | 19/20 |
| Greedy | −0.11% | [−0.49, +0.27] | 9/20 |

The price-aware edge triples in the squeeze because fee dispersion matters more when fees are in
the thousands of bps. FCFS hands scarce squeeze inventory to whoever asks first, at whatever they
bid.

**Decision study (stress).** The LP allocates the same $20.1bn of daily requests as greedy, at
5,176 vs 7,125 $/day of **expected recall cost (−27%)**, while giving up 0.67% of gross revenue.
Its objective is +0.73% over greedy, +2.50% over pro-rata and +3.46% over FCFS.

**Realised vs expected recall cost.** Over 20 paths the optimizer's realised fail/recall cost is
−0.1% vs greedy, with a 95% CI of −3.3% to +3.0%. There are three reasons the ex-ante saving does
not show up:

1. About half of the cost is not allocatable (delivery fails).
2. Buy-ins are rare and lumpy: about six a quarter, and one buy-in can outweigh the entire
   expected-cost difference.
3. Near 100% utilization the scarcity multiplier applies to every borrower. Borrower-specific
   recall sensitivity, the only part allocation can choose, is the smaller factor.

**Risk-aversion sweep (stress, 5 paths).**

| λ | realised cost vs greedy | revenue vs greedy |
|---:|---:|---:|
| 0 | −1.8% | +0.06% |
| 1 | −1.9% | −0.12% |
| 3 | −2.0% | −0.89% |
| 10 | −3.7% | −2.61% |
| 30 | −3.1% | −4.85% |

At λ = 0 the LP keeps conversion-awareness but drops the risk term. It is the only setting that
beats greedy on net revenue, by +$9k on average. Every step above λ = 1 loses more revenue than it
saves. In this calibration, collateral (125% margin) and call discipline are the effective squeeze
defences. A heavier risk penalty in the allocator is not.

## 5. Why greedy-by-bid is nearly optimal

Within one name all requests share a price, so revenue per share (inventory constraint) and revenue
per dollar (counterparty and concentration constraints) both rank requests by the bid. When every
binding constraint agrees with the objective's ranking, the greedy solution is the LP solution up to
interactions between constraints. Greedy loses only when those interactions bind. In the
two-borrower test case (`tests/test_allocation.py`), a limit-bound borrower blocks a second name,
and greedy earns $500k against the LP's $925k. In the simulated book such configurations are rare,
because scarce names have many bidders and limits bind for only 4.7% of requests.

The limit sweep confirms the mechanism:

| Limit scale | vs FCFS | vs pro-rata | vs greedy |
|---|---:|---:|---:|
| 0.25x | +9.1% | +1.0% | −0.12% |
| 0.5x | +2.5% | +0.7% | −0.25% |
| 1x | +1.0% | +0.7% | −0.04% |
| 2x | +0.7% | +0.7% | −0.03% |

Tightening limits makes FCFS far more expensive, because limit room is wasted on low-fee early
arrivals. Greedy barely moves. Policy constraints are cheap: guaranteeing borrowers with
relationship ≥ 0.85 a quarter of their request costs 0.07% of net revenue in both scenarios.

## 6. Validation

There are 126 tests in `tests/`. They cover:

- **Arithmetic.** Rebate = benchmark − fee, ACT/360 accruals, cash-collateral legs reconciling to
  (fee + spread) × collateral, non-cash fees on loan value, required collateral with haircuts,
  mark-to-market calls after a $50 → $60 shock and returns after a fall, fee-curve shape, buy-in
  shortfall and recovery, split value preservation, manufactured dividends, HHI.
- **Allocation.** Across 25 random problems, all five methods respect inventory, counterparty
  limits, book-share and single-name caps, request size and whole-share outputs. The LP is never
  worse than greedy when only inventory binds, and never worse than any method on its own objective
  (all heuristics are feasible for the LP). The tests also cover the two-borrower counterexample,
  risk-based decline, conversion weighting, minimum allocations (honoured when feasible, dropped
  cleanly when not) and the book-share cap.
- **Simulation.** A `CheckedBook` asserts every day, for 5 methods × 2 scenarios, that active
  on-loan shares never exceed lendable. The tests also check determinism, daily revenue
  reconciliation, recall accounting (notices, outcomes, fails, buy-ins and the cost identity), that
  returns restore inventory, that splits rescale loans exactly including recall split-offs, that
  manufactured dividends are paid, that margin calls fire after shocks, and that stress raises
  recalls and fees.
- **Runtime guard.** Every allocation in every run passes `allocation.violations()`, otherwise the
  simulator raises.
- **Reproducibility.** CI re-runs the scenarios and compares the committed `summary_by_method.csv`
  and `decision_study_summary.csv` within a relative tolerance of 1e-3.

## 7. What would change the conclusions

- **Heterogeneous, observable borrower risk.** If fail behaviour were more borrower-specific and
  less market-wide, the risk term would bite in realised costs, not just expected ones.
- **A multi-period allocator.** Reserving limit room and inventory for expected future bids is the
  one structural advantage greedy cannot copy.
- **Real data.** Fee dispersion, conversion rates and fail rates by counterparty are the inputs that
  decide whether the LP's extra terms pay. All three are synthetic here.
