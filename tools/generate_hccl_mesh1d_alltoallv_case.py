#!/usr/bin/env python3
"""Generate an ns-3-ub case for HCCL AlltoAllV Mesh1D AICPU scheduling.

The script copies topology/routing/network files from an existing case,
filters transport_channel.csv to participating ranks, and generates traffic.csv
using the current HCCL Mesh1D baseline schedule:

  - each rank talks to up to ALLTOALLV_DIRECT_FULLMESH_CONCURRENT_SIZE peers per round
  - peers are selected symmetrically around the rank
  - each rank-peer flow is one URMA_WRITE task
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"
COPY_FILES = (
    "node.csv",
    "topology.csv",
    "routing_table.csv",
    "network_attribute.txt",
)
TRAFFIC_HEADER = [
    "taskId",
    "sourceNodeId",
    "destNodeId",
    "dataSize(Byte)",
    "opType",
    "priority",
    "delay",
    "phaseId",
    "dependOnPhases",
]


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
            number = upper[: -len(unit)].strip()
            return int(float(number) * units[unit])
    return int(text)


def format_size_for_name(size_bytes: int) -> str:
    for suffix, scale in (("gb", 1024**3), ("mb", 1024**2), ("kb", 1024)):
        if size_bytes % scale == 0:
            return f"{size_bytes // scale}{suffix}"
    return f"{size_bytes}b"


def mesh1d_phase_peers(rank: int, rank_count: int, phase: int, concurrent: int) -> list[int]:
    pair_num_per_round = (concurrent + 1) // 2
    total_prev = 0
    for prev_phase in range(phase):
        remain = rank_count - 1 - total_prev
        pair_size = (remain + 1) // 2 if remain < concurrent else pair_num_per_round
        count = 0
        for distance in range(
            prev_phase * pair_num_per_round + 1,
            prev_phase * pair_num_per_round + pair_size + 1,
        ):
            left = (rank + rank_count - distance) % rank_count
            right = (rank + distance) % rank_count
            count += 1 if left == right else 2
        total_prev += count

    remain = rank_count - 1 - total_prev
    pair_size = (remain + 1) // 2 if remain < concurrent else pair_num_per_round
    peers: list[int] = []
    for distance in range(
        phase * pair_num_per_round + 1,
        phase * pair_num_per_round + pair_size + 1,
    ):
        left = (rank + rank_count - distance) % rank_count
        right = (rank + distance) % rank_count
        if left == right:
            peers.append(left)
            break
        peers.extend((left, right))
    return peers


def mesh1d_phase_count(rank_count: int, concurrent_limit: int) -> int:
    concurrent = min(concurrent_limit, rank_count - 1)
    return (rank_count - 2 + concurrent) // concurrent


def write_traffic(
    output_path: Path,
    rank_ids: list[int],
    per_rank_bytes: int,
    priority: int,
    phase_delay: str,
    dependency_mode: str,
    concurrent_limit: int,
) -> tuple[int, int, int, int]:
    rank_count = len(rank_ids)
    if rank_count < 2:
        raise ValueError("rank-count must be at least 2")
    per_peer_base = per_rank_bytes // (rank_count - 1)
    per_peer_remainder = per_rank_bytes % (rank_count - 1)
    concurrent = min(concurrent_limit, rank_count - 1)
    phase_count = mesh1d_phase_count(rank_count, concurrent_limit)

    task_id = 0
    phases: set[int] = set()
    last_task_by_unit: dict[tuple[int, int], int] = {}
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER)
        writer.writeheader()
        for phase in range(phase_count):
            for alg_rank, src_node in enumerate(rank_ids):
                peer_offset_in_rank = 0
                for prev_phase in range(phase):
                    peer_offset_in_rank += len(mesh1d_phase_peers(alg_rank, rank_count, prev_phase, concurrent))
                for peer_idx, peer_alg_rank in enumerate(mesh1d_phase_peers(alg_rank, rank_count, phase, concurrent)):
                    data_size = per_peer_base + int(peer_offset_in_rank + peer_idx < per_peer_remainder)
                    if dependency_mode == "phase-barrier":
                        phase_id = phase
                        depend = "" if phase == 0 else str(phase - 1)
                    elif dependency_mode == "thread-serial":
                        phase_id = task_id
                        unit = (alg_rank, peer_idx)
                        previous = last_task_by_unit.get(unit)
                        depend = "" if previous is None else str(previous)
                        last_task_by_unit[unit] = task_id
                    elif dependency_mode == "none":
                        phase_id = 0
                        depend = ""
                    else:
                        raise ValueError(f"unknown dependency_mode {dependency_mode}")
                    phases.add(phase_id)
                    writer.writerow(
                        {
                            "taskId": task_id,
                            "sourceNodeId": src_node,
                            "destNodeId": rank_ids[peer_alg_rank],
                            "dataSize(Byte)": data_size,
                            "opType": "URMA_WRITE",
                            "priority": priority,
                            "delay": phase_delay,
                            "phaseId": phase_id,
                            "dependOnPhases": depend,
                        }
                    )
                    task_id += 1
    return task_id, len(phases), per_peer_base, per_peer_base + int(per_peer_remainder > 0)


def copy_case_files(source_case: Path, output_case: Path) -> None:
    output_case.mkdir(parents=True, exist_ok=True)
    for filename in COPY_FILES:
        src = source_case / filename
        if not src.exists():
            raise FileNotFoundError(src)
        shutil.copy2(src, output_case / filename)


def filter_transport_channels(
    source_csv: Path,
    output_csv: Path,
    rank_ids: set[int],
    priority: int,
    tp_mode: str,
) -> int:
    if not source_csv.exists():
        raise FileNotFoundError(source_csv)
    selected_by_pair: dict[tuple[int, int], dict[str, str]] = {}
    rows_to_write: list[dict[str, str]] = []
    with source_csv.open(newline="") as src_f, output_csv.open("w", newline="") as dst_f:
        reader = csv.DictReader(src_f)
        if reader.fieldnames is None:
            raise ValueError(f"{source_csv} has no header")
        for row in reader:
            if (
                int(row["nodeId1"]) in rank_ids
                and int(row["nodeId2"]) in rank_ids
                and int(row["priority"]) == priority
            ):
                if tp_mode == "full":
                    rows_to_write.append(row)
                elif tp_mode == "single":
                    pair = (int(row["nodeId1"]), int(row["nodeId2"]))
                    prev = selected_by_pair.get(pair)
                    key = (
                        int(row.get("metric", 0)),
                        int(row["portId1"]),
                        int(row["portId2"]),
                        int(row["tpn1"]),
                        int(row["tpn2"]),
                    )
                    if prev is None:
                        selected_by_pair[pair] = row
                    else:
                        prev_key = (
                            int(prev.get("metric", 0)),
                            int(prev["portId1"]),
                            int(prev["portId2"]),
                            int(prev["tpn1"]),
                            int(prev["tpn2"]),
                        )
                        if key < prev_key:
                            selected_by_pair[pair] = row
                else:
                    raise ValueError(f"unknown tp_mode {tp_mode}")
        if tp_mode == "single":
            rows_to_write = [selected_by_pair[pair] for pair in sorted(selected_by_pair)]
        writer = csv.DictWriter(dst_f, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(rows_to_write)
    if len(rows_to_write) == 0:
        raise ValueError(f"no transport channels matched rank set and priority {priority}")
    return len(rows_to_write)


def patch_network_attributes(path: Path, enable_port_trace: bool, disable_packet_trace: bool) -> None:
    lines = path.read_text().splitlines()
    patched: list[str] = []
    seen_port = False
    seen_packet_trace = False
    seen_record_packet = False
    for line in lines:
        if line.startswith('global UB_PORT_TRACE_ENABLE '):
            patched.append(f'global UB_PORT_TRACE_ENABLE "{str(enable_port_trace).lower()}"')
            seen_port = True
        elif line.startswith('global UB_PACKET_TRACE_ENABLE ') and disable_packet_trace:
            patched.append('global UB_PACKET_TRACE_ENABLE "false"')
            seen_packet_trace = True
        elif line.startswith('global UB_RECORD_PKT_TRACE ') and disable_packet_trace:
            patched.append('global UB_RECORD_PKT_TRACE "false"')
            seen_record_packet = True
        else:
            patched.append(line)
    if not seen_port:
        patched.append(f'global UB_PORT_TRACE_ENABLE "{str(enable_port_trace).lower()}"')
    if disable_packet_trace and not seen_packet_trace:
        patched.append('global UB_PACKET_TRACE_ENABLE "false"')
    if disable_packet_trace and not seen_record_packet:
        patched.append('global UB_RECORD_PKT_TRACE "false"')
    path.write_text("\n".join(patched) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--source-case", type=Path, default=DEFAULT_SOURCE_CASE)
    parser.add_argument("-o", "--output-case", type=Path)
    parser.add_argument("-n", "--rank-count", type=int, required=True)
    parser.add_argument("--rank-start", type=int, default=0)
    parser.add_argument("-b", "--per-rank-bytes", required=True, help="Examples: 256MB, 1GB, 16777216")
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument(
        "--concurrent",
        type=int,
        default=16,
        help="ALLTOALLV_DIRECT_FULLMESH_CONCURRENT_SIZE from the HCCL Mesh1D baseline. "
        "Current hccl uses 16; older hccl-xzw experiments used 4.",
    )
    parser.add_argument(
        "--tp-mode",
        choices=("full", "single"),
        default="full",
        help="full keeps all transport channels for each pair; single keeps one TP per directed pair.",
    )
    parser.add_argument("--phase-delay", default="0ns")
    parser.add_argument(
        "--dependency-mode",
        choices=("thread-serial", "phase-barrier", "none"),
        default="thread-serial",
        help="thread-serial chains the same source's Mesh1D peer slot across rounds, "
        "matching thread-queue ordering without a global round barrier. "
        "phase-barrier keeps the older conservative model.",
    )
    parser.add_argument("--disable-packet-trace", action="store_true", default=True)
    parser.add_argument("--port-trace", dest="port_trace", action="store_true", help="Enable port-level trace output.")
    parser.add_argument("--no-port-trace", dest="port_trace", action="store_false", help="Disable port-level trace output.")
    parser.set_defaults(port_trace=False)
    args = parser.parse_args()

    source_case = args.source_case.resolve()
    per_rank_bytes = parse_size(args.per_rank_bytes)
    rank_ids = list(range(args.rank_start, args.rank_start + args.rank_count))
    output_case = args.output_case
    if output_case is None:
        size_name = format_size_for_name(per_rank_bytes)
        output_case = (
            REPO_ROOT
            / "experiments/ubx16/alltoall/cases"
            / f"generated_topology_ubx16_hccl_baseline_threadserial_a2a{args.rank_count}_{size_name}"
        )
    output_case = output_case.resolve()

    copy_case_files(source_case, output_case)
    tp_rows = filter_transport_channels(
        source_case / "transport_channel.csv",
        output_case / "transport_channel.csv",
        set(rank_ids),
        args.priority,
        args.tp_mode,
    )
    task_count, phase_count, per_peer_min_bytes, per_peer_max_bytes = write_traffic(
        output_case / "traffic.csv",
        rank_ids,
        per_rank_bytes,
        args.priority,
        args.phase_delay,
        args.dependency_mode,
        args.concurrent,
    )
    patch_network_attributes(
        output_case / "network_attribute.txt",
        enable_port_trace=args.port_trace,
        disable_packet_trace=args.disable_packet_trace,
    )

    print(f"output_case={output_case}")
    print(f"rank_ids={rank_ids[0]}..{rank_ids[-1]} rank_count={len(rank_ids)}")
    print(
        f"per_rank_bytes={per_rank_bytes} "
        f"per_peer_bytes={per_peer_min_bytes}..{per_peer_max_bytes}"
    )
    print(
        f"dependency_mode={args.dependency_mode} tp_mode={args.tp_mode} "
        f"concurrent={args.concurrent} phases={phase_count} tasks={task_count} "
        f"transport_channel_rows={tp_rows}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
