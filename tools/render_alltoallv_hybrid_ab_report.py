#!/usr/bin/env python3
"""Render UBX16 AllToAllV A+B hybrid experiment report."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/gradient/reports/ns3ub-ubx16-alltoallv-gradient-summary.csv"
HYBRID_SUMMARY = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-summary.csv"
REPORT = REPO_ROOT / "experiments/ubx16/alltoallv/hybrid_ab/reports/ns3ub-ubx16-alltoallv-hybrid-ab-report.html"


LABELS = {
    "baseline": "baseline",
    "closv3": "meshclos",
    "hybrid-min": "hybrid-min",
    "hybrid-pad80": "hybrid-pad80",
}


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def single_rank(row: dict[str, str]) -> float:
    if "single_rank_GBps" in row and row["single_rank_GBps"]:
        return float(row["single_rank_GBps"])
    return float(row["global_GBps"]) / 16.0


def mib(value: str | float) -> float:
    return float(value) / 1024 / 1024


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def profile_href(row: dict[str, str]) -> str:
    if row["algorithm"] in ("baseline", "closv3"):
        return f"../../gradient/profiles/ns3ub-ubx16-a2av-gradient-{row['scenario']}-{row['algorithm']}-rank0-profile.html"
    return f"../profiles/{Path(row['case']).name}-rank0-profile.html"


def rows_by_ratio(base_rows: list[dict[str, str]], hybrid_rows: list[dict[str, str]]) -> dict[float, dict[str, dict[str, str]]]:
    grouped: dict[float, dict[str, dict[str, str]]] = {}
    for row in base_rows:
        if row["algorithm"] not in ("baseline", "closv3"):
            continue
        grouped.setdefault(float(row["max_over_avg"]), {})[row["algorithm"]] = row
    for row in hybrid_rows:
        grouped.setdefault(float(row["max_over_avg"]), {})[row["algorithm"]] = row
    return grouped


def summary_table(grouped: dict[float, dict[str, dict[str, str]]]) -> str:
    chunks: list[str] = []
    for ratio in sorted(grouped):
        algs = grouped[ratio]
        baseline = single_rank(algs["baseline"])
        for alg in ("baseline", "closv3", "hybrid-min", "hybrid-pad80"):
            row = algs[alg]
            gbps = single_rank(row)
            a_rank = mib(row.get("a_network_bytes", "0")) / 16
            b_rank = mib(row.get("b_network_bytes", "0")) / 16
            pad_rank = mib(row.get("padding_bytes", "0")) / 16
            chunks.append(
                f"""<tr>
  <td>{ratio:.1f}x</td>
  <td>{html.escape(LABELS[alg])}</td>
  <td>{mib(row['pair_min_bytes']):.2f}</td>
  <td>{mib(row['pair_avg_bytes']):.2f}</td>
  <td>{mib(row['pair_max_bytes']):.2f}</td>
  <td>{float(row['pair_cv']):.3f}</td>
  <td>{a_rank:.2f}</td>
  <td>{b_rank:.2f}</td>
  <td>{pad_rank:.2f}</td>
  <td>{float(row['makespan_us']):.3f}</td>
  <td>{gbps:.2f}</td>
  <td class="{'good' if gbps >= baseline else 'bad'}">{pct(gbps / baseline - 1.0)}</td>
  <td><a href="{html.escape(profile_href(row))}">{html.escape(Path(row['case']).name)}</a></td>
</tr>"""
            )
    return "\n".join(chunks)


def trend_table(grouped: dict[float, dict[str, dict[str, str]]]) -> str:
    chunks: list[str] = []
    for ratio in sorted(grouped):
        algs = grouped[ratio]
        baseline = single_rank(algs["baseline"])
        clos = single_rank(algs["closv3"])
        hmin = single_rank(algs["hybrid-min"])
        hpad = single_rank(algs["hybrid-pad80"])
        chunks.append(
            f"""<tr>
  <td>{ratio:.1f}x</td>
  <td>{baseline:.2f}</td>
  <td>{clos:.2f}</td>
  <td>{hmin:.2f}</td>
  <td>{hpad:.2f}</td>
  <td class="{'good' if hmin >= baseline else 'bad'}">{pct(hmin / baseline - 1.0)}</td>
  <td class="{'good' if hpad >= baseline else 'bad'}">{pct(hpad / baseline - 1.0)}</td>
</tr>"""
        )
    return "\n".join(chunks)


def render(grouped: dict[float, dict[str, dict[str, str]]]) -> str:
    r2 = grouped[2.0]
    baseline_2 = single_rank(r2["baseline"])
    clos_2 = single_rank(r2["closv3"])
    hmin_2 = single_rank(r2["hybrid-min"])
    hpad_2 = single_rank(r2["hybrid-pad80"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBX16 AllToAllV A+B Hybrid 实验</title>
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
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:last-child, td:last-child {{ text-align:left; }}
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
    <h1>UBX16 AllToAllV A+B Hybrid 实验</h1>
    <p class="muted">A 阶段使用 MeshClos V3 strict 单 TP / 固定平面，priority=7；B 阶段使用 Mesh1D baseline full TP，priority=6。两套 TP 可以走相同物理端口，但 tpn 在每个 node 上重新编号，保证唯一。当前实验采用 A_then_B 串行：所有 B task 依赖全部 A task。</p>
  </header>

  <div class="metrics">
    <div class="metric"><span>2.0x baseline</span><strong>{baseline_2:.2f}</strong><span>GB/s per rank</span></div>
    <div class="metric"><span>2.0x meshclos</span><strong class="bad">{clos_2:.2f}</strong><span>{pct(clos_2 / baseline_2 - 1.0)} vs baseline</span></div>
    <div class="metric"><span>2.0x hybrid-min</span><strong>{hmin_2:.2f}</strong><span>{pct(hmin_2 / baseline_2 - 1.0)} vs baseline</span></div>
    <div class="metric"><span>2.0x hybrid-pad80</span><strong class="bad">{hpad_2:.2f}</strong><span>{pct(hpad_2 / baseline_2 - 1.0)} vs baseline</span></div>
  </div>

  <div class="layout">
    <section>
      <h2>结论</h2>
      <ul>
        <li>priority namespace 的方案跑通了：同一个 case 内 A 可以使用单 TP 固定平面，B 可以使用 full TP。</li>
        <li>A_then_B 串行下，<code>hybrid-min</code> 基本贴近 baseline；这说明保守拆分不会比 baseline 明显更好，因为 B 残差阶段仍然决定尾部。</li>
        <li><code>hybrid-pad80</code> 在 1.5x/2.0x 下不如 baseline，主要原因是 padding 增加网络注入，并且 B 被串行放在 A 之后。</li>
        <li>这个结果不否定 A+B 思路，但说明有效版本不能简单 A_then_B。下一步更值得测的是 A/B overlap，或者让 B 在 A 的早期就以低优先级/独立线程启动。</li>
      </ul>
    </section>

    <section>
      <h2>趋势</h2>
      <table>
        <thead><tr><th>max/avg</th><th>baseline</th><th>meshclos</th><th>hybrid-min</th><th>hybrid-pad80</th><th>min vs baseline</th><th>pad80 vs baseline</th></tr></thead>
        <tbody>
{trend_table(grouped)}
        </tbody>
      </table>
    </section>

    <section>
      <h2>完整数据</h2>
      <table>
        <thead><tr><th>max/avg</th><th>算法</th><th>min MiB</th><th>avg MiB</th><th>max MiB</th><th>CV</th><th>A MiB/rank</th><th>B MiB/rank</th><th>pad MiB/rank</th><th>us</th><th>GB/s/rank</th><th>vs baseline</th><th>case</th></tr></thead>
        <tbody>
{summary_table(grouped)}
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
    parser.add_argument("--hybrid-summary", type=Path, default=HYBRID_SUMMARY)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()
    grouped = rows_by_ratio(load(args.baseline_summary), load(args.hybrid_summary))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(grouped))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
