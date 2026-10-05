"""Command line entry point: `seclend run` reproduces every table, figure and headline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Params


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seclend", description="SIMULATED securities-lending book: experiments and figures")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run every experiment and write results/ and figures/")
    r.add_argument("--out", default="results")
    r.add_argument("--figures", default="figures")
    r.add_argument("--seeds", type=int, default=20, help="market paths for the robustness study (0 = skip)")
    r.add_argument("--no-figures", action="store_true")
    r.add_argument("--quick", action="store_true", help="3 seeds, for a fast smoke run")
    r.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd != "run":
        ap.print_help()
        return 0
    from .experiments import run_everything

    log = (lambda *a, **k: None) if args.quiet else print
    h = run_everything(Path(args.out), Path(args.figures), Params(), n_seeds=3 if args.quick else args.seeds,
                       log=log, make_figures=not args.no_figures)
    if not args.quiet:
        base = h["scenarios"]["base"]["optimized"]
        print(json.dumps({"base_optimized_net_revenue": base["net_revenue"],
                          "base_net_vs_fcfs_pct": base["net_vs_fcfs_pct"],
                          "runtime_seconds": h["runtime_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
