#!/usr/bin/env python3
"""Generate an ns-3-ub case for current HCCL UBX matrix AllToAll(V).

This models InsTempAlltoAllVMesh1D's UBX matrix path in the hccl repo:

  - 16 ranks are treated as a 4x4 matrix.
  - each communication round has 1 single-channel slot plus 4 clos-plane slots.
  - algRank(row, col) is mapped to physical nodeId = col * 4 + row so the
    source code's same-column single-channel slot matches UBX group-local mesh.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
from collections import OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"
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


def split_even(total: int, parts: int) -> list[int]:
    base, rem = divmod(total, parts)
    return [base + int(i < rem) for i in range(parts)]


def matrix_dim(rank_count: int) -> int:
    dim = math.isqrt(rank_count)
    if dim * dim != rank_count:
        raise ValueError("rank-count must be a perfect square for HCCL matrix alltoall")
    return dim


def alg_to_node(alg_rank: int, dim: int, rank_start: int) -> int:
    row, col = divmod(alg_rank, dim)
    return rank_start + col * dim + row


def node_to_alg(node_id: int, dim: int, rank_start: int) -> int:
    physical = node_id - rank_start
    group, local = divmod(physical, dim)
    return local * dim + group


def matrix_rank(row: int, col: int, dim: int) -> int:
    return row * dim + col


def round_slots(round_id: int, my_alg_rank: int, dim: int) -> list[dict[str, int | bool]]:
    if round_id == 0 or round_id >= dim:
        raise ValueError(f"invalid matrix round {round_id}")

    my_row, my_col = divmod(my_alg_rank, dim)
    tx_col = (my_col + round_id) % dim
    rx_col = (my_col + dim - round_id) % dim

    slots: list[dict[str, int | bool]] = [
        {
            "slot_idx": 0,
            "tx_alg": matrix_rank((my_row + round_id) % dim, my_col, dim),
            "rx_alg": matrix_rank((my_row + dim - round_id) % dim, my_col, dim),
            "channel_idx": 0,
            "is_mesh": True,
        }
    ]
    for plane in range(dim):
        peer_row = (plane + dim - my_row) % dim
        slots.append(
            {
                "slot_idx": plane + 1,
                "tx_alg": matrix_rank(peer_row, tx_col, dim),
                "rx_alg": matrix_rank(peer_row, rx_col, dim),
                "channel_idx": plane,
                "is_mesh": False,
            }
        )
    return slots


def peer_size_map(rank_count: int, per_rank_bytes: int) -> dict[tuple[int, int], int]:
    sizes = split_even(per_rank_bytes, rank_count - 1)
    result: dict[tuple[int, int], int] = {}
    for src_alg in range(rank_count):
        idx = 0
        for dst_alg in range(rank_count):
            if dst_alg == src_alg:
                continue
            result[(src_alg, dst_alg)] = sizes[idx]
            idx += 1
    return result


def write_traffic(
    output_path: Path,
    rank_start: int,
    rank_count: int,
    per_rank_bytes: int,
    priority: int,
    phase_delay: str,
    dependency_mode: str,
) -> tuple[int, int, int, int]:
    dim = matrix_dim(rank_count)
    sizes = peer_size_map(rank_count, per_rank_bytes)
    task_id = 0
    seen: set[tuple[int, int]] = set()
    last_task_by_slot: dict[tuple[int, int], int] = {}
    phases: set[int] = set()

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER)
        writer.writeheader()
        for round_id in range(1, dim):
            for src_alg in range(rank_count):
                for slot in round_slots(round_id, src_alg, dim):
                    dst_alg = int(slot["tx_alg"])
                    key = (src_alg, dst_alg)
                    if key in seen:
                        raise ValueError(f"duplicate matrix pair {key}")
                    seen.add(key)

                    if dependency_mode == "none":
                        phase_id = 0
                        depend = ""
                    elif dependency_mode == "round-barrier":
                        phase_id = round_id - 1
                        depend = "" if round_id == 1 else str(round_id - 2)
                    elif dependency_mode == "thread-serial":
                        phase_id = task_id
                        unit = (src_alg, int(slot["slot_idx"]))
                        previous = last_task_by_slot.get(unit)
                        depend = "" if previous is None else str(previous)
                        last_task_by_slot[unit] = task_id
                    else:
                        raise ValueError(f"unknown dependency mode {dependency_mode}")
                    phases.add(phase_id)

                    writer.writerow(
                        {
                            "taskId": task_id,
                            "sourceNodeId": alg_to_node(src_alg, dim, rank_start),
                            "destNodeId": alg_to_node(dst_alg, dim, rank_start),
                            "dataSize(Byte)": sizes[key],
                            "opType": "URMA_WRITE",
                            "priority": priority,
                            "delay": phase_delay,
                            "phaseId": phase_id,
                            "dependOnPhases": depend,
                        }
                    )
                    task_id += 1

    expected = rank_count * (rank_count - 1)
    if task_id != expected:
        raise ValueError(f"wrote {task_id} tasks, expected {expected}")
    all_sizes = list(sizes.values())
    return task_id, min(all_sizes), max(all_sizes), len(phases)


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
            rows_by_pair.setdefault(tuple(sorted((a, b))), []).append(row)
    return rows_by_pair


def load_direct_host_ports(topology_csv: Path, rank_start: int, rank_count: int) -> dict[tuple[int, int], tuple[int, int]]:
    direct_ports: dict[tuple[int, int], tuple[int, int]] = {}
    rank_end = rank_start + rank_count
    with topology_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = int(row["nodeId1"])
            b = int(row["nodeId2"])
            if not (rank_start <= a < rank_end and rank_start <= b < rank_end):
                continue
            pa = int(row["portId1"])
            pb = int(row["portId2"])
            if a < b:
                direct_ports[(a, b)] = (pa, pb)
            else:
                direct_ports[(b, a)] = (pb, pa)
    return direct_ports


def selected_strict_indices(rank_start: int, rank_count: int) -> dict[tuple[int, int], set[int]]:
    dim = matrix_dim(rank_count)
    selected: dict[tuple[int, int], set[int]] = {}
    for round_id in range(1, dim):
        for src_alg in range(rank_count):
            src_node = alg_to_node(src_alg, dim, rank_start)
            for slot in round_slots(round_id, src_alg, dim):
                dst_node = alg_to_node(int(slot["tx_alg"]), dim, rank_start)
                pair = tuple(sorted((src_node, dst_node)))
                if bool(slot["is_mesh"]):
                    selected.setdefault(pair, set()).add(0)
                else:
                    selected.setdefault(pair, set()).add(int(slot["channel_idx"]))
    return selected


def write_transport_channels(
    source_case: Path,
    output_csv: Path,
    rank_start: int,
    rank_count: int,
    priority: int,
    mode: str,
) -> int:
    source_csv = source_case / "transport_channel.csv"
    rows_by_pair = load_tp_rows(source_csv, priority)
    selected = selected_strict_indices(rank_start, rank_count)
    direct_ports = load_direct_host_ports(source_case / "topology.csv", rank_start, rank_count)
    rows_written = 0
    with source_csv.open(newline="") as src_f, output_csv.open("w", newline="") as dst_f:
        reader = csv.DictReader(src_f)
        if reader.fieldnames is None:
            raise ValueError(f"{source_csv} has no header")
        writer = csv.DictWriter(dst_f, fieldnames=reader.fieldnames)
        writer.writeheader()
        for pair in sorted(selected):
            rows = rows_by_pair.get(pair, [])
            if not rows:
                raise ValueError(f"missing transport channels for pair {pair}")
            if mode == "strict":
                for idx in sorted(selected[pair]):
                    if idx == 0 and pair in direct_ports:
                        direct = direct_ports[pair]
                        direct_row = next(
                            (
                                row for row in rows
                                if int(row["portId1"]) == direct[0] and int(row["portId2"]) == direct[1]
                            ),
                            None,
                        )
                        if direct_row is not None:
                            writer.writerow(direct_row)
                            rows_written += 1
                            continue
                    if idx >= len(rows):
                        raise ValueError(f"pair {pair} has {len(rows)} channels, need index {idx}")
                    writer.writerow(rows[idx])
                    rows_written += 1
            elif mode == "ideal":
                for row in rows:
                    writer.writerow(row)
                    rows_written += 1
            else:
                raise ValueError(f"unknown mode {mode}")
    return rows_written


def copy_case_files(source_case: Path, output_case: Path) -> None:
    output_case.mkdir(parents=True, exist_ok=True)
    for filename in COPY_FILES:
        shutil.copy2(source_case / filename, output_case / filename)


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
    parser.add_argument("-n", "--rank-count", type=int, default=16)
    parser.add_argument("--rank-start", type=int, default=0)
    parser.add_argument("-b", "--direct-per-rank-bytes", required=True)
    parser.add_argument("--mode", choices=("strict", "ideal"), default="strict")
    parser.add_argument(
        "--dependency-mode",
        choices=("round-barrier", "thread-serial", "none"),
        default="round-barrier",
    )
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--phase-delay", default="0ns")
    parser.add_argument("--disable-packet-trace", action="store_true", default=True)
    parser.add_argument("--port-trace", dest="port_trace", action="store_true", help="Enable port-level trace output.")
    parser.add_argument("--no-port-trace", dest="port_trace", action="store_false", help="Disable port-level trace output.")
    parser.set_defaults(port_trace=False)
    args = parser.parse_args()

    source_case = args.source_case.resolve()
    per_rank_bytes = parse_size(args.direct_per_rank_bytes)
    output_case = args.output_case
    if output_case is None:
        size_name = format_size_for_name(per_rank_bytes)
        output_case = (
            REPO_ROOT
            / "experiments/ubx16/alltoall/cases"
            / f"generated_topology_ubx16_hccl_matrix_{args.mode}_{args.dependency_mode}_a2a{args.rank_count}_{size_name}"
        )
    output_case = output_case.resolve()

    copy_case_files(source_case, output_case)
    task_count, peer_min, peer_max, phase_count = write_traffic(
        output_case / "traffic.csv",
        args.rank_start,
        args.rank_count,
        per_rank_bytes,
        args.priority,
        args.phase_delay,
        args.dependency_mode,
    )
    tp_rows = write_transport_channels(
        source_case,
        output_case / "transport_channel.csv",
        args.rank_start,
        args.rank_count,
        args.priority,
        args.mode,
    )
    patch_network_attributes(
        output_case / "network_attribute.txt",
        enable_port_trace=args.port_trace,
        disable_packet_trace=args.disable_packet_trace,
    )

    print(f"output_case={output_case}")
    print(f"rank_count={args.rank_count} matrix_dim={matrix_dim(args.rank_count)}")
    print(f"mode={args.mode} dependency_mode={args.dependency_mode}")
    print(f"direct_per_rank_bytes={per_rank_bytes} per_peer_bytes={peer_min}..{peer_max}")
    print(f"phases={phase_count} tasks={task_count} transport_channel_rows={tp_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
