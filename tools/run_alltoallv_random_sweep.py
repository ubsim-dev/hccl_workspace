#!/usr/bin/env python3
"""Run multi-seed AllToAllV mild-random experiments and summarize imbalance."""

from __future__ import annotations

import argparse
import csv
import math
import random
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import run_alltoallv_scenarios as base


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT / "experiments/ubx16/alltoallv/mild-random-sweep/reports"
DEFAULT_SUMMARY = DEFAULT_REPORT_DIR / "ns3ub-ubx16-alltoallv-mild-random-sweep.csv"
DEFAULT_REPORT = DEFAULT_REPORT_DIR / "ns3ub-ubx16-alltoallv-mild-random-sweep.html"


def mild_random_sizes(per_rank_bytes: int, seed: int, sigma: float) -> dict[tuple[int, int], int]:
    rng = random.Random(seed)
    weights_by_src: dict[int, dict[int, float]] = {}
    for src in range(base.RANK_COUNT):
        weights_by_src[src] = {dst: math.exp(rng.gauss(0.0, sigma)) for dst in base.peers(src)}
    return base.fill_by_source_weights(per_rank_bytes, lambda src, dst: weights_by_src[src][dst])


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def generate_algorithm_case(algorithm: str, output_case: Path, per_rank_bytes: int) -> None:
    if output_case.exists():
        shutil.rmtree(output_case)
    base.generate_algorithm_case(algorithm, output_case, per_rank_bytes)


def run_sim(case_dir: Path, docker_container: str | None) -> None:
    base.run_sim(case_dir, docker_container)


def cv(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    return math.sqrt(sum((x - mean) ** 2 for x in values) / len(values)) / mean


def task_rows(case_dir: Path) -> list[dict[str, str]]:
    with (case_dir / "traffic.csv").open(newline="") as f:
        traffic = list(csv.DictReader(f))
    with (case_dir / "output" / "task_statistics.csv").open(newline="") as f:
        stats_by_task = {int(row["taskId"]): row for row in csv.DictReader(f)}
    rows = []
    for row in traffic:
        merged = dict(row)
        merged.update(stats_by_task[int(row["taskId"])])
        rows.append(merged)
    return rows


def chain_metrics(case_dir: Path) -> dict[str, object]:
    rows = task_rows(case_dir)
    by_task = {int(row["taskId"]): row for row in rows}
    next_by_task: dict[int, int] = {}
    depended = set()
    for row in rows:
        dep = row.get("dependOnPhases", "").strip()
        if dep:
            prev = int(float(dep.split()[0]))
            cur = int(row["taskId"])
            next_by_task[prev] = cur
            depended.add(cur)

    chains: list[list[dict[str, str]]] = []
    for row in rows:
        task_id = int(row["taskId"])
        if task_id in depended:
            continue
        chain = [row]
        cur = task_id
        while cur in next_by_task:
            cur = next_by_task[cur]
            chain.append(by_task[cur])
        chains.append(chain)

    chain_bytes = [sum(int(row["dataSize(Byte)"]) for row in chain) for chain in chains]
    chain_end = [max(float(row["taskCompletesTime(us)"]) for row in chain) for chain in chains]
    chain_duration = [
        max(float(row["taskCompletesTime(us)"]) for row in chain)
        - min(float(row["taskStartTime(us)"]) for row in chain)
        for chain in chains
    ]
    slow_idx = max(range(len(chains)), key=lambda idx: chain_end[idx])
    slow_chain = chains[slow_idx]
    return {
        "chain_count": len(chains),
        "chain_bytes_cv": cv([float(x) for x in chain_bytes]),
        "chain_bytes_max_mib": max(chain_bytes) / 1024 / 1024,
        "chain_bytes_mean_mib": (sum(chain_bytes) / len(chain_bytes)) / 1024 / 1024,
        "chain_bytes_max_over_mean": max(chain_bytes) / (sum(chain_bytes) / len(chain_bytes)),
        "chain_duration_max_us": max(chain_duration),
        "slow_chain_src": slow_chain[0]["sourceNodeId"],
        "slow_chain_tasks": " ".join(
            f"{row['sourceNodeId']}->{row['destNodeId']}:{int(row['dataSize(Byte)']) / 1024 / 1024:.2f}MiB"
            for row in slow_chain
        ),
    }


def summarize_one(
    seed: int,
    sigma: float,
    algorithm: str,
    case_dir: Path,
    sizes: dict[tuple[int, int], int],
) -> dict[str, object]:
    summary = base.summarize_case(case_dir, sizes)
    chains = chain_metrics(case_dir)
    return {
        "seed": seed,
        "sigma": sigma,
        "algorithm": algorithm,
        "case": str(case_dir.relative_to(REPO_ROOT)),
        **summary,
        **chains,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "seed",
        "sigma",
        "algorithm",
        "case",
        "tasks",
        "total_bytes",
        "src_cv",
        "dst_cv",
        "cross_fraction",
        "max_pair_bytes",
        "max_src_bytes",
        "max_dst_bytes",
        "makespan_us",
        "global_GBps",
        "rank0_makespan_us",
        "rank0_GBps",
        "chain_count",
        "chain_bytes_cv",
        "chain_bytes_max_mib",
        "chain_bytes_mean_mib",
        "chain_bytes_max_over_mean",
        "chain_duration_max_us",
        "slow_chain_src",
        "slow_chain_tasks",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def percentiles(values: list[float]) -> tuple[float, float, float, float, float]:
    values = sorted(values)
    if not values:
        return 0, 0, 0, 0, 0

    def pick(q: float) -> float:
        idx = round((len(values) - 1) * q)
        return values[idx]

    return values[0], pick(0.25), pick(0.5), pick(0.75), values[-1]


def render_html(rows: list[dict[str, object]], output: Path) -> None:
    by_alg: dict[str, list[dict[str, object]]] = defaultdict(list)
    by_seed: dict[int, dict[str, dict[str, object]]] = defaultdict(dict)
    for row in rows:
        by_alg[str(row["algorithm"])].append(row)
        by_seed[int(row["seed"])][str(row["algorithm"])] = row

    alg_cards = []
    for alg in ["baseline", "matrix", "closv3"]:
        alg_rows = by_alg[alg]
        g = [float(r["global_GBps"]) for r in alg_rows]
        mn, p25, med, p75, mx = percentiles(g)
        alg_cards.append(
            f"""
      <section class="card">
        <h3>{alg}</h3>
        <table>
          <tr><th>min</th><th>p25</th><th>median</th><th>p75</th><th>max</th></tr>
          <tr><td>{mn:.2f}</td><td>{p25:.2f}</td><td>{med:.2f}</td><td>{p75:.2f}</td><td>{mx:.2f}</td></tr>
        </table>
      </section>"""
        )

    seed_rows = []
    for seed in sorted(by_seed):
        algs = by_seed[seed]
        base_g = float(algs["baseline"]["global_GBps"])
        matrix_g = float(algs["matrix"]["global_GBps"])
        clos_g = float(algs["closv3"]["global_GBps"])
        seed_rows.append(
            f"""
        <tr>
          <td>{seed}</td>
          <td>{float(algs['baseline']['dst_cv']):.3f}</td>
          <td>{base_g:.2f}</td>
          <td>{matrix_g:.2f}</td>
          <td class="bad">{(matrix_g / base_g - 1) * 100:.1f}%</td>
          <td>{clos_g:.2f}</td>
          <td class="bad">{(clos_g / base_g - 1) * 100:.1f}%</td>
          <td>{float(algs['matrix']['chain_bytes_max_over_mean']):.2f}</td>
          <td>{float(algs['closv3']['chain_bytes_max_over_mean']):.2f}</td>
        </tr>"""
        )

    worst_matrix = min(
        (r for r in rows if r["algorithm"] == "matrix"),
        key=lambda r: float(r["global_GBps"]),
    )
    worst_clos = min(
        (r for r in rows if r["algorithm"] == "closv3"),
        key=lambda r: float(r["global_GBps"]),
    )

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AllToAllV mild-random 多 seed 扫描</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2937; font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1180px, calc(100% - 36px)); margin: 26px auto 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 27px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 19px; }}
    h3 {{ margin: 0 0 10px; font-size: 16px; }}
    .muted {{ color: #667085; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }}
    section, .card {{ background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-variant-numeric: tabular-nums; }}
    th, td {{ border-bottom: 1px solid #d8dee8; padding: 8px 9px; text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ color: #667085; background: #fbfcfe; font-size: 12px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .bad {{ color: #b42318; }}
    .good {{ color: #087443; }}
    code {{ background: #eef2f7; border-radius: 5px; padding: 1px 5px; }}
    ul {{ margin: 8px 0 0 18px; padding: 0; }}
    li {{ margin: 5px 0; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} table {{ font-size: 12px; }} }}
  </style>
</head>
<body>
<main>
  <h1>AllToAllV mild-random 多 seed 扫描</h1>
  <p class="muted">每个 src 总通信量固定为 16MiB，peer 权重服从 lognormal，扫描不同随机 seed。吞吐单位为 GB/s。</p>
  <div class="grid">
    {''.join(alg_cards)}
  </div>
  <section>
    <h2>指标说明</h2>
    <ul>
      <li><code>Seed</code>：随机数种子。每个 seed 会生成一张不同的 AllToAllV sendCounts 矩阵，但每个源 rank 的总发送量都固定为 16MiB。</li>
      <li><code>Dst CV</code>：目的 rank 收到的数据总量的不均衡系数，计算方式是 <code>std(dst_total_bytes) / mean(dst_total_bytes)</code>。值越大，说明流量越集中到少数目的 rank；0 表示每个目的 rank 收到的数据完全一样。</li>
      <li><code>Baseline / Matrix / ClosV3</code>：对应算法的全局吞吐，单位 GB/s，按全部 task 数据量除以全局 makespan 计算。</li>
      <li><code>Matrix vs base</code> 和 <code>ClosV3 vs base</code>：相对同一个 seed 下 baseline 的全局吞吐变化。负数表示比 baseline 慢。</li>
      <li><code>链不均衡</code>：最重依赖链的数据量 / 平均依赖链的数据量。这里的“链”就是同一个逻辑并行单元上串行执行的一组 task。值越大，说明某个 slot/plane 被分到更多数据，更容易形成拖尾。</li>
    </ul>
  </section>
  <section style="margin-top:14px">
    <h2>结论</h2>
    <ul>
      <li>随机性确实存在，但不是偶发现象：matrix/closv3 对 seed 很敏感，核心相关指标是 <code>chain_bytes_max_over_mean</code>，也就是最重 slot 链相对平均链的倍数。</li>
      <li>最差 matrix seed={worst_matrix['seed']}，global={float(worst_matrix['global_GBps']):.2f} GB/s，最慢链：{str(worst_matrix['slow_chain_tasks'])}。</li>
      <li>最差 closv3 seed={worst_clos['seed']}，global={float(worst_clos['global_GBps']):.2f} GB/s，最慢链：{str(worst_clos['slow_chain_tasks'])}。</li>
      <li>baseline 的多 TP/full 模型对随机 V 更鲁棒；matrix/closv3 如果固定 peer->slot/plane，需要按 size 做负载均衡或拆大流。</li>
    </ul>
  </section>
  <section style="margin-top:14px">
    <h2>逐 seed 结果</h2>
    <table>
      <thead>
        <tr>
          <th>Seed</th><th>Dst CV</th><th>Baseline</th><th>Matrix</th><th>Matrix vs base</th>
          <th>ClosV3</th><th>ClosV3 vs base</th><th>Matrix 链不均衡</th><th>ClosV3 链不均衡</th>
        </tr>
      </thead>
      <tbody>{''.join(seed_rows)}</tbody>
    </table>
  </section>
</main>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", nargs="+", type=int, default=list(range(20260615, 20260627)))
    parser.add_argument("--sigma", type=float, default=0.7)
    parser.add_argument("--algorithms", nargs="+", default=["baseline", "matrix", "closv3"])
    parser.add_argument("--per-rank-bytes", default="16MB")
    parser.add_argument(
        "--case-prefix",
        default="experiments/ubx16/alltoallv/mild-random-sweep/cases/generated_topology_ubx16_a2av_mild_sweep",
    )
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--docker-container", default="hcomm-dev")
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    per_rank_bytes = base.parse_size(args.per_rank_bytes)
    rows: list[dict[str, object]] = []
    for seed in args.seeds:
        sizes = mild_random_sizes(per_rank_bytes, seed, args.sigma)
        for algorithm in args.algorithms:
            case_dir = REPO_ROOT / f"{args.case_prefix}_seed{seed}_{algorithm}_16mb"
            generate_algorithm_case(algorithm, case_dir, per_rank_bytes)
            base.patch_traffic(case_dir, sizes)
            if not args.no_run:
                run_sim(case_dir, args.docker_container)
                row = summarize_one(seed, args.sigma, algorithm, case_dir, sizes)
                rows.append(row)
                print(
                    f"seed={seed} {algorithm}: global={float(row['global_GBps']):.2f}GB/s "
                    f"chain_max/mean={float(row['chain_bytes_max_over_mean']):.2f}"
                )

    if rows:
        write_csv(args.summary, rows)
        render_html(rows, args.report)
        print(f"summary={args.summary}")
        print(f"report={args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
