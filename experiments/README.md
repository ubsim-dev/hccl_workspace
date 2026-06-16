# ns-3-ub 实验目录

这个目录集中保存当前保留的拓扑、case、报告和 profiling 图。新的实验优先放到这里，避免散落在仓库根目录或 `docs/`。

## 拓扑

| 目录 | 用途 |
| --- | --- |
| `topologies/ubx16/generated_topology_ubx16` | 当前 UBX16 拓扑：16 rank，每 4 rank 一个 mesh，4 个 clos 平面。 |
| `topologies/pod64/generated_topology` | POD 64P 拓扑，后续 POD 实验保留使用。 |

## UBX16

| 目录 | 内容 |
| --- | --- |
| `ubx16/alltoall` | AllToAll 固定 16MiB/rank 的 baseline、matrix、MeshClos V3 对比。 |
| `ubx16/alltoallv/scenarios` | AllToAllV 典型场景对比：uniform、mild random、MoE dispatch/combine、cross-group-heavy。 |
| `ubx16/alltoallv/mild-random-sweep` | AllToAllV mild-random 多 seed 扫描，观察随机不均衡和链路拖尾。 |

## POD64

| 目录 | 内容 |
| --- | --- |
| `pod64/alltoall` | POD64 AllToAll baseline case、仿真输出和 rank profile。 |

## 常用入口

- AllToAllV 场景总报告：`ubx16/alltoallv/scenarios/reports/ns3ub-ubx16-alltoallv-scenarios-report.html`
- AllToAllV mild-random 扫描：`ubx16/alltoallv/mild-random-sweep/reports/ns3ub-ubx16-alltoallv-mild-random-sweep.html`
- AllToAll 算法小结：`ubx16/alltoall/notes/ns3ub-ubx16-alltoall-algorithms-summary.zh-CN.md`
- POD64 AllToAll rank0 profile：`pod64/alltoall/reports/ns3ub-pod64-baseline-threadserial-a2a64-16mb-rank0-profile.html`
