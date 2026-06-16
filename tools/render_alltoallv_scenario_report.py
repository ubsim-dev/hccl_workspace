#!/usr/bin/env python3
"""Render representative AllToAllV profiles and one aggregate HTML report."""

from __future__ import annotations

import argparse
import csv
import html
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "experiments/ubx16/alltoallv/scenarios/reports"
SUMMARY = REPORT_DIR / "ns3ub-ubx16-alltoallv-scenarios-summary.csv"
REPORT = REPORT_DIR / "ns3ub-ubx16-alltoallv-scenarios-report.html"
UBX16_SOURCE_CASE = REPO_ROOT / "experiments/topologies/ubx16/generated_topology_ubx16"

ALGORITHM_LABELS = {
    "baseline": "Baseline Mesh1D",
    "matrix": "Matrix",
    "closv3": "MeshClos V3",
}

SCENARIO_LABELS = {
    "uniform": "均匀 AllToAllV",
    "mild_random": "轻微随机 V",
    "dispatch_hot4": "MoE dispatch: 热目的 rank",
    "combine_hot4": "MoE combine: 热源 rank",
    "cross_group_heavy": "跨组重流量",
}

SCENARIO_NOTES = {
    "uniform": "每个 src 的 16MiB 平均分给 15 个 peer，是最接近 AllToAll 的基准场景。",
    "mild_random": "每个 src 总量固定，但 peer 粒度有轻微长尾；目的 rank CV 约 0.25。",
    "dispatch_hot4": "所有 src 倾向发往少数 hot 目的 rank，模拟 dispatch 后 token 聚集到专家所在 rank。",
    "combine_hot4": "少数 hot 源 rank 发出更多数据，模拟 combine 从专家 rank 回流。",
    "cross_group_heavy": "每个 src 主要发跨 mesh 数据，用来观察 clos 平面调度是否充分。",
}


@dataclass(frozen=True)
class Profile:
    scenario: str
    algorithm: str
    rank: int
    reason: str


PROFILES = [
    Profile("uniform", "baseline", 0, "均匀基线 rank0，展示 Mesh1D 多 peer 并发形态"),
    Profile("uniform", "matrix", 0, "均匀 Matrix rank0，展示 round/slot 调度"),
    Profile("uniform", "closv3", 0, "均匀 MeshClos V3 rank0，展示 mesh+4 clos 逻辑单元"),
    Profile("mild_random", "baseline", 0, "轻微随机下 baseline 对 rank0 的多 TP 分摊"),
    Profile("mild_random", "matrix", 0, "轻微随机下 Matrix 单 slot 热点拖尾"),
    Profile("mild_random", "closv3", 0, "轻微随机下 V3 单平面热点拖尾"),
    Profile("dispatch_hot4", "baseline", 0, "dispatch 热目的场景，rank0 作为普通源 rank"),
    Profile("dispatch_hot4", "matrix", 0, "dispatch 热目的场景，Matrix 源 rank 视角"),
    Profile("dispatch_hot4", "closv3", 0, "dispatch 热目的场景，V3 源 rank 视角"),
    Profile("combine_hot4", "baseline", 3, "combine 热源场景，rank3 是 hot source"),
    Profile("combine_hot4", "matrix", 3, "combine 热源场景，Matrix hot source"),
    Profile("combine_hot4", "closv3", 3, "combine 热源场景，V3 hot source"),
    Profile("cross_group_heavy", "baseline", 0, "跨组重流量 baseline rank0"),
    Profile("cross_group_heavy", "closv3", 0, "跨组重流量 V3 rank0"),
]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def load_summary(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def by_scenario(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, str]]]:
    result: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        result.setdefault(row["scenario"], {})[row["algorithm"]] = row
    return result


def fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def signed_pct(value: float, digits: int = 1) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.{digits}f}%"


def mib(value: float) -> str:
    return f"{value / 1024 / 1024:.2f}MiB"


def profile_filename(profile: Profile) -> str:
    return (
        f"ns3ub-ubx16-a2av-{profile.scenario}-{profile.algorithm}-"
        f"rank{profile.rank}-profile.html"
    )


def render_profiles(rows_by_scenario: dict[str, dict[str, dict[str, str]]]) -> list[tuple[Profile, str]]:
    rendered: list[tuple[Profile, str]] = []
    for profile in PROFILES:
        row = rows_by_scenario[profile.scenario][profile.algorithm]
        case = row["case"]
        output = REPORT_DIR / profile_filename(profile)
        title = (
            f"{SCENARIO_LABELS[profile.scenario]} | "
            f"{ALGORITHM_LABELS[profile.algorithm]} | rank {profile.rank}"
        )
        if profile.algorithm == "baseline":
            cmd = [
                "python3",
                "tools/render_ns3ub_mesh1d_rank_profile.py",
                case,
                "-o",
                str(output),
                "--rank",
                str(profile.rank),
                "--concurrent",
                "16",
                "--title",
                title,
            ]
        elif profile.algorithm == "matrix":
            cmd = [
                "python3",
                "tools/render_ns3ub_matrix_rank_profile.py",
                case,
                "-o",
                str(output),
                "--rank",
                str(profile.rank),
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
                str(profile.rank),
                "--rank-count",
                "16",
                "--group-size",
                "4",
                "--source-case",
                str(UBX16_SOURCE_CASE),
                "--title",
                title,
            ]
        run(cmd)
        rendered.append((profile, output.name))
    return rendered


def scenario_cards(rows_by_scenario: dict[str, dict[str, dict[str, str]]]) -> str:
    cards = []
    for scenario in SCENARIO_LABELS:
        algs = rows_by_scenario[scenario]
        best = max(algs.values(), key=lambda row: float(row["global_GBps"]))
        baseline = algs["baseline"]
        clos = algs["closv3"]
        matrix = algs["matrix"]
        baseline_g = float(baseline["global_GBps"])
        best_g = float(best["global_GBps"])
        clos_g = float(clos["global_GBps"])
        matrix_g = float(matrix["global_GBps"])
        opt_vs_baseline = best_g / baseline_g - 1.0
        clos_vs_baseline = clos_g / baseline_g - 1.0
        matrix_vs_baseline = matrix_g / baseline_g - 1.0
        best_label = ALGORITHM_LABELS[best["algorithm"]]
        if abs(opt_vs_baseline) < 1e-9:
            trend_text = "baseline 即最佳"
            trend_class = "good"
        elif opt_vs_baseline > 0:
            trend_text = f"提升 {pct(opt_vs_baseline)}"
            trend_class = "good"
        else:
            trend_text = f"劣化 {pct(abs(opt_vs_baseline))}"
            trend_class = "bad"
        cards.append(
            f"""
      <section class="scenario-card">
        <h3>{html.escape(SCENARIO_LABELS[scenario])}</h3>
        <p>{html.escape(SCENARIO_NOTES[scenario])}</p>
        <div class="metric-row">
          <div><span>最佳</span><strong>{html.escape(best_label)}</strong></div>
          <div><span>最佳吞吐</span><strong>{fmt(best_g)} GB/s</strong></div>
          <div><span>最佳相对 baseline</span><strong class="{trend_class}">{trend_text}</strong></div>
        </div>
        <table>
          <thead><tr><th>算法</th><th>Makespan(us)</th><th>Global GB/s</th><th>vs baseline</th><th>Rank0 TX GB/s</th></tr></thead>
          <tbody>
            <tr><td>Baseline</td><td>{fmt(float(baseline['makespan_us']), 3)}</td><td>{fmt(baseline_g)}</td><td>0.0%</td><td>{fmt(float(baseline['rank0_GBps']))}</td></tr>
            <tr><td>Matrix</td><td>{fmt(float(matrix['makespan_us']), 3)}</td><td>{fmt(matrix_g)}</td><td class="{ 'good' if matrix_vs_baseline >= 0 else 'bad' }">{signed_pct(matrix_vs_baseline)}</td><td>{fmt(float(matrix['rank0_GBps']))}</td></tr>
            <tr><td>MeshClos V3</td><td>{fmt(float(clos['makespan_us']), 3)}</td><td>{fmt(clos_g)}</td><td class="{ 'good' if clos_vs_baseline >= 0 else 'bad' }">{signed_pct(clos_vs_baseline)}</td><td>{fmt(float(clos['rank0_GBps']))}</td></tr>
          </tbody>
        </table>
      </section>"""
        )
    return "\n".join(cards)


def profile_links(rendered: list[tuple[Profile, str]]) -> str:
    items = []
    for profile, filename in rendered:
        items.append(
            f"""
        <tr>
          <td>{html.escape(SCENARIO_LABELS[profile.scenario])}</td>
          <td>{html.escape(ALGORITHM_LABELS[profile.algorithm])}</td>
          <td>rank {profile.rank}</td>
          <td>{html.escape(profile.reason)}</td>
          <td><a href="{html.escape(filename)}">打开 profile</a></td>
        </tr>"""
        )
    return "\n".join(items)


def render_report(rows: list[dict[str, str]], rendered: list[tuple[Profile, str]]) -> str:
    grouped = by_scenario(rows)
    uniform_base = float(grouped["uniform"]["baseline"]["global_GBps"])
    uniform_best = max(float(row["global_GBps"]) for row in grouped["uniform"].values())
    dispatch_base = float(grouped["dispatch_hot4"]["baseline"]["global_GBps"])
    dispatch_v3 = float(grouped["dispatch_hot4"]["closv3"]["global_GBps"])
    mild_base = float(grouped["mild_random"]["baseline"]["global_GBps"])
    mild_v3 = float(grouped["mild_random"]["closv3"]["global_GBps"])
    cross_base = float(grouped["cross_group_heavy"]["baseline"]["global_GBps"])
    cross_best = max(float(row["global_GBps"]) for row in grouped["cross_group_heavy"].values())

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV 场景仿真报告</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#d8dee8; --good:#087443; --bad:#b42318; --blue:#2563eb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1180px, calc(100% - 36px)); margin: 26px auto 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 19px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; letter-spacing: 0; }}
    p {{ margin: 8px 0; }}
    .muted {{ color: var(--muted); }}
    section, .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }}
    .kpi .label {{ color: var(--muted); font-size: 12px; }}
    .kpi .value {{ font-size: 23px; font-weight: 750; line-height: 1.25; }}
    .layout {{ display: grid; gap: 14px; }}
    .scenario-grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; }}
    .scenario-card p {{ color: var(--muted); }}
    .metric-row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 12px 0; }}
    .metric-row div {{ background: #fbfcfe; border: 1px solid #edf1f6; border-radius: 6px; padding: 9px 10px; }}
    .metric-row span {{ display:block; color: var(--muted); font-size: 12px; }}
    .metric-row strong {{ display:block; margin-top: 3px; font-size: 16px; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-variant-numeric: tabular-nums; }}
    th, td {{ padding: 8px 9px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child, .links td:nth-child(4) {{ text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; background: #fbfcfe; }}
    tr:last-child td {{ border-bottom: 0; }}
    .good {{ color: var(--good); }}
    .bad {{ color: var(--bad); }}
    a {{ color: var(--blue); text-decoration: none; font-weight: 650; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background:#eef2f7; border-radius:5px; padding:1px 5px; }}
    ul {{ margin: 8px 0 0 18px; padding: 0; }}
    li {{ margin: 5px 0; }}
    @media (max-width: 860px) {{ .grid, .metric-row {{ grid-template-columns: 1fr; }} table {{ font-size: 12px; }} th, td {{ padding: 7px 5px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>UBX16 AllToAllV 场景仿真报告</h1>
    <p class="muted">拓扑为 16 rank、每 4 rank 一个 mesh、4 个 clos 平面；每 rank 总通信量 16MiB。吞吐按全部 task 数据量 / 全局 makespan 计算。</p>
  </header>

  <div class="grid">
    <div class="card kpi"><div class="label">均匀场景优化收益</div><div class="value good">+{pct(uniform_best / uniform_base - 1.0)}</div></div>
    <div class="card kpi"><div class="label">跨组重流量优化收益</div><div class="value good">+{pct(cross_best / cross_base - 1.0)}</div></div>
    <div class="card kpi"><div class="label">轻微随机 V3 劣化</div><div class="value bad">-{pct(1.0 - mild_v3 / mild_base)}</div></div>
    <div class="card kpi"><div class="label">Dispatch 热点 V3 劣化</div><div class="value bad">-{pct(1.0 - dispatch_v3 / dispatch_base)}</div></div>
  </div>

  <div class="layout">
    <section>
      <h2>核心结论</h2>
      <ul>
        <li>当 V 分布接近均匀，Matrix / MeshClos V3 可以把跨组流量铺到 4 个 clos 平面，global 吞吐从 {fmt(uniform_base)} GB/s 提到 {fmt(uniform_best)} GB/s，提升 {pct(uniform_best / uniform_base - 1.0)}。</li>
        <li>跨组占比很高但仍均匀时，MeshClos V3 仍有收益：从 {fmt(cross_base)} GB/s 到 {fmt(cross_best)} GB/s，提升 {pct(cross_best / cross_base - 1.0)}。</li>
        <li>一旦 AllToAllV 出现目的热点或源热点，当前 Matrix / MeshClos V3 的“peer 绑定单平面/单 slot”会让热点大流拖尾；轻微随机下 V3 比 baseline 低 {pct(1.0 - mild_v3 / mild_base)}，dispatch 热目的下低 {pct(1.0 - dispatch_v3 / dispatch_base)}。</li>
        <li>这个结果说明优化算法对均匀 AllToAll 很有效，但对 MoE 类 AllToAllV 需要进一步做大 peer 流拆分，或者按 size 重新做平面选择，否则热点 V 会吞掉分平面收益。</li>
      </ul>
    </section>

    <section>
      <h2>场景结果</h2>
      <p class="muted">场景表以 <code>Global GB/s</code> 和全局 makespan 判断优劣；<code>Rank0 TX GB/s</code> 只是辅助观察列。combine 场景的 hot source 是 rank 3/7/11/15，因此 rank0 不代表瓶颈。</p>
      <div class="scenario-grid">
        {scenario_cards(grouped)}
      </div>
    </section>

    <section>
      <h2>Profiling 图</h2>
      <p class="muted">这些图是典型 rank 的 TX 视角。点击条形可以看到 task 开始/结束时间、数据量、吞吐和逻辑 slot。</p>
      <table class="links">
        <thead><tr><th>场景</th><th>算法</th><th>Rank</th><th>为什么看它</th><th>链接</th></tr></thead>
        <tbody>
          {profile_links(rendered)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>定量原因</h2>
      <ul>
        <li>Baseline 的 <code>transport_channel</code> 行数更多，模型里一个大的 peer 流可以被多 TP 分摊；所以在 dispatch/combine 热点分布下，baseline 虽然调度粗糙，但热点流不一定被单条 50GB/s 平面卡死。</li>
        <li>MeshClos V3 / Matrix 当前 strict 建模更接近“每个 peer 选择一个 clos 逻辑平面”。均匀时这是优势，因为 15 个 peer 自然分散；热点时这是劣势，因为最大的 peer 数据量达到 {mib(float(grouped['dispatch_hot4']['baseline']['max_pair_bytes']))}，单 peer 拖尾决定 makespan。</li>
        <li>Rank0 数值不能单独代表全局，尤其 combine 场景 hot source 是 rank 3/7/11/15；报告同时给 global GB/s 和热点 rank profiling。</li>
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

    rows = load_summary(args.summary)
    grouped = by_scenario(rows)
    rendered = []
    if not args.skip_profiles:
        rendered = render_profiles(grouped)
    else:
        rendered = [(profile, profile_filename(profile)) for profile in PROFILES]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(rows, rendered))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
