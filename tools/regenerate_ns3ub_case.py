#!/usr/bin/env python3
"""Regenerate ns-3-ub routing/TP files from an existing topology case.

The script treats node.csv and topology.csv as the source of truth, rebuilds a
NetworkSimulationGraph with ns-3-ub-tools/net_sim_builder.py, regenerates
routing_table.csv and transport_channel.csv, then compares them with the input
case.
"""

from __future__ import annotations

import argparse
import csv
import filecmp
import shutil
import sys
from collections import Counter, OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NS3UB_TOOLS = REPO_ROOT / "ns-3-ub" / "scratch" / "ns-3-ub-tools"
TMP_PYDEPS = Path("/private/tmp/ns3ub_pydeps")
if TMP_PYDEPS.exists():
    sys.path.insert(0, str(TMP_PYDEPS))
sys.path.insert(0, str(NS3UB_TOOLS))

import networkx as nx  # noqa: E402
import net_sim_builder as netsim  # noqa: E402


def all_shortest_paths(graph, source, target):
    try:
        return nx.all_shortest_paths(graph, source, target)
    except nx.NetworkXNoPath:
        return []


def parse_node_ids(spec: str) -> list[int]:
    spec = spec.strip()
    if ".." not in spec:
        return [int(spec)]
    start, end = spec.split("..", 1)
    return list(range(int(start), int(end) + 1))


def load_nodes(node_csv: Path) -> tuple[dict[int, tuple[str, int, str]], list[int]]:
    nodes: dict[int, tuple[str, int, str]] = {}
    with node_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            for node_id in parse_node_ids(row["nodeId"]):
                nodes[node_id] = (
                    row["nodeType"],
                    int(row["portNum"]),
                    row["forwardDelay"],
                )

    expected_ids = list(range(len(nodes)))
    actual_ids = sorted(nodes)
    if actual_ids != expected_ids:
        raise ValueError(f"node ids must be contiguous 0..N-1, got {actual_ids[:5]}..{actual_ids[-5:]}")
    return nodes, expected_ids


def load_topology_rows(topology_csv: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with topology_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "u": int(row["nodeId1"]),
                    "u_port": int(row["portId1"]),
                    "v": int(row["nodeId2"]),
                    "v_port": int(row["portId2"]),
                    "bandwidth": row["bandwidth"],
                    "delay": row["delay"],
                }
            )
    return rows


def group_topology_edges(topology_rows: list[dict[str, object]]):
    grouped: OrderedDict[tuple[int, int], dict[str, object]] = OrderedDict()
    for row in topology_rows:
        u = int(row["u"])
        v = int(row["v"])
        key = (u, v) if u <= v else (v, u)
        if key not in grouped:
            grouped[key] = {
                "u": u,
                "v": v,
                "bandwidth": row["bandwidth"],
                "delay": row["delay"],
                "edge_count": 0,
            }
        entry = grouped[key]
        if entry["bandwidth"] != row["bandwidth"] or entry["delay"] != row["delay"]:
            raise ValueError(f"mixed bandwidth/delay on parallel edge {key}")
        entry["edge_count"] = int(entry["edge_count"]) + 1
    return grouped.values()


def infer_priorities(transport_csv: Path) -> list[int]:
    if not transport_csv.exists():
        return [7, 8]
    priorities = set()
    with transport_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            priorities.add(int(row["priority"]))
    return sorted(priorities) or [7, 8]


def build_graph(case_dir: Path) -> netsim.NetworkSimulationGraph:
    nodes, node_ids = load_nodes(case_dir / "node.csv")
    topology_rows = load_topology_rows(case_dir / "topology.csv")
    graph = netsim.NetworkSimulationGraph()

    for node_id in node_ids:
        node_type, _port_num, forward_delay = nodes[node_id]
        if node_type == "DEVICE":
            graph.add_netisim_host(node_id, forward_delay=forward_delay)
        elif node_type == "SWITCH":
            graph.add_netisim_node(node_id, forward_delay=forward_delay)
        else:
            raise ValueError(f"unsupported node type {node_type!r} for node {node_id}")

    for edge in group_topology_edges(topology_rows):
        graph.add_netisim_edge(
            int(edge["u"]),
            int(edge["v"]),
            bandwidth=str(edge["bandwidth"]),
            delay=str(edge["delay"]),
            edge_count=int(edge["edge_count"]),
        )

    total_node_num = len(node_ids)
    graph.port_list = [
        (0 if nodes[node_id][0] == "DEVICE" else 1, nodes[node_id][1])
        for node_id in node_ids
    ]
    graph.link_infos = [
        (
            int(row["u"]),
            int(row["u_port"]),
            int(row["v"]),
            int(row["v_port"]),
            str(row["bandwidth"]),
            str(row["delay"]),
        )
        for row in topology_rows
    ]
    graph.link_port.clear()
    graph.next_hop_ports = [[[] for _ in range(total_node_num)] for _ in range(total_node_num)]
    for u, u_port, v, v_port, _bandwidth, _delay in graph.link_infos:
        graph.link_port[(u, v, u_port)] = [v_port]
        graph.link_port[(v, u, v_port)] = [u_port]
        graph.next_hop_ports[u][v].append(u_port)
        graph.next_hop_ports[v][u].append(v_port)

    return graph


def row_counter(path: Path, ignore_columns: set[str] | None = None) -> Counter[tuple[tuple[str, str], ...]]:
    ignore_columns = ignore_columns or set()
    rows: Counter[tuple[tuple[str, str], ...]] = Counter()
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            normalized = tuple((k, v.strip()) for k, v in row.items() if k not in ignore_columns)
            rows[normalized] += 1
    return rows


def compare_csv(name: str, old: Path, new: Path, ignore_columns: set[str] | None = None, sample_limit: int = 5):
    old_rows = row_counter(old, ignore_columns)
    new_rows = row_counter(new, ignore_columns)
    missing = old_rows - new_rows
    added = new_rows - old_rows
    old_total = sum(old_rows.values())
    new_total = sum(new_rows.values())

    print(f"\n{name}:")
    print(f"  current rows:     {old_total}")
    print(f"  regenerated rows: {new_total}")
    print(f"  missing rows:     {sum(missing.values())}")
    print(f"  added rows:       {sum(added.values())}")
    if not missing and not added:
        print("  semantic diff:    none")
    else:
        for label, diff in (("missing sample", missing), ("added sample", added)):
            print(f"  {label}:")
            for i, (row, count) in enumerate(diff.items()):
                if i >= sample_limit:
                    break
                print(f"    x{count} {dict(row)}")


def route_tuples_by_pair(routing_csv: Path) -> dict[tuple[int, int], set[tuple[int, int, int]]]:
    routes: dict[tuple[int, int], set[tuple[int, int, int]]] = {}
    with routing_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            node = int(row["nodeId"])
            dst = int(row["dstNodeId"])
            if node >= 64 or dst >= 64 or node >= dst:
                continue
            out_ports = [int(x) for x in row["outPorts"].split()]
            metrics = [int(x) for x in row["metrics"].split()]
            dst_port = int(row["dstPortId"])
            pair_routes = routes.setdefault((node, dst), set())
            for i, out_port in enumerate(out_ports):
                metric = metrics[i] if i < len(metrics) else metrics[0]
                pair_routes.add((out_port, dst_port, metric))
    return routes


def tp_tuples_by_pair(transport_csv: Path) -> dict[tuple[int, int], set[tuple[int, int, int]]]:
    channels: dict[tuple[int, int], set[tuple[int, int, int]]] = {}
    with transport_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            pair = (int(row["nodeId1"]), int(row["nodeId2"]))
            channels.setdefault(pair, set()).add(
                (int(row["portId1"]), int(row["portId2"]), int(row["metric"]))
            )
    return channels


def summarize_tp_against_routes(label: str, routing_csv: Path, transport_csv: Path, sample_limit: int = 5) -> None:
    routes = route_tuples_by_pair(routing_csv)
    channels = tp_tuples_by_pair(transport_csv)
    bad_pairs = []
    missing_total = 0
    extra_total = 0
    for pair in sorted(set(routes) | set(channels)):
        missing = routes.get(pair, set()) - channels.get(pair, set())
        extra = channels.get(pair, set()) - routes.get(pair, set())
        if missing or extra:
            bad_pairs.append((pair, missing, extra))
            missing_total += len(missing)
            extra_total += len(extra)

    print(f"\n{label} TP-vs-routing semantic check:")
    print(f"  host pairs checked: {len(set(routes) | set(channels))}")
    print(f"  mismatched pairs:   {len(bad_pairs)}")
    print(f"  missing route refs: {missing_total}")
    print(f"  extra TP refs:      {extra_total}")
    for pair, missing, extra in bad_pairs[:sample_limit]:
        print(f"  pair {pair}:")
        if missing:
            print(f"    missing sample: {sorted(missing)[:sample_limit]}")
        if extra:
            print(f"    extra sample:   {sorted(extra)[:sample_limit]}")


def copy_passthrough_files(case_dir: Path, output_dir: Path) -> None:
    for name in ("network_attribute.txt", "traffic.csv", "node_mapping.csv", "TOPOLOGY.md"):
        src = case_dir / name
        if src.exists():
            shutil.copy2(src, output_dir / name)


def summarize_exact(files: list[str], case_dir: Path, output_dir: Path) -> None:
    print("\nExact file comparison:")
    for name in files:
        old = case_dir / name
        new = output_dir / name
        if old.exists() and new.exists():
            status = "same" if filecmp.cmp(old, new, shallow=False) else "different"
        else:
            status = "missing"
        print(f"  {name}: {status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", default="generated_topology", type=Path)
    parser.add_argument("--output-dir", default=Path("generated_topology/regenerated"), type=Path)
    parser.add_argument("--workers", default=1, type=int)
    parser.add_argument(
        "--priorities",
        default=None,
        help="Comma-separated TP priorities. Defaults to priorities inferred from the current transport_channel.csv.",
    )
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    priorities = (
        [int(x) for x in args.priorities.split(",")]
        if args.priorities
        else infer_priorities(case_dir / "transport_channel.csv")
    )

    graph = build_graph(case_dir)
    graph.output_dir = str(output_dir) + "/"
    graph.gen_route_table(path_finding_algo=all_shortest_paths, multiple_workers=args.workers)
    graph.config_transport_channel(priority_list=priorities)
    graph.write_config()
    copy_passthrough_files(case_dir, output_dir)

    summarize_exact(
        ["node.csv", "topology.csv", "routing_table.csv", "transport_channel.csv"],
        case_dir,
        output_dir,
    )
    compare_csv("routing_table.csv", case_dir / "routing_table.csv", output_dir / "routing_table.csv")
    compare_csv(
        "transport_channel.csv (including TPN)",
        case_dir / "transport_channel.csv",
        output_dir / "transport_channel.csv",
    )
    compare_csv(
        "transport_channel.csv (ignoring TPN counters)",
        case_dir / "transport_channel.csv",
        output_dir / "transport_channel.csv",
        ignore_columns={"tpn1", "tpn2"},
    )
    summarize_tp_against_routes(
        "current",
        case_dir / "routing_table.csv",
        case_dir / "transport_channel.csv",
    )
    summarize_tp_against_routes(
        "regenerated",
        output_dir / "routing_table.csv",
        output_dir / "transport_channel.csv",
    )
    print(f"\nRegenerated case: {output_dir}")
    print(f"Priorities used: {priorities}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
