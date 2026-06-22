#!/usr/bin/env python3
"""Render UBX16 AllToAllV hybrid cap step-barrier comparison."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/gradient/reports/ns3ub-ubx16-alltoallv-gradient-summary.csv"
THREAD_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-ratio-sweep-summary.csv"
STEP_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-step-cap-sweep-summary.csv"
REPORT = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-step-cap-report.html"


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def single_rank(row: dict[str, str]) -> float:
    if row.get("single_rank_GBps"):
        return float(row["single_rank_GBps"])
    return float(row["global_GBps"]) / 16.0


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def mib(value: str | float) -> float:
    return float(value) / 1024 / 1024


def baseline_ref(rows: list[dict[str, str]], alg: str) -> dict[str, str]:
    for row in rows:
        if row["scenario"] == "max2p0x" and row["algorithm"] == alg:
            return row
    raise KeyError(alg)


def by_ratio(rows: list[dict[str, str]], prefix: str) -> dict[float, dict[str, str]]:
    out: dict[float, dict[str, str]] = {}
    for row in rows:
        if row["algorithm"].startswith(prefix) and row["split_kind"] == "cap":
            out[float(row["split_ratio"])] = row
    return out


def render(base_rows: list[dict[str, str]], thread_rows: list[dict[str, str]], step_rows: list[dict[str, str]]) -> str:
    baseline = single_rank(baseline_ref(base_rows, "baseline"))
    clos = single_rank(baseline_ref(base_rows, "closv3"))
    thread = by_ratio(thread_rows, "hybrid-cap")
    step = by_ratio(step_rows, "hybrid-step-cap")
    chunks: list[str] = []
    for ratio in sorted(step):
        srow = step[ratio]
        trow = thread[ratio]
        sgbps = single_rank(srow)
        tgbps = single_rank(trow)
        chunks.append(
            f"""<tr>
  <td>{ratio:.1f}x</td>
  <td>{mib(srow['a_bytes_per_pair']):.2f}</td>
  <td>{mib(srow['a_network_bytes']) / 16:.2f}</td>
  <td>{mib(srow['b_network_bytes']) / 16:.2f}</td>
  <td>{tgbps:.2f}</td>
  <td>{sgbps:.2f}</td>
  <td class="{'good' if sgbps >= tgbps else 'bad'}">{pct(sgbps / tgbps - 1.0)}</td>
  <td class="{'good' if sgbps >= baseline else 'bad'}">{pct(sgbps / baseline - 1.0)}</td>
  <td><code>{html.escape(Path(srow['case']).name)}</code></td>
</tr>"""
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV Hybrid Cap Step-Barrier 对照</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#d8dee8; --good:#087443; --bad:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1120px, calc(100% - 36px)); margin:26px auto 48px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    section, .metric {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .muted {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:18px 0; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:4px; font-size:22px; line-height:1.2; }}
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
    <h1>UBX16 AllToAllV Hybrid Cap Step-Barrier 对照</h1>
    <p class="muted">固定 max/avg=2.0，128MiB/rank。cap 不做 padding。对照 A 阶段使用 v3-thread-serial 与 v3-step-barrier 的差异；B 阶段仍然等待全部 A 完成后启动。</p>
  </header>

  <div class="metrics">
    <div class="metric"><span>baseline</span><strong>{baseline:.2f}</strong><span>GB/s per rank</span></div>
    <div class="metric"><span>pure meshclos / no B</span><strong>{clos:.2f}</strong><span>{pct(clos / baseline - 1.0)} vs baseline</span></div>
    <div class="metric"><span>best step-cap</span><strong>{max(single_rank(x) for x in step.values()):.2f}</strong><span>{pct(max(single_rank(x) for x in step.values()) / baseline - 1.0)} vs baseline</span></div>
  </div>

  <div class="layout">
    <section>
      <h2>结论</h2>
      <ul>
        <li>step-barrier 修正了 cap 不同步时的平面内乱序竞争，所有比例都比 no-step cap 更高。</li>
        <li>但 A/B 严格同步下，cap 比例越大仍然越差；最佳是 cap0.2，约 201.87 GB/s/rank，接近 baseline 但没有超过。</li>
        <li>这说明固定平面要发挥价值，单纯同步是不够的；下一步应该看 A/B overlap 或按 step pipeline，让 B 残差不要等全部 A 完成。</li>
      </ul>
    </section>

    <section>
      <h2>数据</h2>
      <table>
        <thead><tr><th>cap ratio</th><th>A target MiB</th><th>A MiB/rank</th><th>B MiB/rank</th><th>no-step GB/s</th><th>step GB/s</th><th>step vs no-step</th><th>step vs baseline</th><th>case</th></tr></thead>
        <tbody>
{''.join(chunks)}
        </tbody>
      </table>
    </section>
  </div>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-summary", type=Path, default=BASE_SUMMARY)
    parser.add_argument("--thread-summary", type=Path, default=THREAD_SUMMARY)
    parser.add_argument("--step-summary", type=Path, default=STEP_SUMMARY)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(load(args.baseline_summary), load(args.thread_summary), load(args.step_summary)))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
