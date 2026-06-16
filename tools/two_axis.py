#!/usr/bin/env python3
"""Generate logical dual-axis All-to-All traffic.

The traffic model is intentionally transport-independent.  It fixes the
application-level dependency semantics first:

* ``dual-axis-half-split`` first aggregates everything for one destination group at
  ``(dst_group, src_slot)``, then local-scatters the received pieces.
* ``dual-axis-half-split`` also first aggregates everything for one destination slot at
  ``(src_group, dst_slot)``, then Clos-scatters the received pieces.
* ``dual-axis-half-uniform`` keeps the same two-stage dependency graph but
  splits every non-self directed pair 1:1 across the A and B axes, including
  same-group and same-slot pairs.
* ``dual-axis-pipeline`` uses a slot-aware Latin peer-group schedule over n
  phases: phase 0 sends all same-slot cross-group data on Axis A and starts
  B-axis collection, middle phases drain the previous B collection on Axis A
  while collecting the next peer group on Axis B, and the last phase drains
  Axis A while sending same-group local data on Axis B.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SCRATCH_DIR = BASE_DIR.parent
PRIORITY = 7


@dataclass(frozen=True)
class TrafficRow:
    task_id: int
    source_node: int
    dest_node: int
    data_size: int
    op_type: str
    priority: int
    delay: str
    phase_id: int
    depend_on_phases: tuple[int, ...]


@dataclass(frozen=True)
class ManifestRow:
    task_id: int
    origin_src: int
    final_dst: int
    axis: str
    hop_kind: str
    hop_index: str
    source_node: int
    dest_node: int
    intermediate_node: int
    network_class: str
    phase_id: int
    depend_on_phases: tuple[int, ...]
    bytes: int
    slice_id: int
    slice_bytes: int
    total_message_bytes: int


def rank(group_id: int, slot_id: int, group_size: int) -> int:
    return group_id * group_size + slot_id


def group(rank_id: int, group_size: int) -> int:
    return rank_id // group_size


def slot(rank_id: int, group_size: int) -> int:
    return rank_id % group_size


def split_bytes(total: int, left_weight: int, right_weight: int) -> tuple[int, int]:
    if total <= 0:
        raise ValueError("message bytes must be positive")
    if left_weight <= 0 or right_weight <= 0:
        raise ValueError("split weights must be positive")
    left = total * left_weight // (left_weight + right_weight)
    right = total - left
    if left <= 0 or right <= 0:
        raise ValueError("split ratio rounds to an empty axis")
    return left, right


def parse_ratio(text: str) -> tuple[int, int]:
    if ":" not in text:
        raise ValueError("split ratio must use A:B form")
    left, right = text.split(":", 1)
    return int(left), int(right)


def serialize_same_pair_tasks(
    rows: list[TrafficRow],
    manifest: list[ManifestRow],
) -> tuple[list[TrafficRow], list[ManifestRow]]:
    """Serialize tasks that share logical phase and source/destination pair.

    traffic.csv only has phase-level dependencies.  To model pair-local ordering
    without globally serializing unrelated pairs, give each task a private
    physical phase and make later same-pair tasks depend on the previous private
    phase.  Logical cross-phase barriers are expanded to all private phases in
    the depended-on logical phase.
    """

    physical_phase_by_task = {row.task_id: row.task_id for row in rows}
    physical_by_logical: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        physical_by_logical[row.phase_id].append(physical_phase_by_task[row.task_id])
    for phases in physical_by_logical.values():
        phases.sort()

    deps_by_task: dict[int, tuple[int, ...]] = {}
    last_phase_by_pair: dict[tuple[int, int, int], int] = {}
    for row in sorted(rows, key=lambda item: (item.phase_id, item.task_id)):
        deps = set()
        for logical_dep in row.depend_on_phases:
            deps.update(physical_by_logical[logical_dep])
        pair_key = (row.phase_id, row.source_node, row.dest_node)
        if pair_key in last_phase_by_pair:
            deps.add(last_phase_by_pair[pair_key])
        deps_by_task[row.task_id] = tuple(sorted(deps))
        last_phase_by_pair[pair_key] = physical_phase_by_task[row.task_id]

    remapped_rows = [
        replace(
            row,
            phase_id=physical_phase_by_task[row.task_id],
            depend_on_phases=deps_by_task[row.task_id],
        )
        for row in rows
    ]
    remapped_manifest = [
        replace(
            item,
            phase_id=physical_phase_by_task[item.task_id],
            depend_on_phases=deps_by_task[item.task_id],
        )
        for item in manifest
    ]
    return remapped_rows, remapped_manifest


def _append(
    rows: list[TrafficRow],
    manifest: list[ManifestRow],
    *,
    origin_src: int,
    final_dst: int,
    axis: str,
    hop_kind: str,
    hop_index: str,
    source_node: int,
    dest_node: int,
    intermediate_node: int,
    network_class: str,
    phase_id: int,
    depend_on_phases: tuple[int, ...],
    bytes_count: int,
    slice_id: int,
    total_message_bytes: int,
) -> None:
    task_id = len(rows)
    rows.append(
        TrafficRow(
            task_id=task_id,
            source_node=source_node,
            dest_node=dest_node,
            data_size=bytes_count,
            op_type="URMA_WRITE",
            priority=PRIORITY,
            delay="0ns",
            phase_id=phase_id,
            depend_on_phases=depend_on_phases,
        )
    )
    manifest.append(
        ManifestRow(
            task_id=task_id,
            origin_src=origin_src,
            final_dst=final_dst,
            axis=axis,
            hop_kind=hop_kind,
            hop_index=hop_index,
            source_node=source_node,
            dest_node=dest_node,
            intermediate_node=intermediate_node,
            network_class=network_class,
            phase_id=phase_id,
            depend_on_phases=depend_on_phases,
            bytes=bytes_count,
            slice_id=slice_id,
            slice_bytes=bytes_count,
            total_message_bytes=total_message_bytes,
        )
    )


@dataclass(frozen=True)
class SemanticPiece:
    slice_id: int
    origin_src: int
    final_dst: int
    axis: str
    bytes: int
    total_message_bytes: int
    src_group: int
    src_slot: int
    dst_group: int
    dst_slot: int


def build_dual_axis_a2a(
    *,
    group_count: int,
    group_size: int,
    message_bytes: int,
    clos_weight: int,
    local_weight: int,
    task_granularity: str = "aggregate",
) -> tuple[list[TrafficRow], list[ManifestRow]]:
    """Build logical dual-axis All-to-All rows and semantic manifest."""

    if group_count <= 0 or group_size <= 0:
        raise ValueError("group_count and group_size must be positive")
    if task_granularity not in {"aggregate", "slice"}:
        raise ValueError(f"unsupported task granularity: {task_granularity}")

    rows: list[TrafficRow] = []
    manifest: list[ManifestRow] = []
    a_bytes, b_bytes = split_bytes(message_bytes, clos_weight, local_weight)

    pieces: list[SemanticPiece] = []
    next_slice_id = 0

    def add_piece(
        *,
        src_group: int,
        src_slot: int,
        dst_group: int,
        dst_slot: int,
        axis: str,
        bytes_count: int,
    ) -> None:
        nonlocal next_slice_id
        pieces.append(
            SemanticPiece(
                slice_id=next_slice_id,
                origin_src=rank(src_group, src_slot, group_size),
                final_dst=rank(dst_group, dst_slot, group_size),
                axis=axis,
                bytes=bytes_count,
                total_message_bytes=message_bytes,
                src_group=src_group,
                src_slot=src_slot,
                dst_group=dst_group,
                dst_slot=dst_slot,
            )
        )
        next_slice_id += 1

    for src_group in range(group_count):
        for src_slot in range(group_size):
            for dst_group in range(group_count):
                for dst_slot in range(group_size):
                    if src_group == dst_group and src_slot == dst_slot:
                        continue
                    if src_group == dst_group:
                        add_piece(
                            src_group=src_group,
                            src_slot=src_slot,
                            dst_group=dst_group,
                            dst_slot=dst_slot,
                            axis="L",
                            bytes_count=message_bytes,
                        )
                    elif src_slot == dst_slot:
                        add_piece(
                            src_group=src_group,
                            src_slot=src_slot,
                            dst_group=dst_group,
                            dst_slot=dst_slot,
                            axis="C",
                            bytes_count=message_bytes,
                        )
                    else:
                        add_piece(
                            src_group=src_group,
                            src_slot=src_slot,
                            dst_group=dst_group,
                            dst_slot=dst_slot,
                            axis="A",
                            bytes_count=a_bytes,
                        )
                        add_piece(
                            src_group=src_group,
                            src_slot=src_slot,
                            dst_group=dst_group,
                            dst_slot=dst_slot,
                            axis="B",
                            bytes_count=b_bytes,
                        )

    def append_task(
        *,
        source_node: int,
        dest_node: int,
        network_class: str,
        phase_id: int,
        depend_on_phases: tuple[int, ...],
        carried: list[tuple[SemanticPiece, str, str, int]],
    ) -> None:
        if not carried:
            return
        task_groups = [[item] for item in carried] if task_granularity == "slice" else [carried]
        for group_items in task_groups:
            task_id = len(rows)
            rows.append(
                TrafficRow(
                    task_id=task_id,
                    source_node=source_node,
                    dest_node=dest_node,
                    data_size=sum(piece.bytes for piece, _hop_kind, _hop_index, _intermediate in group_items),
                    op_type="URMA_WRITE",
                    priority=PRIORITY,
                    delay="0ns",
                    phase_id=phase_id,
                    depend_on_phases=depend_on_phases,
                )
            )
            for piece, hop_kind, hop_index, intermediate_node in group_items:
                manifest.append(
                    ManifestRow(
                        task_id=task_id,
                        origin_src=piece.origin_src,
                        final_dst=piece.final_dst,
                        axis=piece.axis,
                        hop_kind=hop_kind,
                        hop_index=hop_index,
                        source_node=source_node,
                        dest_node=dest_node,
                        intermediate_node=intermediate_node,
                        network_class=network_class,
                        phase_id=phase_id,
                        depend_on_phases=depend_on_phases,
                        bytes=piece.bytes,
                        slice_id=piece.slice_id,
                        slice_bytes=piece.bytes,
                        total_message_bytes=piece.total_message_bytes,
                    )
                )
    # Axis A phase 0: each source sends all pieces for one destination group to
    # (dst_group, src_slot).  Same-slot cross-group pieces finish here.
    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_group in range(group_count):
                if dst_group == src_group:
                    continue
                mid = rank(dst_group, src_slot, group_size)
                carried = []
                for piece in pieces:
                    if piece.src_group != src_group or piece.src_slot != src_slot:
                        continue
                    if piece.dst_group != dst_group or piece.axis not in {"A", "C"}:
                        continue
                    hop_kind = "only" if piece.axis == "C" else "first"
                    hop_index = "only" if piece.axis == "C" else "0"
                    carried.append((piece, hop_kind, hop_index, mid))
                append_task(
                    source_node=src,
                    dest_node=mid,
                    network_class="clos",
                    phase_id=0,
                    depend_on_phases=(),
                    carried=carried,
                )

    # Axis B phase 10: each source sends all pieces for one destination slot to
    # (src_group, dst_slot).  Same-group pieces finish here.
    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_slot in range(group_size):
                if dst_slot == src_slot:
                    continue
                mid = rank(src_group, dst_slot, group_size)
                carried = []
                for piece in pieces:
                    if piece.src_group != src_group or piece.src_slot != src_slot:
                        continue
                    if piece.dst_slot != dst_slot or piece.axis not in {"B", "L"}:
                        continue
                    hop_kind = "only" if piece.axis == "L" else "first"
                    hop_index = "only" if piece.axis == "L" else "0"
                    carried.append((piece, hop_kind, hop_index, mid))
                append_task(
                    source_node=src,
                    dest_node=mid,
                    network_class="local",
                    phase_id=10,
                    depend_on_phases=(),
                    carried=carried,
                )

    # Axis A phase 1: each (dst_group, src_slot) local-scatters the A pieces it
    # received in phase 0 to the final destination slot.
    for dst_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(dst_group, src_slot, group_size)
            for dst_slot in range(group_size):
                if dst_slot == src_slot:
                    continue
                dst = rank(dst_group, dst_slot, group_size)
                carried = [
                    (piece, "second", "1", src)
                    for piece in pieces
                    if piece.axis == "A"
                    and piece.dst_group == dst_group
                    and piece.src_slot == src_slot
                    and piece.dst_slot == dst_slot
                ]
                append_task(
                    source_node=src,
                    dest_node=dst,
                    network_class="local",
                    phase_id=1,
                    depend_on_phases=(0, 10),
                    carried=carried,
                )

    # Axis B phase 11: each (src_group, dst_slot) Clos-scatters the B pieces it
    # received in phase 10 to the final destination group.
    for src_group in range(group_count):
        for dst_slot in range(group_size):
            src = rank(src_group, dst_slot, group_size)
            for dst_group in range(group_count):
                if dst_group == src_group:
                    continue
                dst = rank(dst_group, dst_slot, group_size)
                carried = [
                    (piece, "second", "1", src)
                    for piece in pieces
                    if piece.axis == "B"
                    and piece.src_group == src_group
                    and piece.dst_group == dst_group
                    and piece.dst_slot == dst_slot
                ]
                append_task(
                    source_node=src,
                    dest_node=dst,
                    network_class="clos",
                    phase_id=11,
                    depend_on_phases=(0, 10),
                    carried=carried,
                )

    return rows, manifest


def build_dual_axis_half_uniform_a2a(
    *,
    group_count: int,
    group_size: int,
    message_bytes: int,
    clos_weight: int,
    local_weight: int,
    task_granularity: str = "aggregate",
) -> tuple[list[TrafficRow], list[ManifestRow]]:
    """Build half-uniform dual-axis All-to-All rows.

    Every non-self final pair is split into an A slice and a B slice.  Slices
    that are already on the final network coordinate are emitted as one-hop
    ``only`` transfers; cross-group cross-slot slices remain two-hop.
    """

    if group_count <= 0 or group_size <= 0:
        raise ValueError("group_count and group_size must be positive")
    if task_granularity not in {"aggregate", "slice"}:
        raise ValueError(f"unsupported task granularity: {task_granularity}")

    rows: list[TrafficRow] = []
    manifest: list[ManifestRow] = []
    a_bytes, b_bytes = split_bytes(message_bytes, clos_weight, local_weight)

    pieces: list[SemanticPiece] = []
    next_slice_id = 0

    def add_piece(
        *,
        src_group: int,
        src_slot: int,
        dst_group: int,
        dst_slot: int,
        axis: str,
        bytes_count: int,
    ) -> None:
        nonlocal next_slice_id
        pieces.append(
            SemanticPiece(
                slice_id=next_slice_id,
                origin_src=rank(src_group, src_slot, group_size),
                final_dst=rank(dst_group, dst_slot, group_size),
                axis=axis,
                bytes=bytes_count,
                total_message_bytes=message_bytes,
                src_group=src_group,
                src_slot=src_slot,
                dst_group=dst_group,
                dst_slot=dst_slot,
            )
        )
        next_slice_id += 1

    for src_group in range(group_count):
        for src_slot in range(group_size):
            for dst_group in range(group_count):
                for dst_slot in range(group_size):
                    if src_group == dst_group and src_slot == dst_slot:
                        continue
                    add_piece(
                        src_group=src_group,
                        src_slot=src_slot,
                        dst_group=dst_group,
                        dst_slot=dst_slot,
                        axis="A",
                        bytes_count=a_bytes,
                    )
                    add_piece(
                        src_group=src_group,
                        src_slot=src_slot,
                        dst_group=dst_group,
                        dst_slot=dst_slot,
                        axis="B",
                        bytes_count=b_bytes,
                    )

    def append_task(
        *,
        source_node: int,
        dest_node: int,
        network_class: str,
        phase_id: int,
        depend_on_phases: tuple[int, ...],
        carried: list[tuple[SemanticPiece, str, str, int]],
    ) -> None:
        if not carried:
            return
        task_groups = [[item] for item in carried] if task_granularity == "slice" else [carried]
        for group_items in task_groups:
            task_id = len(rows)
            rows.append(
                TrafficRow(
                    task_id=task_id,
                    source_node=source_node,
                    dest_node=dest_node,
                    data_size=sum(piece.bytes for piece, _hop_kind, _hop_index, _intermediate in group_items),
                    op_type="URMA_WRITE",
                    priority=PRIORITY,
                    delay="0ns",
                    phase_id=phase_id,
                    depend_on_phases=depend_on_phases,
                )
            )
            for piece, hop_kind, hop_index, intermediate_node in group_items:
                manifest.append(
                    ManifestRow(
                        task_id=task_id,
                        origin_src=piece.origin_src,
                        final_dst=piece.final_dst,
                        axis=piece.axis,
                        hop_kind=hop_kind,
                        hop_index=hop_index,
                        source_node=source_node,
                        dest_node=dest_node,
                        intermediate_node=intermediate_node,
                        network_class=network_class,
                        phase_id=phase_id,
                        depend_on_phases=depend_on_phases,
                        bytes=piece.bytes,
                        slice_id=piece.slice_id,
                        slice_bytes=piece.bytes,
                        total_message_bytes=piece.total_message_bytes,
                    )
                )

    # A axis, Clos first.  Same-slot cross-group A slices finish here.
    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_group in range(group_count):
                if dst_group == src_group:
                    continue
                mid = rank(dst_group, src_slot, group_size)
                carried = []
                for piece in pieces:
                    if piece.axis != "A" or piece.src_group != src_group or piece.src_slot != src_slot:
                        continue
                    if piece.dst_group != dst_group:
                        continue
                    hop_kind = "only" if piece.dst_slot == src_slot else "first"
                    hop_index = "only" if hop_kind == "only" else "0"
                    carried.append((piece, hop_kind, hop_index, mid))
                append_task(
                    source_node=src,
                    dest_node=mid,
                    network_class="clos",
                    phase_id=0,
                    depend_on_phases=(),
                    carried=carried,
                )

    # B axis, local first.  Same-group B slices finish here.
    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_slot in range(group_size):
                if dst_slot == src_slot:
                    continue
                mid = rank(src_group, dst_slot, group_size)
                carried = []
                for piece in pieces:
                    if piece.axis != "B" or piece.src_group != src_group or piece.src_slot != src_slot:
                        continue
                    if piece.dst_slot != dst_slot:
                        continue
                    hop_kind = "only" if piece.dst_group == src_group else "first"
                    hop_index = "only" if hop_kind == "only" else "0"
                    carried.append((piece, hop_kind, hop_index, mid))
                append_task(
                    source_node=src,
                    dest_node=mid,
                    network_class="local",
                    phase_id=10,
                    depend_on_phases=(),
                    carried=carried,
                )

    # A axis, local second stage.  Same-group A slices only need this hop.
    for dst_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(dst_group, src_slot, group_size)
            for dst_slot in range(group_size):
                if dst_slot == src_slot:
                    continue
                dst = rank(dst_group, dst_slot, group_size)
                carried = []
                for piece in pieces:
                    if piece.axis != "A" or piece.dst_group != dst_group:
                        continue
                    if piece.src_slot != src_slot or piece.dst_slot != dst_slot:
                        continue
                    if piece.src_group == dst_group:
                        carried.append((piece, "only", "only", dst))
                    else:
                        carried.append((piece, "second", "1", src))
                append_task(
                    source_node=src,
                    dest_node=dst,
                    network_class="local",
                    phase_id=1,
                    depend_on_phases=(0, 10),
                    carried=carried,
                )

    # B axis, Clos second stage.  Same-slot B slices only need this hop.
    for src_group in range(group_count):
        for dst_slot in range(group_size):
            src = rank(src_group, dst_slot, group_size)
            for dst_group in range(group_count):
                if dst_group == src_group:
                    continue
                dst = rank(dst_group, dst_slot, group_size)
                carried = []
                for piece in pieces:
                    if piece.axis != "B" or piece.src_group != src_group:
                        continue
                    if piece.dst_group != dst_group or piece.dst_slot != dst_slot:
                        continue
                    if piece.src_slot == dst_slot:
                        carried.append((piece, "only", "only", dst))
                    else:
                        carried.append((piece, "second", "1", src))
                append_task(
                    source_node=src,
                    dest_node=dst,
                    network_class="clos",
                    phase_id=11,
                    depend_on_phases=(0, 10),
                    carried=carried,
                )

    return rows, manifest


def pipeline_peer_group(
    src_group: int,
    src_slot: int,
    dst_slot: int,
    phase_index: int,
    group_count: int,
) -> int:
    if not (0 <= phase_index < group_count - 1):
        raise ValueError(f"phase_index must be in 0..{group_count - 2}, got {phase_index}")
    if src_slot == dst_slot:
        raise ValueError("pipeline peer-group schedule is only defined for cross-slot transfers")
    slot_delta = (src_slot - dst_slot) % group_count
    group_offset = ((slot_delta - 1 + phase_index) % (group_count - 1)) + 1
    return (src_group + group_offset) % group_count


def build_dual_axis_pipeline_a2a(
    *,
    group_count: int,
    group_size: int,
    message_bytes: int,
) -> tuple[list[TrafficRow], list[ManifestRow]]:
    """Build slot-aware dual-axis pipeline All-to-All traffic.

    This builder implements the n-phase pipeline semantics for an n x n rank
    grid.  It intentionally keeps one semantic slice per final directed pair:
    cross-group cross-slot pairs are not split by bytes; they go through B then
    A.
    """

    if group_count <= 1 or group_size <= 1:
        raise ValueError("pipeline requires at least a 2x2 grid")
    if group_count != group_size:
        raise ValueError("pipeline is defined for an n x n grid: group_count must equal group_size")
    if message_bytes <= 0:
        raise ValueError("message bytes must be positive")

    rows: list[TrafficRow] = []
    manifest: list[ManifestRow] = []
    next_slice_id = 0

    def new_piece(
        *,
        src_group: int,
        src_slot: int,
        dst_group: int,
        dst_slot: int,
        axis: str,
    ) -> SemanticPiece:
        nonlocal next_slice_id
        piece = SemanticPiece(
            slice_id=next_slice_id,
            origin_src=rank(src_group, src_slot, group_size),
            final_dst=rank(dst_group, dst_slot, group_size),
            axis=axis,
            bytes=message_bytes,
            total_message_bytes=message_bytes,
            src_group=src_group,
            src_slot=src_slot,
            dst_group=dst_group,
            dst_slot=dst_slot,
        )
        next_slice_id += 1
        return piece

    def append_task(
        *,
        source_node: int,
        dest_node: int,
        network_class: str,
        phase_id: int,
        depend_on_phases: tuple[int, ...],
        carried: list[tuple[SemanticPiece, str, str, int]],
    ) -> None:
        if not carried:
            return
        task_id = len(rows)
        rows.append(
            TrafficRow(
                task_id=task_id,
                source_node=source_node,
                dest_node=dest_node,
                data_size=sum(piece.bytes for piece, _kind, _index, _mid in carried),
                op_type="URMA_WRITE",
                priority=PRIORITY,
                delay="0ns",
                phase_id=phase_id,
                depend_on_phases=depend_on_phases,
            )
        )
        for piece, hop_kind, hop_index, intermediate_node in carried:
            manifest.append(
                ManifestRow(
                    task_id=task_id,
                    origin_src=piece.origin_src,
                    final_dst=piece.final_dst,
                    axis=piece.axis,
                    hop_kind=hop_kind,
                    hop_index=hop_index,
                    source_node=source_node,
                    dest_node=dest_node,
                    intermediate_node=intermediate_node,
                    network_class=network_class,
                    phase_id=phase_id,
                    depend_on_phases=depend_on_phases,
                    bytes=piece.bytes,
                    slice_id=piece.slice_id,
                    slice_bytes=piece.bytes,
                    total_message_bytes=piece.total_message_bytes,
                )
            )

    # Phase 0 / Axis A: direct same-slot cross-group data.  This is all done
    # in the first phase.
    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_group in range(group_count):
                if dst_group == src_group:
                    continue
                dst = rank(dst_group, src_slot, group_size)
                piece = new_piece(
                    src_group=src_group,
                    src_slot=src_slot,
                    dst_group=dst_group,
                    dst_slot=src_slot,
                    axis="A",
                )
                append_task(
                    source_node=src,
                    dest_node=dst,
                    network_class="clos",
                    phase_id=0,
                    depend_on_phases=(),
                    carried=[(piece, "only", "direct", dst)],
                )

    # Phase p / Axis B: collect cross-group cross-slot data for the slot-aware
    # peer group.  For a fixed (src_group, dst_slot, phase), different src_slot
    # values map to different dst_group values so the next A-axis drain does not
    # collapse into one aggregated Clos task.  Phase n-1 / Axis B sends
    # same-group local-only data after all peer groups have been collected.
    collected_by_phase: dict[int, list[SemanticPiece]] = defaultdict(list)
    for phase in range(group_count - 1):
        depend = () if phase == 0 else (phase - 1,)
        for src_group in range(group_count):
            for src_slot in range(group_size):
                src = rank(src_group, src_slot, group_size)
                for dst_slot in range(group_size):
                    if dst_slot == src_slot:
                        continue
                    dst_group = pipeline_peer_group(
                        src_group,
                        src_slot,
                        dst_slot,
                        phase,
                        group_count,
                    )
                    mid = rank(src_group, dst_slot, group_size)
                    piece = new_piece(
                        src_group=src_group,
                        src_slot=src_slot,
                        dst_group=dst_group,
                        dst_slot=dst_slot,
                        axis="B",
                    )
                    collected_by_phase[phase].append(piece)
                    append_task(
                        source_node=src,
                        dest_node=mid,
                        network_class="local",
                        phase_id=phase,
                        depend_on_phases=depend,
                        carried=[(piece, "first", str(phase), mid)],
                    )

    final_phase = group_count - 1
    for src_group in range(group_count):
        for src_slot in range(group_size):
            src = rank(src_group, src_slot, group_size)
            for dst_slot in range(group_size):
                if dst_slot == src_slot:
                    continue
                dst = rank(src_group, dst_slot, group_size)
                piece = new_piece(
                    src_group=src_group,
                    src_slot=src_slot,
                    dst_group=src_group,
                    dst_slot=dst_slot,
                    axis="B",
                )
                append_task(
                    source_node=src,
                    dest_node=dst,
                    network_class="local",
                    phase_id=final_phase,
                    depend_on_phases=(final_phase - 1,),
                    carried=[(piece, "only", "local", dst)],
                )

    # Phase p+1 / Axis A: drain B-collected data from phase p.
    for collected_phase, pieces in sorted(collected_by_phase.items()):
        phase = collected_phase + 1
        depend = (phase - 1,)
        by_hop: dict[tuple[int, int, int], list[SemanticPiece]] = defaultdict(list)
        for piece in pieces:
            by_hop[(piece.src_group, piece.dst_group, piece.dst_slot)].append(piece)
        for (src_group, dst_group, dst_slot), carried_pieces in sorted(by_hop.items()):
            src = rank(src_group, dst_slot, group_size)
            dst = rank(dst_group, dst_slot, group_size)
            append_task(
                source_node=src,
                dest_node=dst,
                network_class="clos",
                phase_id=phase,
                depend_on_phases=depend,
                carried=[(piece, "second", str(phase), src) for piece in sorted(carried_pieces, key=lambda p: p.slice_id)],
            )

    rows.sort(key=lambda row: (row.phase_id, row.task_id))
    task_id_remap = {row.task_id: idx for idx, row in enumerate(rows)}
    rows = [
        TrafficRow(
            task_id=task_id_remap[row.task_id],
            source_node=row.source_node,
            dest_node=row.dest_node,
            data_size=row.data_size,
            op_type=row.op_type,
            priority=row.priority,
            delay=row.delay,
            phase_id=row.phase_id,
            depend_on_phases=row.depend_on_phases,
        )
        for row in rows
    ]
    manifest = [
        ManifestRow(
            task_id=task_id_remap[item.task_id],
            origin_src=item.origin_src,
            final_dst=item.final_dst,
            axis=item.axis,
            hop_kind=item.hop_kind,
            hop_index=item.hop_index,
            source_node=item.source_node,
            dest_node=item.dest_node,
            intermediate_node=item.intermediate_node,
            network_class=item.network_class,
            phase_id=item.phase_id,
            depend_on_phases=item.depend_on_phases,
            bytes=item.bytes,
            slice_id=item.slice_id,
            slice_bytes=item.slice_bytes,
            total_message_bytes=item.total_message_bytes,
        )
        for item in manifest
    ]
    return rows, manifest


def deps_text(deps: tuple[int, ...]) -> str:
    return " ".join(str(item) for item in deps)


def phase_dependencies(rows: list[TrafficRow]) -> dict[int, str]:
    deps_by_phase: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        deps_by_phase[row.phase_id].add(deps_text(row.depend_on_phases))
    out: dict[int, str] = {}
    for phase_id, deps in sorted(deps_by_phase.items()):
        if len(deps) != 1:
            raise ValueError(f"phase {phase_id} has inconsistent dependencies: {sorted(deps)}")
        out[phase_id] = next(iter(deps))
    return out


def check_dual_axis_a2a(
    rows: list[TrafficRow],
    manifest: list[ManifestRow],
    *,
    algorithm: str = "half-split",
    dependency_shape: str = "logical",
) -> dict[str, object]:
    if algorithm not in {"half-split", "half-uniform", "pipeline"}:
        raise ValueError(f"unknown dual-axis algorithm {algorithm}")
    if dependency_shape not in {"logical", "serial-same-pair"}:
        raise ValueError(f"unknown dependency shape {dependency_shape}")
    half_algorithm = algorithm in {"half-split", "half-uniform"}

    pair_bytes = Counter()
    first_hops = {}
    second_hops = []
    by_axis = Counter()
    by_hop = Counter()
    by_network = Counter()
    traffic_by_network = Counter()
    traffic_task_ids = set()
    manifest_by_task: dict[int, list[ManifestRow]] = defaultdict(list)
    ranks = set()
    message_bytes_by_pair: dict[tuple[int, int], int] = {}
    slice_attrs: dict[int, tuple[int, int, str, int, int]] = {}
    slice_hops: dict[int, list[ManifestRow]] = defaultdict(list)
    errors: list[str] = []

    for row in rows:
        if row.task_id in traffic_task_ids:
            errors.append(f"duplicate traffic task id {row.task_id}")
        traffic_task_ids.add(row.task_id)
        ranks.update((row.source_node, row.dest_node))
        traffic_by_network[row.phase_id] += 1

    expected_task_ids = set(range(len(rows)))
    if traffic_task_ids != expected_task_ids:
        errors.append(
            f"traffic task ids must be dense 0..{len(rows) - 1}, got {sorted(traffic_task_ids)[:16]}"
        )

    for item in manifest:
        manifest_by_task[item.task_id].append(item)
        ranks.update((item.origin_src, item.final_dst, item.source_node, item.dest_node))
        by_axis[item.axis] += 1
        by_hop[item.hop_index] += 1
        by_network[item.network_class] += 1
        key = (item.origin_src, item.final_dst)
        message_bytes_by_pair.setdefault(key, item.total_message_bytes)
        if message_bytes_by_pair[key] != item.total_message_bytes:
            errors.append(f"inconsistent message bytes for pair {key}")

        attrs = (
            item.origin_src,
            item.final_dst,
            item.axis,
            item.slice_bytes,
            item.total_message_bytes,
        )
        previous_attrs = slice_attrs.setdefault(item.slice_id, attrs)
        if previous_attrs != attrs:
            errors.append(f"inconsistent attrs for slice {item.slice_id}: {previous_attrs} vs {attrs}")
        if item.bytes != item.slice_bytes:
            errors.append(f"slice {item.slice_id} transfer bytes must equal sliceBytes")
        slice_hops[item.slice_id].append(item)

        if item.hop_kind in {"only", "second"}:
            pair_bytes[key] += item.bytes
        if item.hop_kind == "first":
            if item.slice_id in first_hops:
                errors.append(f"duplicate first hop for slice {item.slice_id}")
            first_hops[item.slice_id] = item
        if item.hop_kind == "second":
            second_hops.append(item)

    traffic_by_id = {row.task_id: row for row in rows}
    for row in rows:
        items = manifest_by_task.get(row.task_id, [])
        if not items:
            errors.append(f"traffic task {row.task_id} carries no semantic slice")
            continue
        carried_bytes = sum(item.bytes for item in items)
        if carried_bytes != row.data_size:
            errors.append(
                f"traffic task {row.task_id} dataSize mismatch: row={row.data_size} manifest={carried_bytes}"
            )
        for item in items:
            if item.source_node != row.source_node or item.dest_node != row.dest_node:
                errors.append(f"traffic task {row.task_id} endpoint mismatch for slice {item.slice_id}")
            if item.phase_id != row.phase_id or item.depend_on_phases != row.depend_on_phases:
                errors.append(f"traffic task {row.task_id} phase/dependency mismatch for slice {item.slice_id}")
    for task_id in sorted(manifest_by_task):
        if task_id not in traffic_by_id:
            errors.append(f"manifest references missing traffic task {task_id}")

    for item in second_hops:
        first = first_hops.get(item.slice_id)
        if first is None:
            errors.append(f"missing first hop for slice {item.slice_id}")
            continue
        if item.source_node != first.dest_node:
            errors.append(
                f"second hop source mismatch for {item.origin_src}->{item.final_dst} axis {item.axis}"
            )
        if half_algorithm and dependency_shape == "logical" and item.depend_on_phases != (0, 10):
            errors.append(
                f"second hop dependency mismatch for {item.origin_src}->{item.final_dst} axis {item.axis}"
            )
        if algorithm == "pipeline" and dependency_shape == "logical":
            expected_dep = (first.phase_id,)
            if item.phase_id != first.phase_id + 1:
                errors.append(
                    f"pipeline second hop phase mismatch for slice {item.slice_id}: "
                    f"first={first.phase_id} second={item.phase_id}"
                )
            if item.depend_on_phases != expected_dep:
                errors.append(
                    f"pipeline second hop dependency mismatch for slice {item.slice_id}: "
                    f"{item.depend_on_phases} != {expected_dep}"
                )

    for slice_id, hops in sorted(slice_hops.items()):
        kinds = sorted(item.hop_kind for item in hops)
        axis = hops[0].axis
        if algorithm == "half-split":
            if axis in {"A", "B"} and kinds != ["first", "second"]:
                errors.append(f"split slice {slice_id} must have first+second hops, got {kinds}")
            if axis in {"C", "L"} and kinds != ["only"]:
                errors.append(f"direct slice {slice_id} must have only hop, got {kinds}")
        elif algorithm == "half-uniform":
            if axis not in {"A", "B"}:
                errors.append(f"half-uniform slice {slice_id} has unexpected axis {axis}")
            if kinds not in (["only"], ["first", "second"]):
                errors.append(f"half-uniform slice {slice_id} must be one-hop or two-hop, got {kinds}")
        else:
            if kinds not in (["only"], ["first", "second"]):
                errors.append(f"pipeline slice {slice_id} must be direct or two-hop, got {kinds}")

    missing_pairs = []
    bad_byte_pairs = []
    # Derive the expected domain from origin/final ranks.  This keeps the
    # checker usable for small 2x2 examples and later 64-rank cases.
    domain = sorted({item.origin_src for item in manifest} | {item.final_dst for item in manifest})
    expected_pairs = {(src, dst) for src in domain for dst in domain if src != dst}
    for pair in sorted(expected_pairs):
        expected = message_bytes_by_pair.get(pair)
        if expected is None:
            missing_pairs.append(pair)
            continue
        actual = pair_bytes[pair]
        if actual != expected:
            bad_byte_pairs.append({"src": pair[0], "dst": pair[1], "expected": expected, "actual": actual})

    phase_deps = phase_dependencies(rows)
    if dependency_shape == "logical":
        if half_algorithm:
            if phase_deps.get(0, "") != "":
                errors.append("phase 0 must have no dependency")
            if phase_deps.get(10, "") != "":
                errors.append("phase 10 must have no dependency")
            if phase_deps.get(1, "") != "0 10":
                errors.append("phase 1 must depend on 0 10")
            if phase_deps.get(11, "") != "0 10":
                errors.append("phase 11 must depend on 0 10")
        else:
            phases = sorted(phase_deps)
            if phases != list(range(max(phases) + 1)):
                errors.append(f"pipeline phases must be dense from 0, got {phases}")
            for phase in phases:
                expected = "" if phase == 0 else str(phase - 1)
                if phase_deps.get(phase) != expected:
                    errors.append(f"pipeline phase {phase} must depend on {expected!r}")
    else:
        row_phase_ids = {row.phase_id for row in rows}
        for row in rows:
            if row.phase_id != row.task_id:
                errors.append(f"serial-same-pair task {row.task_id} must use private phase {row.task_id}")
            missing_deps = [dep for dep in row.depend_on_phases if dep not in row_phase_ids]
            if missing_deps:
                errors.append(f"task {row.task_id} depends on missing phases {missing_deps}")
    if missing_pairs:
        errors.append(f"missing final pairs: {missing_pairs[:8]}")
    if bad_byte_pairs:
        errors.append(f"bad final bytes: {bad_byte_pairs[:8]}")

    return {
        "ok": not errors,
        "algorithm": algorithm,
        "errors": errors,
        "rank_count": len(domain),
        "final_pair_count": len(expected_pairs),
        "traffic_rows": len(rows),
        "manifest_rows": len(manifest),
        "semantic_slice_count": len(slice_attrs),
        "slice_bytes_distribution": dict(sorted(Counter(attr[3] for attr in slice_attrs.values()).items())),
        "by_axis": dict(sorted(by_axis.items())),
        "by_hop": dict(sorted(by_hop.items())),
        "by_network": dict(sorted(by_network.items())),
        "traffic_by_phase": dict(sorted(traffic_by_network.items())),
        "phase_dependencies": {str(key): value for key, value in phase_deps.items()},
        "missing_pairs": [{"src": src, "dst": dst} for src, dst in missing_pairs],
        "bad_byte_pairs": bad_byte_pairs,
    }


def write_traffic_csv(path: Path, rows: list[TrafficRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "taskId",
        "sourceNode",
        "destNode",
        "dataSize(Byte)",
        "opType",
        "priority",
        "delay",
        "phaseId",
        "dependOnPhases",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(fields)
        for row in rows:
            writer.writerow(
                [
                    row.task_id,
                    row.source_node,
                    row.dest_node,
                    row.data_size,
                    row.op_type,
                    row.priority,
                    row.delay,
                    row.phase_id,
                    deps_text(row.depend_on_phases),
                ]
            )


def write_manifest_csv(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            writer.writerow(
                {
                    "taskId": data["task_id"],
                    "originSrc": data["origin_src"],
                    "finalDst": data["final_dst"],
                    "axis": data["axis"],
                    "hopKind": data["hop_kind"],
                    "hopIndex": data["hop_index"],
                    "sourceNode": data["source_node"],
                    "destNode": data["dest_node"],
                    "intermediateNode": data["intermediate_node"],
                    "networkClass": data["network_class"],
                    "phaseId": data["phase_id"],
                    "dependOnPhases": deps_text(row.depend_on_phases),
                    "bytes": data["bytes"],
                    "sliceId": data["slice_id"],
                    "sliceBytes": data["slice_bytes"],
                    "totalMessageBytes": data["total_message_bytes"],
                }
            )


def _rank_count_from_manifest(rows: list[ManifestRow]) -> int:
    domain = {row.origin_src for row in rows} | {row.final_dst for row in rows}
    if not domain:
        return 0
    expected = set(range(max(domain) + 1))
    if domain != expected:
        raise ValueError(f"rank ids must be dense from 0, got {sorted(domain)}")
    return len(domain)


def write_traffic_with_slices_csv(
    path: Path,
    traffic_rows: list[TrafficRow],
    manifest_rows: list[ManifestRow],
) -> None:
    rank_count = _rank_count_from_manifest(manifest_rows)
    _ = rank_count
    manifest_by_task: dict[int, list[ManifestRow]] = defaultdict(list)
    for item in manifest_rows:
        manifest_by_task[item.task_id].append(item)
    fields = [
        "taskId",
        "sourceNode",
        "destNode",
        "dataSize(Byte)",
        "opType",
        "priority",
        "delay",
        "phaseId",
        "dependOnPhases",
        "slices",
        "sliceBytes",
        "sliceOriginSrcs",
        "sliceFinalDsts",
        "sliceAxes",
        "sliceHopKinds",
        "sliceHopIndexes",
        "sliceIntermediates",
        "networkClass",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for traffic in traffic_rows:
            metas = sorted(manifest_by_task.get(traffic.task_id, []), key=lambda item: item.slice_id)
            if not metas:
                raise ValueError(f"traffic task {traffic.task_id} carries no semantic slice")
            network_classes = {meta.network_class for meta in metas}
            if len(network_classes) != 1:
                raise ValueError(f"traffic task {traffic.task_id} mixes network classes: {network_classes}")
            writer.writerow(
                {
                    "taskId": traffic.task_id,
                    "sourceNode": traffic.source_node,
                    "destNode": traffic.dest_node,
                    "dataSize(Byte)": traffic.data_size,
                    "opType": traffic.op_type,
                    "priority": traffic.priority,
                    "delay": traffic.delay,
                    "phaseId": traffic.phase_id,
                    "dependOnPhases": deps_text(traffic.depend_on_phases),
                    "slices": " ".join(str(meta.slice_id) for meta in metas),
                    "sliceBytes": " ".join(str(meta.slice_bytes) for meta in metas),
                    "sliceOriginSrcs": " ".join(str(meta.origin_src) for meta in metas),
                    "sliceFinalDsts": " ".join(str(meta.final_dst) for meta in metas),
                    "sliceAxes": " ".join(str(meta.axis) for meta in metas),
                    "sliceHopKinds": " ".join(str(meta.hop_kind) for meta in metas),
                    "sliceHopIndexes": " ".join(str(meta.hop_index) for meta in metas),
                    "sliceIntermediates": " ".join(str(meta.intermediate_node) for meta in metas),
                    "networkClass": next(iter(network_classes)),
                }
            )


def write_logical_slices_csv(path: Path, rows: list[ManifestRow]) -> None:
    _rank_count_from_manifest(rows)
    final_rows: dict[int, ManifestRow] = {}
    for row in rows:
        if row.hop_kind not in {"only", "second"}:
            continue
        previous = final_rows.setdefault(row.slice_id, row)
        if previous.slice_bytes != row.slice_bytes:
            raise ValueError(f"slice {row.slice_id} has inconsistent sizes")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["round", "src", "dst", "slices", "bytes"])
        for slice_id, row in sorted(final_rows.items()):
            writer.writerow([0, row.origin_src, row.final_dst, slice_id, row.slice_bytes])


def write_case_note(
    path: Path,
    *,
    group_count: int,
    group_size: int,
    message_bytes: int,
    split_ratio: str,
    algorithm: str,
    task_granularity: str,
    same_pair_order: str,
    summary: dict[str, object],
) -> None:
    text = f"""# Dual-axis All-to-All logical 2D traffic case

User context: generated after the user asked to implement the dual-axis
All-to-All traffic script and first inspect a simple 2x2 traffic.csv.

Scenario:
- purpose: algorithm construction and semantic checker smoke test
- group_count: {group_count}
- group_size: {group_size}
- rank_count: {group_count * group_size}
- message_bytes_per_directed_pair: {message_bytes}
- split_ratio_A_clos_first_to_B_local_first: {split_ratio}
- algorithm: {algorithm}
- task_granularity: {task_granularity}
- same_pair_order: {same_pair_order}

Algorithm:
- Rank coordinate: rank = `(group, slot)`.
- Phase 0 / Axis A first round: `(src_group, src_slot)` sends the aggregate
  of all slices whose final destination group is `dst_group` to
  `(dst_group, src_slot)` over Clos.  Same-slot cross-group pairs finish in this
  phase as Clos-only traffic.
- Phase 10 / Axis B first round: `(src_group, src_slot)` sends the aggregate
  of all slices whose final destination slot is `dst_slot` to
  `(src_group, dst_slot)` over local.  Same-group pairs finish in this phase as
  local-only traffic.
- Barrier: phases 1 and 11 both depend on first-stage phases 0 and 10.
- Phase 1 / Axis A second round: `(dst_group, src_slot)` local-scatters the A
  slices it received to `(dst_group, dst_slot)`.
- Phase 11 / Axis B second round: `(src_group, dst_slot)` Clos-scatters the B
  slices it received to `(dst_group, dst_slot)`.
- Cross-group cross-slot pairs are split by `split_ratio`: the A slice goes
  Clos then local; the B slice goes local then Clos.  The final
  `(dst_group, dst_slot)` receives data from both intermediate coordinates:
  `(dst_group, src_slot)` and `(src_group, dst_slot)`.
- For `dual-axis-half-uniform`, every non-self pair is split by `split_ratio`.
  Same-slot cross-group A slices and same-slot cross-group B slices are one-hop
  Clos transfers.  Same-group A slices and same-group B slices are one-hop local
  transfers.  Cross-group cross-slot slices still use the two-hop A/B paths.

CSV semantics:
- traffic.csv is task-granularity input for simulation.  A row may carry
  multiple semantic slices because one alltoall transfer aggregates all data
  with the same intermediate coordinate.
- task_granularity controls whether same-route slices are aggregated into one
  task row (`aggregate`) or emitted as one task per semantic slice (`slice`).
- same_pair_order controls whether tasks with the same logical phase and
  `sourceNode,destNode` are allowed to run in parallel (`parallel`) or are
  serialized by assigning each task a private physical phase (`serial`).
- dual_axis_a2a_manifest.csv is slice-transfer granularity and is the
  authoritative semantic audit trail.
- traffic-with-slices-logical.csv is final semantic-slice granularity for
  alltoall coverage checking.

Generated artifacts:
- traffic.csv
- traffic-with-slices.csv
- traffic-with-slices-logical.csv
- dual_axis_a2a_manifest.csv
- expected_summary.json

Checker summary:
```json
{json.dumps(summary, indent=2, sort_keys=True)}
```
"""
    (path / "CASE.md").write_text(text, encoding="utf-8")


def default_out_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return SCRATCH_DIR / f"{stamp}-dual-axis-a2a-2x2-logical"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-count", type=int, default=2)
    parser.add_argument("--group-size", type=int, default=2)
    parser.add_argument("--message-bytes", type=int, default=64 * 1024)
    parser.add_argument("--split-ratio", default="1:1")
    parser.add_argument("--algorithm", choices=["half-split", "half-uniform"], default="half-split")
    parser.add_argument("--task-granularity", choices=["aggregate", "slice"], default="aggregate")
    parser.add_argument("--same-pair-order", choices=["parallel", "serial"], default="parallel")
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    clos_weight, local_weight = parse_ratio(args.split_ratio)
    if args.algorithm == "half-split":
        rows, manifest = build_dual_axis_a2a(
            group_count=args.group_count,
            group_size=args.group_size,
            message_bytes=args.message_bytes,
            clos_weight=clos_weight,
            local_weight=local_weight,
            task_granularity=args.task_granularity,
        )
        checker_algorithm = "half-split"
    else:
        rows, manifest = build_dual_axis_half_uniform_a2a(
            group_count=args.group_count,
            group_size=args.group_size,
            message_bytes=args.message_bytes,
            clos_weight=clos_weight,
            local_weight=local_weight,
            task_granularity=args.task_granularity,
        )
        checker_algorithm = "half-uniform"
    dependency_shape = "logical"
    if args.same_pair_order == "serial":
        rows, manifest = serialize_same_pair_tasks(rows, manifest)
        dependency_shape = "serial-same-pair"
    summary = check_dual_axis_a2a(rows, manifest, algorithm=checker_algorithm, dependency_shape=dependency_shape)
    if not summary["ok"]:
        raise SystemExit(json.dumps(summary, indent=2, sort_keys=True))
    out_dir = args.out_dir or default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_traffic_csv(out_dir / "traffic.csv", rows)
    write_traffic_with_slices_csv(out_dir / "traffic-with-slices.csv", rows, manifest)
    write_logical_slices_csv(out_dir / "traffic-with-slices-logical.csv", manifest)
    write_manifest_csv(out_dir / "dual_axis_a2a_manifest.csv", manifest)
    (out_dir / "expected_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_case_note(
        out_dir,
        group_count=args.group_count,
        group_size=args.group_size,
        message_bytes=args.message_bytes,
        split_ratio=args.split_ratio,
        algorithm=args.algorithm,
        task_granularity=args.task_granularity,
        same_pair_order=args.same_pair_order,
        summary=summary,
    )
    print(f"case_dir={out_dir}")
    print(f"traffic={out_dir / 'traffic.csv'}")
    print(f"manifest={out_dir / 'dual_axis_a2a_manifest.csv'}")
    print(f"summary={out_dir / 'expected_summary.json'}")
    print("ok=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())