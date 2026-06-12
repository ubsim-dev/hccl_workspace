#!/usr/bin/env python3
"""Generate ns-3-ub cases for HCCL AllToAll MeshClos2D V3.

strict:
  - intra group pairs use channels[0], matching Mesh2DV3.
  - inter group pairs use the V3 shift/link schedule, spreading peers over
    channel indices.

ideal:
  - same traffic, but keep all TPs for each generated pair.

Traffic is emitted in V3 logical order. By default all tasks stay in one ns-3
phase because the V3 executor has one stage and the per-link step loop is not a
proven global barrier. Use --dependency-mode v3-thread-serial to model each
algorithm thread/channel as a serial queue while keeping different threads
parallel.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from collections import OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CASE = REPO_ROOT / "generated_topology"
COPY_FILES = ("node.csv", "topology.csv", "routing_table.csv", "network_attribute.txt")
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
        shutil.copy2(source_case / filename, output_case / filename)


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
    direct_per_rank_bytes: int,
    priority: int,
    phase_delay: str,
) -> tuple[int, int, int]:
    peer_sizes = split_even(direct_per_rank_bytes, rank_count - 1)
    task_id = 0
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER)
        writer.writeheader()
        for src_alg in range(rank_count):
            peer_idx = 0
            for dst_alg in range(rank_count):
                if dst_alg == src_alg:
                    continue
                writer.writerow(
                    {
                        "taskId": task_id,
                        "sourceNodeId": rank_node(rank_start, src_alg),
                        "destNodeId": rank_node(rank_start, dst_alg),
                        "dataSize(Byte)": peer_sizes[peer_idx],
                        "opType": "URMA_WRITE",
                        "priority": priority,
                        "delay": phase_delay,
                        "phaseId": 0,
                        "dependOnPhases": "",
                    }
                )
                task_id += 1
                peer_idx += 1
    return task_id, min(peer_sizes), max(peer_sizes)


def peer_size_map(rank_count: int, direct_per_rank_bytes: int) -> dict[tuple[int, int], int]:
    peer_sizes = split_even(direct_per_rank_bytes, rank_count - 1)
    sizes: dict[tuple[int, int], int] = {}
    for src_alg in range(rank_count):
        peer_idx = 0
        for dst_alg in range(rank_count):
            if dst_alg == src_alg:
                continue
            sizes[(src_alg, dst_alg)] = peer_sizes[peer_idx]
            peer_idx += 1
    return sizes


def infer_inter_channel_count(
    source_csv: Path,
    rank_start: int,
    rank_count: int,
    group_size: int,
    priority: int,
) -> int:
    rows_by_pair = load_tp_rows(source_csv, priority)
    channel_count = 0
    for pair in generated_pairs(rank_start, rank_count):
        a_alg = pair[0] - rank_start
        b_alg = pair[1] - rank_start
        if a_alg // group_size == b_alg // group_size:
            continue
        channel_count = max(channel_count, len(rows_by_pair.get(pair, [])))
    if channel_count == 0:
        raise ValueError("could not infer inter channel count from transport_channel.csv")
    return channel_count


def write_v3_logical_traffic(
    output_path: Path,
    rank_start: int,
    rank_count: int,
    group_size: int,
    clos_channel_count: int,
    direct_per_rank_bytes: int,
    priority: int,
    phase_delay: str,
    dependency_mode: str,
) -> tuple[int, int, int, int]:
    if rank_count % group_size != 0:
        raise ValueError("rank_count must be divisible by group_size")
    if clos_channel_count <= 0:
        raise ValueError("clos_channel_count must be positive")

    sizes = peer_size_map(rank_count, direct_per_rank_bytes)
    all_sizes = list(sizes.values())
    task_id = 0
    seen: set[tuple[int, int]] = set()
    last_task_by_unit: dict[tuple[str, int, int], int] = {}
    phases: set[int] = set()

    def write_row(
        writer: csv.DictWriter,
        src_alg: int,
        dst_alg: int,
        unit: tuple[str, int, int],
    ) -> None:
        nonlocal task_id
        key = (src_alg, dst_alg)
        if key in seen:
            raise ValueError(f"duplicate traffic pair {key}")
        seen.add(key)
        if dependency_mode == "none":
            phase_id = 0
            depend_on = ""
        elif dependency_mode == "v3-thread-serial":
            phase_id = task_id
            previous = last_task_by_unit.get(unit)
            depend_on = "" if previous is None else str(previous)
            last_task_by_unit[unit] = task_id
        else:
            raise ValueError(f"unknown dependency_mode {dependency_mode}")
        phases.add(phase_id)
        writer.writerow(
            {
                "taskId": task_id,
                "sourceNodeId": rank_node(rank_start, src_alg),
                "destNodeId": rank_node(rank_start, dst_alg),
                "dataSize(Byte)": sizes[key],
                "opType": "URMA_WRITE",
                "priority": priority,
                "delay": phase_delay,
                "phaseId": phase_id,
                "dependOnPhases": depend_on,
            }
        )
        task_id += 1

    group_num = rank_count // group_size
    color_round_num = pairwise_round_num(group_num)
    inter_steps = 0
    if color_round_num > 0:
        inter_steps = (group_size * color_round_num + clos_channel_count - 1) // clos_channel_count

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER)
        writer.writeheader()

        # Mesh2DV3: for each rank, connectedRank is selected by neighborIdx.
        # Emit by neighborIdx first so taskId order reflects logical mesh units.
        for neighbor_idx in range(group_size - 1):
            for src_alg in range(rank_count):
                group_base = (src_alg // group_size) * group_size
                local_rank = src_alg % group_size
                dst_alg = group_base + ((local_rank + 1 + neighbor_idx) % group_size)
                write_row(writer, src_alg, dst_alg, ("mesh", src_alg, neighbor_idx))

        # MeshClosV3: for step then linkIdx, each source rank maps to one peer.
        # This is the source algorithm order, but no phase dependency is added.
        for step in range(inter_steps):
            for link_idx in range(clos_channel_count):
                micro_round = step * clos_channel_count + link_idx
                if color_round_num == 0:
                    continue
                shift = micro_round // color_round_num
                color_round = micro_round % color_round_num
                if shift >= group_size:
                    continue
                for src_alg in range(rank_count):
                    my_group, my_local = divmod(src_alg, group_size)
                    peer_group, my_group_is_left = pair_group_in_round(group_num, my_group, color_round)
                    if peer_group == INVALID_GROUP_ID:
                        continue
                    if my_group_is_left:
                        connected_local = (my_local + shift) % group_size
                    else:
                        connected_local = (my_local + group_size - shift % group_size) % group_size
                    dst_alg = peer_group * group_size + connected_local
                    write_row(writer, src_alg, dst_alg, ("clos", src_alg, link_idx))

    expected = rank_count * (rank_count - 1)
    if task_id != expected:
        missing = expected - task_id
        raise ValueError(f"V3 traffic wrote {task_id} tasks, expected {expected}, missing={missing}")
    return task_id, min(all_sizes), max(all_sizes), len(phases)


def generated_pairs(rank_start: int, rank_count: int) -> set[tuple[int, int]]:
    pairs = set()
    for a_alg in range(rank_count):
        for b_alg in range(a_alg + 1, rank_count):
            pairs.add((rank_node(rank_start, a_alg), rank_node(rank_start, b_alg)))
    return pairs


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


INVALID_GROUP_ID = -1


def pairwise_round_num(group_num: int) -> int:
    if group_num <= 1:
        return 0
    return group_num - 1 if group_num % 2 == 0 else group_num


def pair_group_in_round(group_num: int, my_group: int, round_id: int) -> tuple[int, bool]:
    if group_num <= 1 or my_group >= group_num:
        return INVALID_GROUP_ID, True

    schedule_group_num = group_num if group_num % 2 == 0 else group_num + 1
    round_num = schedule_group_num - 1
    dummy_group = group_num
    groups = list(range(schedule_group_num))

    for _ in range(round_id % round_num):
        last = groups[-1]
        for idx in range(schedule_group_num - 1, 1, -1):
            groups[idx] = groups[idx - 1]
        groups[1] = last

    for idx in range(schedule_group_num // 2):
        left = groups[idx]
        right = groups[schedule_group_num - 1 - idx]
        if left == my_group:
            return (INVALID_GROUP_ID if right == dummy_group else right), True
        if right == my_group:
            return (INVALID_GROUP_ID if left == dummy_group else left), False
    return INVALID_GROUP_ID, True


def selected_strict_index(
    pair: tuple[int, int],
    rank_start: int,
    rank_count: int,
    group_size: int,
    channel_count: int,
) -> int:
    a_alg = pair[0] - rank_start
    b_alg = pair[1] - rank_start
    a_group, a_local = divmod(a_alg, group_size)
    b_group, b_local = divmod(b_alg, group_size)
    if a_group == b_group:
        return 0

    group_num = rank_count // group_size
    color_round_num = pairwise_round_num(group_num)
    if color_round_num == 0:
        return 0

    for color_round in range(color_round_num):
        peer_group, a_group_is_left = pair_group_in_round(group_num, a_group, color_round)
        if peer_group != b_group:
            continue
        if a_group_is_left:
            shift = (b_local - a_local) % group_size
        else:
            shift = (a_local - b_local) % group_size
        micro_round = shift * color_round_num + color_round
        return micro_round % channel_count

    raise ValueError(f"groups {a_group} and {b_group} are never paired")


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
    pairs = generated_pairs(rank_start, rank_count)
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
                rows = [rows[selected_strict_index(pair, rank_start, rank_count, group_size, len(rows))]]
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
    parser.add_argument("-b", "--direct-per-rank-bytes", required=True)
    parser.add_argument("--mode", choices=("strict", "ideal"), required=True)
    parser.add_argument(
        "--traffic-order",
        choices=("v3-logical", "allpair"),
        default="v3-logical",
        help="v3-logical emits Mesh2D neighborIdx then MeshClosV3 step/linkIdx order; "
        "allpair emits plain source-major all-to-all.",
    )
    parser.add_argument(
        "--dependency-mode",
        choices=("none", "v3-thread-serial"),
        default="none",
        help="none keeps all generated V3 tasks in one runnable phase; "
        "v3-thread-serial chains tasks on the same source algorithm thread/channel "
        "using phaseId/dependOnPhases.",
    )
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--phase-delay", default="0ns")
    parser.add_argument("--disable-packet-trace", action="store_true", default=True)
    parser.add_argument("--no-port-trace", action="store_true")
    args = parser.parse_args()

    if args.rank_count % args.group_size != 0:
        raise ValueError("rank-count must be divisible by group-size")

    source_case = args.source_case.resolve()
    direct_per_rank_bytes = parse_size(args.direct_per_rank_bytes)
    output_case = args.output_case
    if output_case is None:
        size_name = format_size_for_name(direct_per_rank_bytes)
        output_case = REPO_ROOT / f"generated_topology_hccl_meshclos2d_v3_{args.mode}_a2a{args.rank_count}_{size_name}"
    output_case = output_case.resolve()

    copy_case_files(source_case, output_case)
    if args.traffic_order == "allpair":
        if args.dependency_mode != "none":
            raise ValueError("--dependency-mode is only supported with --traffic-order v3-logical")
        task_count, peer_min, peer_max = write_traffic(
            output_case / "traffic.csv",
            args.rank_start,
            args.rank_count,
            direct_per_rank_bytes,
            args.priority,
            args.phase_delay,
        )
        phase_count = 1
    else:
        clos_channel_count = infer_inter_channel_count(
            source_case / "transport_channel.csv",
            args.rank_start,
            args.rank_count,
            args.group_size,
            args.priority,
        )
        task_count, peer_min, peer_max, phase_count = write_v3_logical_traffic(
            output_case / "traffic.csv",
            args.rank_start,
            args.rank_count,
            args.group_size,
            clos_channel_count,
            direct_per_rank_bytes,
            args.priority,
            args.phase_delay,
            args.dependency_mode,
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
        f"rank_count={args.rank_count} group_size={args.group_size}"
    )
    print(
        f"mode={args.mode} traffic_order={args.traffic_order} "
        f"dependency_mode={args.dependency_mode} direct_per_rank_bytes={direct_per_rank_bytes}"
    )
    print(f"per_peer_bytes={peer_min}..{peer_max}")
    print(f"phases={phase_count} tasks={task_count} transport_channel_rows={tp_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
