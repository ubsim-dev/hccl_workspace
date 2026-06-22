#!/usr/bin/env python3
"""Render rank0 profiles for UBX16 AllToAllV hybrid A/B cases."""

from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab"
PROFILE_DIR = BASE / "profiles"
DEFAULT_SUMMARIES = [
    BASE / "reports/ns3ub-ubx16-alltoallv-hybrid-ab-summary.csv",
    BASE / "reports/ns3ub-ubx16-alltoallv-hybrid-ab-ratio-sweep-summary.csv",
    BASE / "reports/ns3ub-ubx16-alltoallv-hybrid-ab-step-cap-sweep-summary.csv",
]
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def output_name(row: dict[str, str]) -> str:
    return f"{Path(row['case']).name}-rank0-profile.html"


def title(row: dict[str, str]) -> str:
    return (
        f"UBX16 AllToAllV {row['algorithm']} | "
        f"max/avg {float(row['max_over_avg']):.1f}x | rank0"
    )


def render(row: dict[str, str], output: Path) -> None:
    cmd = [
        "python3",
        "tools/render_ns3ub_rank_profile.py",
        row["case"],
        "-o",
        str(output),
        "--rank",
        "0",
        "--rank-count",
        "16",
        "--group-size",
        "4",
        "--source-case",
        str(UBX16_SOURCE_CASE),
        "--title",
        title(row),
    ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="append", type=Path)
    parser.add_argument("--output-dir", type=Path, default=PROFILE_DIR)
    args = parser.parse_args()

    summaries = args.summary if args.summary else DEFAULT_SUMMARIES
    rows_by_case: dict[str, dict[str, str]] = {}
    for summary in summaries:
        for row in load(summary):
            if row["algorithm"] in ("baseline", "closv3"):
                continue
            rows_by_case[row["case"]] = row

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for row in sorted(rows_by_case.values(), key=lambda r: Path(r["case"]).name):
        render(row, args.output_dir / output_name(row))


if __name__ == "__main__":
    main()
