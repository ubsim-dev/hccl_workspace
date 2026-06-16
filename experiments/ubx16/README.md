# UBX16 实验

UBX16 拓扑：16 rank，每 4 rank 一个 mesh 组；每张卡有 3 条组内 mesh 直连端口和 4 条跨组 clos 平面端口。

| 目录 | 说明 |
| --- | --- |
| `alltoall` | AllToAll 算法对比和 profile。 |
| `alltoallv` | AllToAllV 场景化实验和随机扫描。 |

基础拓扑在 `../topologies/ubx16/generated_topology_ubx16`。
