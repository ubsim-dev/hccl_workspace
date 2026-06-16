#!/usr/bin/env python3
"""Generate UBX16 AllToAllV scenario cases for baseline, matrix, and MeshClos V3.

The algorithm generators define traffic order, dependencies, and TP selection.
This script overwrites traffic.csv dataSize(Byte) with deterministic AllToAllV
size matrices that model representative MoE dispatch/combine skew patterns.
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
AVG_PER_RANK_BYTES = 16 * 1024 * 1024
PRIORITY = 7
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"


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


def split_by_weights(total: int, weights: list[float]) -> list[int]:
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("weights must sum to positive")
    raw = [total * w / weight_sum for w in weights]
    values = [int(x) for x in raw]
    remainder = total - sum(values)
    order = sorted(range(len(weights)), key=lambda i: raw[i] - values[i], reverse=True)
    for idx in order[:remainder]:
        values[idx] += 1
    return values


def peers(src: int) -> list[int]:
    return [dst for dst in range(RANK_COUNT) if dst != src]


def fill_by_source_weights(per_rank_bytes: int, weight_fn) -> dict[tuple[int, int], int]:
    sizes: dict[tuple[int, int], int] = {}
    for src in range(RANK_COUNT):
        dsts = peers(src)
        weights = [weight_fn(src, dst) for dst in dsts]
        values = split_by_weights(per_rank_bytes, weights)
        for dst, size in zip(dsts, values):
            sizes[(src, dst)] = size
    return sizes


def transpose_sizes(sizes: dict[tuple[int, int], int]) -> dict[tuple[int, int], int]:
    return {(dst, src): size for (src, dst), size in sizes.items()}


def scenario_sizes(name: str, per_rank_bytes: int) -> dict[tuple[int, int], int]:
    if name == "uniform":
        return fill_by_source_weights(per_rank_bytes, lambda _src, _dst: 1.0)

    if name == "mild_random":
        rng = random.Random(20260615)
        weights_by_src: dict[int, dict[int, float]] = {}
        for src in range(RANK_COUNT):
            weights_by_src[src] = {dst: math.exp(rng.gauss(0.0, 0.7)) for dst in peers(src)}
        return fill_by_source_weights(per_rank_bytes, lambda src, dst: weights_by_src[src][dst])

    if name == "dispatch_hot4":
        hot = {3, 7, 11, 15}
        return fill_by_source_weights(
            per_rank_bytes,
            lambda src, dst: 12.0 if dst in hot and dst != src else 1.0,
        )

    if name == "combine_hot4":
        return transpose_sizes(scenario_sizes("dispatch_hot4", per_rank_bytes))

    if name == "cross_group_heavy":
        return fill_by_source_weights(
            per_rank_bytes,
            lambda src, dst: 8.0 if src // GROUP_SIZE != dst // GROUP_SIZE else 1.0,
        )

    raise ValueError(f"unknown scenario {name}")


def matrix_stats(sizes: dict[tuple[int, int], int]) -> dict[str, float]:
    src_totals = [sum(size for (src, _), size in sizes.items() if src == rank) for rank in range(RANK_COUNT)]
    dst_totals = [sum(size for (_, dst), size in sizes.items() if dst == rank) for rank in range(RANK_COUNT)]
    total = sum(sizes.values())
    cross = sum(size for (src, dst), size in sizes.items() if src // GROUP_SIZE != dst // GROUP_SIZE)

    def cv(values: list[int]) -> float:
        mean = sum(values) / len(values)
        var = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(var) / mean if mean else 0.0

    return {
        "total_bytes": total,
        "src_cv": cv(src_totals),
        "dst_cv": cv(dst_totals),
        "cross_fraction": cross / total if total else 0.0,
        "max_pair_bytes": max(sizes.values()),
        "max_src_bytes": max(src_totals),
        "max_dst_bytes": max(dst_totals),
    }


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


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
        src = int(row["sourceNodeId"])
        dst = int(row["destNodeId"])
        key = (src, dst)
        if key not in sizes:
            raise ValueError(f"no size for pair {key}")
        row["dataSize(Byte)"] = str(sizes[key])
        seen.add(key)
    if seen != set(sizes):
        missing = sorted(set(sizes) - seen)[:8]
        raise ValueError(f"traffic missing {len(set(sizes) - seen)} pairs, examples={missing}")

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_algorithm_case(algorithm: str, output_case: Path, per_rank_bytes: int) -> None:
    if output_case.exists():
        shutil.rmtree(output_case)

    size_arg = str(per_rank_bytes)
    if algorithm == "baseline":
        run_cmd(
            [
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
        )
    elif algorithm == "matrix":
        run_cmd(
            [
                "python3",
                "tools/generate_hccl_matrix_alltoall_case.py",
                "--source-case",
                str(UBX16_SOURCE_CASE),
                "--output-case",
                str(output_case),
                "--rank-count",
                str(RANK_COUNT),
                "--direct-per-rank-bytes",
                size_arg,
                "--mode",
                "strict",
                "--dependency-mode",
                "thread-serial",
            ]
        )
    elif algorithm == "closv3":
        run_cmd(
            [
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
        )
    else:
        raise ValueError(f"unknown algorithm {algorithm}")


def run_sim(case_dir: Path, docker_container: str | None) -> None:
    if docker_container:
        container_case = f"/workspace/hccl_workspace/{case_dir.relative_to(REPO_ROOT)}"
        run_cmd(
            [
                "docker",
                "exec",
                docker_container,
                "bash",
                "-lc",
                f'cd /workspace/hccl_workspace && ./ns-3-ub/ns3 run "scratch/ub-quick-example --case-path={container_case}"',
            ]
        )
    else:
        run_cmd(
            [
                "./ns-3-ub/ns3",
                "run",
                f"scratch/ub-quick-example --case-path={case_dir.resolve()}",
            ]
        )


def summarize_case(case_dir: Path, sizes: dict[tuple[int, int], int]) -> dict[str, float]:
    path = case_dir / "output" / "task_statistics.csv"
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    makespan = max(float(row["taskCompletesTime(us)"]) for row in rows)
    total_bytes = sum(int(row["dataSize(Byte)"]) for row in rows)
    rank0_rows = [row for row in rows if int(row["sourceNodeId"]) == 0]
    rank0_makespan = max(float(row["taskCompletesTime(us)"]) for row in rank0_rows)
    rank0_bytes = sum(int(row["dataSize(Byte)"]) for row in rank0_rows)
    stats = matrix_stats(sizes)
    return {
        **stats,
        "tasks": len(rows),
        "makespan_us": makespan,
        "global_GBps": total_bytes / makespan / 1e3,
        "rank0_makespan_us": rank0_makespan,
        "rank0_GBps": rank0_bytes / rank0_makespan / 1e3,
    }


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "scenario",
        "algorithm",
        "case",
        "tasks",
        "total_bytes",
        "src_cv",
        "dst_cv",
        "cross_fraction",
        "max_pair_bytes",
        "max_src_bytes",
        "max_dst_bytes",
        "makespan_us",
        "global_GBps",
        "rank0_makespan_us",
        "rank0_GBps",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=["uniform", "mild_random", "dispatch_hot4", "combine_hot4", "cross_group_heavy"],
    )
    parser.add_argument("--algorithms", nargs="+", default=["baseline", "matrix", "closv3"])
    parser.add_argument("--per-rank-bytes", default="16MB")
    parser.add_argument(
        "--case-prefix",
        default="experiments/ubx16/alltoallv/scenarios/cases/generated_topology_ubx16_a2av",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=REPO_ROOT
        / "experiments/ubx16/alltoallv/scenarios/reports/ns3ub-ubx16-alltoallv-scenarios-summary.csv",
    )
    parser.add_argument("--docker-container", default="hcomm-dev")
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    per_rank_bytes = parse_size(args.per_rank_bytes)
    summary_rows: list[dict[str, object]] = []
    for scenario in args.scenarios:
        sizes = scenario_sizes(scenario, per_rank_bytes)
        for algorithm in args.algorithms:
            case_dir = REPO_ROOT / f"{args.case_prefix}_{scenario}_{algorithm}_16mb"
            generate_algorithm_case(algorithm, case_dir, per_rank_bytes)
            patch_traffic(case_dir, sizes)
            if not args.no_run:
                run_sim(case_dir, args.docker_container)
                summary = summarize_case(case_dir, sizes)
                summary_rows.append(
                    {
                        "scenario": scenario,
                        "algorithm": algorithm,
                        "case": str(case_dir.relative_to(REPO_ROOT)),
                        **summary,
                    }
                )
                print(
                    f"{scenario}/{algorithm}: makespan={summary['makespan_us']:.3f}us "
                    f"global={summary['global_GBps']:.2f}GB/s"
                )

    if summary_rows:
        write_summary(args.summary, summary_rows)
        print(f"summary={args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
