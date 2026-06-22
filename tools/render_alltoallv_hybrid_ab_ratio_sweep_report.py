#!/usr/bin/env python3
"""Render UBX16 AllToAllV A+B ratio sweep report."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/gradient/reports/ns3ub-ubx16-alltoallv-gradient-summary.csv"
SWEEP_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-ratio-sweep-summary.csv"
REPORT = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-ratio-sweep-report.html"


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def single_rank(row: dict[str, str]) -> float:
    if row.get("single_rank_GBps"):
        return float(row["single_rank_GBps"])
    return float(row["global_GBps"]) / 16.0


def mib(value: str | float) -> float:
    return float(value) / 1024 / 1024


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def profile_href(row: dict[str, str]) -> str:
    return f"../profiles/{Path(row['case']).name}-rank0-profile.html"


def base_refs(base_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    for row in base_rows:
        if row["scenario"] == "max2p0x" and row["algorithm"] in ("baseline", "closv3"):
            refs[row["algorithm"]] = row
    return refs


def table(rows: list[dict[str, str]], baseline: float) -> str:
    chunks: list[str] = []
    for row in sorted(rows, key=lambda r: (r["split_kind"], float(r["split_ratio"]))):
        gbps = single_rank(row)
        chunks.append(
            f"""<tr>
  <td>{html.escape(row['split_kind'])}</td>
  <td>{float(row['split_ratio']):.1f}x</td>
  <td>{mib(row['a_bytes_per_pair']):.2f}</td>
  <td>{mib(row['a_network_bytes']) / 16:.2f}</td>
  <td>{mib(row['b_network_bytes']) / 16:.2f}</td>
  <td>{mib(row['padding_bytes']) / 16:.2f}</td>
  <td>{float(row['tasks']):.0f}</td>
  <td>{float(row['makespan_us']):.3f}</td>
  <td>{gbps:.2f}</td>
  <td class="{'good' if gbps >= baseline else 'bad'}">{pct(gbps / baseline - 1.0)}</td>
  <td><a href="{html.escape(profile_href(row))}">{html.escape(Path(row['case']).name)}</a></td>
</tr>"""
        )
    return "\n".join(chunks)


def best(rows: list[dict[str, str]], kind: str) -> dict[str, str]:
    candidates = [row for row in rows if row["split_kind"] == kind]
    return max(candidates, key=single_rank)


def render(base_rows: list[dict[str, str]], sweep_rows: list[dict[str, str]]) -> str:
    refs = base_refs(base_rows)
    baseline = single_rank(refs["baseline"])
    clos = single_rank(refs["closv3"])
    best_cap = best(sweep_rows, "cap")
    best_pad = best(sweep_rows, "pad")
    pair_avg = mib(sweep_rows[0]["pair_avg_bytes"])
    pair_min = mib(sweep_rows[0]["pair_min_bytes"])
    pair_max = mib(sweep_rows[0]["pair_max_bytes"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV Hybrid A/B Ratio Sweep</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#d8dee8; --good:#087443; --bad:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1180px, calc(100% - 36px)); margin:26px auto 48px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0; }}
    p {{ margin:8px 0; }}
    section, .metric {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .muted {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:4px; font-size:22px; line-height:1.2; }}
    .layout {{ display:grid; gap:14px; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-variant-numeric:tabular-nums; }}
    th, td {{ border-bottom:1px solid var(--line); padding:8px 7px; text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    th:first-child, td:first-child, th:last-child, td:last-child {{ text-align:left; }}
    th {{ color:var(--muted); font-size:12px; background:#fbfcfe; }}
    tr:last-child td {{ border-bottom:0; }}
    a {{ color:#2563eb; text-decoration:none; font-weight:650; }}
    a:hover {{ text-decoration:underline; }}
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
    <h1>UBX16 AllToAllV Hybrid A/B Ratio Sweep</h1>
    <p class="muted">固定 max/avg=2.0，128MiB/rank。A 阶段走固定平面 single TP，B 阶段走 baseline full TP，当前为 A_then_B 同步。cap 表示不 padding，A 每条流最多发送 ratio*avg；pad 表示强制每条流发送 ratio*avg，不足部分算冗余通信。</p>
  </header>

  <div class="metrics">
    <div class="metric"><span>baseline</span><strong>{baseline:.2f}</strong><span>GB/s per rank</span></div>
    <div class="metric"><span>pure meshclos</span><strong>{clos:.2f}</strong><span>{pct(clos / baseline - 1.0)} vs baseline</span></div>
    <div class="metric"><span>best cap</span><strong>{single_rank(best_cap):.2f}</strong><span>{best_cap['split_ratio']}x, {pct(single_rank(best_cap) / baseline - 1.0)}</span></div>
    <div class="metric"><span>best pad</span><strong>{single_rank(best_pad):.2f}</strong><span>{best_pad['split_ratio']}x, {pct(single_rank(best_pad) / baseline - 1.0)}</span></div>
  </div>

  <div class="layout">
    <section>
      <h2>流量分布</h2>
      <p>non-self peer 流大小：min {pair_min:.2f} MiB，avg {pair_avg:.2f} MiB，max {pair_max:.2f} MiB，max/avg=2.0。</p>
    </section>

    <section>
      <h2>结论</h2>
      <ul>
        <li>不 padding 的 cap 同步切分不是好方向：ratio 从 0.2 增大到 1.5 时，A 阶段越来越像固定平面尾部，整体从 197.12 GB/s 降到 117.28 GB/s。</li>
        <li>padding 小比例可以接近 baseline：pad0.2/pad0.4 约 202-203 GB/s，和 baseline 203.20 GB/s 基本持平。</li>
        <li>padding 大比例会明显变差：pad2.0 的有效带宽只有 120.56 GB/s，因为每 rank padding 约 128 MiB，网络注入约翻倍。</li>
        <li>所以如果 A/B 严格同步，合理比例只能很小；想让固定平面真的有收益，需要改成 overlap/pipeline，而不是扩大同步 A 阶段。</li>
      </ul>
    </section>

    <section>
      <h2>完整数据</h2>
      <table>
        <thead><tr><th>模式</th><th>比例</th><th>A target MiB</th><th>A MiB/rank</th><th>B MiB/rank</th><th>pad MiB/rank</th><th>tasks</th><th>us</th><th>GB/s/rank</th><th>vs baseline</th><th>case</th></tr></thead>
        <tbody>
{table(sweep_rows, baseline)}
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
    parser.add_argument("--sweep-summary", type=Path, default=SWEEP_SUMMARY)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(load(args.baseline_summary), load(args.sweep_summary)))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
