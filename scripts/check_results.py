"""Verify that a fresh run reproduces the committed result tables.

Usage: python scripts/check_results.py <fresh_results_dir> <committed_results_dir>

Compares the scenario summary and the decision study (both independent of the
number of robustness seeds) within a small relative tolerance, so CI fails if
the committed numbers quoted in README.md / ANALYSIS.md drift from the code.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

TABLES = {"summary_by_method.csv": ["scenario", "method"], "decision_study_summary.csv": ["scenario", "method"]}
RTOL = 1e-3


def main(fresh: Path, committed: Path) -> int:
    bad = 0
    for name, keys in TABLES.items():
        a = pd.read_csv(fresh / name).set_index(keys).sort_index()
        b = pd.read_csv(committed / name).set_index(keys).sort_index()
        num = [c for c in b.columns if c in a.columns and pd.api.types.is_numeric_dtype(b[c])]
        diff = ~np.isclose(a[num].to_numpy(float), b[num].to_numpy(float), rtol=RTOL, atol=1e-6, equal_nan=True)
        if diff.any():
            r, c = np.argwhere(diff)[0]
            print(f"{name}: {diff.sum()} values differ, e.g. {b.index[r]} {num[c]}: "
                  f"fresh={a[num].iloc[r, c]} committed={b[num].iloc[r, c]}")
            bad += 1
        else:
            print(f"{name}: {len(num)} columns x {len(b)} rows reproduce within rtol={RTOL}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
