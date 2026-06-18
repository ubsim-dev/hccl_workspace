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


def profile_name(row: dict[str, str]) -> str:
    return f"ns3ub-ubx16-a2av-gradient-{row['scenario']}-{row['algorithm']}-rank0-profile.html"


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


def render_profiles(rows: list[dict[str, str]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for row in rows:
        render_profile(row, REPORT_DIR / profile_name(row))


def table_rows(rows: list[dict[str, str]]) -> str:
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["scenario"], {})[row["algorithm"]] = row
    chunks = []
    for scenario in sorted(grouped, key=lambda s: float(grouped[s]["baseline"]["max_over_avg"])):
        algs = grouped[scenario]
        baseline_g = float(algs["baseline"]["global_GBps"])
        for algorithm in ("baseline", "matrix", "closv3"):
            row = algs[algorithm]
            gbps = float(row["global_GBps"])
            vs = 0.0 if algorithm == "baseline" else gbps / baseline_g - 1.0
            chunks.append(
                f"""          <tr>
            <td>{float(row['max_over_avg']):.1f}x</td>
            <td>{html.escape(ALGORITHM_LABELS[algorithm])}</td>
            <td>{mib(float(row['pair_min_bytes']))}</td>
            <td>{mib(float(row['pair_avg_bytes']))}</td>
            <td>{mib(float(row['pair_max_bytes']))}</td>
            <td>{float(row['pair_cv']):.3f}</td>
            <td>{float(row['dst_cv']):.3f}</td>
            <td>{float(row['cross_fraction']) * 100:.1f}%</td>
            <td>{float(row['makespan_us']):.3f}</td>
            <td>{gbps:.2f}</td>
            <td class="{ 'good' if vs >= 0 else 'bad' }">{pct(vs)}</td>
            <td><a href="{profile_name(row)}">rank0</a></td>
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
        baseline = float(algs["baseline"]["global_GBps"])
        matrix = float(algs["matrix"]["global_GBps"])
        clos = float(algs["closv3"]["global_GBps"])
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
    baseline_drop = 1.0 - float(r2["baseline"]["global_GBps"]) / float(r1["baseline"]["global_GBps"])
    clos_drop = 1.0 - float(r2["closv3"]["global_GBps"]) / float(r1["closv3"]["global_GBps"])
    matrix_drop = 1.0 - float(r2["matrix"]["global_GBps"]) / float(r1["matrix"]["global_GBps"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV 等差不均衡扫描</title>
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
    <h1>UBX16 AllToAllV 等差不均衡扫描</h1>
    <p class="muted">每 rank 总发送量固定为 128MiB，15 条 peer 流按环形 offset 形成等差数列。扫描参数是最大流 / 平均流：1.0、1.2、1.5、2.0。目的 rank 总量保持均衡，所以这里主要观察单 src 的 peer 粒度不均衡。</p>
  </header>

  <div class="metrics">
    <div class="metric"><span>baseline 1.0→2.0 劣化</span><strong>{baseline_drop * 100:.1f}%</strong></div>
    <div class="metric"><span>matrix 1.0→2.0 劣化</span><strong class="bad">{matrix_drop * 100:.1f}%</strong></div>
    <div class="metric"><span>V3 1.0→2.0 劣化</span><strong class="bad">{clos_drop * 100:.1f}%</strong></div>
    <div class="metric"><span>2.0x 最大单流</span><strong>{mib(float(r2['baseline']['pair_max_bytes']))} MiB</strong></div>
  </div>

  <div class="layout">
    <section>
      <h2>结论</h2>
      <ul>
        <li>均匀时 Matrix/V3 略优于 baseline：baseline 为 {float(r1['baseline']['global_GBps']):.2f} GB/s，V3 为 {float(r1['closv3']['global_GBps']):.2f} GB/s。</li>
        <li>当最大 peer 流达到平均流 1.2x，baseline 仍为 {float(by_ratio[1.2]['baseline']['global_GBps']):.2f} GB/s，但 V3 降到 {float(by_ratio[1.2]['closv3']['global_GBps']):.2f} GB/s，开始明显低于 baseline。</li>
        <li>2.0x 时 baseline 为 {float(r2['baseline']['global_GBps']):.2f} GB/s，V3 为 {float(r2['closv3']['global_GBps']):.2f} GB/s；V3 相对 baseline 低 {abs(float(r2['closv3']['global_GBps']) / float(r2['baseline']['global_GBps']) - 1.0) * 100:.1f}%。</li>
        <li>这说明固定平面/单 TP 的优化算法适合均匀 AllToAll；对 AllToAllV，如果单 peer 流变大，热点流会拖尾，需要进一步做大流切分或 size-aware 平面分配。</li>
      </ul>
    </section>

    <section>
      <h2>趋势汇总</h2>
      <table>
        <thead><tr><th>max/avg</th><th>pair CV</th><th>Baseline GB/s</th><th>Matrix GB/s</th><th>V3 GB/s</th><th>Matrix vs baseline</th><th>V3 vs baseline</th></tr></thead>
        <tbody>
{summary_rows(rows)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>完整结果</h2>
      <table>
        <thead>
          <tr><th>max/avg</th><th>算法</th><th>min MiB</th><th>avg MiB</th><th>max MiB</th><th>pair CV</th><th>dst CV</th><th>跨组占比</th><th>us</th><th>GB/s</th><th>vs baseline</th><th>Profile</th></tr>
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
        <li><code>dst CV</code> 约为 0，说明目的 rank 总接收量被刻意保持均衡，避免目的热点干扰判断。</li>
        <li>2.0x 场景的理论最小值会接近 0；生成器强制所有 task 至少 1 byte，以避免 0-byte task 干扰 ns-3 调度。</li>
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
        render_profiles(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(rows))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
