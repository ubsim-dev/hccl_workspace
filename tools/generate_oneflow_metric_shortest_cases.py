#!/usr/bin/env python3
"""Generate one-flow 0->1 cases for routing metric / shortest-path checks."""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path


COPY_FILES = ("node.csv", "topology.csv", "transport_channel.csv")
TRAFFIC_HEADER = [
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


def parse_size(value: str) -> int:
    text = value.strip().upper()
    units = {"B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2, "G": 1024**3, "GB": 1024**3}
    for suffix, scale in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)].strip()) * scale)
    return int(text)


def patch_network_attributes(src: Path, dst: Path, use_shortest_paths: bool, enable_multipath: bool) -> None:
    patched: list[str] = []
    shortest_keys = (
        "default ns3::UbApp::UseShortestPaths ",
        "default ns3::UbTransportChannel::UseShortestPaths ",
        "default ns3::UbLdstApi::UseShortestPaths ",
    )
    multipath_key = "default ns3::UbApp::EnableMultiPath "
    seen = {key: False for key in shortest_keys}
    seen_multipath = False
    for line in src.read_text().splitlines():
        replaced = False
        if line.startswith(multipath_key):
            patched.append(f'{multipath_key}"{str(enable_multipath).lower()}"')
            seen_multipath = True
            continue
        for key in shortest_keys:
            if line.startswith(key):
                patched.append(f'{key}"{str(use_shortest_paths).lower()}"')
                seen[key] = True
                replaced = True
                break
        if replaced:
            continue
        if line.startswith('global UB_PORT_TRACE_ENABLE '):
            patched.append('global UB_PORT_TRACE_ENABLE "true"')
        elif line.startswith('global UB_PACKET_TRACE_ENABLE '):
            patched.append('global UB_PACKET_TRACE_ENABLE "false"')
        elif line.startswith('global UB_RECORD_PKT_TRACE '):
            patched.append('global UB_RECORD_PKT_TRACE "false"')
        else:
            patched.append(line)
    for key, value in seen.items():
        if not value:
            patched.append(f'{key}"{str(use_shortest_paths).lower()}"')
    if not seen_multipath:
        patched.append(f'{multipath_key}"{str(enable_multipath).lower()}"')
    dst.write_text("\n".join(patched) + "\n")


def write_routing_table(src: Path, dst: Path, equal_metrics: bool) -> None:
    with src.open(newline="") as f_in, dst.open("w", newline="") as f_out:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"{src} has no header")
        writer = csv.DictWriter(f_out, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if row["nodeId"] == "0" and row["dstNodeId"] == "1":
                row["metrics"] = "1" if equal_metrics else ("1" if row["outPorts"] == "8" else "4")
            writer.writerow(row)


def write_traffic(dst: Path, data_size: int, priority: int, second_priority: int | None = None) -> None:
    with dst.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER)
        writer.writeheader()
        priorities = [priority]
        if second_priority is not None:
            priorities.append(second_priority)
        for task_id, task_priority in enumerate(priorities):
            writer.writerow(
                {
                    "taskId": task_id,
                    "sourceNodeId": 0,
                    "destNodeId": 1,
                    "dataSize(Byte)": data_size,
                    "opType": "URMA_WRITE",
                    "priority": task_priority,
                    "delay": "0ns",
                    "phaseId": 0,
                    "dependOnPhases": "",
                }
            )


def generate_case(
    source_case: Path,
    output_case: Path,
    equal_metrics: bool,
    use_shortest_paths: bool,
    data_size: int,
    priority: int,
    second_priority: int | None,
    empty_tp: bool,
    enable_multipath: bool,
) -> None:
    output_case.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        shutil.copy2(source_case / name, output_case / name)
    if empty_tp:
        with (source_case / "transport_channel.csv").open(newline="") as f_in:
            reader = csv.reader(f_in)
            header = next(reader)
        with (output_case / "transport_channel.csv").open("w", newline="") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(header)
    write_routing_table(source_case / "routing_table.csv", output_case / "routing_table.csv", equal_metrics)
    patch_network_attributes(
        source_case / "network_attribute.txt",
        output_case / "network_attribute.txt",
        use_shortest_paths,
        enable_multipath,
    )
    write_traffic(output_case / "traffic.csv", data_size, priority, second_priority)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-case", type=Path, default=Path("generated_topology_a2a16_256mb"))
    parser.add_argument("--output-root", type=Path, default=Path("."))
    parser.add_argument("--data-size", default="128MB")
    parser.add_argument("--priority", type=int, default=7)
    parser.add_argument("--second-priority", type=int)
    parser.add_argument("--empty-tp", action="store_true")
    parser.add_argument("--disable-multipath", action="store_true")
    args = parser.parse_args()

    data_size = parse_size(args.data_size)
    for metric_name, equal_metrics in (("equal", True), ("different", False)):
        for sp_name, use_shortest_paths in (("sp_on", True), ("sp_off", False)):
            suffix = "_emptytp" if args.empty_tp else ""
            if args.second_priority is not None:
                suffix += f"_pri{args.priority}_{args.second_priority}"
            if args.disable_multipath:
                suffix += "_singlepath"
            case = args.output_root / f"generated_topology_oneflow_0_1_metric_{metric_name}_{sp_name}_{args.data_size.lower()}{suffix}"
            generate_case(
                args.source_case,
                case,
                equal_metrics,
                use_shortest_paths,
                data_size,
                args.priority,
                args.second_priority,
                args.empty_tp,
                not args.disable_multipath,
            )
            print(case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
