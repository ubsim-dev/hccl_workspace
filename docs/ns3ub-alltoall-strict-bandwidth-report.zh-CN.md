# ns-3-ub AllToAll Strict 建模带宽实验报告

日期：2026-06-11

## 结论摘要

本轮实验对比了三个模型：

- Baseline Mesh1D：`ins_temp_all_to_all_v_mesh_1D` 建模，rank-peer 数据按可用 TP 聚合。
- MeshClos V2 strict：`ins_temp_alltoall_mesh_clos_v2.cc` strict 建模，源码式单 peer 选单 channel，2D 两阶段转发。
- MeshClos V3 strict：`ins_temp_alltoall_mesh_clos_v3.cc` strict 建模，跨组按 group-pairwise/linkIdx 分散，同组仍使用 `channel[0]`。

主要结论：

1. Baseline Mesh1D 的单 rank 带宽最高，128MiB/256MiB 数据量下基本稳定在 340-404 GB/s。
2. V2 strict 明显最差，direct payload 只有约 47-53 GB/s；即使 network injection 口径也只有约 75-89 GB/s。
3. V3 strict 随 rank 数增加收益明显：16 rank 约 90 GB/s，32 rank 约 138-143 GB/s，64 rank 约 192-197 GB/s。
4. 128MiB 与 256MiB 的结果基本一致，说明当前数据量已经足够打到稳定带宽区间，不是“流量太少没打满”的主要问题。
5. V3 strict 仍显著低于之前测得的 V3 ideal 上界，说明拓扑有更高潜力，当前瓶颈主要来自 strict 的 channel 选择和同组 `channel[0]` 集中。

## 实验口径

所有 case 都关闭 port trace 和 packet trace，以减少仿真开销。带宽计算：

- `Direct GB/s`：每 rank 用户层目标通信量 / 全任务 makespan。
- `Network GB/s`：每 rank 实际写入 `traffic.csv` 的网络注入量 / 全任务 makespan。

对于 Baseline Mesh1D 和 V3 strict，`Direct GB/s == Network GB/s`。对于 V2 strict，2D 两阶段转发会产生额外网络流量，因此 `Network GB/s` 大于 `Direct GB/s`。

## Baseline Mesh1D

| Rank | 每 rank direct 数据 | Tasks | Makespan(us) | Direct GB/s | Network GB/s |
|---:|---:|---:|---:|---:|---:|
| 16 | 128MiB | 240 | 335.034 | 400.61 | 400.61 |
| 16 | 256MiB | 240 | 663.909 | 404.33 | 404.33 |
| 32 | 128MiB | 992 | 377.368 | 355.67 | 355.67 |
| 32 | 256MiB | 992 | 718.461 | 373.63 | 373.63 |
| 64 | 128MiB | 4032 | 392.823 | 341.67 | 341.67 |
| 64 | 256MiB | 4032 | 784.706 | 342.08 | 342.08 |

观察：Baseline 能稳定利用多 TP，rank 增大后带宽略降，但仍维持 340 GB/s 以上。

## MeshClos V2 Strict

| Rank | 每 rank direct 数据 | Tasks | Makespan(us) | Direct GB/s | Network GB/s |
|---:|---:|---:|---:|---:|---:|
| 16 | 128MiB | 256 | 2601.402 | 51.59 | 75.67 |
| 16 | 256MiB | 256 | 5202.856 | 51.59 | 75.67 |
| 32 | 128MiB | 640 | 2520.303 | 53.25 | 89.33 |
| 32 | 256MiB | 640 | 5038.709 | 53.27 | 89.36 |
| 64 | 128MiB | 1792 | 2837.033 | 47.31 | 84.11 |
| 64 | 256MiB | 1792 | 5674.536 | 47.31 | 84.10 |

观察：V2 strict 的 direct payload 带宽基本被限制在 50 GB/s 左右。256MiB 的 makespan 几乎是 128MiB 的 2 倍，说明不是小流量启动开销导致，而是结构性瓶颈。

## MeshClos V3 Strict

| Rank | 每 rank direct 数据 | Tasks | Makespan(us) | Direct GB/s | Network GB/s |
|---:|---:|---:|---:|---:|---:|
| 16 | 128MiB | 240 | 1487.565 | 90.23 | 90.23 |
| 16 | 256MiB | 240 | 2975.088 | 90.23 | 90.23 |
| 32 | 128MiB | 992 | 938.639 | 142.99 | 142.99 |
| 32 | 256MiB | 992 | 1943.520 | 138.12 | 138.12 |
| 64 | 128MiB | 4032 | 682.922 | 196.53 | 196.53 |
| 64 | 256MiB | 4032 | 1399.651 | 191.79 | 191.79 |

观察：V3 strict 的收益随 rank 数增加非常明显。原因是跨组 peer 占比增加，V3 的 group-pairwise/linkIdx 分散机制开始更充分地使用 8 条跨组 channel。但同组流量仍集中在 `channel[0]`，所以仍低于 topology ideal 上限。

## V3 Ideal 参考上界

| Rank | 每 rank direct 数据 | Tasks | Makespan(us) | Direct GB/s |
|---:|---:|---:|---:|---:|
| 16 | 256MiB | 240 | 631.716 | 424.93 |
| 32 | 256MiB | 992 | 597.701 | 449.11 |
| 64 | 128MiB | 4032 | 340.600 | 394.06 |

这组不是 strict 源码建模，只是保留每个 pair 的全部 TP 作为拓扑上界参考。它说明当前拓扑并非没有带宽潜力，strict 模型低带宽主要来自算法/channel 选择。

## 解释

从结果看，节点数增加确实让优化版本看到收益，尤其是 V3：

- 16 rank 时只有 2 个 8 卡组，跨组 peer 占比为 `8/15`。
- 32 rank 时为 4 组，跨组 peer 占比为 `24/31`。
- 64 rank 时为 8 组，跨组 peer 占比为 `56/63`。

V3 的跨组调度会把不同 peer 分散到不同 `linkIdx`，因此跨组占比越高，收益越明显。V2 虽然也有 inter channel hash，但两阶段 2D decomposition 带来额外网络流量，且 strict 下单 peer 单 channel 的瓶颈仍然强，所以 direct 带宽没有改善到可接受水平。

## 产物位置

关键 case 目录：

- `generated_topology_hccl_mesh1d_strict_a2a{16,32,64}_{128mb,256mb}`
- `generated_topology_hccl_meshclos2d_v2_strict_a2a{16,32,64}_{128mb,256mb}`
- `generated_topology_hccl_meshclos2d_v3_strict_a2a{16,32,64}_{128mb,256mb}`

HTML 报告：

- `docs/ns3ub-alltoall-strict-bandwidth-report.zh-CN.html`
