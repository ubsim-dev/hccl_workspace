# UBX16 AllToAll

当前保留 3 类主要结果：

| 算法 | 说明 | 典型 profile |
| --- | --- | --- |
| baseline | 当前 HCCL Mesh1D baseline，16 并发，full TP 建模。 | `reports/ns3ub-ubx16-baseline-threadserial-a2a16-16mb-rank0-profile.html` |
| matrix | HCCL matrix 优化建模。 | `reports/ns3ub-ubx16-hccl-matrix-strict-roundbarrier-a2a16-16mb-rank0-profile.html` |
| MeshClos V3 | 分平面 MeshClos V3 strict/thread-serial 建模。 | `reports/ns3ub-ubx16-v3-strict-threadserial-a2a16-16mb-rank0-profile.html` |

目录说明：

- `cases/`：ns-3-ub case 目录。
- `reports/`：HTML profiling 图。
- `notes/`：算法和建模说明。

入口文档：`notes/ns3ub-ubx16-alltoall-algorithms-summary.zh-CN.md`。
