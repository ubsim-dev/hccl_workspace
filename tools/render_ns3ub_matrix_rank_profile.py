#!/usr/bin/env python3
"""Render a single-rank HCCL UBX matrix AllToAll task profile."""

from __future__ import annotations

import argparse
import csv
import html
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Task:
    task_id: int
    src: int
    dst: int
    size: int
    phase: int
    start_us: float
    end_us: float
    throughput_gbps: float
    slot_kind: str
    slot_idx: int
    round_id: int


def matrix_dim(rank_count: int) -> int:
    dim = math.isqrt(rank_count)
    if dim * dim != rank_count:
        raise ValueError("rank-count must be a perfect square")
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


def round_slots(round_id: int, my_alg_rank: int, dim: int) -> list[tuple[int, str, int]]:
    my_row, my_col = divmod(my_alg_rank, dim)
    tx_col = (my_col + round_id) % dim
    result = [
        (
            matrix_rank((my_row + round_id) % dim, my_col, dim),
            "mesh",
            0,
        )
    ]
    for plane in range(dim):
        peer_row = (plane + dim - my_row) % dim
        result.append((matrix_rank(peer_row, tx_col, dim), "clos", plane))
    return result


def slot_map_for_rank(rank: int, rank_count: int, rank_start: int) -> dict[int, tuple[str, int, int]]:
    dim = matrix_dim(rank_count)
    src_alg = node_to_alg(rank, dim, rank_start)
    mapping: dict[int, tuple[str, int, int]] = {}
    for round_id in range(1, dim):
        for dst_alg, kind, idx in round_slots(round_id, src_alg, dim):
            dst_node = alg_to_node(dst_alg, dim, rank_start)
            mapping[dst_node] = (kind, idx, round_id)
    return mapping


def load_rank_tasks(case_dir: Path, rank: int, rank_count: int, rank_start: int, priority: int) -> list[Task]:
    slot_by_dst = slot_map_for_rank(rank, rank_count, rank_start)
    tasks: list[Task] = []
    with (case_dir / "output" / "task_statistics.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority or int(row["sourceNodeId"]) != rank:
                continue
            dst = int(row["destNodeId"])
            kind, idx, round_id = slot_by_dst[dst]
            tasks.append(
                Task(
                    task_id=int(row["taskId"]),
                    src=int(row["sourceNodeId"]),
                    dst=dst,
                    size=int(row["dataSize(Byte)"]),
                    phase=int(row["phaseId"]),
                    start_us=float(row["taskStartTime(us)"]),
                    end_us=float(row["taskCompletesTime(us)"]),
                    throughput_gbps=float(row["taskThroughput(Gbps)"]),
                    slot_kind=kind,
                    slot_idx=idx,
                    round_id=round_id,
                )
            )
    return sorted(tasks, key=lambda t: (t.slot_kind, t.slot_idx, t.round_id, t.task_id))


def render_html(case_dir: Path, tasks: list[Task], rank: int, rank_count: int, title: str) -> str:
    dim = matrix_dim(rank_count)
    max_end = max((task.end_us for task in tasks), default=1.0)
    total_bytes = sum(task.size for task in tasks)
    direct_gbs = total_bytes / max_end / 1e3 if max_end else 0

    lanes: dict[tuple[str, int], list[Task]] = defaultdict(list)
    for task in tasks:
        lanes[(task.slot_kind, task.slot_idx)].append(task)

    lane_order = [("mesh", 0)]
    lane_order.extend(("clos", idx) for idx in range(dim))
    lane_order = [lane for lane in lane_order if lane in lanes]

    left = 190
    right = 28
    top = 86
    row_h = 30
    row_gap = 8
    width = 1180
    plot_w = width - left - right
    x_scale = plot_w / max_end if max_end else 1
    height = top + len(lane_order) * (row_h + row_gap) + 62

    ticks = []
    for tick_idx in range(9):
        t = max_end * tick_idx / 8
        x = left + t * x_scale
        ticks.append(
            f'<line class="grid" x1="{x:.2f}" y1="{top - 30}" x2="{x:.2f}" y2="{height - 48}" />'
            f'<text class="tick" x="{x:.2f}" y="{top - 38}" text-anchor="middle">{t:.1f}us</text>'
        )

    rows = []
    for row_idx, lane in enumerate(lane_order):
        y = top + row_idx * (row_h + row_gap)
        kind, idx = lane
        label = "matrix-mesh-slot[0]" if kind == "mesh" else f"matrix-clos-slot[{idx}]"
        lane_tasks = sorted(lanes[lane], key=lambda task: (task.start_us, task.round_id, task.task_id))
        rows.append(
            f'<rect class="row-bg" x="0" y="{y - 5:.2f}" width="{width}" height="{row_h:.2f}" />'
            f'<text class="row-label" x="16" y="{y + 14:.2f}">{html.escape(label)}</text>'
            f'<text class="row-meta" x="150" y="{y + 14:.2f}">{len(lane_tasks)} task</text>'
        )
        for task in lane_tasks:
            x = left + task.start_us * x_scale
            w = max(2.0, (task.end_us - task.start_us) * x_scale)
            label_text = f"r{task.round_id} {task.task_id}: {task.src}->{task.dst}"
            duration_us = task.end_us - task.start_us
            gbps = task.throughput_gbps / 8
            tip = (
                f"task {task.task_id} round {task.round_id} {task.src}->{task.dst} phase {task.phase}\\n"
                f"{task.start_us:.3f}us - {task.end_us:.3f}us\\n"
                f"{task.size} B, {task.throughput_gbps:.1f} Gbps"
            )
            rows.append(
                f'<g class="task" tabindex="0" data-task-id="{task.task_id}" '
                f'data-flow="{task.src}->{task.dst}" data-round="{task.round_id}" '
                f'data-phase="{task.phase}" data-slot="{html.escape(label)}" '
                f'data-start="{task.start_us:.6f} us" data-end="{task.end_us:.6f} us" '
                f'data-duration="{duration_us:.6f} us" data-size="{task.size} B" '
                f'data-throughput="{task.throughput_gbps:.3f} Gbps / {gbps:.3f} GB/s">'
                f'<title>{html.escape(tip)}</title>'
                f'<rect class="bar phase{task.round_id % 4}" x="{x:.2f}" y="{y + 3:.2f}" '
                f'width="{w:.2f}" height="18" rx="3" />'
                f'<text class="bar-label" x="{x + 5:.2f}" y="{y + 16:.2f}">{html.escape(label_text)}</text>'
                f'</g>'
            )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2937; font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1220px, calc(100% - 32px)); margin: 24px auto 42px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    .muted {{ color: #667085; margin: 0 0 16px; }}
    .panel {{ background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 14px; overflow-x: auto; }}
    svg {{ min-width: {width}px; }}
    .grid {{ stroke: #e4e8ef; stroke-width: 1; }}
    .tick, .row-meta {{ fill: #667085; font-size: 12px; }}
    .row-label {{ fill: #1f2937; font-size: 13px; font-weight: 650; }}
    .row-bg {{ fill: #fbfcfe; }}
    .bar {{ stroke: rgba(31,41,55,.25); stroke-width: .6; }}
    .task {{ cursor: pointer; outline: none; }}
    .task:focus .bar, .task.selected .bar {{ stroke: #111827; stroke-width: 1.8; opacity: 1; }}
    .phase0 {{ fill: #2563eb; }}
    .phase1 {{ fill: #0f766e; }}
    .phase2 {{ fill: #b42318; }}
    .phase3 {{ fill: #7c3aed; }}
    .bar-label {{ fill: white; font-size: 11px; pointer-events: none; }}
    .details {{ margin-top: 12px; background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 14px; }}
    .details h2 {{ margin: 0 0 10px; font-size: 16px; }}
    .detail-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .detail-item {{ background: #fbfcfe; border: 1px solid #edf1f6; border-radius: 6px; padding: 8px 10px; }}
    .detail-label {{ color: #667085; font-size: 12px; }}
    .detail-value {{ color: #111827; font-weight: 650; font-variant-numeric: tabular-nums; }}
    code {{ background: #eef2f7; padding: 1px 5px; border-radius: 5px; }}
    @media (max-width: 760px) {{ .detail-grid {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <p class="muted">case: <code>{html.escape(str(case_dir))}</code> | rank {rank} | tasks {len(tasks)} | makespan {max_end:.3f}us | direct {direct_gbs:.2f} GB/s</p>
  <div class="panel">
    <svg width="{width}" height="{height}" role="img" aria-label="HCCL matrix rank profile">
      {''.join(ticks)}
      {''.join(rows)}
    </svg>
  </div>
  <section class="details" aria-live="polite">
    <h2>Task detail</h2>
    <div class="detail-grid" id="detail-grid">
      <div class="detail-item"><div class="detail-label">选择</div><div class="detail-value">点击上方任意条形流</div></div>
    </div>
  </section>
</main>
<script>
  const detailGrid = document.getElementById('detail-grid');
  const fields = [
    ['taskId', 'Task'],
    ['flow', 'Flow'],
    ['slot', 'Slot'],
    ['round', 'Round'],
    ['phase', 'Phase'],
    ['start', 'Start'],
    ['end', 'End'],
    ['duration', 'Duration'],
    ['size', 'Data'],
    ['throughput', 'Throughput'],
  ];
  function selectTask(node) {{
    document.querySelectorAll('.task.selected').forEach((el) => el.classList.remove('selected'));
    node.classList.add('selected');
    detailGrid.innerHTML = fields.map(([key, label]) => {{
      const value = node.dataset[key] || '-';
      return `<div class="detail-item"><div class="detail-label">${{label}}</div><div class="detail-value">${{value}}</div></div>`;
    }}).join('');
  }}
  document.querySelectorAll('.task').forEach((node) => {{
    node.addEventListener('click', () => selectTask(node));
    node.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter' || event.key === ' ') {{
        event.preventDefault();
        selectTask(node);
      }}
    }});
  }});
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--rank-count", type=int, default=16)
    parser.add_argument("--rank-start", type=int, default=0)
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--title", default="HCCL UBX matrix rank profile")
    args = parser.parse_args()

    tasks = load_rank_tasks(args.case_dir, args.rank, args.rank_count, args.rank_start, args.priority)
    if not tasks:
        raise ValueError(f"no tasks found for rank {args.rank}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(args.case_dir, tasks, args.rank, args.rank_count, args.title))
    print(f"wrote {args.output}")
    print(f"rank={args.rank} tasks={len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
