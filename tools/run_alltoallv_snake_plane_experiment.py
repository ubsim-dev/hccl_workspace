#!/usr/bin/env python3
"""Run UBX16 AllToAllV snake plane scheduling experiment."""

from __future__ import annotations

import argparse
import csv
import html
import math
import random
import shutil
import statistics
import subprocess
from collections import OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"
BASE = REPO_ROOT / "experiments/ubx16/alltoallv/snake"
CASES = BASE / "cases"
REPORTS = BASE / "reports"
RANK_COUNT = 16
GROUP_SIZE = 4
PLANE_COUNT = 4
PRIORITY = 7
CROSS_PRIORITY_BASE = 3
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
    units = {"B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2, "G": 1024**3, "GB": 1024**3}
    upper = text.upper()
    for unit in sorted(units, key=len, reverse=True):
        if upper.endswith(unit):
            return int(float(upper[: -len(unit)].strip()) * units[unit])
    return int(text)


def size_name(size: int) -> str:
    for suffix, scale in (("gb", 1024**3), ("mb", 1024**2), ("kb", 1024)):
        if size % scale == 0:
            return f"{size // scale}{suffix}"
    return f"{size}b"


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header")
        return reader.fieldnames, list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def group(rank: int) -> int:
    return rank // GROUP_SIZE


def same_group(a: int, b: int) -> bool:
    return group(a) == group(b)


def make_random_matrix(per_rank_bytes: int, seed: int, load_skew: float, sigma: float) -> dict[tuple[int, int], int]:
    rng = random.Random(seed)
    factors = [1.0 + (load_skew - 1.0) * rng.random() for _ in range(RANK_COUNT)]
    scale = per_rank_bytes / statistics.mean(factors)
    matrix: dict[tuple[int, int], int] = {}
    for src, factor in enumerate(factors):
        weights = [0.0 if dst == src else rng.lognormvariate(0.0, sigma) for dst in range(RANK_COUNT)]
        total_weight = sum(weights)
        target = int(scale * factor)
        raw = [target * w / total_weight for w in weights]
        ints = [int(x) for x in raw]
        remain = target - sum(ints)
        order = sorted(range(RANK_COUNT), key=lambda i: raw[i] - ints[i], reverse=True)
        for idx in order[:remain]:
            ints[idx] += 1
        for dst, size in enumerate(ints):
            if dst != src:
                matrix[(src, dst)] = size
    return matrix


def matrix_stats(sizes: dict[tuple[int, int], int]) -> dict[str, float]:
    pair_values = list(sizes.values())
    src_totals = [sum(size for (src, _), size in sizes.items() if src == rank) for rank in range(RANK_COUNT)]
    dst_totals = [sum(size for (_, dst), size in sizes.items() if dst == rank) for rank in range(RANK_COUNT)]
    cross_total = sum(size for (src, dst), size in sizes.items() if not same_group(src, dst))
    total = sum(pair_values)
    return {
        "total_bytes": float(total),
        "pair_avg_bytes": statistics.mean(pair_values),
        "pair_min_bytes": float(min(pair_values)),
        "pair_max_bytes": float(max(pair_values)),
        "pair_cv": statistics.pstdev(pair_values) / statistics.mean(pair_values),
        "src_min_bytes": float(min(src_totals)),
        "src_max_bytes": float(max(src_totals)),
        "src_max_min_ratio": float(max(src_totals) / min(src_totals)),
        "src_cv": statistics.pstdev(src_totals) / statistics.mean(src_totals),
        "dst_cv": statistics.pstdev(dst_totals) / statistics.mean(dst_totals),
        "cross_fraction": cross_total / total,
    }


def copy_base(output_case: Path) -> None:
    if output_case.exists():
        shutil.rmtree(output_case)
    output_case.mkdir(parents=True, exist_ok=True)
    for name in ("node.csv", "topology.csv", "routing_table.csv", "network_attribute.txt"):
        shutil.copy2(SOURCE_CASE / name, output_case / name)
    patch_network(output_case / "network_attribute.txt")


def patch_network(path: Path) -> None:
    out: list[str] = []
    for line in path.read_text().splitlines():
        if line.startswith('global UB_PORT_TRACE_ENABLE '):
            out.append('global UB_PORT_TRACE_ENABLE "false"')
        elif line.startswith('global UB_PACKET_TRACE_ENABLE '):
            out.append('global UB_PACKET_TRACE_ENABLE "false"')
        elif line.startswith('global UB_RECORD_PKT_TRACE '):
            out.append('global UB_RECORD_PKT_TRACE "false"')
        else:
            out.append(line)
    path.write_text("\n".join(out) + "\n")


def load_tp_rows() -> tuple[list[str], dict[tuple[int, int], list[dict[str, str]]]]:
    fields, rows = read_csv(SOURCE_CASE / "transport_channel.csv")
    by_pair: dict[tuple[int, int], list[dict[str, str]]] = OrderedDict()
    for row in rows:
        if int(row["priority"]) != PRIORITY:
            continue
        a = int(row["nodeId1"])
        b = int(row["nodeId2"])
        if a < RANK_COUNT and b < RANK_COUNT:
            by_pair.setdefault((min(a, b), max(a, b)), []).append(row)
    return fields, by_pair


def load_direct_ports() -> dict[tuple[int, int], tuple[int, int]]:
    direct: dict[tuple[int, int], tuple[int, int]] = {}
    _, rows = read_csv(SOURCE_CASE / "topology.csv")
    for row in rows:
        a = int(row["nodeId1"])
        b = int(row["nodeId2"])
        if a >= RANK_COUNT or b >= RANK_COUNT:
            continue
        pa = int(row["portId1"])
        pb = int(row["portId2"])
        direct[(min(a, b), max(a, b))] = (pa, pb) if a < b else (pb, pa)
    return direct


def select_direct_row(rows: list[dict[str, str]], ports: tuple[int, int]) -> dict[str, str]:
    for row in rows:
        if int(row["portId1"]) == ports[0] and int(row["portId2"]) == ports[1]:
            return row
    return rows[0]


def select_plane_row(rows: list[dict[str, str]], plane: int) -> dict[str, str]:
    for row in rows:
        if int(row["portId1"]) == 3 + plane or int(row["portId2"]) == 3 + plane:
            return row
    return rows[plane % len(rows)]


def plane_priority(plane: int) -> int:
    return CROSS_PRIORITY_BASE + plane


def mesh1d_phase_peers(rank: int, phase: int, concurrent: int) -> list[int]:
    pair_num_per_round = (concurrent + 1) // 2
    total_prev = 0
    for prev_phase in range(phase):
        remain = RANK_COUNT - 1 - total_prev
        pair_size = (remain + 1) // 2 if remain < concurrent else pair_num_per_round
        count = 0
        for distance in range(prev_phase * pair_num_per_round + 1, prev_phase * pair_num_per_round + pair_size + 1):
            left = (rank + RANK_COUNT - distance) % RANK_COUNT
            right = (rank + distance) % RANK_COUNT
            count += 1 if left == right else 2
        total_prev += count

    remain = RANK_COUNT - 1 - total_prev
    pair_size = (remain + 1) // 2 if remain < concurrent else pair_num_per_round
    peers: list[int] = []
    for distance in range(phase * pair_num_per_round + 1, phase * pair_num_per_round + pair_size + 1):
        left = (rank + RANK_COUNT - distance) % RANK_COUNT
        right = (rank + distance) % RANK_COUNT
        if left == right:
            peers.append(left)
            break
        peers.extend((left, right))
    return peers


def write_baseline_case(output_case: Path, sizes: dict[tuple[int, int], int]) -> None:
    copy_base(output_case)
    fields, rows = read_csv(SOURCE_CASE / "transport_channel.csv")
    write_csv(
        output_case / "transport_channel.csv",
        fields,
        [row for row in rows if int(row["priority"]) == PRIORITY and int(row["nodeId1"]) < RANK_COUNT and int(row["nodeId2"]) < RANK_COUNT],
    )
    traffic: list[dict[str, object]] = []
    last_by_unit: dict[tuple[int, int], int] = {}
    task_id = 0
    concurrent = RANK_COUNT
    phase_count = math.ceil((RANK_COUNT - 1) / concurrent)
    for phase in range(phase_count):
        for src in range(RANK_COUNT):
            for peer_idx, dst in enumerate(mesh1d_phase_peers(src, phase, concurrent)):
                prev = last_by_unit.get((src, peer_idx))
                traffic.append(
                    {
                        "taskId": task_id,
                        "sourceNodeId": src,
                        "destNodeId": dst,
                        "dataSize(Byte)": sizes[(src, dst)],
                        "opType": "URMA_WRITE",
                        "priority": PRIORITY,
                        "delay": "0ns",
                        "phaseId": task_id,
                        "dependOnPhases": "" if prev is None else str(prev),
                    }
                )
                last_by_unit[(src, peer_idx)] = task_id
                task_id += 1
    write_csv(output_case / "traffic.csv", TRAFFIC_HEADER, traffic)


SNAKE_PATTERN = [0, 1, 2, 3, 3, 2, 1, 0, 0, 1, 2, 3]


def snake_assignments(sizes: dict[tuple[int, int], int], offset: bool) -> dict[tuple[int, int], int]:
    assignment: dict[tuple[int, int], int] = {}
    for src in range(RANK_COUNT):
        cross = [(dst, sizes[(src, dst)]) for dst in range(RANK_COUNT) if dst != src and not same_group(src, dst)]
        for idx, (dst, _) in enumerate(sorted(cross, key=lambda x: x[1], reverse=True)):
            plane = SNAKE_PATTERN[idx]
            if offset:
                plane = (plane + src % PLANE_COUNT) % PLANE_COUNT
            assignment[(src, dst)] = plane
    return assignment


def write_snake_case(output_case: Path, sizes: dict[tuple[int, int], int], offset: bool) -> dict[str, float]:
    copy_base(output_case)
    fields, by_pair = load_tp_rows()
    direct_ports = load_direct_ports()
    assignment = snake_assignments(sizes, offset)

    selected_rows: list[dict[str, str]] = []
    for src in range(RANK_COUNT):
        for dst in range(src + 1, RANK_COUNT):
            rows = by_pair[(src, dst)]
            if same_group(src, dst):
                row = dict(select_direct_row(rows, direct_ports[(src, dst)]))
                row["priority"] = str(PRIORITY)
                selected_rows.append(row)
            else:
                for plane in range(PLANE_COUNT):
                    row = dict(select_plane_row(rows, plane))
                    row["priority"] = str(plane_priority(plane))
                    selected_rows.append(row)
    write_csv(output_case / "transport_channel.csv", fields, selected_rows)

    traffic: list[dict[str, object]] = []
    plane_bytes = [0] * PLANE_COUNT
    dst_plane_counts: dict[tuple[int, int], int] = {}

    # Build source-plane queues first, then perform deterministic list scheduling.
    # This matches the design doc's start=max(src_plane_available, dst_plane_available)
    # model and avoids imposing a source-rank-order receiver queue.
    source_queues: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for src in range(RANK_COUNT):
        queues: list[list[tuple[int, int]]] = [[] for _ in range(PLANE_COUNT)]
        for dst in range(RANK_COUNT):
            if src == dst:
                continue
            if same_group(src, dst):
                continue
            plane = assignment[(src, dst)]
            queues[plane].append((dst, sizes[(src, dst)]))
        for plane in range(PLANE_COUNT):
            source_queues[(src, plane)] = queues[plane]

    src_avail = {(src, plane): 0.0 for src in range(RANK_COUNT) for plane in range(PLANE_COUNT)}
    dst_avail = {(dst, plane): 0.0 for dst in range(RANK_COUNT) for plane in range(PLANE_COUNT)}
    src_prev: dict[tuple[int, int], int] = {}
    dst_prev: dict[tuple[int, int], int] = {}
    queue_index = {key: 0 for key in source_queues}
    task_id = 0
    remaining = sum(len(queue) for queue in source_queues.values())
    while remaining:
        best: tuple[float, float, int, int, int, int, int] | None = None
        for (src, plane), queue in source_queues.items():
            idx = queue_index[(src, plane)]
            if idx >= len(queue):
                continue
            dst, size = queue[idx]
            start = max(src_avail[(src, plane)], dst_avail[(dst, plane)])
            finish = start + size
            candidate = (start, finish, src, plane, dst, size, idx)
            if best is None or candidate < best:
                best = candidate
        if best is None:
            raise RuntimeError("scheduler has remaining work but no candidate")
        _, finish, src, plane, dst, size, _ = best
        deps = []
        if (src, plane) in src_prev:
            deps.append(str(src_prev[(src, plane)]))
        if (dst, plane) in dst_prev:
            deps.append(str(dst_prev[(dst, plane)]))
        traffic.append(
            {
                "taskId": task_id,
                "sourceNodeId": src,
                "destNodeId": dst,
                "dataSize(Byte)": size,
                "opType": "URMA_WRITE",
                "priority": plane_priority(plane),
                "delay": "0ns",
                "phaseId": task_id,
                "dependOnPhases": " ".join(deps),
            }
        )
        src_prev[(src, plane)] = task_id
        dst_prev[(dst, plane)] = task_id
        src_avail[(src, plane)] = finish
        dst_avail[(dst, plane)] = finish
        queue_index[(src, plane)] += 1
        dst_plane_counts[(dst, plane)] = dst_plane_counts.get((dst, plane), 0) + 1
        plane_bytes[plane] += size
        task_id += 1
        remaining -= 1

    # Pod-local mesh traffic is outside the snake scheduler. Keep it independent;
    # direct host-host contention is still modeled by ns-3 links.
    for src in range(RANK_COUNT):
        for dst in range(RANK_COUNT):
            if src == dst or not same_group(src, dst):
                continue
            traffic.append(
                {
                    "taskId": task_id,
                    "sourceNodeId": src,
                    "destNodeId": dst,
                    "dataSize(Byte)": sizes[(src, dst)],
                    "opType": "URMA_WRITE",
                    "priority": PRIORITY,
                    "delay": "0ns",
                    "phaseId": task_id,
                    "dependOnPhases": "",
                }
            )
            task_id += 1

    write_csv(output_case / "traffic.csv", TRAFFIC_HEADER, traffic)
    max_dst_queue = max(dst_plane_counts.values()) if dst_plane_counts else 0
    return {
        "plane0_bytes": float(plane_bytes[0]),
        "plane1_bytes": float(plane_bytes[1]),
        "plane2_bytes": float(plane_bytes[2]),
        "plane3_bytes": float(plane_bytes[3]),
        "plane_cv": statistics.pstdev(plane_bytes) / statistics.mean(plane_bytes),
        "max_dst_plane_queue": float(max_dst_queue),
    }


def run_sim(case_dir: Path, container: str, mtp_threads: int) -> None:
    rel = case_dir.relative_to(REPO_ROOT)
    arg = f"--case-path=/workspace/hccl_workspace/{rel}"
    if mtp_threads > 0:
        arg += f" --mtp-threads={mtp_threads}"
    run_cmd(["docker", "exec", container, "bash", "-lc", f'cd /workspace/hccl_workspace && ./ns-3-ub/ns3 run "scratch/ub-quick-example {arg}"'])


def summarize(case_dir: Path, sizes: dict[tuple[int, int], int], extra: dict[str, float]) -> dict[str, float]:
    _, rows = read_csv(case_dir / "output" / "task_statistics.csv")
    makespan = max(float(row["taskCompletesTime(us)"]) for row in rows)
    total = sum(sizes.values())
    rank0_rows = [row for row in rows if int(row["sourceNodeId"]) == 0]
    rank0_makespan = max(float(row["taskCompletesTime(us)"]) for row in rank0_rows)
    rank0_bytes = sum(sizes[(0, dst)] for dst in range(RANK_COUNT) if dst != 0)
    return {
        **matrix_stats(sizes),
        **extra,
        "tasks": float(len(rows)),
        "makespan_us": makespan,
        "global_GBps": total / makespan / 1e3,
        "single_rank_GBps": total / makespan / 1e3 / RANK_COUNT,
        "rank0_makespan_us": rank0_makespan,
        "rank0_GBps": rank0_bytes / rank0_makespan / 1e3,
    }


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "algorithm",
        "case",
        "tasks",
        "total_bytes",
        "pair_avg_bytes",
        "pair_min_bytes",
        "pair_max_bytes",
        "pair_cv",
        "src_min_bytes",
        "src_max_bytes",
        "src_max_min_ratio",
        "src_cv",
        "dst_cv",
        "cross_fraction",
        "plane0_bytes",
        "plane1_bytes",
        "plane2_bytes",
        "plane3_bytes",
        "plane_cv",
        "max_dst_plane_queue",
        "makespan_us",
        "global_GBps",
        "single_rank_GBps",
        "rank0_makespan_us",
        "rank0_GBps",
    ]
    write_csv(path, fields, rows)


def render_report(summary_rows: list[dict[str, object]], output: Path, seed: int, per_rank_bytes: int) -> None:
    baseline = next(row for row in summary_rows if row["algorithm"] == "baseline")
    base_gbps = float(baseline["single_rank_GBps"])
    trs = []
    for row in summary_rows:
        gbps = float(row["single_rank_GBps"])
        trs.append(
            f"""<tr>
  <td>{html.escape(str(row['algorithm']))}</td>
  <td>{float(row['makespan_us']):.3f}</td>
  <td>{gbps:.2f}</td>
  <td class="{'good' if gbps >= base_gbps else 'bad'}">{(gbps / base_gbps - 1) * 100:+.1f}%</td>
  <td>{float(row['plane_cv']):.3f}</td>
  <td>{float(row['max_dst_plane_queue']):.0f}</td>
  <td><code>{html.escape(Path(str(row['case'])).name)}</code></td>
</tr>"""
        )
    output.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV Snake Plane Scheduling</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#d8dee8; --good:#087443; --bad:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1100px, calc(100% - 36px)); margin:26px auto 48px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    section, .metric {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .muted {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:4px; font-size:22px; }}
    .layout {{ display:grid; gap:14px; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-variant-numeric:tabular-nums; }}
    th, td {{ border-bottom:1px solid var(--line); padding:8px 7px; text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    th:first-child, td:first-child, th:last-child, td:last-child {{ text-align:left; }}
    th {{ color:var(--muted); font-size:12px; background:#fbfcfe; }}
    code {{ background:#eef2f7; border-radius:5px; padding:1px 5px; }}
    .good {{ color:var(--good); }}
    .bad {{ color:var(--bad); }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:5px 0; }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>UBX16 AllToAllV Snake Plane Scheduling</h1>
    <p class="muted">单 seed 随机场景，rank 总发送量 max/min≈1.2，{per_rank_bytes / 1024 / 1024:.0f}MiB/rank。snake 对每个 src 的跨 Pod peer 按大小排序后蛇形分配到 4 个 plane；snake-offset 再叠加 src_rank % 4 plane offset。每个 src-plane 与 dst-plane 都显式串行建模。</p>
  </header>
  <div class="metrics">
    <div class="metric"><span>seed</span><strong>{seed}</strong></div>
    <div class="metric"><span>src max/min</span><strong>{float(baseline['src_max_min_ratio']):.3f}</strong></div>
    <div class="metric"><span>pair CV</span><strong>{float(baseline['pair_cv']):.3f}</strong></div>
    <div class="metric"><span>cross fraction</span><strong>{float(baseline['cross_fraction']) * 100:.1f}%</strong></div>
  </div>
  <div class="layout">
    <section>
      <h2>结果</h2>
      <table>
        <thead><tr><th>算法</th><th>makespan us</th><th>GB/s/rank</th><th>vs baseline</th><th>plane CV</th><th>max dst-plane queue</th><th>case</th></tr></thead>
        <tbody>
{''.join(trs)}
        </tbody>
      </table>
    </section>
    <section>
      <h2>说明</h2>
      <ul>
        <li>baseline 使用当前 Mesh1D full-TP 建模。</li>
        <li>snake/snake-offset 每个跨 Pod pair 只保留一个 plane TP，并通过 phase dependency 模拟 src-plane 和 dst-plane 独占队列。</li>
        <li>这版只验证单个随机场景是否有收益；后续需要扫随机种子、pair 分布 CV 和 src load skew。</li>
      </ul>
    </section>
  </div>
</main>
</body>
</html>
"""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-rank-bytes", default="128MB")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--load-skew", type=float, default=1.2)
    parser.add_argument("--sigma", type=float, default=0.35)
    parser.add_argument("--docker-container", default="hcomm-dev")
    parser.add_argument("--mtp-threads", type=int, default=8)
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    per_rank_bytes = parse_size(args.per_rank_bytes)
    suffix = size_name(per_rank_bytes)
    sizes = make_random_matrix(per_rank_bytes, args.seed, args.load_skew, args.sigma)
    scenarios = [
        ("baseline", CASES / f"generated_topology_ubx16_a2av_snake_baseline_seed{args.seed}_{suffix}", {}),
        ("snake", CASES / f"generated_topology_ubx16_a2av_snake_seed{args.seed}_{suffix}", {"offset": False}),
        ("snake-offset", CASES / f"generated_topology_ubx16_a2av_snake_offset_seed{args.seed}_{suffix}", {"offset": True}),
    ]
    summaries: list[dict[str, object]] = []
    for algorithm, case_dir, opts in scenarios:
        if algorithm == "baseline":
            write_baseline_case(case_dir, sizes)
            extra = {"plane0_bytes": 0.0, "plane1_bytes": 0.0, "plane2_bytes": 0.0, "plane3_bytes": 0.0, "plane_cv": 0.0, "max_dst_plane_queue": 0.0}
        else:
            extra = write_snake_case(case_dir, sizes, bool(opts["offset"]))
        if not args.no_run:
            run_sim(case_dir, args.docker_container, args.mtp_threads)
            summary = summarize(case_dir, sizes, extra)
            summaries.append({"algorithm": algorithm, "case": str(case_dir.relative_to(REPO_ROOT)), **summary})
            print(f"{algorithm}: makespan={summary['makespan_us']:.3f}us single_rank={summary['single_rank_GBps']:.2f}GB/s")

    if summaries:
        REPORTS.mkdir(parents=True, exist_ok=True)
        summary_path = REPORTS / f"ns3ub-ubx16-alltoallv-snake-seed{args.seed}-{suffix}-summary.csv"
        report_path = REPORTS / f"ns3ub-ubx16-alltoallv-snake-seed{args.seed}-{suffix}-report.html"
        write_summary(summary_path, summaries)
        render_report(summaries, report_path, args.seed, per_rank_bytes)
        print(f"summary={summary_path}")
        print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
