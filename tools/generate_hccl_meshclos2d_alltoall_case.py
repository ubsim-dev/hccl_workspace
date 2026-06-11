#!/usr/bin/env python3
"""Generate ns-3-ub cases for HCCL AllToAll MeshClos2D V2.

Two TP models are supported:

  strict: model source behavior, one selected TP per rank pair.
          Intra uses channels[0]; inter uses
          (myAlgRank + connectedAlgRank) % channel_count.
  ideal:  keep all TPs for generated rank pairs, allowing ns-3-ub multipath.

The generated traffic models the parallel executor's two-stage 2D alltoall
decomposition. It is intended for uniform AllToAll, not variable AlltoAllV.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from collections import OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CASE = REPO_ROOT / "generated_topology"
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
            return int(float(upper[: -len(unit)].strip()) * units[unit])
    return int(text)


def format_size_for_name(size_bytes: int) -> str:
    for suffix, scale in (("gb", 1024**3), ("mb", 1024**2), ("kb", 1024)):
        if size_bytes % scale == 0:
            return f"{size_bytes // scale}{suffix}"
    return f"{size_bytes}b"


def copy_case_files(source_case: Path, output_case: Path) -> None:
    output_case.mkdir(parents=True, exist_ok=True)
    for filename in COPY_FILES:
        src = source_case / filename
        if not src.exists():
            raise FileNotFoundError(src)
        shutil.copy2(src, output_case / filename)


def patch_network_attributes(path: Path, enable_port_trace: bool, disable_packet_trace: bool) -> None:
    lines = path.read_text().splitlines()
    patched: list[str] = []
    seen_port = False
    seen_packet = False
    for line in lines:
        if line.startswith('global UB_PORT_TRACE_ENABLE '):
            patched.append(f'global UB_PORT_TRACE_ENABLE "{str(enable_port_trace).lower()}"')
            seen_port = True
        elif line.startswith('global UB_RECORD_PKT_TRACE ') and disable_packet_trace:
            patched.append('global UB_RECORD_PKT_TRACE "false"')
            seen_packet = True
        else:
            patched.append(line)
    if not seen_port:
        patched.append(f'global UB_PORT_TRACE_ENABLE "{str(enable_port_trace).lower()}"')
    if disable_packet_trace and not seen_packet:
        patched.append('global UB_RECORD_PKT_TRACE "false"')
    path.write_text("\n".join(patched) + "\n")


def split_even(total: int, parts: int) -> list[int]:
    base, rem = divmod(total, parts)
    return [base + int(i < rem) for i in range(parts)]


def rank_node(rank_start: int, alg_rank: int) -> int:
    return rank_start + alg_rank


def write_traffic(
    output_path: Path,
    rank_start: int,
    rank_count: int,
    group_size: int,
    direct_per_rank_bytes: int,
    split_ratio: float,
    priority: int,
    phase_delay: str,
) -> tuple[int, int, int]:
    group_count = rank_count // group_size
    if group_count < 2:
        raise ValueError("MeshClos2D needs at least two groups")

    # Treat the CLI byte count as direct alltoall network payload, excluding self.
    # Uniform alltoall input has one same-size chunk for every rank including self.
    peer_chunks = split_even(direct_per_rank_bytes, rank_count - 1)
    peer_chunk_min = min(peer_chunks)
    peer_chunk_max = max(peer_chunks)
    if peer_chunk_min != peer_chunk_max:
        print(
            "warning: per-rank bytes is not divisible by rank_count-1; "
            "using the larger peer chunk for MeshClos2D chunk sizing"
        )
    peer_bytes = peer_chunk_max

    intra_stage0_bytes = int(peer_bytes * rank_count * split_ratio) // group_size
    inter_stage0_bytes = (peer_bytes * rank_count - int(peer_bytes * rank_count * split_ratio)) // group_count
    intra_stage1_bytes = (peer_bytes * rank_count - int(peer_bytes * rank_count * split_ratio)) // group_size
    inter_stage1_bytes = int(peer_bytes * rank_count * split_ratio) // group_count

    task_id = 0
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER)
        writer.writeheader()
        for phase in (0, 1):
            depend = "" if phase == 0 else "0"
            intra_bytes = intra_stage0_bytes if phase == 0 else intra_stage1_bytes
            inter_bytes = inter_stage0_bytes if phase == 0 else inter_stage1_bytes

            for src_alg in range(rank_count):
                src_node = rank_node(rank_start, src_alg)
                src_group = src_alg // group_size
                src_x = src_alg % group_size

                if intra_bytes > 0:
                    group_base = src_group * group_size
                    for dst_x in range(group_size):
                        dst_alg = group_base + dst_x
                        if dst_alg == src_alg:
                            continue
                        writer.writerow(
                            {
                                "taskId": task_id,
                                "sourceNodeId": src_node,
                                "destNodeId": rank_node(rank_start, dst_alg),
                                "dataSize(Byte)": intra_bytes,
                                "opType": "URMA_WRITE",
                                "priority": priority,
                                "delay": phase_delay,
                                "phaseId": phase,
                                "dependOnPhases": depend,
                            }
                        )
                        task_id += 1

                if inter_bytes > 0:
                    for dst_group in range(group_count):
                        if dst_group == src_group:
                            continue
                        dst_alg = dst_group * group_size + src_x
                        writer.writerow(
                            {
                                "taskId": task_id,
                                "sourceNodeId": src_node,
                                "destNodeId": rank_node(rank_start, dst_alg),
                                "dataSize(Byte)": inter_bytes,
                                "opType": "URMA_WRITE",
                                "priority": priority,
                                "delay": phase_delay,
                                "phaseId": phase,
                                "dependOnPhases": depend,
                            }
                        )
                        task_id += 1

    if intra_stage0_bytes == intra_stage1_bytes and inter_stage0_bytes == inter_stage1_bytes:
        return task_id, intra_stage0_bytes, inter_stage0_bytes
    return task_id, max(intra_stage0_bytes, intra_stage1_bytes), max(inter_stage0_bytes, inter_stage1_bytes)


def load_tp_rows(source_csv: Path, priority: int) -> dict[tuple[int, int], list[dict[str, str]]]:
    rows_by_pair: dict[tuple[int, int], list[dict[str, str]]] = OrderedDict()
    with source_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{source_csv} has no header")
        for row in reader:
            if int(row["priority"]) != priority:
                continue
            a = int(row["nodeId1"])
            b = int(row["nodeId2"])
            rows_by_pair.setdefault((a, b), []).append(row)
    return rows_by_pair


def generated_pairs(rank_start: int, rank_count: int, group_size: int) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    group_count = rank_count // group_size
    for src_alg in range(rank_count):
        src_group = src_alg // group_size
        src_x = src_alg % group_size
        for dst_x in range(group_size):
            dst_alg = src_group * group_size + dst_x
            if dst_alg != src_alg:
                a = rank_node(rank_start, src_alg)
                b = rank_node(rank_start, dst_alg)
                pairs.add((a, b) if a < b else (b, a))
        for dst_group in range(group_count):
            if dst_group != src_group:
                dst_alg = dst_group * group_size + src_x
                a = rank_node(rank_start, src_alg)
                b = rank_node(rank_start, dst_alg)
                pairs.add((a, b) if a < b else (b, a))
    return pairs


def selected_strict_index(
    pair: tuple[int, int],
    rank_start: int,
    group_size: int,
    group_count: int,
    channel_count: int,
) -> int:
    a_alg = pair[0] - rank_start
    b_alg = pair[1] - rank_start
    a_group, a_x = divmod(a_alg, group_size)
    b_group, b_x = divmod(b_alg, group_size)
    if a_group == b_group:
        return 0
    if a_x != b_x:
        raise ValueError(f"not an inter MeshClos pair: {pair}")
    a_inter_alg = a_group
    b_inter_alg = b_group
    return (a_inter_alg + b_inter_alg) % channel_count


def write_transport_channels(
    source_csv: Path,
    output_csv: Path,
    rank_start: int,
    rank_count: int,
    group_size: int,
    priority: int,
    mode: str,
) -> int:
    rows_by_pair = load_tp_rows(source_csv, priority)
    pairs = generated_pairs(rank_start, rank_count, group_size)
    group_count = rank_count // group_size

    rows_written = 0
    with source_csv.open(newline="") as src_f, output_csv.open("w", newline="") as dst_f:
        reader = csv.DictReader(src_f)
        if reader.fieldnames is None:
            raise ValueError(f"{source_csv} has no header")
        writer = csv.DictWriter(dst_f, fieldnames=reader.fieldnames)
        writer.writeheader()
        for pair in sorted(pairs):
            rows = rows_by_pair.get(pair, [])
            if not rows:
                raise ValueError(f"missing transport channels for pair {pair}")
            if mode == "strict":
                selected = selected_strict_index(pair, rank_start, group_size, group_count, len(rows))
                rows = [rows[selected]]
            for row in rows:
                writer.writerow(row)
                rows_written += 1
    return rows_written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--source-case", type=Path, default=DEFAULT_SOURCE_CASE)
    parser.add_argument("-o", "--output-case", type=Path)
    parser.add_argument("-n", "--rank-count", type=int, required=True)
    parser.add_argument("--rank-start", type=int, default=0)
    parser.add_argument("--group-size", type=int, default=8)
    parser.add_argument("-b", "--direct-per-rank-bytes", required=True, help="Direct alltoall payload excluding self")
    parser.add_argument("--split-ratio", type=float, default=0.5)
    parser.add_argument("--mode", choices=("strict", "ideal"), required=True)
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--phase-delay", default="0ns")
    parser.add_argument("--disable-packet-trace", action="store_true", default=True)
    parser.add_argument("--no-port-trace", action="store_true")
    args = parser.parse_args()

    if args.rank_count % args.group_size != 0:
        raise ValueError("rank-count must be divisible by group-size")
    if not 0.0 <= args.split_ratio <= 1.0:
        raise ValueError("split-ratio must be in [0, 1]")

    source_case = args.source_case.resolve()
    direct_per_rank_bytes = parse_size(args.direct_per_rank_bytes)
    output_case = args.output_case
    if output_case is None:
        size_name = format_size_for_name(direct_per_rank_bytes)
        output_case = (
            REPO_ROOT
            / f"generated_topology_hccl_meshclos2d_{args.mode}_a2a{args.rank_count}_{size_name}"
        )
    output_case = output_case.resolve()

    copy_case_files(source_case, output_case)
    task_count, axis0_bytes, axis1_bytes = write_traffic(
        output_case / "traffic.csv",
        args.rank_start,
        args.rank_count,
        args.group_size,
        direct_per_rank_bytes,
        args.split_ratio,
        args.priority,
        args.phase_delay,
    )
    tp_rows = write_transport_channels(
        source_case / "transport_channel.csv",
        output_case / "transport_channel.csv",
        args.rank_start,
        args.rank_count,
        args.group_size,
        args.priority,
        args.mode,
    )
    patch_network_attributes(
        output_case / "network_attribute.txt",
        enable_port_trace=not args.no_port_trace,
        disable_packet_trace=args.disable_packet_trace,
    )

    print(f"output_case={output_case}")
    print(
        f"rank_ids={args.rank_start}..{args.rank_start + args.rank_count - 1} "
        f"rank_count={args.rank_count} group_size={args.group_size} groups={args.rank_count // args.group_size}"
    )
    print(f"mode={args.mode} direct_per_rank_bytes={direct_per_rank_bytes}")
    print(f"intra_bytes_per_flow={axis0_bytes} inter_bytes_per_flow={axis1_bytes}")
    print(f"phases=2 tasks={task_count} transport_channel_rows={tp_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
