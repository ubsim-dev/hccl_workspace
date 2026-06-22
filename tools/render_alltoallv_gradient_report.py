#!/usr/bin/env python3
"""Render UBX16 deterministic AllToAllV gradient sweep report."""

from __future__ import annotations

import argparse
import csv
import html
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "experiments/ubx16/alltoallv/gradient"
REPORT_DIR = BASE / "reports"
PROFILE_DIR = BASE / "profiles"
SUMMARY = REPORT_DIR / "ns3ub-ubx16-alltoallv-gradient-summary.csv"
REPORT = REPORT_DIR / "ns3ub-ubx16-alltoallv-gradient-report.html"
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"

ALGORITHM_LABELS = {
    "baseline": "Baseline Mesh1D full-TP",
    "matrix": "Matrix strict",
    "closv3": "MeshClos V3 strict",
}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def pct(value: float, digits: int = 1) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.{digits}f}%"


def mib(value: float) -> str:
    return f"{value / 1024 / 1024:.2f}"


def single_rank_gbps(row: dict[str, str]) -> float:
    return float(row["global_GBps"]) / 16.0


def profile_name(row: dict[str, str]) -> str:
    return f"ns3ub-ubx16-a2av-gradient-{row['scenario']}-{row['algorithm']}-rank0-profile.html"


def profile_href(row: dict[str, str]) -> str:
    return f"../profiles/{profile_name(row)}"


def render_profile(row: dict[str, str], output: Path) -> None:
    case = row["case"]
    title = (
        f"UBX16 AllToAllV gradient {float(row['max_over_avg']):.1f}x | "
        f"{ALGORITHM_LABELS[row['algorithm']]} | rank0"
    )
    if row["algorithm"] == "baseline":
        cmd = [
            "python3",
            "tools/render_ns3ub_mesh1d_rank_profile.py",
            case,
            "-o",
            str(output),
            "--rank",
            "0",
            "--concurrent",
            "16",
            "--title",
            title,
        ]
    elif row["algorithm"] == "matrix":
        cmd = [
            "python3",
            "tools/render_ns3ub_matrix_rank_profile.py",
            case,
            "-o",
            str(output),
            "--rank",
            "0",
            "--rank-count",
            "16",
            "--title",
            title,
        ]
    else:
        cmd = [
            "python3",
            "tools/render_ns3ub_rank_profile.py",
            case,
            "-o",
            str(output),
            "--rank",
            "0",
            "--rank-count",
            "16",
            "--group-size",
            "4",
            "--source-case",
            str(UBX16_SOURCE_CASE),
            "--title",
            title,
        ]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def render_profiles(rows: list[dict[str, str]], profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        render_profile(row, profile_dir / profile_name(row))


def table_rows(rows: list[dict[str, str]]) -> str:
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["scenario"], {})[row["algorithm"]] = row
    chunks = []
    for scenario in sorted(grouped, key=lambda s: float(grouped[s]["baseline"]["max_over_avg"])):
        algs = grouped[scenario]
        baseline_g = single_rank_gbps(algs["baseline"])
        for algorithm in ("baseline", "matrix", "closv3"):
            row = algs[algorithm]
            gbps = single_rank_gbps(row)
            vs = 0.0 if algorithm == "baseline" else gbps / baseline_g - 1.0
            chunks.append(
                f"""          <tr>
            <td>{float(row['max_over_avg']):.1f}x</td>
            <td>{html.escape(ALGORITHM_LABELS[algorithm])}</td>
            <td>{mib(float(row['pair_min_bytes']))}</td>
            <td>{mib(float(row['pair_avg_bytes']))}</td>
            <td>{mib(float(row['pair_max_bytes']))}</td>
            <td>{float(row['pair_cv']):.3f}</td>
            <td>{float(row['cross_fraction']) * 100:.1f}%</td>
            <td>{float(row['makespan_us']):.3f}</td>
            <td>{gbps:.2f}</td>
            <td>{float(row['rank0_GBps']):.2f}</td>
            <td class="{ 'good' if vs >= 0 else 'bad' }">{pct(vs)}</td>
            <td><a href="{profile_href(row)}">rank0</a></td>
          </tr>"""
            )
    return "\n".join(chunks)


def summary_rows(rows: list[dict[str, str]]) -> str:
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["scenario"], {})[row["algorithm"]] = row
    chunks = []
    for scenario in sorted(grouped, key=lambda s: float(grouped[s]["baseline"]["max_over_avg"])):
        algs = grouped[scenario]
        baseline = single_rank_gbps(algs["baseline"])
        matrix = single_rank_gbps(algs["matrix"])
        clos = single_rank_gbps(algs["closv3"])
        chunks.append(
            f"""          <tr>
            <td>{float(algs['baseline']['max_over_avg']):.1f}x</td>
            <td>{float(algs['baseline']['pair_cv']):.3f}</td>
            <td>{baseline:.2f}</td>
            <td>{matrix:.2f}</td>
            <td>{clos:.2f}</td>
            <td class="{ 'good' if matrix >= baseline else 'bad' }">{pct(matrix / baseline - 1.0)}</td>
            <td class="{ 'good' if clos >= baseline else 'bad' }">{pct(clos / baseline - 1.0)}</td>
          </tr>"""
        )
    return "\n".join(chunks)


def render_report(rows: list[dict[str, str]]) -> str:
    by_ratio: dict[float, dict[str, dict[str, str]]] = {}
    for row in rows:
        by_ratio.setdefault(float(row["max_over_avg"]), {})[row["algorithm"]] = row
    r1 = by_ratio[1.0]
    r2 = by_ratio[2.0]
    baseline_drop = 1.0 - single_rank_gbps(r2["baseline"]) / single_rank_gbps(r1["baseline"])
    clos_drop = 1.0 - single_rank_gbps(r2["closv3"]) / single_rank_gbps(r1["closv3"])
    matrix_drop = 1.0 - single_rank_gbps(r2["matrix"]) / single_rank_gbps(r1["matrix"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV 不均衡敏感性扫描</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#d8dee8; --good:#087443; --bad:#b42318; --blue:#2563eb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1180px, calc(100% - 36px)); margin:26px auto 48px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    p {{ margin:8px 0; }}
    section, .metric {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .muted {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; margin:18px 0; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:4px; font-size:22px; line-height:1.2; }}
    .layout {{ display:grid; gap:14px; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-variant-numeric:tabular-nums; }}
    th, td {{ border-bottom:1px solid var(--line); padding:8px 7px; text-align:right; white-space:nowrap; }}
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align:left; }}
    th {{ color:var(--muted); font-size:12px; background:#fbfcfe; }}
    tr:last-child td {{ border-bottom:0; }}
    a {{ color:var(--blue); text-decoration:none; font-weight:650; }}
    a:hover {{ text-decoration:underline; }}
    code {{ background:#eef2f7; border-radius:5px; padding:1px 5px; }}
    .good {{ color:var(--good); }}
    .bad {{ color:var(--bad); }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:5px 0; }}
    @media (max-width:900px) {{ .metrics {{ grid-template-columns:1fr; }} table {{ font-size:12px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>UBX16 AllToAllV 不均衡敏感性扫描</h1>
    <p class="muted">本阶段只证明一件事：peer 粒度不均衡变大时，baseline、matrix、MeshClos V3 的带宽分别会退化多少。HCCL AllToAllV count 按 128MiB/rank 设置，并保留 self slot 参与等差分布计算；ns-3 只建模网络 peer 流，所以 self slot 会被丢弃。15 条网络 peer 流按环形 offset 形成等差数列；扫描参数是包含 self slot 的最大流 / 平均流：1.0、1.2、1.5、2.0。主指标是单 rank 有效带宽：全局网络吞吐 / 16。</p>
  </header>

  <div class="metrics">
    <div class="metric"><span>baseline 退化</span><strong>{baseline_drop * 100:.1f}%</strong><span>max/avg 1.0x → 2.0x</span></div>
    <div class="metric"><span>matrix 退化</span><strong class="bad">{matrix_drop * 100:.1f}%</strong><span>固定轮次更敏感</span></div>
    <div class="metric"><span>V3 退化</span><strong class="bad">{clos_drop * 100:.1f}%</strong><span>固定平面最敏感</span></div>
    <div class="metric"><span>2.0x V3 vs baseline</span><strong class="bad">{pct(single_rank_gbps(r2['closv3']) / single_rank_gbps(r2['baseline']) - 1.0)}</strong><span>同一不均衡度下</span></div>
  </div>

  <div class="layout">
    <section>
      <h2>结论</h2>
      <ul>
        <li>均匀 1.0x 只作为 sanity check：三种算法都接近 strict 单 TP 网络上限，baseline 单 rank 为 {single_rank_gbps(r1['baseline']):.2f} GB/s，V3 为 {single_rank_gbps(r1['closv3']):.2f} GB/s，差距只有 {abs(single_rank_gbps(r1['closv3']) / single_rank_gbps(r1['baseline']) - 1.0) * 100:.1f}%。</li>
        <li>不均衡一旦增加，退化速度明显分化：max/avg=1.2x 时 baseline 仍有 {single_rank_gbps(by_ratio[1.2]['baseline']):.2f} GB/s，V3 降到 {single_rank_gbps(by_ratio[1.2]['closv3']):.2f} GB/s，相对 baseline 低 {abs(single_rank_gbps(by_ratio[1.2]['closv3']) / single_rank_gbps(by_ratio[1.2]['baseline']) - 1.0) * 100:.1f}%。</li>
        <li>max/avg=2.0x 时 baseline 从均匀场景退化 {baseline_drop * 100:.1f}%，matrix 退化 {matrix_drop * 100:.1f}%，V3 退化 {clos_drop * 100:.1f}%；V3 同场景相对 baseline 低 {abs(single_rank_gbps(r2['closv3']) / single_rank_gbps(r2['baseline']) - 1.0) * 100:.1f}%。</li>
        <li>阶段性结论：baseline 对 peer 粒度不均衡更鲁棒；matrix/V3 的固定轮次、固定平面映射在均匀 AllToAll 下有效，但在 AllToAllV 中容易把大流串到同一逻辑通道上，形成拖尾。</li>
        <li>下一步优化方向应从“均匀场景提带宽”转为“大流切分、size-aware 平面分配、按流量重排 peer 顺序”，目标是降低不均衡场景的尾部 makespan。</li>
      </ul>
    </section>

    <section>
      <h2>退化趋势</h2>
      <table>
        <thead><tr><th>max/avg</th><th>pair CV</th><th>Baseline 单 rank GB/s</th><th>Matrix 单 rank GB/s</th><th>V3 单 rank GB/s</th><th>Matrix vs baseline</th><th>V3 vs baseline</th></tr></thead>
        <tbody>
{summary_rows(rows)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>完整结果</h2>
      <table>
        <thead>
          <tr><th>max/avg</th><th>算法</th><th>min MiB</th><th>avg MiB</th><th>max MiB</th><th>pair CV</th><th>跨组占比</th><th>us</th><th>单 rank GB/s</th><th>Rank0 TX GB/s</th><th>vs baseline</th><th>Profile</th></tr>
        </thead>
        <tbody>
{table_rows(rows)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>说明</h2>
      <ul>
        <li><code>pair CV</code> 是所有 src-dst peer 流大小的变异系数；本实验每个源 rank 的形状相同，所以它等价于 rank0 出流的 CV。</li>
        <li><code>单 rank GB/s</code> 使用全局网络吞吐 / 16，表示集群同步完成口径下平均每个 rank 的有效网络带宽；不是除以 15。除以 15 得到的是平均单 peer 流带宽。</li>
        <li><code>Rank0 TX GB/s</code> 是 rank0 自己 15 条发送 task 完成时间下的局部 TX 视角，可能高于或低于全局同步口径。</li>
        <li>本实验保留 self slot 参与 HCCL count 分布，再丢弃 self slot。因此报告中的 min/avg/max 是 15 条网络 peer 流的统计，网络注入总量会略小于 128MiB/rank。</li>
        <li>2.0x 场景下 self slot 可能接近 0；生成器强制所有 slot 至少 1 byte，以避免 0-byte task 干扰 ns-3 调度。</li>
      </ul>
    </section>
  </div>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--output", type=Path, default=REPORT)
    parser.add_argument("--skip-profiles", action="store_true")
    args = parser.parse_args()
    rows = load_rows(args.summary)
    if not args.skip_profiles:
        render_profiles(rows, args.output.parent.parent / "profiles")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(rows))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
