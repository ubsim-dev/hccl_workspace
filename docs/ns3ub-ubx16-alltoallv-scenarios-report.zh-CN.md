# UBX16 AllToAllV 场景仿真报告

本文比较 3 个算法在典型 AllToAllV 分布下的 ns-3-ub 结果：

- `baseline`：当前 `hccl` Mesh1D baseline，`ALLTOALLV_DIRECT_FULLMESH_CONCURRENT_SIZE = 16`，peer 内保留 full channel/TP 分片。
- `matrix`：当前 `hccl` matrix 优化版本，strict 单平面建模。
- `closv3`：`hccl-xzw` MeshClos V3 分平面优化版本，strict 单平面建模。

拓扑为 UBX16：16 rank，每 4 rank 一组；组内 3 条 mesh 直连，跨组 4 条 clos 平面。平均数据量为 16 MiB/rank。

完整 CSV：`docs/ns3ub-ubx16-alltoallv-scenarios-summary.csv`

## 场景设计

| 场景 | 含义 | MoE 对应关系 |
| --- | --- | --- |
| `uniform` | 每个 src 到每个 dst 数据量基本相等 | 理想均匀 AllToAll |
| `mild_random` | 每个 src 总量固定，peer 粒度 lognormal 轻度随机 | token 路由轻度不均衡 |
| `dispatch_hot4` | 每个 src 总量固定，4 个 hot expert/rank 接收大部分流量 | MoE dispatch 热专家 |
| `combine_hot4` | `dispatch_hot4` 的转置，hot rank 作为发送端发回数据 | MoE combine |
| `cross_group_heavy` | 每个 src 总量固定，跨组流量占比约 97% | 专家主要分布在远端组 |

## 结果汇总

全局带宽按 `sum(all task bytes) / max(task completion time)` 计算。

| 场景 | src CV | dst CV | 跨组占比 | baseline GB/s | matrix GB/s | closv3 GB/s | 最优 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| uniform | 0.00 | 0.00 | 0.80 | 3631.29 | 3844.14 | 3844.13 | matrix |
| mild_random | 0.00 | 0.25 | 0.80 | 2703.19 | 1058.79 | 1235.25 | baseline |
| dispatch_hot4 | 0.00 | 1.26 | 0.81 | 1197.55 | 1028.08 | 1035.46 | baseline |
| combine_hot4 | 1.26 | 0.00 | 0.81 | 1198.60 | 1028.08 | 1115.22 | baseline |
| cross_group_heavy | 0.00 | 0.00 | 0.97 | 3062.52 | 3173.90 | 3174.03 | closv3 |

## 主要观察

1. 均匀或跨组均匀时，matrix 和 closv3 更好。
   这两个优化版本会把跨组通信组织到 4 个 clos 平面上，uniform 和 cross-group-heavy 这种负载比较均匀的场景能稳定跑满 clos 平面。

2. V 分布有明显热点时，baseline 反而更好。
   原因不是 baseline 调度更聪明，而是当前 baseline 建模保留了 peer 内 full channel/TP 分片；一个很大的 `src->dst` 热点流可以拆到多条 channel 上。matrix/closv3 strict 建模下，一个 peer 基本绑定一个平面，单条热点大流会被 50 GB/s 级别的单平面限制住。

3. dispatch 和 combine 的瓶颈方向不同。
   `dispatch_hot4` 是目的端热点，`combine_hot4` 是源端热点。两者全局带宽接近，但 rank0 视角会差很多，所以后续不能只看 rank0 profile，需要同时看全局 makespan 和热点 rank 的 profile。

4. closv3 在 skew 场景下比 matrix 略好，但收益被单平面热点限制住。
   例如 `combine_hot4` 中 closv3 为 1115.22 GB/s，高于 matrix 的 1028.08 GB/s，但仍低于 baseline 的 1198.60 GB/s。

## 当前判断

如果真实算子坚持“每个 peer 只走一个选定平面”，matrix/closv3 在 AllToAllV skew 场景下会被热点 peer 限制；如果能对大 peer 做分片，让同一个 `src->dst` 的大消息跨多个平面/TP 发送，理论上会显著改善 MoE dispatch/combine 的长尾。

下一步建议对 `dispatch_hot4` 和 `combine_hot4` 做两类验证：

- 选 hot rank profile，看长尾到底卡在源端串行、目的端接收，还是单 clos 平面。
- 给 closv3 增加“大 peer 分片到多个平面”的 ideal/variant 建模，估算优化上限。
