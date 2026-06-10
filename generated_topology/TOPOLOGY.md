# 8-plane 64-NPU slice

Generated from the 1024-NPU 8-plane topology script, not by manual CSV editing.

Source case:

- `../20260509-8plane-1024npu-topology`

Slice rule:

- Keep original NPU hosts `0..63`.
- Keep every switch directly related to those hosts: their L1 switches, those
  L1 switches' L2 switches, and those L1 switches' 5808 switches.
- Keep links among retained nodes only.
- Compact node IDs for ns-3 runtime; see `node_mapping.csv` for original IDs.

Counts:

- Hosts: 64
- L1 switches: 64
- L2 switches: 16
- 5808 switches: 32
- Total nodes: 176
- Physical links: 1248
- Host fullmesh links: 224

Routing:

- Same 8-NPU group uses direct fullmesh shortest path.
- Other host pairs ignore host-host fullmesh as transit and route through L1/L2/5808.

Link model:

- Link bandwidth: `400Gbps`.
- Link delay: `1ns`.
