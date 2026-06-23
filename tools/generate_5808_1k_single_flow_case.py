#!/usr/bin/env python3
"""Generate a 1024-rank 5808-style topology case for one same-pod flow.

The full 1k topology has 16 pods. Each pod has 64 ranks connected to 12
bottom switches and no rank-to-rank mesh links. Switches with the same local
index are fully connected across pods. For a single same-pod flow we only need
routes and transport channels for one source/destination pair, avoiding the
very large all-pair transport_channel.csv.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NS3_TOOLS = REPO_ROOT / "ns-3-ub" / "scratch" / "ns-3-ub-tools"
sys.path.insert(0, str(NS3_TOOLS))

if "tqdm" not in sys.modules:
    tqdm_module = types.ModuleType("tqdm")

    class _Tqdm:
        def __init__(self, iterable=None, *args, **kwargs):
            self.iterable = iterable

        def __iter__(self):
            return iter(self.iterable if self.iterable is not None else ())

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, n=1):
            return None

    def _tqdm(iterable=None, *args, **kwargs):
        return _Tqdm(iterable, *args, **kwargs)

    tqdm_module.tqdm = _tqdm
    sys.modules["tqdm"] = tqdm_module

import net_sim_builder as netsim  # noqa: E402


NUM_GROUPS = 16
HOSTS_PER_GROUP = 64
SWITCHES_PER_GROUP = 12
TOTAL_HOSTS = NUM_GROUPS * HOSTS_PER_GROUP
TOTAL_SWITCHES = NUM_GROUPS * SWITCHES_PER_GROUP


def write_network_attribute(path: Path, port_trace: bool) -> None:
    text = f"""ENABLE_QCN 0
ENABLE_PFC 0
ENABLE_IRN 0
ENABLE_ROCE 0
USE_DYNAMIC_PFC_THRESHOLD 1
PACKET_PAYLOAD_SIZE 1024
L2_CHUNK_SIZE 4000
L2_ACK_INTERVAL 1
L2_BACK_TO_ZERO 0
L2_TEST_READ 0
L2_TEST_WRITE 1
L2_TEST_ATOMIC 0
L2_TEST_SEND 0
L2_TEST_READ_FLUSH 0
L2_TEST_WRITE_FLUSH 0
L2_TEST_ATOMIC_FLUSH 0
L2_TEST_SEND_FLUSH 0
L2_TEST_RANDOM_SEED 1
UB_TRAFFIC_LOG_ENABLE 0
UB_PACKET_TRACE_ENABLE 0
UB_PORT_TRACE_ENABLE {1 if port_trace else 0}
UB_FLOW_TRACE_ENABLE 0
UB_PFC_TRACE_ENABLE 0
UB_QCN_TRACE_ENABLE 0
UB_RTT_TRACE_ENABLE 0
UB_RDMA_TRACE_ENABLE 0
UB_CNP_TRACE_ENABLE 0
"""
    template = REPO_ROOT / "experiments/ubx64_switch12/alltoall/cases/generated_topology_ubx64_switch12_baseline_threadserial_a2a64_0p875mibflow/network_attribute.txt"
    if template.exists():
        text = template.read_text(encoding="utf-8")
        text = text.replace('global UB_PORT_TRACE_ENABLE "false"', f'global UB_PORT_TRACE_ENABLE "{str(port_trace).lower()}"')
    (path / "network_attribute.txt").write_text(text, encoding="utf-8")


def build_graph(args: argparse.Namespace) -> netsim.NetworkSimulationGraph:
    graph = netsim.NetworkSimulationGraph()
    graph.output_dir = str(args.output_case) + "/"

    for host_id in range(TOTAL_HOSTS):
        graph.add_netisim_host(host_id, forward_delay=f"{args.forward_delay}ns")

    switch_base_id = TOTAL_HOSTS
    for switch_idx in range(TOTAL_SWITCHES):
        graph.add_netisim_node(switch_base_id + switch_idx, forward_delay=f"{args.forward_delay}ns")

    def group_switches(group_idx: int) -> list[int]:
        start = switch_base_id + group_idx * SWITCHES_PER_GROUP
        return list(range(start, start + SWITCHES_PER_GROUP))

    for group_idx in range(NUM_GROUPS):
        hosts = range(group_idx * HOSTS_PER_GROUP, (group_idx + 1) * HOSTS_PER_GROUP)
        switches = group_switches(group_idx)
        for host_id in hosts:
            for switch_id in switches:
                graph.add_netisim_edge(
                    host_id,
                    switch_id,
                    bandwidth=args.link_bandwidth,
                    delay=f"{args.host_switch_delay}ns",
                    edge_count=args.host_switch_edge_count,
                )

    for sw_idx in range(SWITCHES_PER_GROUP):
        same_index = [group_switches(group_idx)[sw_idx] for group_idx in range(NUM_GROUPS)]
        for i, sw_a in enumerate(same_index):
            for sw_b in same_index[i + 1 :]:
                graph.add_netisim_edge(
                    sw_a,
                    sw_b,
                    bandwidth=args.link_bandwidth,
                    delay=f"{args.switch_switch_delay}ns",
                    edge_count=args.inter_switch_edge_count,
                )

    graph.build_graph_config()
    return graph


def write_pair_route(graph: netsim.NetworkSimulationGraph, output_case: Path, src: int, dst: int) -> None:
    src_group = src // HOSTS_PER_GROUP
    dst_group = dst // HOSTS_PER_GROUP
    src_switch_base = TOTAL_HOSTS + src_group * SWITCHES_PER_GROUP
    dst_switch_base = TOTAL_HOSTS + dst_group * SWITCHES_PER_GROUP

    route_rows: dict[tuple[int, int, int], list[tuple[int, int]]] = {}

    def add_route(node: int, dst_node: int, dst_port: int, out_port: int, metric: int) -> None:
        route_rows.setdefault((node, dst_node, dst_port), []).append((out_port, metric))

    for sw_idx in range(SWITCHES_PER_GROUP):
        src_sw = src_switch_base + sw_idx
        dst_sw = dst_switch_base + sw_idx

        src_out_port = graph.get_link_ports(src, src_sw)[0]
        dst_port = graph.get_link_ports(dst, dst_sw)[0]

        if src_group == dst_group:
            sw_out_port = graph.get_link_ports(src_sw, dst)[0]
            metric = 1
            add_route(src, dst, dst_port, src_out_port, metric + 1)
            add_route(src_sw, dst, dst_port, sw_out_port, metric)
            graph.route_dict4tp[(src, dst)].append((src_out_port, metric + 1, dst_port))
            continue

        src_sw_to_dst_sw_ports = graph.get_link_ports(src_sw, dst_sw)
        dst_sw_out_port = graph.get_link_ports(dst_sw, dst)[0]
        for sw_out_port in src_sw_to_dst_sw_ports:
            add_route(src, dst, dst_port, src_out_port, 3)
            add_route(src_sw, dst, dst_port, sw_out_port, 2)
            add_route(dst_sw, dst, dst_port, dst_sw_out_port, 1)
            graph.route_dict4tp[(src, dst)].append((src_out_port, 3, dst_port))

    with (output_case / "routing_table.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["nodeId", "dstNodeId", "dstPortId", "outPorts", "metrics"])
        for key, values in sorted(route_rows.items()):
            out_ports = " ".join(str(v[0]) for v in values)
            metrics = " ".join(str(v[1]) for v in values)
            writer.writerow([*key, out_ports, metrics])


def write_traffic(output_case: Path, src: int, dst: int, bytes_: int, priority: int) -> None:
    with (output_case / "traffic.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "taskId",
                "sourceNodeId",
                "destNodeId",
                "dataSize(Byte)",
                "opType",
                "priority",
                "delay",
                "phaseId",
                "dependOnPhases",
            ]
        )
        writer.writerow([0, src, dst, bytes_, "URMA_WRITE", priority, "0ns", 0, ""])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-case", type=Path, required=True)
    parser.add_argument("--src", type=int, default=0)
    parser.add_argument("--dst", type=int, default=63)
    parser.add_argument("--bytes", type=int, default=1 << 30)
    parser.add_argument("--link-bandwidth", default="400Gbps")
    parser.add_argument("--host-switch-delay", type=int, default=100)
    parser.add_argument("--switch-switch-delay", type=int, default=20)
    parser.add_argument("--forward-delay", type=int, default=1)
    parser.add_argument("--host-switch-edge-count", type=int, default=1)
    parser.add_argument("--inter-switch-edge-count", type=int, default=4)
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--port-trace", action="store_true")
    args = parser.parse_args()

    if not (0 <= args.src < TOTAL_HOSTS and 0 <= args.dst < TOTAL_HOSTS):
        raise SystemExit("src/dst must be host IDs in [0, 1023]")
    if args.src == args.dst:
        raise SystemExit("src and dst must differ")

    if args.output_case.exists():
        shutil.rmtree(args.output_case)
    args.output_case.mkdir(parents=True)

    graph = build_graph(args)
    write_pair_route(graph, args.output_case, args.src, args.dst)
    graph.config_transport_channel([args.priority])
    graph.write_config()
    write_network_attribute(args.output_case, args.port_trace)
    write_traffic(args.output_case, args.src, args.dst, args.bytes, args.priority)

    print(f"output_case={args.output_case.resolve()}")
    print(f"src={args.src} dst={args.dst} bytes={args.bytes}")
    print(f"same_pod={args.src // HOSTS_PER_GROUP == args.dst // HOSTS_PER_GROUP}")
    print(f"transport_channel_rows={sum(1 for _ in open(args.output_case / 'transport_channel.csv')) - 1}")


if __name__ == "__main__":
    main()
