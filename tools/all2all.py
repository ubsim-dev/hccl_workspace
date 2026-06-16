#!/usr/bin/env python3
"""Generate intra-oneshot cross-plane-round-symm All-to-All traffic."""

from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRATCH_DIR = ROOT / "scratch"
BASE_CASE = SCRATCH_DIR / "20260518-ubx-4x4-clos-fullmesh"
DEFAULT_CASE = SCRATCH_DIR / "20260519-ubx-a2a-intra-oneshot-cross-plane-round-symm-separated-1mib"
ALGORITHM = "intra-oneshot-cross-plane-round-symm"
ALGORITHM_LABEL = "intra-oneshot cross-plane-round symm"
PRIORITY = 7
HOST_L1_PORT_BASE = 4
DEFAULT_MESSAGE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Link:
    link_id: int
    a: int
    a_port: int
    b: int
    b_port: int
    bandwidth: str
    delay: str


@dataclass(frozen=True)
class Segment:
    src: int
    dst: int
    link_id: int
    src_port: int
    dst_port: int

    @property
    def directed_key(self) -> str:
        return f"{self.src}:{self.src_port}->{self.dst}:{self.dst_port}"


@dataclass(frozen=True)
class Transfer:
    task_id: int
    origin_src: int
    final_dst: int
    source_node: int
    dest_node: int
    bytes: int
    phase_id: int
    depend_on_phases: tuple[int, ...]
    network_class: str
    candidate_plane: int
    round_id: int
    plane: int
    slice_id: int
    axis: str
    hop_kind: str
    hop_index: str
    path_nodes: tuple[int, ...]
    path_segments: tuple[Segment, ...]


def rank(group_id: int, slot_id: int, group_size: int) -> int:
    return group_id * group_size + slot_id


def group(rank_id: int, group_size: int) -> int:
    return rank_id // group_size


def slot(rank_id: int, group_size: int) -> int:
    return rank_id % group_size


def l1(rank_count: int, plane_id: int) -> int:
    return rank_count + plane_id


def deps_text(deps: tuple[int, ...]) -> str:
    return " ".join(str(dep) for dep in deps)


def bytes_slug(num_bytes: int) -> str:
    if num_bytes % (1024 * 1024) == 0:
        return f"{num_bytes // (1024 * 1024)}mib"
    if num_bytes % 1024 == 0:
        return f"{num_bytes // 1024}kib"
    return f"{num_bytes}b"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_links(base_case: Path) -> dict[tuple[int, int], list[Link]]:
    by_pair: dict[tuple[int, int], list[Link]] = defaultdict(list)
    with (base_case / "topology.csv").open("r", encoding="utf-8", newline="") as f:
        for link_id, row in enumerate(csv.DictReader(f)):
            link = Link(
                link_id=link_id,
                a=int(row["nodeId1"]),
                a_port=int(row["portId1"]),
                b=int(row["nodeId2"]),
                b_port=int(row["portId2"]),
                bandwidth=row["bandwidth"],
                delay=row["delay"],
            )
            by_pair[tuple(sorted((link.a, link.b)))].append(link)
    return by_pair


def endpoint_ports(link: Link, src: int, dst: int) -> tuple[int, int]:
    if link.a == src and link.b == dst:
        return link.a_port, link.b_port
    if link.a == dst and link.b == src:
        return link.b_port, link.a_port
    raise ValueError(f"link {link} does not connect {src}->{dst}")


def choose_segment(
    by_pair: dict[tuple[int, int], list[Link]],
    src: int,
    dst: int,
    src_port: int | None = None,
) -> Segment:
    candidates = by_pair.get(tuple(sorted((src, dst))), [])
    if not candidates:
        raise AssertionError(f"missing link {src}->{dst}")
    if src_port is None:
        link = candidates[0]
    else:
        matches = [link for link in candidates if endpoint_ports(link, src, dst)[0] == src_port]
        if not matches:
            raise AssertionError(f"missing link {src}->{dst} from source port {src_port}")
        link = matches[0]
    out_port, in_port = endpoint_ports(link, src, dst)
    return Segment(src=src, dst=dst, link_id=link.link_id, src_port=out_port, dst_port=in_port)


def local_path(
    by_pair: dict[tuple[int, int], list[Link]],
    src: int,
    dst: int,
) -> tuple[tuple[int, ...], tuple[Segment, ...]]:
    return (src, dst), (choose_segment(by_pair, src, dst),)


def cross_path(
    by_pair: dict[tuple[int, int], list[Link]],
    src: int,
    dst: int,
    plane_id: int,
    *,
    rank_count: int,
) -> tuple[tuple[int, ...], tuple[Segment, ...]]:
    middle = l1(rank_count, plane_id)
    nodes = (src, middle, dst)
    segments = (
        choose_segment(by_pair, src, middle, HOST_L1_PORT_BASE + plane_id),
        choose_segment(by_pair, middle, dst, dst),
    )
    return nodes, segments


def board_pairs_by_round(group_count: int) -> list[list[tuple[int, int]]]:
    if group_count % 2 != 0:
        raise ValueError("bidirectional board matching requires an even group_count")
    boards = list(range(group_count))
    fixed = boards[0]
    rotating = boards[1:]
    rounds: list[list[tuple[int, int]]] = []
    for _round_id in range(group_count - 1):
        left = [fixed] + rotating[: (group_count // 2) - 1]
        right = list(reversed(rotating[(group_count // 2) - 1 :]))
        rounds.append(list(zip(left, right)))
        rotating = [rotating[-1]] + rotating[:-1]
    return list(reversed(rounds))


def build_transfers(
    *,
    group_count: int,
    group_size: int,
    plane_count: int,
    message_bytes: int,
    base_case: Path = BASE_CASE,
) -> list[Transfer]:
    if group_count <= 1:
        raise ValueError("group_count must be greater than 1")
    if group_size <= 1:
        raise ValueError("group_size must be greater than 1")
    if group_count != group_size:
        raise ValueError("first implementation requires group_count == group_size")
    if plane_count != group_size:
        raise ValueError("first implementation requires plane_count == group_size")
    if message_bytes <= 0:
        raise ValueError("message_bytes must be positive")

    by_pair = load_links(base_case)
    rank_count = group_count * group_size
    local_phase_id = group_count - 1
    transfers: list[Transfer] = []
    next_slice_id = 0

    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_slot in range(group_size):
                if dst_slot == src_slot:
                    continue
                dst = rank(src_group, dst_slot, group_size)
                nodes, segments = local_path(by_pair, src, dst)
                transfers.append(
                    Transfer(
                        task_id=len(transfers),
                        origin_src=src,
                        final_dst=dst,
                        source_node=src,
                        dest_node=dst,
                        bytes=message_bytes,
                        phase_id=local_phase_id,
                        depend_on_phases=(),
                        network_class="local",
                        candidate_plane=-1,
                        round_id=-1,
                        plane=-1,
                        slice_id=next_slice_id,
                        axis="I",
                        hop_kind="intra-oneshot",
                        hop_index="local",
                        path_nodes=nodes,
                        path_segments=segments,
                    )
                )
                next_slice_id += 1

    for round_id, board_pairs in enumerate(board_pairs_by_round(group_count)):
        depend = () if round_id == 0 else (round_id - 1,)
        for group_a, group_b in board_pairs:
            for src_group, dst_group in ((group_a, group_b), (group_b, group_a)):
                for src_slot in range(group_size):
                    src = rank(src_group, src_slot, group_size)
                    for dst_slot in range(group_size):
                        plane_id = (src_slot + dst_slot) % plane_count
                        dst = rank(dst_group, dst_slot, group_size)
                        nodes, segments = cross_path(
                            by_pair,
                            src,
                            dst,
                            plane_id,
                            rank_count=rank_count,
                        )
                        transfers.append(
                            Transfer(
                                task_id=len(transfers),
                                origin_src=src,
                                final_dst=dst,
                                source_node=src,
                                dest_node=dst,
                                bytes=message_bytes,
                                phase_id=round_id,
                                depend_on_phases=depend,
                                network_class="clos",
                                candidate_plane=plane_id,
                                round_id=round_id,
                                plane=plane_id,
                                slice_id=next_slice_id,
                                axis="C",
                                hop_kind="cross-plane-round",
                                hop_index=f"r{round_id}-p{plane_id}",
                                path_nodes=nodes,
                                path_segments=segments,
                            )
                        )
                        next_slice_id += 1

    return transfers


def check_alltoall(
    transfers: list[Transfer],
    *,
    group_count: int,
    group_size: int,
    plane_count: int,
    message_bytes: int,
) -> dict[str, object]:
    rank_count = group_count * group_size
    local_phase_id = group_count - 1
    expected_pairs = {(src, dst) for src in range(rank_count) for dst in range(rank_count) if src != dst}
    pair_counts = Counter((item.origin_src, item.final_dst) for item in transfers)
    actual_pairs = set(pair_counts)
    local = [item for item in transfers if item.network_class == "local"]
    cross = [item for item in transfers if item.network_class == "clos"]

    errors: list[str] = []
    if actual_pairs != expected_pairs:
        errors.append("final pair coverage mismatch")
    duplicate_pairs = sorted((src, dst, count) for (src, dst), count in pair_counts.items() if count != 1)
    if duplicate_pairs:
        errors.append("duplicate final pairs")
    if any(item.origin_src == item.final_dst for item in transfers):
        errors.append("self traffic exists")

    expected_local = rank_count * (group_size - 1)
    expected_cross = rank_count * group_size * (group_count - 1)
    if len(local) != expected_local:
        errors.append(f"local task count mismatch: {len(local)} != {expected_local}")
    if len(cross) != expected_cross:
        errors.append(f"cross task count mismatch: {len(cross)} != {expected_cross}")

    for item in local:
        if group(item.origin_src, group_size) != group(item.final_dst, group_size):
            errors.append(f"local task {item.task_id} crosses groups")
        if item.phase_id != local_phase_id:
            errors.append(f"local task {item.task_id} uses wrong phase")
        if item.depend_on_phases:
            errors.append(f"local task {item.task_id} has dependencies")
        if item.candidate_plane != -1:
            errors.append(f"local task {item.task_id} has candidate plane")

    src_round_counts = Counter((item.source_node, item.round_id) for item in cross)
    src_round_planes: dict[tuple[int, int], set[int]] = defaultdict(set)
    round_plane_counts: dict[int, Counter[int]] = defaultdict(Counter)
    round_board_targets: dict[tuple[int, int], set[int]] = defaultdict(set)
    pair_phase: dict[tuple[int, int], int] = {}
    expected_board_pairs_by_round = {
        round_id: set()
        for round_id in range(group_count - 1)
    }
    for round_id, board_pairs in enumerate(board_pairs_by_round(group_count)):
        for group_a, group_b in board_pairs:
            expected_board_pairs_by_round[round_id].add((group_a, group_b))
            expected_board_pairs_by_round[round_id].add((group_b, group_a))
    for item in cross:
        src_group = group(item.source_node, group_size)
        src_slot = slot(item.source_node, group_size)
        dst_group = group(item.final_dst, group_size)
        dst_slot = slot(item.final_dst, group_size)
        expected_plane = (src_slot + dst_slot) % group_size
        pair_phase[(item.origin_src, item.final_dst)] = item.phase_id
        if item.round_id not in range(group_count - 1):
            errors.append(f"cross task {item.task_id} has invalid round")
        if item.phase_id != item.round_id:
            errors.append(f"cross task {item.task_id} phase does not match round")
        if item.depend_on_phases != (() if item.round_id == 0 else (item.round_id - 1,)):
            errors.append(f"cross task {item.task_id} has wrong dependencies")
        if local_phase_id in item.depend_on_phases:
            errors.append(f"cross task {item.task_id} depends on local phase")
        if (src_group, dst_group) not in expected_board_pairs_by_round[item.round_id]:
            errors.append(f"cross task {item.task_id} goes to wrong dst group")
        if item.candidate_plane != expected_plane:
            errors.append(f"cross task {item.task_id} uses wrong plane")
        src_round_planes[(item.source_node, item.round_id)].add(item.candidate_plane)
        round_plane_counts[item.round_id][item.candidate_plane] += 1
        round_board_targets[(item.round_id, src_group)].add(dst_group)

    for src in range(rank_count):
        for round_id in range(group_count - 1):
            count = src_round_counts[(src, round_id)]
            if count != plane_count:
                errors.append(f"src {src} round {round_id} has {count} cross flows")
            planes = src_round_planes[(src, round_id)]
            if planes != set(range(plane_count)):
                errors.append(f"src {src} round {round_id} uses planes {sorted(planes)}")

    expected_plane_count_per_round = rank_count
    for round_id in range(group_count - 1):
        for plane_id in range(plane_count):
            count = round_plane_counts[round_id][plane_id]
            if count != expected_plane_count_per_round:
                errors.append(
                    f"round {round_id} plane {plane_id} has {count} flows, expected {expected_plane_count_per_round}"
                )
        actual_board_pairs = {
            (src_group, dst_group)
            for src_group in range(group_count)
            for dst_group in round_board_targets[(round_id, src_group)]
        }
        if actual_board_pairs != expected_board_pairs_by_round[round_id]:
            errors.append(f"round {round_id} board pairs {sorted(actual_board_pairs)}")

    reverse_phase_errors = []
    for item in cross:
        reverse_phase = pair_phase.get((item.final_dst, item.origin_src))
        if reverse_phase != item.phase_id:
            reverse_phase_errors.append(
                {"src": item.origin_src, "dst": item.final_dst, "phase": item.phase_id, "reverse_phase": reverse_phase}
            )
    if reverse_phase_errors:
        errors.append("reverse pair phase mismatch")

    checks = {
        "all_pairs_once": actual_pairs == expected_pairs and not duplicate_pairs,
        "no_self_traffic": all(item.origin_src != item.final_dst for item in transfers),
        "local_count_ok": len(local) == expected_local,
        "cross_count_ok": len(cross) == expected_cross,
        "source_round_plane_ok": not any("uses planes" in error or "cross flows" in error for error in errors),
        "round_plane_balance_ok": not any(error.startswith("round") and "plane" in error for error in errors),
        "dependency_ok": not any("depend" in error or "dependencies" in error for error in errors),
        "reverse_same_phase_ok": not reverse_phase_errors,
    }
    checks["ok"] = not errors and all(checks.values())

    return {
        "algorithm": ALGORITHM,
        "algorithm_label": ALGORITHM_LABEL,
        "rank_count": rank_count,
        "group_count": group_count,
        "group_size": group_size,
        "plane_count": plane_count,
        "message_bytes": message_bytes,
        "final_pair_count": len(expected_pairs),
        "local_task_count": len(local),
        "cross_task_count": len(cross),
        "traffic_rows": len(transfers),
        "remote_rounds": group_count - 1,
        "local_phase_id": local_phase_id,
        "operator_payload_bytes": len(expected_pairs) * message_bytes,
        "network_payload_bytes": sum(item.bytes for item in transfers),
        "phase_counts": dict(sorted(Counter(str(item.phase_id) for item in transfers).items())),
        "candidate_plane_counts": dict(sorted(Counter(str(item.candidate_plane) for item in transfers).items())),
        "round_plane_counts": {
            str(round_id): {str(plane): round_plane_counts[round_id][plane] for plane in range(plane_count)}
            for round_id in range(group_count - 1)
        },
        "round_board_pairs": {
            str(round_id): [
                {"src_group": src_group, "dst_group": dst_group}
                for src_group, dst_group in sorted(expected_board_pairs_by_round[round_id])
            ]
            for round_id in range(group_count - 1)
        },
        "checks": checks,
        "errors": errors,
        "reverse_phase_errors": reverse_phase_errors,
        "missing_pairs": [
            {"src": src, "dst": dst}
            for src, dst in sorted(expected_pairs - actual_pairs)
        ],
        "duplicate_pairs": [
            {"src": src, "dst": dst, "count": count}
            for src, dst, count in duplicate_pairs
        ],
    }


def copy_base_files(case: Path, base_case: Path) -> None:
    if case.exists():
        shutil.rmtree(case)
    case.mkdir(parents=True)
    for name in ("node.csv", "topology.csv", "routing_table.csv", "transport_channel.csv", "network_attribute.txt"):
        shutil.copy2(base_case / name, case / name)


def write_traffic(case: Path, transfers: list[Transfer]) -> None:
    with (case / "traffic.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(
            [
                "taskId",
                "sourceNode",
                "destNode",
                "dataSize(Byte)",
                "opType",
                "priority",
                "delay",
                "phaseId",
                "dependOnPhases",
                "srcEntityId",
                "dstEntityId",
                "srcPortHint",
                "ctpDstPortHint",
            ]
        )
        for item in transfers:
            src_entity = item.path_segments[-1].dst_port
            dst_entity = src_entity
            src_port = item.path_segments[0].src_port
            dst_port = item.path_segments[-1].dst_port
            writer.writerow(
                [
                    item.task_id,
                    item.source_node,
                    item.dest_node,
                    item.bytes,
                    "URMA_WRITE",
                    PRIORITY,
                    "0ns",
                    item.phase_id,
                    deps_text(item.depend_on_phases),
                    src_entity,
                    dst_entity,
                    src_port,
                    dst_port,
                ]
            )


def write_slices(case: Path, transfers: list[Transfer]) -> None:
    fields = [
        "taskId",
        "parentTaskId",
        "sourceNode",
        "destNode",
        "dataSize(Byte)",
        "phaseId",
        "dependOnPhases",
        "networkClass",
        "candidatePlane",
        "round",
        "plane",
        "semanticSlices",
        "sliceOriginSrcs",
        "sliceFinalDsts",
        "sliceAxes",
        "sliceHopKinds",
        "sliceHopIndices",
        "srcPortHint",
        "ctpDstPortHint",
    ]
    with (case / "traffic-with-slices.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in transfers:
            writer.writerow(
                {
                    "taskId": item.task_id,
                    "parentTaskId": item.task_id,
                    "sourceNode": item.source_node,
                    "destNode": item.dest_node,
                    "dataSize(Byte)": item.bytes,
                    "phaseId": item.phase_id,
                    "dependOnPhases": deps_text(item.depend_on_phases),
                    "networkClass": item.network_class,
                    "candidatePlane": item.candidate_plane,
                    "round": item.round_id,
                    "plane": item.plane,
                    "semanticSlices": item.slice_id,
                    "sliceOriginSrcs": item.origin_src,
                    "sliceFinalDsts": item.final_dst,
                    "sliceAxes": item.axis,
                    "sliceHopKinds": item.hop_kind,
                    "sliceHopIndices": item.hop_index,
                    "srcPortHint": item.path_segments[0].src_port,
                    "ctpDstPortHint": item.path_segments[-1].dst_port,
                }
            )


def write_manifest(case: Path, transfers: list[Transfer]) -> None:
    fields = [
        "taskId",
        "originSrc",
        "finalDst",
        "axis",
        "hopKind",
        "hopIndex",
        "sourceNode",
        "destNode",
        "intermediateNode",
        "networkClass",
        "phaseId",
        "dependOnPhases",
        "bytes",
        "sliceId",
        "sliceBytes",
        "totalMessageBytes",
    ]
    with (case / "dual_axis_a2a_manifest.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in transfers:
            writer.writerow(
                {
                    "taskId": item.task_id,
                    "originSrc": item.origin_src,
                    "finalDst": item.final_dst,
                    "axis": item.axis,
                    "hopKind": item.hop_kind,
                    "hopIndex": item.hop_index,
                    "sourceNode": item.source_node,
                    "destNode": item.dest_node,
                    "intermediateNode": item.dest_node if item.network_class == "local" else item.path_nodes[1],
                    "networkClass": item.network_class,
                    "phaseId": item.phase_id,
                    "dependOnPhases": deps_text(item.depend_on_phases),
                    "bytes": item.bytes,
                    "sliceId": item.slice_id,
                    "sliceBytes": item.bytes,
                    "totalMessageBytes": item.bytes,
                }
            )


def write_expected_paths(case: Path, transfers: list[Transfer]) -> None:
    fields = [
        "taskId",
        "parentTaskId",
        "phase",
        "layer",
        "networkClass",
        "src",
        "dst",
        "bytes",
        "candidatePlane",
        "pathNodes",
        "pathPorts",
        "linkIds",
        "semanticSlices",
    ]
    with (case / "expected_paths.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in transfers:
            writer.writerow(
                {
                    "taskId": item.task_id,
                    "parentTaskId": item.task_id,
                    "phase": item.phase_id,
                    "layer": f"{ALGORITHM}-{item.network_class}",
                    "networkClass": item.network_class,
                    "src": item.source_node,
                    "dst": item.dest_node,
                    "bytes": item.bytes,
                    "candidatePlane": item.candidate_plane,
                    "pathNodes": " ".join(str(node) for node in item.path_nodes),
                    "pathPorts": " | ".join(segment.directed_key for segment in item.path_segments),
                    "linkIds": " ".join(str(segment.link_id) for segment in item.path_segments),
                    "semanticSlices": item.slice_id,
                }
            )


def write_checker_csv(case: Path, transfers: list[Transfer]) -> None:
    fields = [
        "task_id",
        "round_id",
        "src",
        "dst",
        "message_id",
        "slice_id",
        "slice_count",
        "plane",
        "bytes",
        "network_type",
        "algorithm",
    ]
    with (case / "alltoall_checker_slices.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in transfers:
            writer.writerow(
                {
                    "task_id": item.task_id,
                    "round_id": item.round_id,
                    "src": item.origin_src,
                    "dst": item.final_dst,
                    "message_id": f"{item.origin_src}->{item.final_dst}",
                    "slice_id": item.slice_id,
                    "slice_count": 1,
                    "plane": item.plane,
                    "bytes": item.bytes,
                    "network_type": item.network_class,
                    "algorithm": ALGORITHM,
                }
            )


def write_case_note(case: Path, summary: dict[str, object]) -> None:
    text = f"""# Intra-Oneshot Cross-Plane-Round-Symm All-to-All case

Generated by `scratch/ubx-alltoall/generate_intra_oneshot_cross_plane_round_a2a.py`.

User context:

- New All-to-All algorithm for `group_count x group_size` UBX / SUTurbo-like topology.
- Board-local traffic is one-shot direct fullmesh.
- Cross-board traffic uses `group_count - 1` phases, and each rank sends one flow on each plane per phase.
- Cross-board phases use bidirectional board matching, so reverse pairs are in the same phase.
- Cross-board plane formula: `scheduled_plane = (source_slot + destination_slot) mod plane_count`.

Topology:

- topology: UBX
- group count: `{summary["group_count"]}`
- group size: `{summary["group_size"]}`
- plane count: `{summary["plane_count"]}`

Algorithm:

- id: `{summary["algorithm"]}`
- label: `{summary["algorithm_label"]}`
- final pairs: `{summary["final_pair_count"]}`
- local tasks: `{summary["local_task_count"]}`
- cross tasks: `{summary["cross_task_count"]}`
- remote rounds: `{summary["remote_rounds"]}`
- local phase id: `{summary["local_phase_id"]}`
- reverse same phase: `{summary["checks"]["reverse_same_phase_ok"]}`

Artifacts:

- `traffic.csv`: ns-3 traffic input.
- `traffic-with-slices.csv`: task to final-pair / round / plane mapping.
- `dual_axis_a2a_manifest.csv`: semantic slice manifest.
- `expected_paths.csv`: expected local and L1 paths.
- `alltoall_checker_slices.csv`: checker-oriented flat slice output.
- `phase_behavior.html`: per-phase board-pair and rank-flow visualization generated from `traffic-with-slices.csv`.
- `expected_summary.json`: checker result and static counts.
"""
    (case / "CASE.md").write_text(text, encoding="utf-8")


def write_phase_html(case: Path) -> None:
    rows = read_csv(case / "traffic-with-slices.csv")
    phases = sorted({int(row["phaseId"]) for row in rows})
    by_phase: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_phase[int(row["phaseId"])].append(row)

    styles = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #18212f; background: #f7f8fa; }
h1 { font-size: 24px; margin-bottom: 8px; }
h2 { font-size: 18px; margin-top: 28px; }
.note { color: #4b5563; margin-bottom: 20px; }
.phase { background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; margin: 18px 0; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin: 10px 0 14px; }
.metric { border: 1px solid #d8dee8; border-radius: 6px; padding: 8px 10px; background: #fbfcfe; }
.pairs { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.pair { border: 1px solid #b9c5d6; border-radius: 6px; padding: 6px 8px; background: #eef4ff; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
.diagram-wrap { overflow-x: auto; border: 1px solid #d8dee8; border-radius: 6px; background: #fbfcfe; padding: 8px; }
.phase-diagram { min-width: 920px; width: 100%; height: auto; }
.board-box { fill: #ffffff; stroke: #c7d1df; stroke-width: 1.4; }
.board-label { font-size: 14px; font-weight: 700; fill: #18212f; }
.rank-circle { fill: #e8eef7; stroke: #9dafc5; stroke-width: 1; }
.rank-label { font-size: 11px; text-anchor: middle; dominant-baseline: central; fill: #18212f; }
.flow-line { fill: none; stroke-width: 1.6; opacity: 0.78; }
.flow-label { font-size: 9px; fill: #18212f; text-anchor: middle; dominant-baseline: central; paint-order: stroke; stroke: #fbfcfe; stroke-width: 3px; }
.plane-0 { stroke: #2563eb; }
.plane-1 { stroke: #059669; }
.plane-2 { stroke: #d97706; }
.plane-3 { stroke: #dc2626; }
.plane-local { stroke: #64748b; }
table { border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 12px; background: white; }
th, td { border: 1px solid #d8dee8; padding: 5px 6px; text-align: left; }
th { background: #edf1f7; }
.local { background: #f4f8ec; }
"""

    def board_of(node: int) -> int:
        return node // 4

    def slot_of(node: int) -> int:
        return node % 4

    board_positions = {
        0: (60, 50),
        1: (640, 50),
        2: (60, 360),
        3: (640, 360),
    }
    board_width = 230
    board_height = 150

    def rank_point(node: int) -> tuple[int, int]:
        board_id = board_of(node)
        slot_id = slot_of(node)
        left, top = board_positions[board_id]
        offsets = {
            0: (55, 60),
            1: (175, 60),
            2: (55, 118),
            3: (175, 118),
        }
        x_offset, y_offset = offsets[slot_id]
        return left + x_offset, top + y_offset

    def curve_path(source_node: int, dest_node: int, plane_id: int, local: bool) -> str:
        source_x, source_y = rank_point(source_node)
        dest_x, dest_y = rank_point(dest_node)
        if local:
            curve_offset = 22 + 6 * ((source_node + dest_node) % 3)
            control_y = min(source_y, dest_y) - curve_offset
            return f"M {source_x} {source_y} Q {(source_x + dest_x) / 2:.1f} {control_y:.1f} {dest_x} {dest_y}"
        midpoint_x = (source_x + dest_x) / 2
        midpoint_y = (source_y + dest_y) / 2
        bend = (plane_id - 1.5) * 28
        if abs(dest_x - source_x) >= abs(dest_y - source_y):
            control_x = midpoint_x
            control_y = midpoint_y + bend
        else:
            control_x = midpoint_x + bend
            control_y = midpoint_y
        return f"M {source_x} {source_y} Q {control_x:.1f} {control_y:.1f} {dest_x} {dest_y}"

    def flow_label_point(source_node: int, dest_node: int, plane_id: int, local: bool) -> tuple[float, float]:
        source_x, source_y = rank_point(source_node)
        dest_x, dest_y = rank_point(dest_node)
        midpoint_x = (source_x + dest_x) / 2
        midpoint_y = (source_y + dest_y) / 2
        if local:
            return midpoint_x, midpoint_y - 20
        bend = (plane_id - 1.5) * 10
        return midpoint_x, midpoint_y + bend

    def draw_phase(phase: int, phase_rows: list[dict[str, str]]) -> str:
        board_shapes = []
        for board_id, (left, top) in board_positions.items():
            ranks = []
            for slot_id in range(4):
                node = board_id * 4 + slot_id
                x_pos, y_pos = rank_point(node)
                ranks.append(
                    f'<circle class="rank-circle" cx="{x_pos}" cy="{y_pos}" r="15"></circle>'
                    f'<text class="rank-label" x="{x_pos}" y="{y_pos}">{node}</text>'
                )
            board_shapes.append(
                f'<rect class="board-box" x="{left}" y="{top}" width="{board_width}" height="{board_height}" rx="6"></rect>'
                f'<text class="board-label" x="{left + 14}" y="{top + 24}">Board {board_id}</text>'
                f'{"".join(ranks)}'
            )

        flow_shapes = []
        for row in sorted(phase_rows, key=lambda item: (int(item["sourceNode"]), int(item["destNode"]))):
            source_node = int(row["sourceNode"])
            dest_node = int(row["destNode"])
            plane_id = int(row["candidatePlane"])
            local = row["networkClass"] == "local"
            plane_class = "plane-local" if local else f"plane-{plane_id}"
            marker = "marker-local" if local else f"marker-{plane_id}"
            label_x, label_y = flow_label_point(source_node, dest_node, plane_id, local)
            label = "L" if local else f"P{plane_id}"
            flow_shapes.append(
                f'<path class="flow-line {plane_class}" d="{curve_path(source_node, dest_node, plane_id, local)}" '
                f'marker-end="url(#{marker})"></path>'
                f'<text class="flow-label" x="{label_x:.1f}" y="{label_y:.1f}">{source_node} -> {dest_node} {label}</text>'
            )

        return (
            f'<div class="diagram-wrap" data-phase="{phase}">'
            f'<svg class="phase-diagram" viewBox="0 0 930 560" role="img" '
            f'aria-label="phase {phase} traffic">'
            '<defs>'
            '<marker id="marker-0" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
            '<path d="M 0 0 L 8 4 L 0 8 z" fill="#2563eb"></path></marker>'
            '<marker id="marker-1" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
            '<path d="M 0 0 L 8 4 L 0 8 z" fill="#059669"></path></marker>'
            '<marker id="marker-2" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
            '<path d="M 0 0 L 8 4 L 0 8 z" fill="#d97706"></path></marker>'
            '<marker id="marker-3" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
            '<path d="M 0 0 L 8 4 L 0 8 z" fill="#dc2626"></path></marker>'
            '<marker id="marker-local" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
            '<path d="M 0 0 L 8 4 L 0 8 z" fill="#64748b"></path></marker>'
            '</defs>'
            f'{"".join(board_shapes)}'
            f'{"".join(flow_shapes)}'
            '</svg></div>'
        )

    def row_table(phase_rows: list[dict[str, str]]) -> str:
        head = "<tr><th>task</th><th>source</th><th>dest</th><th>board pair</th><th>plane</th><th>src port</th><th>dst port</th><th>depend</th></tr>"
        body = []
        for row in sorted(phase_rows, key=lambda item: (int(item["sourceNode"]), int(item["destNode"]))):
            src = int(row["sourceNode"])
            dst = int(row["destNode"])
            body.append(
                "<tr>"
                f"<td>{html.escape(row['taskId'])}</td>"
                f"<td>{src}</td>"
                f"<td>{dst}</td>"
                f"<td>{board_of(src)} -> {board_of(dst)}</td>"
                f"<td>{html.escape(row['candidatePlane'])}</td>"
                f"<td>{html.escape(row['srcPortHint'])}</td>"
                f"<td>{html.escape(row['ctpDstPortHint'])}</td>"
                f"<td>{html.escape(row['dependOnPhases'])}</td>"
                "</tr>"
            )
        return f"<table>{head}{''.join(body)}</table>"

    sections = []
    for phase in phases:
        phase_rows = by_phase[phase]
        board_pairs = sorted(
            {
                (board_of(int(row["sourceNode"])), board_of(int(row["destNode"])))
                for row in phase_rows
            }
        )
        pair_labels = []
        seen_pairs: set[tuple[int, int]] = set()
        for src, dst in board_pairs:
            if (src, dst) in seen_pairs:
                continue
            if src != dst and (dst, src) in board_pairs:
                pair_labels.append(f"Board {src} &harr; Board {dst}")
                seen_pairs.add((src, dst))
                seen_pairs.add((dst, src))
            else:
                pair_labels.append(f"Board {src} -> Board {dst}")
                seen_pairs.add((src, dst))
        pair_text = "".join(f"<div class='pair'>{label}</div>" for label in pair_labels)
        network_class = "local" if all(row["networkClass"] == "local" for row in phase_rows) else "cross"
        counts = Counter(row["candidatePlane"] for row in phase_rows)
        count_text = ", ".join(f"plane {plane}: {count}" for plane, count in sorted(counts.items()))
        sections.append(
            f"<section class='phase {'local' if network_class == 'local' else ''}'>"
            f"<h2>Phase {phase} - {html.escape(network_class)}</h2>"
            f"<div class='summary'>"
            f"<div class='metric'><strong>flow count</strong><br>{len(phase_rows)}</div>"
            f"<div class='metric'><strong>plane count</strong><br>{html.escape(count_text)}</div>"
            f"</div>"
            f"<div class='pairs'>{pair_text}</div>"
            f"{draw_phase(phase, phase_rows)}"
            f"{row_table(phase_rows)}"
            "</section>"
        )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Intra-Oneshot Cross-Plane-Round-Symm Phase Behavior</title>
  <style>{styles}</style>
</head>
<body>
  <h1>板内一次发、板间平面对轮 Symm All-to-All Phase 行为</h1>
  <p class="note">此 HTML 由脚本读取生成后的 <code>traffic-with-slices.csv</code> 生成，不是手写示意图。</p>
  {''.join(sections)}
</body>
</html>
"""
    (case / "phase_behavior.html").write_text(document, encoding="utf-8")


def prepare_case(
    case: Path,
    *,
    group_count: int = 4,
    group_size: int = 4,
    plane_count: int = 4,
    message_bytes: int = DEFAULT_MESSAGE_BYTES,
    base_case: Path = BASE_CASE,
) -> dict[str, object]:
    transfers = build_transfers(
        group_count=group_count,
        group_size=group_size,
        plane_count=plane_count,
        message_bytes=message_bytes,
        base_case=base_case,
    )
    summary = check_alltoall(
        transfers,
        group_count=group_count,
        group_size=group_size,
        plane_count=plane_count,
        message_bytes=message_bytes,
    )
    summary["case_dir"] = str(case)
    copy_base_files(case, base_case)
    write_traffic(case, transfers)
    write_slices(case, transfers)
    write_manifest(case, transfers)
    write_expected_paths(case, transfers)
    write_checker_csv(case, transfers)
    write_phase_html(case)
    (case / "expected_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_case_note(case, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--group-count", type=int, default=4)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--plane-count", type=int, default=4)
    parser.add_argument("--message-bytes", type=int, default=DEFAULT_MESSAGE_BYTES)
    parser.add_argument("--base-case", type=Path, default=BASE_CASE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = prepare_case(
        args.case_dir,
        group_count=args.group_count,
        group_size=args.group_size,
        plane_count=args.plane_count,
        message_bytes=args.message_bytes,
        base_case=args.base_case,
    )
    status = "PASS" if summary["checks"]["ok"] else "FAIL"
    print(f"{status} {ALGORITHM}")
    print(f"case={args.case_dir}")
    print(
        "pairs={final_pair_count} local={local_task_count} cross={cross_task_count} rounds={remote_rounds}".format(
            **summary
        )
    )
    return 0 if summary["checks"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())