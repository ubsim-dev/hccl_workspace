#!/usr/bin/env python3
"""Generate the 8-plane 1024-NPU topology.

The script writes node.csv and topology.csv for a custom graph:

- 1024 NPU hosts.
- 8 planes.
- 16 pods per plane.
- 8 L1 switches per pod per plane.
- 2 L2 switches per pod per plane.
- 4 5808 switches per plane, grouped as two logical 5808 pairs.
- Full-mesh links inside every 8-NPU group, e.g. 0..7, 8..15, ...

By default it does not generate routing_table.csv. Full all-host routing for 1024
hosts is large and should be generated only for a bounded workload or by the
repo toolchain after scratch/ns-3-ub-tools is restored.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path


HOST_COUNT = 1024
PLANE_COUNT = 8
POD_COUNT = 16
NPUS_PER_POD = 64
L1_PER_POD_PER_PLANE = 8
L2_PER_POD_PER_PLANE = 2
M5808_PER_PLANE = 4

HOST_PLANE_PORTS = 8
HOST_FULLMESH_PORTS = 7
HOST_PORTS = HOST_PLANE_PORTS + HOST_FULLMESH_PORTS
L1_DOWN_PORTS = 8
L1_L2_UP_PORTS = 4
L1_M5808_UP_PORTS = 4
L1_PORTS = L1_DOWN_PORTS + L1_L2_UP_PORTS + L1_M5808_UP_PORTS
L2_PORTS = 16
M5808_PORTS = 128

DEFAULT_BANDWIDTH = "400Gbps"
DEFAULT_DELAY = "20ns"
DEFAULT_FORWARD_DELAY = "1ns"


@dataclass(frozen=True)
class NodeRanges:
    host_start: int
    l1_start: int
    l2_start: int
    m5808_start: int
    total_nodes: int


def l1_count() -> int:
    return PLANE_COUNT * POD_COUNT * L1_PER_POD_PER_PLANE


def l2_count() -> int:
    return PLANE_COUNT * POD_COUNT * L2_PER_POD_PER_PLANE


def m5808_count() -> int:
    return PLANE_COUNT * M5808_PER_PLANE


def ranges() -> NodeRanges:
    l1_start = HOST_COUNT
    l2_start = l1_start + l1_count()
    m5808_start = l2_start + l2_count()
    total_nodes = m5808_start + m5808_count()
    return NodeRanges(
        host_start=0,
        l1_start=l1_start,
        l2_start=l2_start,
        m5808_start=m5808_start,
        total_nodes=total_nodes,
    )


def npu_id(pod: int, group: int, local: int) -> int:
    """NPU id inside pod/group.

    Each pod has 8 NPU groups. Each group has 8 NPUs. One L1 per plane serves
    one group of 8 NPUs.
    """
    return pod * NPUS_PER_POD + group * L1_DOWN_PORTS + local


def npu_fullmesh_port(local: int, peer_local: int) -> int:
    """Dedicated host port used for the peer in the same 8-NPU group."""
    if local == peer_local:
        raise ValueError("fullmesh peer must differ from local")
    peer_index = peer_local if peer_local < local else peer_local - 1
    return HOST_PLANE_PORTS + peer_index


def l1_id(r: NodeRanges, plane: int, pod: int, group: int) -> int:
    index = (plane * POD_COUNT + pod) * L1_PER_POD_PER_PLANE + group
    return r.l1_start + index


def l2_id(r: NodeRanges, plane: int, pod: int, l2_idx: int) -> int:
    index = (plane * POD_COUNT + pod) * L2_PER_POD_PER_PLANE + l2_idx
    return r.l2_start + index


def m5808_id(r: NodeRanges, plane: int, m5808_idx: int) -> int:
    return r.m5808_start + plane * M5808_PER_PLANE + m5808_idx


def validate_static_spec() -> None:
    assert HOST_COUNT == POD_COUNT * NPUS_PER_POD
    assert HOST_PLANE_PORTS == PLANE_COUNT
    assert HOST_FULLMESH_PORTS == L1_DOWN_PORTS - 1
    assert HOST_PORTS == 15
    assert L1_PORTS == 16
    assert L1_DOWN_PORTS == 8
    assert L1_L2_UP_PORTS == 4
    assert L1_M5808_UP_PORTS == 4
    assert L2_PORTS == L1_PER_POD_PER_PLANE * 2
    assert M5808_PORTS == POD_COUNT * L1_PER_POD_PER_PLANE
    assert l1_count() == 1024
    assert l2_count() == 256
    assert m5808_count() == 32


def add_link(
    links: list[tuple[int, int, int, int, str, str]],
    used_ports: dict[int, set[int]],
    a: int,
    a_port: int,
    b: int,
    b_port: int,
    bandwidth: str,
    delay: str,
) -> None:
    if a_port in used_ports.setdefault(a, set()):
        raise ValueError(f"duplicate port: node {a} port {a_port}")
    if b_port in used_ports.setdefault(b, set()):
        raise ValueError(f"duplicate port: node {b} port {b_port}")
    used_ports[a].add(a_port)
    used_ports[b].add(b_port)
    links.append((a, a_port, b, b_port, bandwidth, delay))


def build_links(
    bandwidth: str,
    delay: str,
) -> tuple[list[tuple[int, int, int, int, str, str]], dict[int, set[int]]]:
    r = ranges()
    links: list[tuple[int, int, int, int, str, str]] = []
    used_ports: dict[int, set[int]] = {}

    # NPU -> L1. Each NPU has 8 ports, one per plane. Within each pod, every
    # plane has 8 L1 switches and each L1 serves the same 8-NPU group.
    for pod in range(POD_COUNT):
        for group in range(L1_PER_POD_PER_PLANE):
            for local in range(L1_DOWN_PORTS):
                host = npu_id(pod, group, local)
                for plane in range(PLANE_COUNT):
                    add_link(
                        links,
                        used_ports,
                        host,
                        plane,
                        l1_id(r, plane, pod, group),
                        local,
                        bandwidth,
                        delay,
                    )

    # NPU full mesh inside each 8-NPU group, e.g. 0..7. These direct links use
    # host ports 8..14 and the same bandwidth/delay as the rest of the topology.
    for pod in range(POD_COUNT):
        for group in range(L1_PER_POD_PER_PLANE):
            for local_a in range(L1_DOWN_PORTS):
                host_a = npu_id(pod, group, local_a)
                for local_b in range(local_a + 1, L1_DOWN_PORTS):
                    host_b = npu_id(pod, group, local_b)
                    add_link(
                        links,
                        used_ports,
                        host_a,
                        npu_fullmesh_port(local_a, local_b),
                        host_b,
                        npu_fullmesh_port(local_b, local_a),
                        bandwidth,
                        delay,
                    )

    # L1 -> L2. Each L1 spends 4 ports on 2 L2 switches: 2 links to each L2.
    # Each L2 has 16 ports: 8 L1 * 2 links.
    for plane in range(PLANE_COUNT):
        for pod in range(POD_COUNT):
            for group in range(L1_PER_POD_PER_PLANE):
                l1 = l1_id(r, plane, pod, group)
                for l2_idx in range(L2_PER_POD_PER_PLANE):
                    l2 = l2_id(r, plane, pod, l2_idx)
                    for parallel_idx in range(2):
                        l1_port = L1_DOWN_PORTS + l2_idx * 2 + parallel_idx
                        l2_port = group * 2 + parallel_idx
                        add_link(links, used_ports, l1, l1_port, l2, l2_port, bandwidth, delay)

    # L1 -> 5808. Per plane, 4 physical 5808 switches form two logical pairs:
    # ports 12,13 connect pair 0; ports 14,15 connect pair 1. Therefore each L1
    # reaches all 4 physical 5808 switches with one 400G link each.
    for plane in range(PLANE_COUNT):
        for pod in range(POD_COUNT):
            for group in range(L1_PER_POD_PER_PLANE):
                l1 = l1_id(r, plane, pod, group)
                global_l1_in_plane = pod * L1_PER_POD_PER_PLANE + group
                for m_idx in range(M5808_PER_PLANE):
                    add_link(
                        links,
                        used_ports,
                        l1,
                        L1_DOWN_PORTS + L1_L2_UP_PORTS + m_idx,
                        m5808_id(r, plane, m_idx),
                        global_l1_in_plane,
                        bandwidth,
                        delay,
                    )

    return links, used_ports


def assert_ports(used_ports: dict[int, set[int]]) -> None:
    r = ranges()

    for host in range(HOST_COUNT):
        ports = used_ports.get(host, set())
        if ports != set(range(HOST_PORTS)):
            raise ValueError(f"NPU {host} ports wrong: {sorted(ports)}")

    for node in range(r.l1_start, r.l2_start):
        ports = used_ports.get(node, set())
        if ports != set(range(L1_PORTS)):
            raise ValueError(f"L1 {node} ports wrong: {sorted(ports)}")

    for node in range(r.l2_start, r.m5808_start):
        ports = used_ports.get(node, set())
        if ports != set(range(L2_PORTS)):
            raise ValueError(f"L2 {node} ports wrong: {sorted(ports)}")

    for node in range(r.m5808_start, r.total_nodes):
        ports = used_ports.get(node, set())
        if ports != set(range(M5808_PORTS)):
            raise ValueError(f"5808 {node} ports wrong: {sorted(ports)}")


def write_node_csv(output_dir: Path, forward_delay: str) -> None:
    r = ranges()
    rows = [
        ("0..1023", "DEVICE", HOST_PORTS, "", forward_delay),
        (f"{r.l1_start}..{r.l2_start - 1}", "SWITCH", L1_PORTS, "", forward_delay),
        (f"{r.l2_start}..{r.m5808_start - 1}", "SWITCH", L2_PORTS, "", forward_delay),
        (f"{r.m5808_start}..{r.total_nodes - 1}", "SWITCH", M5808_PORTS, "", forward_delay),
    ]
    with (output_dir / "node.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["nodeId", "nodeType", "portNum", "forwardDelay", "allocationDelay"])
        writer.writerows(rows)


def write_topology_csv(
    output_dir: Path,
    links: list[tuple[int, int, int, int, str, str]],
) -> None:
    with (output_dir / "topology.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["nodeId1", "portId1", "nodeId2", "portId2", "bandwidth", "delay"])
        writer.writerows(links)


def write_topology_summary(output_dir: Path, link_count: int) -> None:
    r = ranges()
    text = f"""# 8-plane 1024-NPU topology

Generated files:

- `node.csv`
- `topology.csv`

Counts:

- NPU hosts: {HOST_COUNT}
- Planes: {PLANE_COUNT}
- Pods: {POD_COUNT}
- L1 switches: {l1_count()}
- L2 switches: {l2_count()}
- 5808 switches: {m5808_count()}
- Total nodes: {r.total_nodes}
- Physical links: {link_count}

Node ID ranges:

- NPU: 0..1023
- L1: {r.l1_start}..{r.l2_start - 1}
- L2: {r.l2_start}..{r.m5808_start - 1}
- 5808: {r.m5808_start}..{r.total_nodes - 1}

Port model:

- NPU: 15 ports. Ports 0..7 go to the eight planes; ports 8..14 form the
  full mesh inside the local 8-NPU group.
- L1: ports 0..7 down to NPUs, 8..11 to L2, 12..15 to 5808.
- L2: 16 ports, two links from each of 8 L1 in one pod/plane.
- 5808: 128 ports, one link from each L1 in one plane.

5808 interpretation:

- Per plane, four physical 5808 switches form two logical pairs.
- Each L1 connects to both logical pairs.
- Equivalently, each L1 reaches all four physical 5808 switches with one 400G port each.

Routing note:

- This script intentionally does not emit full `routing_table.csv`.
- For 1024 hosts, all-pairs route generation can be large.
- Generate route entries from the actual workload or restore `scratch/ns-3-ub-tools`
  and use its route generator when full all-pairs routing is really needed.
"""
    (output_dir / "TOPOLOGY.md").write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory for generated CSV files.",
    )
    parser.add_argument("--bandwidth", default=DEFAULT_BANDWIDTH)
    parser.add_argument("--delay", default=DEFAULT_DELAY)
    parser.add_argument("--forward-delay", default=DEFAULT_FORWARD_DELAY)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    validate_static_spec()
    links, used_ports = build_links(args.bandwidth, args.delay)
    assert_ports(used_ports)
    write_node_csv(output_dir, args.forward_delay)
    write_topology_csv(output_dir, links)
    write_topology_summary(output_dir, len(links))

    r = ranges()
    print(f"output_dir={output_dir}")
    print(f"hosts={HOST_COUNT} l1={l1_count()} l2={l2_count()} m5808={m5808_count()}")
    print(f"nodes={r.total_nodes} links={len(links)}")
    print("wrote node.csv topology.csv TOPOLOGY.md")


if __name__ == "__main__":
    main()