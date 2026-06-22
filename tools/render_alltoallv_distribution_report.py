#!/usr/bin/env python3
"""Render UBX16 AllToAllV distribution sweep report."""

from __future__ import annotations

import argparse
import csv
import html
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "experiments/ubx16/alltoallv/distribution"
REPORT_DIR = BASE / "reports"
SUMMARY = REPORT_DIR / "ns3ub-ubx16-alltoallv-distribution-summary.csv"
REPORT = REPORT_DIR / "ns3ub-ubx16-alltoallv-distribution-report.html"
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"

ALGORITHM_LABELS = {
    "baseline": "baseline",
    "meshclos": "meshclos",
}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def mib(value: float) -> str:
    return f"{value / 1024 / 1024:.2f}"


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def profile_name(row: dict[str, str]) -> str:
    return f"ns3ub-ubx16-a2av-distribution-{row['scenario']}-{row['algorithm']}-rank0-profile.html"


def render_profile(row: dict[str, str], output: Path) -> None:
    case = row["case"]
    title = (
        f"UBX16 AllToAllV distribution {float(row['target_max_min']):.2f}x | "
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


def grouped_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, str]]]:
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["scenario"], {})[row["algorithm"]] = row
    return grouped


def sort_scenarios(grouped: dict[str, dict[str, dict[str, str]]]) -> list[str]:
    return sorted(grouped, key=lambda s: float(grouped[s]["baseline"]["target_max_min"]))


def trend_rows(rows: list[dict[str, str]]) -> str:
    grouped = grouped_rows(rows)
    chunks = []
    for scenario in sort_scenarios(grouped):
        algs = grouped[scenario]
        baseline = float(algs["baseline"]["single_rank_GBps"])
        meshclos = float(algs["meshclos"]["single_rank_GBps"])
        chunks.append(
            f"""          <tr>
            <td>{float(algs['baseline']['target_max_min']):.2f}x</td>
            <td>{float(algs['baseline']['rank0_send_max_over_avg']):.2f}x</td>
            <td>{float(algs['baseline']['rank0_recv_max_over_avg']):.2f}x</td>
            <td>{baseline:.2f}</td>
            <td>{meshclos:.2f}</td>
            <td class="{ 'good' if meshclos >= baseline else 'bad' }">{pct(meshclos / baseline - 1.0)}</td>
          </tr>"""
        )
    return "\n".join(chunks)


def detail_rows(rows: list[dict[str, str]]) -> str:
    grouped = grouped_rows(rows)
    chunks = []
    for scenario in sort_scenarios(grouped):
        baseline = float(grouped[scenario]["baseline"]["single_rank_GBps"])
        for algorithm in ("baseline", "meshclos"):
            row = grouped[scenario][algorithm]
            gbps = float(row["single_rank_GBps"])
            vs = 0.0 if algorithm == "baseline" else gbps / baseline - 1.0
            chunks.append(
                f"""          <tr>
            <td>{float(row['target_max_min']):.2f}x</td>
            <td>{html.escape(ALGORITHM_LABELS[algorithm])}</td>
            <td>{mib(float(row['rank0_send_min_bytes']))}</td>
            <td>{mib(float(row['rank0_send_avg_bytes']))}</td>
            <td>{mib(float(row['rank0_send_max_bytes']))}</td>
            <td>{float(row['rank0_send_max_over_avg']):.2f}x</td>
            <td>{float(row['rank0_send_max_over_min']):.2f}x</td>
            <td>{mib(float(row['rank0_recv_min_bytes']))}</td>
            <td>{mib(float(row['rank0_recv_avg_bytes']))}</td>
            <td>{mib(float(row['rank0_recv_max_bytes']))}</td>
            <td>{float(row['rank0_recv_max_over_avg']):.2f}x</td>
            <td>{float(row['rank0_recv_max_over_min']):.2f}x</td>
            <td>{float(row['makespan_us']):.3f}</td>
            <td>{gbps:.2f}</td>
            <td>{float(row['rank0_tx_GBps']):.2f}</td>
            <td class="{ 'good' if vs >= 0 else 'bad' }">{pct(vs)}</td>
            <td><a href="{profile_name(row)}">rank0</a></td>
          </tr>"""
            )
    return "\n".join(chunks)


def render_report(rows: list[dict[str, str]]) -> str:
    grouped = grouped_rows(rows)
    first = grouped[sort_scenarios(grouped)[0]]
    last = grouped[sort_scenarios(grouped)[-1]]
    baseline_drop = 1.0 - float(last["baseline"]["single_rank_GBps"]) / float(first["baseline"]["single_rank_GBps"])
    meshclos_drop = 1.0 - float(last["meshclos"]["single_rank_GBps"]) / float(first["meshclos"]["single_rank_GBps"])
    meshclos_vs_last = float(last["meshclos"]["single_rank_GBps"]) / float(last["baseline"]["single_rank_GBps"]) - 1.0
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV 分布型不均衡扫描</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#d8dee8; --good:#087443; --bad:#b42318; --blue:#2563eb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1240px, calc(100% - 36px)); margin:26px auto 48px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    p {{ margin:8px 0; }}
    section, .metric {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .muted {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; margin:18px 0; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:4px; font-size:22px; line-height:1.2; }}
    .layout {{ display:grid; gap:14px; }}
    table {{ width:100%; border-collapse:collapse; table-layout:auto; font-variant-numeric:tabular-nums; }}
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
    .table-wrap {{ overflow-x:auto; }}
    @media (max-width:900px) {{ .metrics {{ grid-template-columns:1fr; }} table {{ font-size:12px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>UBX16 AllToAllV 分布型不均衡扫描</h1>
    <p class="muted">每个源 rank 总发送量固定为 128MiB。15 条 peer 流使用确定性近似随机分布，并将每个源 rank 的最大/最小流大小控制在 1.00x、1.25x、1.50x、2.00x。这里只比较 baseline 和 meshclos，观察不均衡度升高时单 rank 有效带宽如何下降。</p>
  </header>

  <div class="metrics">
    <div class="metric"><span>baseline 退化</span><strong>{baseline_drop * 100:.1f}%</strong><span>1.00x → 2.00x</span></div>
    <div class="metric"><span>meshclos 退化</span><strong class="bad">{meshclos_drop * 100:.1f}%</strong><span>1.00x → 2.00x</span></div>
    <div class="metric"><span>2.00x meshclos vs baseline</span><strong class="bad">{pct(meshclos_vs_last)}</strong><span>同一分布下</span></div>
    <div class="metric"><span>每 rank 总量</span><strong>128MiB</strong><span>发送侧固定</span></div>
  </div>

  <div class="layout">
    <section>
      <h2>结论</h2>
      <ul>
        <li>这组不是等差数列，而是受控的近似随机分布；每个源 rank 总发送量固定，单源 max/min 不超过 2x。</li>
        <li>发送侧和接收侧分别统计 rank0 的 min/avg/max、max/avg、max/min；报告不讨论热点，只展示 rank0 profile 和全局完成口径带宽。</li>
        <li>随着流量不均衡上升，baseline 和 meshclos 的带宽都会下降；meshclos 的固定平面/固定线程映射对大流拖尾更敏感。</li>
      </ul>
    </section>

    <section>
      <h2>退化趋势</h2>
      <table>
        <thead><tr><th>目标 max/min</th><th>rank0 发送 max/avg</th><th>rank0 接收 max/avg</th><th>baseline 单 rank GB/s</th><th>meshclos 单 rank GB/s</th><th>meshclos vs baseline</th></tr></thead>
        <tbody>
{trend_rows(rows)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>完整结果</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>目标 max/min</th><th>算法</th><th>send min MiB</th><th>send avg MiB</th><th>send max MiB</th><th>send max/avg</th><th>send max/min</th><th>recv min MiB</th><th>recv avg MiB</th><th>recv max MiB</th><th>recv max/avg</th><th>recv max/min</th><th>us</th><th>单 rank GB/s</th><th>Rank0 TX GB/s</th><th>vs baseline</th><th>Profile</th></tr>
          </thead>
          <tbody>
{detail_rows(rows)}
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>口径</h2>
      <ul>
        <li><code>单 rank GB/s</code> 是全局同步完成口径下的平均单 rank 带宽。</li>
        <li><code>Rank0 TX GB/s</code> 只看 rank0 发送 task 的局部完成时间，用来辅助解释 rank0 profile。</li>
        <li><code>send</code> 是 rank0 发往其他 15 个 peer 的流大小统计；<code>recv</code> 是其他 15 个 rank 发往 rank0 的流大小统计。</li>
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
