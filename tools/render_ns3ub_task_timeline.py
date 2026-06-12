#!/usr/bin/env python3
"""Render an ns-3-ub task timeline as a standalone HTML file.

For strict MeshClos V3 cases, each rank pair has one transport channel, so a
task can be mapped back to the source rank's selected TX port. Rows are grouped
by source rank and source port, which is a close proxy for the communication
thread / physical plane view discussed in the AllToAll analysis.
"""

from __future__ import annotations

import argparse
import csv
import html
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Channel:
    node1: int
    port1: int
    tpn1: int
    node2: int
    port2: int
    tpn2: int


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
    src_port: int | None
    src_tpn: int | None
    dst_port: int | None
    dst_tpn: int | None
    is_intra: bool


def load_channels(path: Path, priority: int) -> dict[tuple[int, int], list[Channel]]:
    channels: dict[tuple[int, int], list[Channel]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority:
                continue
            node1 = int(row["nodeId1"])
            node2 = int(row["nodeId2"])
            key = tuple(sorted((node1, node2)))
            channels[key].append(
                Channel(
                    node1=node1,
                    port1=int(row["portId1"]),
                    tpn1=int(row["tpn1"]),
                    node2=node2,
                    port2=int(row["portId2"]),
                    tpn2=int(row["tpn2"]),
                )
            )
    return channels


def channel_for_direction(
    channels: dict[tuple[int, int], list[Channel]],
    src: int,
    dst: int,
) -> tuple[int | None, int | None, int | None, int | None]:
    rows = channels.get(tuple(sorted((src, dst))), [])
    if len(rows) != 1:
        return None, None, None, None
    ch = rows[0]
    if ch.node1 == src:
        return ch.port1, ch.tpn1, ch.port2, ch.tpn2
    return ch.port2, ch.tpn2, ch.port1, ch.tpn1


def load_tasks(case_dir: Path, priority: int, group_size: int) -> list[Task]:
    channels = load_channels(case_dir / "transport_channel.csv", priority)
    tasks: list[Task] = []
    with (case_dir / "output" / "task_statistics.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if int(row["priority"]) != priority:
                continue
            src = int(row["sourceNodeId"])
            dst = int(row["destNodeId"])
            src_port, src_tpn, dst_port, dst_tpn = channel_for_direction(channels, src, dst)
            tasks.append(
                Task(
                    task_id=int(row["taskId"]),
                    src=src,
                    dst=dst,
                    size=int(row["dataSize(Byte)"]),
                    phase=int(row["phaseId"]),
                    start_us=float(row["taskStartTime(us)"]),
                    end_us=float(row["taskCompletesTime(us)"]),
                    throughput_gbps=float(row["taskThroughput(Gbps)"]),
                    src_port=src_port,
                    src_tpn=src_tpn,
                    dst_port=dst_port,
                    dst_tpn=dst_tpn,
                    is_intra=(src // group_size) == (dst // group_size),
                )
            )
    return tasks


def lane_key(task: Task) -> tuple[int, int | str]:
    return task.src, task.src_port if task.src_port is not None else "multi"


def pack_sublanes(tasks: list[Task]) -> list[tuple[Task, int]]:
    sublane_ends: list[float] = []
    packed: list[tuple[Task, int]] = []
    for task in sorted(tasks, key=lambda t: (t.start_us, t.end_us, t.task_id)):
        assigned = None
        for idx, end_us in enumerate(sublane_ends):
            if task.start_us >= end_us:
                assigned = idx
                break
        if assigned is None:
            assigned = len(sublane_ends)
            sublane_ends.append(task.end_us)
        else:
            sublane_ends[assigned] = task.end_us
        packed.append((task, assigned))
    return packed


def render_html(case_dir: Path, tasks: list[Task], title: str) -> str:
    max_end = max((task.end_us for task in tasks), default=1.0)
    total_bytes = sum(task.size for task in tasks)
    rank_count = len({task.src for task in tasks} | {task.dst for task in tasks})
    direct_per_rank = total_bytes / rank_count if rank_count else 0
    direct_gbs = direct_per_rank / max_end / 1e3 if max_end else 0

    groups: dict[tuple[int, int | str], list[Task]] = defaultdict(list)
    for task in tasks:
        groups[lane_key(task)].append(task)

    left = 190
    right = 32
    top = 86
    row_gap = 8
    sublane_h = 12
    min_row_h = 20
    width = 1320
    plot_w = width - left - right
    x_scale = plot_w / max_end if max_end else 1

    row_entries = []
    y = top
    for key in sorted(groups, key=lambda k: (k[0], 999 if k[1] == "multi" else int(k[1]))):
        packed = pack_sublanes(groups[key])
        sublane_count = max((sub for _, sub in packed), default=0) + 1
        row_h = max(min_row_h, sublane_count * sublane_h + 8)
        row_entries.append((key, packed, y, row_h, sublane_count))
        y += row_h + row_gap
    height = y + 52

    tick_count = 8
    tick_parts = []
    for idx in range(tick_count + 1):
        t = max_end * idx / tick_count
        x = left + t * x_scale
        tick_parts.append(
            f'<line class="grid" x1="{x:.2f}" y1="{top - 28}" x2="{x:.2f}" y2="{height - 46}" />'
            f'<text class="tick" x="{x:.2f}" y="{top - 36}" text-anchor="middle">{t:.1f}us</text>'
        )

    row_parts = []
    for key, packed, row_y, row_h, sublane_count in row_entries:
        src, port = key
        label = f"rank {src} / port {port}"
        row_tasks = groups[key]
        row_bytes = sum(task.size for task in row_tasks)
        row_max = max(task.end_us for task in row_tasks)
        row_gbs = row_bytes / row_max / 1e3 if row_max else 0
        row_parts.append(
            f'<rect class="row-bg" x="0" y="{row_y - 4:.2f}" width="{width}" height="{row_h:.2f}" />'
            f'<text class="row-label" x="16" y="{row_y + 10:.2f}">{html.escape(label)}</text>'
            f'<text class="row-meta" x="122" y="{row_y + 10:.2f}">{len(row_tasks)} flows, {row_gbs:.1f} GB/s</text>'
        )
        for task, sublane in packed:
            x = left + task.start_us * x_scale
            bar_w = max(1.0, (task.end_us - task.start_us) * x_scale)
            bar_y = row_y + 2 + sublane * sublane_h
            cls = "bar intra" if task.is_intra else "bar inter"
            if task.src_port is None:
                cls += " ambiguous"
            tooltip = (
                f"task {task.task_id}: {task.src}->{task.dst}\\n"
                f"{task.size} bytes, phase {task.phase}\\n"
                f"{task.start_us:.3f}us - {task.end_us:.3f}us\\n"
                f"{task.throughput_gbps / 8:.2f} GB/s\\n"
                f"src port/tpn: {task.src_port}/{task.src_tpn}\\n"
                f"dst port/tpn: {task.dst_port}/{task.dst_tpn}"
            )
            row_parts.append(
                f'<rect class="{cls}" x="{x:.2f}" y="{bar_y:.2f}" width="{bar_w:.2f}" height="9" rx="2">'
                f'<title>{html.escape(tooltip)}</title></rect>'
            )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2937; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1380px, calc(100% - 32px)); margin: 24px auto 44px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    .muted {{ color: #667085; margin: 0 0 16px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }}
    .metric {{ background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 12px; }}
    .metric .k {{ color: #667085; font-size: 12px; }}
    .metric .v {{ font-size: 22px; font-weight: 700; }}
    .panel {{ overflow-x: auto; background: #fff; border: 1px solid #d8dee8; border-radius: 8px; }}
    svg {{ display: block; min-width: {width}px; }}
    .grid {{ stroke: #e3e8f0; stroke-width: 1; }}
    .tick, .row-meta {{ fill: #667085; font-size: 11px; }}
    .row-bg {{ fill: #fbfcfe; stroke: #edf1f6; }}
    .row-label {{ fill: #111827; font-size: 12px; font-weight: 650; }}
    .bar {{ stroke: rgba(17,24,39,.28); stroke-width: .5; opacity: .84; }}
    .bar.intra {{ fill: #0f766e; }}
    .bar.inter {{ fill: #2563eb; }}
    .bar.ambiguous {{ fill: #b45309; }}
    .legend {{ display: flex; gap: 16px; align-items: center; color: #475467; font-size: 12px; margin: 10px 0 0; }}
    .swatch {{ display: inline-block; width: 12px; height: 8px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }}
    @media (max-width: 760px) {{ .metrics {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>{html.escape(title)}</h1>
  <p class="muted">行按源 rank + 源端口聚合，条形为 ns-3 task 持续时间；同一行内重叠的流会在子 lane 中展开。悬停条形可看 task、rank pair、端口和吞吐。</p>
  <div class="metrics">
    <div class="metric"><div class="k">Case</div><div class="v">{html.escape(case_dir.name)}</div></div>
    <div class="metric"><div class="k">Tasks</div><div class="v">{len(tasks)}</div></div>
    <div class="metric"><div class="k">Makespan</div><div class="v">{max_end:.3f} us</div></div>
    <div class="metric"><div class="k">Direct/rank</div><div class="v">{direct_gbs:.2f} GB/s</div></div>
  </div>
  <div class="panel">
    <svg width="{width}" height="{height}" role="img" aria-label="{html.escape(title)}">
      {''.join(tick_parts)}
      <text class="tick" x="{left}" y="{height - 20}" text-anchor="start">time (us)</text>
      {''.join(row_parts)}
    </svg>
  </div>
  <div class="legend">
    <span><span class="swatch" style="background:#0f766e"></span>组内流</span>
    <span><span class="swatch" style="background:#2563eb"></span>跨组流</span>
    <span><span class="swatch" style="background:#b45309"></span>无法唯一映射到单 TP</span>
  </div>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--title", default="ns-3-ub Task Timeline")
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    tasks = load_tasks(case_dir, args.priority, args.group_size)
    if not tasks:
        raise ValueError(f"no tasks found in {case_dir}")

    html_text = render_html(case_dir, tasks, args.title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
