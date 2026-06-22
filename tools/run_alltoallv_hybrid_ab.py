#!/usr/bin/env python3
"""Run UBX16 AllToAllV A+B hybrid experiments.

The hybrid model uses two transport-channel namespaces in one ns-3 case:

* priority 7: A stage, MeshClos V3 strict single-TP/fixed-plane channels.
* priority 6: B stage, Mesh1D baseline full-TP channels.

TPNs are unique per node even when A/B use the same physical ports.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
from pathlib import Path

import run_alltoallv_gradient_sweep as gradient


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab"
CASES = BASE / "cases"
REPORTS = BASE / "reports"
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"
A_PRIORITY = 7
B_PRIORITY = 6


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header")
        return reader.fieldnames, list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def copy_base_files(output_case: Path) -> None:
    output_case.mkdir(parents=True, exist_ok=True)
    for name in ("node.csv", "topology.csv", "routing_table.csv", "network_attribute.txt"):
        shutil.copy2(UBX16_SOURCE_CASE / name, output_case / name)


def patch_network_attributes(path: Path) -> None:
    lines = path.read_text().splitlines()
    out: list[str] = []
    seen_port = False
    for line in lines:
        if line.startswith('global UB_PORT_TRACE_ENABLE '):
            out.append('global UB_PORT_TRACE_ENABLE "false"')
            seen_port = True
        elif line.startswith('global UB_PACKET_TRACE_ENABLE '):
            out.append('global UB_PACKET_TRACE_ENABLE "false"')
        elif line.startswith('global UB_RECORD_PKT_TRACE '):
            out.append('global UB_RECORD_PKT_TRACE "false"')
        else:
            out.append(line)
    if not seen_port:
        out.append('global UB_PORT_TRACE_ENABLE "false"')
    path.write_text("\n".join(out) + "\n")


def generate_stage_cases(tmp_base: Path, per_rank_bytes: int, a_dependency_mode: str) -> tuple[Path, Path]:
    a_case = tmp_base / "a_v3"
    b_case = tmp_base / "b_baseline"
    if tmp_base.exists():
        shutil.rmtree(tmp_base)
    run_cmd(
        [
            "python3",
            "tools/generate_hccl_meshclos2d_v3_alltoall_case.py",
            "--source-case",
            str(UBX16_SOURCE_CASE),
            "--output-case",
            str(a_case),
            "--rank-count",
            str(gradient.RANK_COUNT),
            "--group-size",
            str(gradient.GROUP_SIZE),
            "--direct-per-rank-bytes",
            str(per_rank_bytes),
            "--mode",
            "strict",
            "--traffic-order",
            "v3-logical",
            "--dependency-mode",
            a_dependency_mode,
        ]
    )
    run_cmd(
        [
            "python3",
            "tools/generate_hccl_mesh1d_alltoallv_case.py",
            "--source-case",
            str(UBX16_SOURCE_CASE),
            "--output-case",
            str(b_case),
            "--rank-count",
            str(gradient.RANK_COUNT),
            "--per-rank-bytes",
            str(per_rank_bytes),
            "--dependency-mode",
            "thread-serial",
            "--tp-mode",
            "full",
            "--concurrent",
            "16",
        ]
    )
    return a_case, b_case


def remap_b_tpns(a_rows: list[dict[str, str]], b_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    used: dict[int, set[int]] = {}

    def mark(node: int, tpn: int) -> None:
        used.setdefault(node, set()).add(tpn)

    for row in a_rows:
        mark(int(row["nodeId1"]), int(row["tpn1"]))
        mark(int(row["nodeId2"]), int(row["tpn2"]))

    next_tpn = {node: (max(tpns) + 1 if tpns else 0) for node, tpns in used.items()}

    def alloc(node: int) -> int:
        value = next_tpn.get(node, 0)
        while value in used.setdefault(node, set()):
            value += 1
        used[node].add(value)
        next_tpn[node] = value + 1
        return value

    remapped: list[dict[str, str]] = []
    for row in b_rows:
        new = dict(row)
        n1 = int(new["nodeId1"])
        n2 = int(new["nodeId2"])
        new["tpn1"] = str(alloc(n1))
        new["tpn2"] = str(alloc(n2))
        new["priority"] = str(B_PRIORITY)
        remapped.append(new)
    return remapped


def pair_key(row: dict[str, str]) -> tuple[int, int]:
    return int(row["sourceNodeId"]), int(row["destNodeId"])


def parse_hybrid_mode(mode: str, avg: float, pair_values: list[int]) -> tuple[str, int, dict[str, float]]:
    if mode == "min":
        target = min(pair_values)
        return "cap", target, {"split_ratio": target / avg if avg > 0 else 0.0}
    if mode == "pad80":
        return "pad", int(avg * 0.8), {"split_ratio": 0.8}

    match = re.fullmatch(r"(cap|pad)(\d+)p(\d+)", mode)
    if not match:
        raise ValueError(f"unknown hybrid mode {mode}")
    kind = match.group(1)
    ratio = float(f"{match.group(2)}.{match.group(3)}")
    return kind, int(avg * ratio), {"split_ratio": ratio}


def build_hybrid_case(
    output_case: Path,
    a_case: Path,
    b_case: Path,
    sizes: dict[tuple[int, int], int],
    mode: str,
) -> dict[str, float]:
    if output_case.exists():
        shutil.rmtree(output_case)
    copy_base_files(output_case)
    patch_network_attributes(output_case / "network_attribute.txt")

    tc_fields, a_tc = read_csv(a_case / "transport_channel.csv")
    _, b_tc = read_csv(b_case / "transport_channel.csv")
    for row in a_tc:
        row["priority"] = str(A_PRIORITY)
    combined_tc = a_tc + remap_b_tpns(a_tc, b_tc)
    write_csv(output_case / "transport_channel.csv", tc_fields, combined_tc)

    traffic_fields, a_rows = read_csv(a_case / "traffic.csv")
    _, b_rows = read_csv(b_case / "traffic.csv")
    pair_values = list(sizes.values())
    avg = sum(pair_values) / len(pair_values)
    split_kind, target_bytes, split_meta = parse_hybrid_mode(mode, avg, pair_values)
    a_bytes_by_pair: dict[tuple[int, int], int] = {}
    padding_bytes = 0
    for key, value in sizes.items():
        if split_kind == "pad":
            a_bytes_by_pair[key] = target_bytes
            padding_bytes += max(0, target_bytes - value)
        else:
            a_bytes_by_pair[key] = min(value, target_bytes)

    a_task_ids: list[int] = []
    rows: list[dict[str, object]] = []
    task_id = 0
    for src_row in a_rows:
        key = pair_key(src_row)
        row = dict(src_row)
        row["taskId"] = task_id
        row["dataSize(Byte)"] = a_bytes_by_pair[key]
        row["priority"] = A_PRIORITY
        row["phaseId"] = task_id
        row["dependOnPhases"] = src_row["dependOnPhases"]
        rows.append(row)
        a_task_ids.append(task_id)
        task_id += 1
        if key not in sizes:
            raise ValueError(f"A traffic has unknown pair {key}")

    a_deps = " ".join(str(x) for x in a_task_ids)
    b_bytes_total = 0
    b_task_count = 0
    for src_row in b_rows:
        key = pair_key(src_row)
        original = sizes[key]
        b_bytes = max(0, original - a_bytes_by_pair[key])
        if b_bytes == 0:
            continue
        row = dict(src_row)
        row["taskId"] = task_id
        row["dataSize(Byte)"] = b_bytes
        row["priority"] = B_PRIORITY
        row["phaseId"] = task_id
        row["dependOnPhases"] = a_deps
        rows.append(row)
        task_id += 1
        b_bytes_total += b_bytes
        b_task_count += 1

    write_csv(output_case / "traffic.csv", traffic_fields, rows)
    effective_total = sum(pair_values)
    a_network_bytes = sum(a_bytes_by_pair.values())
    network_total = a_network_bytes + b_bytes_total
    return {
        **split_meta,
        "split_kind": split_kind,
        "a_bytes_per_pair": float(target_bytes),
        "a_network_bytes": float(a_network_bytes),
        "b_network_bytes": float(b_bytes_total),
        "padding_bytes": float(padding_bytes),
        "effective_total_bytes": float(effective_total),
        "network_total_bytes": float(network_total),
        "b_tasks": float(b_task_count),
    }


def run_sim(case_dir: Path, docker_container: str, mtp_threads: int) -> None:
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


def summarize_case(
    case_dir: Path,
    sizes: dict[tuple[int, int], int],
    meta: dict[str, float],
) -> dict[str, float]:
    _, rows = read_csv(case_dir / "output" / "task_statistics.csv")
    makespan = max(float(row["taskCompletesTime(us)"]) for row in rows)
    rank0_rows = [row for row in rows if int(row["sourceNodeId"]) == 0]
    rank0_makespan = max(float(row["taskCompletesTime(us)"]) for row in rank0_rows)
    rank0_effective = sum(size for (src, _), size in sizes.items() if src == 0)
    return {
        **gradient.matrix_stats(sizes),
        **meta,
        "tasks": float(len(rows)),
        "makespan_us": makespan,
        "effective_global_GBps": meta["effective_total_bytes"] / makespan / 1e3,
        "network_global_GBps": meta["network_total_bytes"] / makespan / 1e3,
        "single_rank_GBps": meta["effective_total_bytes"] / makespan / 1e3 / gradient.RANK_COUNT,
        "rank0_makespan_us": rank0_makespan,
        "rank0_GBps": rank0_effective / rank0_makespan / 1e3,
    }


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
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
        "split_kind",
        "split_ratio",
        "a_bytes_per_pair",
        "a_network_bytes",
        "b_network_bytes",
        "padding_bytes",
        "effective_total_bytes",
        "network_total_bytes",
        "b_tasks",
        "makespan_us",
        "effective_global_GBps",
        "network_global_GBps",
        "single_rank_GBps",
        "rank0_makespan_us",
        "rank0_GBps",
    ]
    write_csv(path, fields, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-over-avg", nargs="+", type=float, default=[1.0, 1.2, 1.5, 2.0])
    parser.add_argument(
        "--modes",
        nargs="+",
        default=["min", "pad80"],
        help="Hybrid modes: min, pad80, or ratio modes like cap0p8/pad0p8.",
    )
    parser.add_argument("--per-rank-bytes", default="128MB")
    parser.add_argument("--summary", type=Path, default=REPORTS / "ns3ub-ubx16-alltoallv-hybrid-ab-summary.csv")
    parser.add_argument("--docker-container", default="hcomm-dev")
    parser.add_argument("--mtp-threads", type=int, default=8)
    parser.add_argument(
        "--a-dependency-mode",
        choices=("v3-thread-serial", "v3-step-barrier"),
        default="v3-thread-serial",
        help="Dependency model used inside the A-stage MeshClos V3 traffic.",
    )
    parser.add_argument(
        "--algorithm-prefix",
        default="hybrid",
        help="Prefix written to the algorithm and case names.",
    )
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    per_rank_bytes = gradient.parse_size(args.per_rank_bytes)
    suffix = gradient.size_name(per_rank_bytes)
    tmp_base = BASE / "_stage_templates" / suffix / args.a_dependency_mode
    a_case, b_case = generate_stage_cases(tmp_base, per_rank_bytes, args.a_dependency_mode)

    summary_rows: list[dict[str, object]] = []
    for ratio in args.max_over_avg:
        scenario = f"max{str(ratio).replace('.', 'p')}x"
        sizes = gradient.gradient_sizes(per_rank_bytes, ratio)
        for mode in args.modes:
            algorithm = f"{args.algorithm_prefix}-{mode}"
            case_dir = CASES / f"generated_topology_ubx16_a2av_{algorithm}_{scenario}_{suffix}"
            meta = build_hybrid_case(case_dir, a_case, b_case, sizes, mode)
            if not args.no_run:
                run_sim(case_dir, args.docker_container, args.mtp_threads)
                summary = summarize_case(case_dir, sizes, meta)
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
                    f"single_rank={summary['single_rank_GBps']:.2f}GB/s "
                    f"network={summary['network_global_GBps'] / gradient.RANK_COUNT:.2f}GB/s/rank"
                )
    if summary_rows:
        write_summary(args.summary, summary_rows)
        print(f"summary={args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
