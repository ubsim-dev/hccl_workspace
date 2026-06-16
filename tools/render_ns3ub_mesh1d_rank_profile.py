#!/usr/bin/env python3
"""Render a single-rank Mesh1D baseline task profile.

Rows are logical Mesh1D parallel slots within each round, not physical ports.
The Nth task for that source in a round is shown on mesh1d-thread[N].
"""

from __future__ import annotations

import argparse
import csv
import html
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
    unit_idx: int


def load_unit_indices(case_dir: Path, rank: int, priority: int, concurrent: int | None) -> dict[int, int]:
    task_rows: list[dict[str, str]] = []
    with (case_dir / "traffic.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority or int(row["sourceNodeId"]) != rank:
                continue
            task_rows.append(row)

    if concurrent is None:
        first_round = [row for row in task_rows if not row.get("dependOnPhases", "").strip()]
        concurrent = len(first_round) if first_round else len(task_rows)
    if concurrent <= 0:
        raise ValueError("concurrent must be positive")

    unit_by_task: dict[int, int] = {}
    # Mesh1D generation writes tasks round-major. In thread-serial mode phaseId
    # is unique per task, so phaseId cannot be used to recover the lane.
    for idx, row in enumerate(task_rows):
        unit_by_task[int(row["taskId"])] = idx % concurrent
    return unit_by_task


def load_rank_tasks(case_dir: Path, rank: int, priority: int, concurrent: int | None) -> list[Task]:
    unit_by_task = load_unit_indices(case_dir, rank, priority, concurrent)
    tasks: list[Task] = []
    with (case_dir / "output" / "task_statistics.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority or int(row["sourceNodeId"]) != rank:
                continue
            task_id = int(row["taskId"])
            tasks.append(
                Task(
                    task_id=task_id,
                    src=int(row["sourceNodeId"]),
                    dst=int(row["destNodeId"]),
                    size=int(row["dataSize(Byte)"]),
                    phase=int(row["phaseId"]),
                    start_us=float(row["taskStartTime(us)"]),
                    end_us=float(row["taskCompletesTime(us)"]),
                    throughput_gbps=float(row["taskThroughput(Gbps)"]),
                    unit_idx=unit_by_task[task_id],
                )
            )
    return sorted(tasks, key=lambda t: (t.unit_idx, t.phase, t.task_id))


def render_html(case_dir: Path, tasks: list[Task], rank: int, title: str) -> str:
    max_end = max((task.end_us for task in tasks), default=1.0)
    total_bytes = sum(task.size for task in tasks)
    direct_gbs = total_bytes / max_end / 1e3 if max_end else 0

    lanes: dict[int, list[Task]] = defaultdict(list)
    for task in tasks:
        lanes[task.unit_idx].append(task)

    left = 176
    right = 28
    top = 88
    row_h = 30
    row_gap = 8
    width = 1180
    plot_w = width - left - right
    x_scale = plot_w / max_end if max_end else 1
    height = top + len(lanes) * (row_h + row_gap) + 70

    ticks = []
    for idx in range(9):
        t = max_end * idx / 8
        x = left + t * x_scale
        ticks.append(
            f'<line class="grid" x1="{x:.2f}" y1="{top - 30}" x2="{x:.2f}" y2="{height - 48}" />'
            f'<text class="tick" x="{x:.2f}" y="{top - 38}" text-anchor="middle">{t:.1f}us</text>'
        )

    rows = []
    for row_idx, unit_idx in enumerate(sorted(lanes)):
        y = top + row_idx * (row_h + row_gap)
        lane_tasks = sorted(lanes[unit_idx], key=lambda t: (t.start_us, t.task_id))
        rows.append(
            f'<rect class="row-bg" x="0" y="{y - 5:.2f}" width="{width}" height="{row_h:.2f}" />'
            f'<text class="row-label" x="16" y="{y + 14:.2f}">mesh1d-thread[{unit_idx}]</text>'
            f'<text class="row-meta" x="130" y="{y + 14:.2f}">{len(lane_tasks)} task</text>'
        )
        for task in lane_tasks:
            x = left + task.start_us * x_scale
            w = max(2.0, (task.end_us - task.start_us) * x_scale)
            label = f"{task.task_id}: {task.src}->{task.dst}"
            lane_label = f"mesh1d-thread[{unit_idx}]"
            duration_us = task.end_us - task.start_us
            gbps = task.throughput_gbps / 8
            tip = (
                f"task {task.task_id} {task.src}->{task.dst} phase {task.phase}\\n"
                f"{task.start_us:.3f}us - {task.end_us:.3f}us\\n"
                f"{task.size} B, {task.throughput_gbps:.1f} Gbps"
            )
            rows.append(
                f'<g class="task" tabindex="0" data-task-id="{task.task_id}" '
                f'data-flow="{task.src}->{task.dst}" data-slot="{html.escape(lane_label)}" '
                f'data-phase="{task.phase}" data-start="{task.start_us:.6f} us" '
                f'data-end="{task.end_us:.6f} us" data-duration="{duration_us:.6f} us" '
                f'data-size="{task.size} B" '
                f'data-throughput="{task.throughput_gbps:.3f} Gbps / {gbps:.3f} GB/s">'
                f'<title>{html.escape(tip)}</title>'
                f'<rect class="bar phase{task.phase % 4}" x="{x:.2f}" y="{y + 3:.2f}" '
                f'width="{w:.2f}" height="18" rx="3" />'
                f'<text class="bar-label" x="{x + 5:.2f}" y="{y + 16:.2f}">{html.escape(label)}</text>'
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
    <svg width="{width}" height="{height}" role="img" aria-label="Mesh1D rank profile">
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
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--concurrent", type=int, help="Logical Mesh1D slots per round. Inferred from traffic.csv when omitted.")
    parser.add_argument("--title", default="Mesh1D rank profile")
    args = parser.parse_args()

    tasks = load_rank_tasks(args.case_dir, args.rank, args.priority, args.concurrent)
    if not tasks:
        raise ValueError(f"no tasks found for rank {args.rank}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(args.case_dir, tasks, args.rank, args.title))
    print(f"wrote {args.output}")
    print(f"rank={args.rank} tasks={len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
