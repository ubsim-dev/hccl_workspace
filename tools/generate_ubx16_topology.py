#!/usr/bin/env python3
"""Generate a 16-rank UBX-like ns-3-ub base topology.

Topology:
  - 16 ranks, grouped as 4 ranks per mesh group.
  - Each rank has 3 direct in-group mesh links.
  - Each rank has 4 clos links, one to each plane switch.
  - Switches 16..19 represent the 4 clos planes.

The generated shortest-path routing yields:
  - in-group rank pairs: one direct mesh TP.
  - cross-group rank pairs: four switch-plane TPs.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import types
from pathlib import Path

import networkx as nx


REPO_ROOT = Path(__file__).resolve().parents[1]
NS3_TOOLS = REPO_ROOT / "ns-3-ub" / "scratch" / "ns-3-ub-tools"
DEFAULT_NETWORK_ATTR = REPO_ROOT / "generated_topology" / "network_attribute.txt"

sys.path.insert(0, str(NS3_TOOLS))
try:
    import tqdm  # noqa: F401
except ModuleNotFoundError:
    tqdm_fallback = types.ModuleType("tqdm")

    def _tqdm(iterable=None, *args, **kwargs):
        if iterable is None:
            class _NoopProgress:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def update(self, _=1):
                    return None

            return _NoopProgress()
        return iterable

    tqdm_fallback.tqdm = _tqdm
    sys.modules["tqdm"] = tqdm_fallback
import net_sim_builder as netsim  # noqa: E402


def all_shortest_paths(graph: nx.Graph, source: int, target: int):
    try:
        return nx.all_shortest_paths(graph, source, target)
    except nx.NetworkXNoPath:
        return []


def patch_network_attributes(path: Path, enable_port_trace: bool, disable_packet_trace: bool) -> None:
    lines = path.read_text().splitlines()
    patched: list[str] = []
    seen_port = False
    seen_packet = False
    for line in lines:
        if line.startswith('global UB_PORT_TRACE_ENABLE '):
            patched.append(f'global UB_PORT_TRACE_ENABLE "{str(enable_port_trace).lower()}"')
            seen_port = True
        elif line.startswith('global UB_RECORD_PKT_TRACE ') and disable_packet_trace:
            patched.append('global UB_RECORD_PKT_TRACE "false"')
            seen_packet = True
        else:
            patched.append(line)
    if not seen_port:
        patched.append(f'global UB_PORT_TRACE_ENABLE "{str(enable_port_trace).lower()}"')
    if disable_packet_trace and not seen_packet:
        patched.append('global UB_RECORD_PKT_TRACE "false"')
    path.write_text("\n".join(patched) + "\n")


def build_topology(
    rank_count: int,
    group_size: int,
    plane_count: int,
    bandwidth: str,
    mesh_delay: str,
    clos_delay: str,
) -> netsim.NetworkSimulationGraph:
    if rank_count % group_size != 0:
        raise ValueError("rank-count must be divisible by group-size")

    graph = netsim.NetworkSimulationGraph()
    for rank in range(rank_count):
        graph.add_netisim_host(rank, forward_delay="1ns")

    first_switch = rank_count
    for plane in range(plane_count):
        graph.add_netisim_node(first_switch + plane, forward_delay="1ns")

    # Add mesh links first so host ports 0..group_size-2 are in-group links.
    for group_base in range(0, rank_count, group_size):
        group_ranks = list(range(group_base, group_base + group_size))
        for i, src in enumerate(group_ranks):
            for dst in group_ranks[i + 1 :]:
                graph.add_netisim_edge(src, dst, bandwidth=bandwidth, delay=mesh_delay, edge_count=1)

    # Then add one clos uplink per plane.
    for rank in range(rank_count):
        for plane in range(plane_count):
            graph.add_netisim_edge(
                rank,
                first_switch + plane,
                bandwidth=bandwidth,
                delay=clos_delay,
                edge_count=1,
            )

    return graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output-case", type=Path, default=REPO_ROOT / "generated_topology_ubx16")
    parser.add_argument("--rank-count", type=int, default=16)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--plane-count", type=int, default=4)
    parser.add_argument("--bandwidth", default="400Gbps")
    parser.add_argument("--mesh-delay", default="1ns")
    parser.add_argument("--clos-delay", default="1ns")
    parser.add_argument("--priority", type=int, nargs="+", default=[7])
    parser.add_argument("--network-attribute", type=Path, default=DEFAULT_NETWORK_ATTR)
    parser.add_argument("--no-port-trace", action="store_true")
    parser.add_argument("--disable-packet-trace", action="store_true", default=True)
    args = parser.parse_args()

    output_case = args.output_case.resolve()
    graph = build_topology(
        rank_count=args.rank_count,
        group_size=args.group_size,
        plane_count=args.plane_count,
        bandwidth=args.bandwidth,
        mesh_delay=args.mesh_delay,
        clos_delay=args.clos_delay,
    )
    graph.output_dir = str(output_case)
    graph.build_graph_config()
    graph.gen_route_table(path_finding_algo=all_shortest_paths, multiple_workers=1)
    graph.config_transport_channel(priority_list=args.priority)
    graph.write_config()

    shutil.copy2(args.network_attribute.resolve(), output_case / "network_attribute.txt")
    patch_network_attributes(
        output_case / "network_attribute.txt",
        enable_port_trace=not args.no_port_trace,
        disable_packet_trace=args.disable_packet_trace,
    )

    print(f"output_case={output_case}")
    print(
        f"ranks={args.rank_count} group_size={args.group_size} "
        f"planes={args.plane_count} bandwidth={args.bandwidth}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
