#!/usr/bin/env python3
"""Run deterministic UBX16 AllToAllV distribution-size experiments.

The traffic model keeps every source rank at a fixed total send volume, but
uses a deterministic pseudo-random per-peer distribution instead of an
arithmetic progression.  The max/min ratio of each source rank is bounded by
the sweep parameter.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RANK_COUNT = 16
GROUP_SIZE = 4
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"
DEFAULT_BASE = REPO_ROOT / "experiments/ubx16/alltoallv/distribution"


def parse_size(value: str) -> int:
    text = value.strip()
    units = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
    }
    upper = text.upper()
    for unit in sorted(units, key=len, reverse=True):
        if upper.endswith(unit):
            return int(float(upper[: -len(unit)].strip()) * units[unit])
    return int(text)


def size_name(size_bytes: int) -> str:
    for suffix, scale in (("gb", 1024**3), ("mb", 1024**2), ("kb", 1024)):
        if size_bytes % scale == 0:
            return f"{size_bytes // scale}{suffix}"
    return f"{size_bytes}b"


def normalize_to_total(weights: list[float], total: int) -> list[int]:
    raw = [w / sum(weights) * total for w in weights]
    ints = [int(x) for x in raw]
    remainder = total - sum(ints)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - ints[i], reverse=True)
    for idx in order[:remainder]:
        ints[idx] += 1
    if any(value <= 0 for value in ints):
        raise ValueError("distribution generated a zero-byte flow")
    if sum(ints) != total:
        raise ValueError("internal error: normalized total mismatch")
    return ints


def peer_order(src: int) -> list[int]:
    return [dst for dst in range(RANK_COUNT) if dst != src]


def bounded_distribution_sizes(per_rank_bytes: int, max_min_ratio: float, seed: int) -> dict[tuple[int, int], int]:
    if max_min_ratio < 1.0 or max_min_ratio > 2.0:
        raise ValueError("max_min_ratio must be in [1.0, 2.0]")
    sizes: dict[tuple[int, int], int] = {}
    for src in range(RANK_COUNT):
        peers = peer_order(src)
        if max_min_ratio == 1.0:
            weights = [1.0] * len(peers)
        else:
            rng = random.Random(seed + src * 1009)
            # Use a smooth bounded random distribution.  The samples are
            # rescaled into [1, max_min_ratio], so every source rank has a
            # controlled max/min while the order is not an arithmetic series.
            samples = [rng.lognormvariate(0.0, 0.65) for _ in peers]
            lo, hi = min(samples), max(samples)
            if hi == lo:
                weights = [1.0] * len(peers)
            else:
                weights = [
                    1.0 + (sample - lo) / (hi - lo) * (max_min_ratio - 1.0)
                    for sample in samples
                ]
        values = normalize_to_total(weights, per_rank_bytes)
        for dst, value in zip(peers, values):
            sizes[(src, dst)] = value
    return sizes


def cv(values: list[float]) -> float:
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(var) / mean


def bytes_stats(values: list[int]) -> dict[str, float]:
    avg = sum(values) / len(values)
    mn = min(values)
    mx = max(values)
    return {
        "min_bytes": mn,
        "avg_bytes": avg,
        "max_bytes": mx,
        "max_over_avg": mx / avg if avg else 0.0,
        "max_over_min": mx / mn if mn else 0.0,
        "cv": cv([float(v) for v in values]),
    }


def traffic_stats(sizes: dict[tuple[int, int], int], rank: int = 0) -> dict[str, float]:
    pair_values = list(sizes.values())
    src_totals = [
        sum(size for (src, _), size in sizes.items() if src == r)
        for r in range(RANK_COUNT)
    ]
    dst_totals = [
        sum(size for (_, dst), size in sizes.items() if dst == r)
        for r in range(RANK_COUNT)
    ]
    rank_send = [size for (src, _), size in sizes.items() if src == rank]
    rank_recv = [size for (_, dst), size in sizes.items() if dst == rank]
    total = sum(pair_values)
    cross = sum(size for (src, dst), size in sizes.items() if src // GROUP_SIZE != dst // GROUP_SIZE)
    send = bytes_stats(rank_send)
    recv = bytes_stats(rank_recv)
    return {
        "total_bytes": total,
        "pair_cv": cv([float(x) for x in pair_values]),
        "src_total_cv": cv([float(x) for x in src_totals]),
        "dst_total_cv": cv([float(x) for x in dst_totals]),
        "cross_fraction": cross / total if total else 0.0,
        "rank0_send_min_bytes": send["min_bytes"],
        "rank0_send_avg_bytes": send["avg_bytes"],
        "rank0_send_max_bytes": send["max_bytes"],
        "rank0_send_max_over_avg": send["max_over_avg"],
        "rank0_send_max_over_min": send["max_over_min"],
        "rank0_send_cv": send["cv"],
        "rank0_recv_min_bytes": recv["min_bytes"],
        "rank0_recv_avg_bytes": recv["avg_bytes"],
        "rank0_recv_max_bytes": recv["max_bytes"],
        "rank0_recv_max_over_avg": recv["max_over_avg"],
        "rank0_recv_max_over_min": recv["max_over_min"],
        "rank0_recv_cv": recv["cv"],
    }


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def generate_algorithm_case(algorithm: str, output_case: Path, per_rank_bytes: int) -> None:
    if output_case.exists():
        shutil.rmtree(output_case)
    size_arg = str(per_rank_bytes)
    if algorithm == "baseline":
        cmd = [
            "python3",
            "tools/generate_hccl_mesh1d_alltoallv_case.py",
            "--source-case",
            str(UBX16_SOURCE_CASE),
            "--output-case",
            str(output_case),
            "--rank-count",
            str(RANK_COUNT),
            "--per-rank-bytes",
            size_arg,
            "--dependency-mode",
            "thread-serial",
            "--tp-mode",
            "full",
            "--concurrent",
            "16",
        ]
    elif algorithm == "meshclos":
        cmd = [
            "python3",
            "tools/generate_hccl_meshclos2d_v3_alltoall_case.py",
            "--source-case",
            str(UBX16_SOURCE_CASE),
            "--output-case",
            str(output_case),
            "--rank-count",
            str(RANK_COUNT),
            "--group-size",
            str(GROUP_SIZE),
            "--direct-per-rank-bytes",
            size_arg,
            "--mode",
            "strict",
            "--traffic-order",
            "v3-logical",
            "--dependency-mode",
            "v3-thread-serial",
        ]
    else:
        raise ValueError(f"unknown algorithm {algorithm}")
    run_cmd(cmd)


def patch_traffic(case_dir: Path, sizes: dict[tuple[int, int], int]) -> None:
    path = case_dir / "traffic.csv"
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    if fieldnames is None:
        raise ValueError(f"{path} has no header")
    seen: set[tuple[int, int]] = set()
    for row in rows:
        key = (int(row["sourceNodeId"]), int(row["destNodeId"]))
        row["dataSize(Byte)"] = str(sizes[key])
        seen.add(key)
    if seen != set(sizes):
        raise ValueError(f"traffic pair mismatch for {case_dir}")
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_sim(case_dir: Path, docker_container: str | None, mtp_threads: int) -> None:
    if docker_container:
        arg = f"--case-path=/workspace/hccl_workspace/{case_dir.relative_to(REPO_ROOT)}"
        if mtp_threads > 0:
            arg += f" --mtp-threads={mtp_threads}"
        run_cmd(
            [
                "docker",
                "exec",
                docker_container,
                "bash",
                "-lc",
                f'cd /workspace/hccl_workspace && ./ns-3-ub/ns3 run "scratch/ub-quick-example {arg}"',
            ]
        )
    else:
        arg = f"--case-path={case_dir.resolve()}"
        if mtp_threads > 0:
            arg += f" --mtp-threads={mtp_threads}"
        run_cmd(["./ns-3-ub/ns3", "run", f"scratch/ub-quick-example {arg}"])


def summarize_case(case_dir: Path, sizes: dict[tuple[int, int], int]) -> dict[str, float]:
    path = case_dir / "output" / "task_statistics.csv"
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    makespan = max(float(row["taskCompletesTime(us)"]) for row in rows)
    total_bytes = sum(int(float(row["dataSize(Byte)"])) for row in rows)
    rank0_rows = [row for row in rows if int(row["sourceNodeId"]) == 0]
    rank0_makespan = max(float(row["taskCompletesTime(us)"]) for row in rank0_rows)
    rank0_bytes = sum(int(float(row["dataSize(Byte)"])) for row in rank0_rows)
    return {
        **traffic_stats(sizes, rank=0),
        "tasks": len(rows),
        "makespan_us": makespan,
        "single_rank_GBps": total_bytes / makespan / 1e3 / RANK_COUNT,
        "rank0_makespan_us": rank0_makespan,
        "rank0_tx_GBps": rank0_bytes / rank0_makespan / 1e3,
    }


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "scenario",
        "target_max_min",
        "algorithm",
        "case",
        "tasks",
        "total_bytes",
        "pair_cv",
        "src_total_cv",
        "dst_total_cv",
        "cross_fraction",
        "rank0_send_min_bytes",
        "rank0_send_avg_bytes",
        "rank0_send_max_bytes",
        "rank0_send_max_over_avg",
        "rank0_send_max_over_min",
        "rank0_send_cv",
        "rank0_recv_min_bytes",
        "rank0_recv_avg_bytes",
        "rank0_recv_max_bytes",
        "rank0_recv_max_over_avg",
        "rank0_recv_max_over_min",
        "rank0_recv_cv",
        "makespan_us",
        "single_rank_GBps",
        "rank0_makespan_us",
        "rank0_tx_GBps",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-min", nargs="+", type=float, default=[1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--algorithms", nargs="+", default=["baseline", "meshclos"])
    parser.add_argument("--per-rank-bytes", default="128MB")
    parser.add_argument("--seed", type=int, default=20260618)
    parser.add_argument("--case-prefix", default=str(DEFAULT_BASE / "cases/generated_topology_ubx16_a2av_dist"))
    parser.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_BASE / "reports/ns3ub-ubx16-alltoallv-distribution-summary.csv",
    )
    parser.add_argument("--docker-container", default="hcomm-dev")
    parser.add_argument("--mtp-threads", type=int, default=8)
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    per_rank_bytes = parse_size(args.per_rank_bytes)
    suffix = size_name(per_rank_bytes)
    summary_rows: list[dict[str, object]] = []
    for ratio in args.max_min:
        scenario = f"maxmin{str(ratio).replace('.', 'p')}x"
        sizes = bounded_distribution_sizes(per_rank_bytes, ratio, args.seed)
        for algorithm in args.algorithms:
            case_dir = REPO_ROOT / f"{args.case_prefix}_{scenario}_{algorithm}_{suffix}"
            generate_algorithm_case(algorithm, case_dir, per_rank_bytes)
            patch_traffic(case_dir, sizes)
            if not args.no_run:
                run_sim(case_dir, args.docker_container, args.mtp_threads)
                summary = summarize_case(case_dir, sizes)
                summary_rows.append(
                    {
                        "scenario": scenario,
                        "target_max_min": ratio,
                        "algorithm": algorithm,
                        "case": str(case_dir.relative_to(REPO_ROOT)),
                        **summary,
                    }
                )
                print(
                    f"{scenario}/{algorithm}: makespan={summary['makespan_us']:.3f}us "
                    f"single-rank={summary['single_rank_GBps']:.2f}GB/s"
                )
    if summary_rows:
        write_summary(args.summary, summary_rows)
        print(f"summary={args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
