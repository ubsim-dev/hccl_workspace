#!/usr/bin/env python3
"""Run deterministic UBX16 AllToAllV gradient-size experiments.

Each source rank owns a fixed AllToAllV count including the self slot.  The
rankSize slots form an arithmetic progression by cyclic distance, matching the
HCCL opbase traffic script.  The self slot is then dropped because it does not
inject network traffic in ns-3.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RANK_COUNT = 16
GROUP_SIZE = 4
PRIORITY = 7
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"
DEFAULT_BASE = REPO_ROOT / "experiments/ubx16/alltoallv/gradient"


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


def split_linear(total: int, count: int, max_over_avg: float) -> list[int]:
    if count <= 0:
        return []
    avg = total / count
    if max_over_avg < 1.0 or max_over_avg > 2.0:
        raise ValueError("max_over_avg must be in [1.0, 2.0]")
    if count == 1:
        return [total]
    min_over_avg = 2.0 - max_over_avg
    values = [
        avg * (min_over_avg + (max_over_avg - min_over_avg) * i / (count - 1))
        for i in range(count)
    ]
    ints = [int(x) for x in values]
    remainder = total - sum(ints)
    order = sorted(range(count), key=lambda i: values[i] - ints[i], reverse=True)
    for idx in order[:remainder]:
        ints[idx] += 1
    zero_count = sum(1 for value in ints if value == 0)
    if zero_count:
        for i, value in enumerate(ints):
            if value == 0:
                ints[i] = 1
        for i in sorted(range(count), key=lambda idx: ints[idx], reverse=True):
            if zero_count == 0:
                break
            take = min(zero_count, ints[i] - 1)
            if take > 0:
                ints[i] -= take
                zero_count -= take
    if sum(ints) != total:
        raise ValueError("internal error: split does not preserve total")
    return ints


def gradient_sizes(per_rank_bytes: int, max_over_avg: float) -> dict[tuple[int, int], int]:
    sizes: dict[tuple[int, int], int] = {}
    values = split_linear(per_rank_bytes, RANK_COUNT, max_over_avg)
    for src in range(RANK_COUNT):
        for offset, size in enumerate(values):
            dst = (src + offset) % RANK_COUNT
            if dst != src:
                sizes[(src, dst)] = size
    return sizes


def cv(values: list[float]) -> float:
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(var) / mean


def matrix_stats(sizes: dict[tuple[int, int], int]) -> dict[str, float]:
    pair_values = list(sizes.values())
    src_totals = [
        sum(size for (src, _), size in sizes.items() if src == rank)
        for rank in range(RANK_COUNT)
    ]
    dst_totals = [
        sum(size for (_, dst), size in sizes.items() if dst == rank)
        for rank in range(RANK_COUNT)
    ]
    total = sum(pair_values)
    cross = sum(size for (src, dst), size in sizes.items() if src // GROUP_SIZE != dst // GROUP_SIZE)
    return {
        "total_bytes": total,
        "pair_avg_bytes": sum(pair_values) / len(pair_values),
        "pair_min_bytes": min(pair_values),
        "pair_max_bytes": max(pair_values),
        "pair_cv": cv([float(x) for x in pair_values]),
        "src_cv": cv([float(x) for x in src_totals]),
        "dst_cv": cv([float(x) for x in dst_totals]),
        "cross_fraction": cross / total if total else 0.0,
    }


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def generate_algorithm_case(
    algorithm: str,
    output_case: Path,
    per_rank_bytes: int,
    closv3_dependency_mode: str,
) -> None:
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
    elif algorithm == "matrix":
        cmd = [
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
    elif algorithm == "closv3":
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
            closv3_dependency_mode,
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
    arg = f"--case-path=/workspace/hccl_workspace/{case_dir.relative_to(REPO_ROOT)}"
    if mtp_threads > 0:
        arg += f" --mtp-threads={mtp_threads}"
    if docker_container:
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
        local_arg = f"--case-path={case_dir.resolve()}"
        if mtp_threads > 0:
            local_arg += f" --mtp-threads={mtp_threads}"
        run_cmd(["./ns-3-ub/ns3", "run", f"scratch/ub-quick-example {local_arg}"])


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
        **matrix_stats(sizes),
        "tasks": len(rows),
        "makespan_us": makespan,
        "global_GBps": total_bytes / makespan / 1e3,
        "rank0_makespan_us": rank0_makespan,
        "rank0_GBps": rank0_bytes / rank0_makespan / 1e3,
    }


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "scenario",
        "max_over_avg",
        "algorithm",
        "case",
        "tasks",
        "total_bytes",
        "pair_avg_bytes",
        "pair_min_bytes",
        "pair_max_bytes",
        "pair_cv",
        "src_cv",
        "dst_cv",
        "cross_fraction",
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
    parser.add_argument("--max-over-avg", nargs="+", type=float, default=[1.0, 1.2, 1.5, 2.0])
    parser.add_argument("--algorithms", nargs="+", default=["baseline", "matrix", "closv3"])
    parser.add_argument("--per-rank-bytes", default="128MB")
    parser.add_argument(
        "--closv3-dependency-mode",
        choices=("none", "v3-thread-serial", "v3-plane-step-barrier", "v3-step-barrier"),
        default="v3-thread-serial",
        help="Dependency model used when generating closv3 cases.",
    )
    parser.add_argument("--case-prefix", default=str(DEFAULT_BASE / "cases/generated_topology_ubx16_a2av_gradient"))
    parser.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_BASE / "reports/ns3ub-ubx16-alltoallv-gradient-summary.csv",
    )
    parser.add_argument("--docker-container", default="hcomm-dev")
    parser.add_argument("--mtp-threads", type=int, default=8)
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    per_rank_bytes = parse_size(args.per_rank_bytes)
    suffix = size_name(per_rank_bytes)
    summary_rows: list[dict[str, object]] = []
    for ratio in args.max_over_avg:
        scenario = f"max{str(ratio).replace('.', 'p')}x"
        sizes = gradient_sizes(per_rank_bytes, ratio)
        for algorithm in args.algorithms:
            case_dir = REPO_ROOT / f"{args.case_prefix}_{scenario}_{algorithm}_{suffix}"
            generate_algorithm_case(algorithm, case_dir, per_rank_bytes, args.closv3_dependency_mode)
            patch_traffic(case_dir, sizes)
            if not args.no_run:
                run_sim(case_dir, args.docker_container, args.mtp_threads)
                summary = summarize_case(case_dir, sizes)
                summary_rows.append(
                    {
                        "scenario": scenario,
                        "max_over_avg": ratio,
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
