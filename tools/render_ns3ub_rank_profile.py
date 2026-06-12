#!/usr/bin/env python3
"""Render a single-rank HCCL V3-style task profiling timeline.

Rows are logical algorithm parallel units, not physical ports:
  - mesh-thread[i] for same-mesh peers, matching Mesh2DV3 neighborIdx.
  - clos-thread[i] for cross-mesh peers, matching MeshClosV3 linkIdx.

The bars use ns-3 task start/end times. If multiple tasks mapped to the same
logical unit overlap in ns-3, the row is split into visual sublanes instead of
inventing a serialization that is not present in task_statistics.csv.
"""

from __future__ import annotations

import argparse
import csv
import html
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


INVALID_GROUP_ID = -1


@dataclass
class Task:
    task_id: int
    src: int
    dst: int
    size: int
    start_us: float
    end_us: float
    throughput_gbps: float
    unit_kind: str
    unit_idx: int


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


def infer_inter_channel_count(source_case: Path, rank_count: int, group_size: int, priority: int) -> int:
    counts: dict[tuple[int, int], int] = defaultdict(int)
    with (source_case / "transport_channel.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority:
                continue
            a = int(row["nodeId1"])
            b = int(row["nodeId2"])
            if a >= rank_count or b >= rank_count:
                continue
            if a // group_size == b // group_size:
                continue
            counts[tuple(sorted((a, b)))] += 1
    if not counts:
        raise ValueError(f"could not infer inter channel count from {source_case}")
    return max(counts.values())


def logical_unit(src: int, dst: int, rank_count: int, group_size: int, clos_channels: int) -> tuple[str, int]:
    src_group, src_local = divmod(src, group_size)
    dst_group, dst_local = divmod(dst, group_size)
    if src_group == dst_group:
        ordered_peers = [
            src_group * group_size + ((src_local + 1 + idx) % group_size)
            for idx in range(group_size - 1)
        ]
        return "mesh", ordered_peers.index(dst)

    group_num = rank_count // group_size
    color_round_num = pairwise_round_num(group_num)
    if color_round_num == 0:
        return "clos", 0

    for color_round in range(color_round_num):
        peer_group, src_group_is_left = pair_group_in_round(group_num, src_group, color_round)
        if peer_group != dst_group:
            continue
        if src_group_is_left:
            shift = (dst_local - src_local) % group_size
        else:
            shift = (src_local - dst_local) % group_size
        micro_round = shift * color_round_num + color_round
        return "clos", micro_round % clos_channels

    raise ValueError(f"rank pair {src}->{dst} is not covered by V3 group-pairwise schedule")


def load_rank_tasks(
    case_dir: Path,
    rank: int,
    rank_count: int,
    group_size: int,
    clos_channels: int,
    priority: int,
) -> list[Task]:
    tasks: list[Task] = []
    with (case_dir / "output" / "task_statistics.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority:
                continue
            src = int(row["sourceNodeId"])
            if src != rank:
                continue
            dst = int(row["destNodeId"])
            unit_kind, unit_idx = logical_unit(src, dst, rank_count, group_size, clos_channels)
            tasks.append(
                Task(
                    task_id=int(row["taskId"]),
                    src=src,
                    dst=dst,
                    size=int(row["dataSize(Byte)"]),
                    start_us=float(row["taskStartTime(us)"]),
                    end_us=float(row["taskCompletesTime(us)"]),
                    throughput_gbps=float(row["taskThroughput(Gbps)"]),
                    unit_kind=unit_kind,
                    unit_idx=unit_idx,
                )
            )
    return sorted(tasks, key=lambda t: (t.unit_kind, t.unit_idx, t.task_id))


def pack_sublanes(tasks: list[Task]) -> list[tuple[Task, int]]:
    ends: list[float] = []
    packed: list[tuple[Task, int]] = []
    for task in sorted(tasks, key=lambda t: (t.start_us, t.end_us, t.task_id)):
        sublane = None
        for idx, end_us in enumerate(ends):
            if task.start_us >= end_us:
                sublane = idx
                break
        if sublane is None:
            sublane = len(ends)
            ends.append(task.end_us)
        else:
            ends[sublane] = task.end_us
        packed.append((task, sublane))
    return packed


def render_html(case_dir: Path, tasks: list[Task], rank: int, group_size: int, clos_channels: int, title: str) -> str:
    max_end = max((task.end_us for task in tasks), default=1.0)
    total_bytes = sum(task.size for task in tasks)
    direct_gbs = total_bytes / max_end / 1e3 if max_end else 0

    lanes: dict[tuple[str, int], list[Task]] = defaultdict(list)
    for task in tasks:
        lanes[(task.unit_kind, task.unit_idx)].append(task)

    lane_order = [("mesh", idx) for idx in range(group_size - 1)]
    lane_order.extend(("clos", idx) for idx in range(clos_channels))
    lane_order = [lane for lane in lane_order if lane in lanes]

    left = 172
    right = 28
    top = 84
    row_gap = 8
    sublane_h = 15
    min_row_h = 24
    width = 1180
    plot_w = width - left - right
    x_scale = plot_w / max_end if max_end else 1

    row_entries = []
    y = top
    for lane in lane_order:
        packed = pack_sublanes(lanes[lane])
        sublane_count = max((s for _, s in packed), default=0) + 1
        row_h = max(min_row_h, sublane_count * sublane_h + 9)
        row_entries.append((lane, packed, y, row_h))
        y += row_h + row_gap
    height = y + 52

    ticks = []
    for idx in range(9):
        t = max_end * idx / 8
        x = left + t * x_scale
        ticks.append(
            f'<line class="grid" x1="{x:.2f}" y1="{top - 28}" x2="{x:.2f}" y2="{height - 44}" />'
            f'<text class="tick" x="{x:.2f}" y="{top - 36}" text-anchor="middle">{t:.1f}us</text>'
        )

    rows = []
    for lane, packed, row_y, row_h in row_entries:
        kind, idx = lane
        label = f"{kind}-thread[{idx}]"
        lane_tasks = lanes[lane]
        rows.append(
            f'<rect class="row-bg" x="0" y="{row_y - 4:.2f}" width="{width}" height="{row_h:.2f}" />'
            f'<text class="row-label" x="16" y="{row_y + 12:.2f}">{html.escape(label)}</text>'
            f'<text class="row-meta" x="124" y="{row_y + 12:.2f}">{len(lane_tasks)} task</text>'
        )
        for task, sublane in packed:
            x = left + task.start_us * x_scale
            w = max(1.0, (task.end_us - task.start_us) * x_scale)
            bar_y = row_y + 3 + sublane * sublane_h
            cls = "bar mesh" if task.unit_kind == "mesh" else "bar clos"
            label_x = x + 4
            tooltip = (
                f"task {task.task_id}: {task.src}->{task.dst}\\n"
                f"{task.start_us:.3f}us - {task.end_us:.3f}us\\n"
                f"{task.size} bytes\\n"
                f"{task.throughput_gbps / 8:.2f} GB/s\\n"
                f"{task.unit_kind}-thread[{task.unit_idx}]"
            )
            rows.append(
                f'<rect class="{cls}" x="{x:.2f}" y="{bar_y:.2f}" width="{w:.2f}" height="11" rx="2">'
                f'<title>{html.escape(tooltip)}</title></rect>'
                f'<text class="bar-label" x="{label_x:.2f}" y="{bar_y + 8.5:.2f}">{task.dst}</text>'
            )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2937; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1240px, calc(100% - 32px)); margin: 24px auto 44px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    .muted {{ color: #667085; margin: 0 0 14px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }}
    .metric {{ background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; }}
    .k {{ color: #667085; font-size: 12px; }}
    .v {{ font-size: 22px; font-weight: 700; }}
    .panel {{ overflow-x: auto; background: #fff; border: 1px solid #d8dee8; border-radius: 8px; }}
    svg {{ display: block; min-width: {width}px; }}
    .grid {{ stroke: #e3e8f0; stroke-width: 1; }}
    .tick, .row-meta {{ fill: #667085; font-size: 11px; }}
    .row-bg {{ fill: #fbfcfe; stroke: #edf1f6; }}
    .row-label {{ fill: #111827; font-size: 12px; font-weight: 650; }}
    .bar {{ stroke: rgba(17,24,39,.24); stroke-width: .5; opacity: .86; }}
    .bar.mesh {{ fill: #0f766e; }}
    .bar.clos {{ fill: #2563eb; }}
    .bar-label {{ fill: #fff; font-size: 9px; pointer-events: none; }}
    .legend {{ display: flex; gap: 16px; color: #475467; font-size: 12px; margin-top: 10px; }}
    .swatch {{ display: inline-block; width: 12px; height: 8px; border-radius: 2px; margin-right: 5px; }}
    @media (max-width: 760px) {{ .metrics {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <p class="muted">单 rank TX 视角；行是 V3 源码逻辑并行单元，条形是 ns-3 task 的实际 start/end。条形上的数字是目的 rank。</p>
  <div class="metrics">
    <div class="metric"><div class="k">Case</div><div class="v">{html.escape(case_dir.name)}</div></div>
    <div class="metric"><div class="k">Rank</div><div class="v">{rank}</div></div>
    <div class="metric"><div class="k">Tasks</div><div class="v">{len(tasks)}</div></div>
    <div class="metric"><div class="k">TX GB/s</div><div class="v">{direct_gbs:.2f}</div></div>
  </div>
  <div class="panel">
    <svg width="{width}" height="{height}" role="img" aria-label="{html.escape(title)}">
      {''.join(ticks)}
      {''.join(rows)}
    </svg>
  </div>
  <div class="legend">
    <span><span class="swatch" style="background:#0f766e"></span>mesh 组内 task</span>
    <span><span class="swatch" style="background:#2563eb"></span>clos 跨组 task</span>
  </div>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--rank-count", type=int, required=True)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--source-case", type=Path, default=Path("generated_topology"))
    parser.add_argument("--clos-channels", type=int)
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--title")
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    source_case = args.source_case.resolve()
    clos_channels = args.clos_channels
    if clos_channels is None:
        clos_channels = infer_inter_channel_count(source_case, args.rank_count, args.group_size, args.priority)

    tasks = load_rank_tasks(case_dir, args.rank, args.rank_count, args.group_size, clos_channels, args.priority)
    if not tasks:
        raise ValueError(f"no TX tasks for rank {args.rank} in {case_dir}")

    title = args.title or f"Rank {args.rank} V3 Logical Timeline"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(case_dir, tasks, args.rank, args.group_size, clos_channels, title))
    print(f"wrote {args.output}")
    print(f"rank={args.rank} tasks={len(tasks)} clos_channels={clos_channels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
