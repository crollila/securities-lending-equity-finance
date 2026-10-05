# Resume bullets

These are written so that every claim is true of this repository. It is a simulator built on
SIMULATED data, not a book I ran. Each figure can be traced to `results/` after running `seclend run`.

## Short version (pick 2–3)

- Built a day-by-day **securities-lending book simulator** in Python. It covers inventory, borrower
  demand, fee and rebate economics, cash and non-cash collateral with haircuts, daily mark-to-market
  and netted margin calls, recalls, returns, settlement fails, buy-ins and corporate actions, on a
  SIMULATED 300-security, 36-counterparty book with about $50bn of lendable inventory.
- Formulated scarce-inventory allocation as a **linear programme** (HiGHS) that maximises
  conversion-weighted, recall-risk-adjusted lending revenue. It is subject to inventory,
  counterparty-limit, book-share and single-name concentration constraints, plus optional
  relationship minimums. Across 20 paired market paths it beat first-come-first-served by **+0.8%**
  (base) and **+2.4%** (short squeeze) of net revenue, winning 20/20 paths, and beat pro-rata by
  **+0.5% / +1.6%**.
- Designed a **short-squeeze stress**: 99.5% utilization, fees from 650 to 2,950 bps, 3x price gaps,
  owner-driven recalls, and margin raised to 125%. Showed revenue rising 18% while fail, recall and
  buy-in costs rose 10x, and traced about half of those costs to the lender's own late deliveries.
- Reported a **negative result**: highest-bid greedy matches the LP on revenue (−0.06%, 95% CI
  [−0.12, −0.00]). Explained why with a same-state decision study and limit sweeps. The optimizer's
  value lies in risk (27% lower expected recall cost at 0.67% revenue cost) and tight-limit regimes
  (+9% vs FCFS).

## Longer version (project section)

**Securities Lending & Equity Finance Simulator**, independent project (Python, NumPy, SciPy/HiGHS, pandas)

- Modelled the full agency-lending loan lifecycle on a SIMULATED book: locate → allocation →
  borrow → collateral → daily mark-to-market → recall or return → fail → buy-in. This includes
  splits, manufactured dividends, rating-based margin add-ons and counterparty-level netting with a
  minimum transfer amount.
- Implemented GC-versus-hard-to-borrow fee dynamics with a utilization-driven fee curve, negative
  rebates, a reserve-fee treatment of inventory opportunity cost, and separate fee and
  reinvestment-spread revenue legs.
- Compared FCFS, pro-rata, greedy and two LP allocators under common random numbers. Evidence came
  at three levels: same-state daily decisions, full-quarter runs, and 20-path paired confidence
  intervals.
- Wrote 126 tests covering fee, rebate and collateral arithmetic, margin calls after price shocks,
  recall accounting, inventory restoration, corporate-action adjustments, daily inventory
  invariants, and LP-dominance properties. CI re-runs the experiments and fails if the published
  tables drift.

## Interview talking points

- Why a negative rebate exists, and why the lender's take on a hard-to-borrow loan can be 20x or
  more its take on GC.
- Why sorting by bid is nearly revenue-optimal when every binding constraint is measured in shares
  or dollars, and the specific counterexample (a counterparty limit binding across two names) where
  it fails.
- Why a 27% ex-ante reduction in expected recall cost barely moves realised costs over a quarter:
  costs that can't be allocated away, lumpy buy-ins, and market-wide scarcity.
- What I would build next: a rolling-horizon allocator that reserves limit room for expected future
  bids, and estimating borrower fail models from data rather than assuming them.
