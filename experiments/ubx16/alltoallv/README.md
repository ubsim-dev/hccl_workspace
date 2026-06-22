# UBX16 AllToAllV

AllToAllV 实验按场景组织：

| 目录 | 说明 |
| --- | --- |
| `scenarios` | 典型 V 分布：uniform、mild random、MoE dispatch/combine、cross-group-heavy。 |
| `mild-random-sweep` | mild-random 多 seed 扫描，量化随机不均衡对 baseline、matrix、MeshClos V3 的影响。 |
| `gradient` | 确定性等差不均衡扫描；每 rank 总发送量固定为 128MiB，按最大流 / 平均流 1.0、1.2、1.5、2.0 扫描。 |
| `distribution` | 受控近似随机分布扫描；每 rank 总发送量固定为 128MiB，rank0 发送/接收侧分别展示 min/avg/max、max/avg、max/min，只比较 baseline 和 meshclos。 |

常用报告：

- `scenarios/reports/ns3ub-ubx16-alltoallv-scenarios-report.html`
- `mild-random-sweep/reports/ns3ub-ubx16-alltoallv-mild-random-sweep.html`
- `gradient/reports/ns3ub-ubx16-alltoallv-gradient-report.html`
- `distribution/reports/ns3ub-ubx16-alltoallv-distribution-report.html`
