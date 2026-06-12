# UBX16 AllToAll 算法与 ns-3-ub 仿真小结

本文记录当前保留的 3 组 UBX16 AllToAll/AllToAllV 建模结果。仿真拓扑为真实 UBX16 抽象：16 rank，每 4 rank 一个 mesh 组；每张卡有 3 条组内 mesh 直连端口，以及 4 条跨组 clos 平面端口。数据量为 16 MiB/rank，profile 图均展示 rank 0 的任务时间线。

## 结果汇总

| 算法 | case | rank0 makespan | rank0 direct BW | profile |
| --- | --- | ---: | ---: | --- |
| HCCL matrix baseline | `generated_topology_ubx16_hccl_matrix_strict_roundbarrier_a2a16_16mb` | 69.829 us | 240.26 GB/s | [HTML](ns3ub-ubx16-hccl-matrix-strict-roundbarrier-a2a16-16mb-rank0-profile.html) |
| MeshClos V3 | `generated_topology_ubx16_hccl_meshclos2d_v3_strict_threadserial_a2a16_16mb` | 69.830 us | 240.26 GB/s | [HTML](ns3ub-ubx16-v3-strict-threadserial-a2a16-16mb-rank0-profile.html) |
| Mesh1D old baseline | `generated_topology_ubx16_hccl_mesh1d_threadserial_a2a16_16mb` | 88.785 us | 188.96 GB/s | [HTML](ns3ub-ubx16-mesh1d-threadserial-a2a16-16mb-rank0-profile.html) |

## 算法形态

### HCCL matrix baseline

对应当前 `hccl` 仓库里的 UBX matrix 实现。UBX16 被视为 4x4 矩阵，每轮 rank 0 同时发 5 条流：1 条组内 mesh 流，加 4 条跨组 clos 平面流。rank 0 一共 3 轮完成对 15 个 peer 的发送。

建模时使用 round barrier，因为当前 matrix 路径源码里有按通信轮次同步的行为。profile 上能看到每轮 4 条 clos 平面基本同时跑满，mesh 槽位每轮只承担一个组内 peer。

### MeshClos V3

对应 `hccl-xzw` 里的 MeshClos V3 优化算法建模。它把组内 mesh 任务和跨组 clos 任务拆成独立并行单元：rank 0 有 3 个 mesh thread 和 4 个 clos thread。每个 clos thread 串行发送 3 个跨组目标，4 个 clos 平面并发工作。

这个模型下，clos 是主要瓶颈；3 条 mesh 直连只在开头一段时间有任务，完成后基本空闲。最终性能与当前 HCCL matrix baseline 非常接近，都是单 TP/单平面 strict 口径下约 240 GB/s。

### Mesh1D old baseline

对应 `hccl-xzw` 中较早的 Mesh1D 风格基线。它不是按 UBX 的 1 mesh + 4 clos 平面结构来组织通信，而是用 4 个逻辑并行 slot 分摊 peer。结果里可以看到不同 slot 的任务耗时差异较大，链路/路径利用更不均衡。

在当前 UBX16 strict 建模下，这版 rank0 带宽约 188.96 GB/s，明显低于 matrix baseline 和 MeshClos V3。

## 当前结论

在 strict 单 TP/单平面口径下，HCCL matrix baseline 和 MeshClos V3 都已经把 4 条 clos 平面基本打满，rank0 direct 带宽都在 240.26 GB/s 左右。MeshClos V3 的核心收益不是让单条 peer 流使用 full TP，而是把不同跨组 peer 映射到不同 clos 平面并发发送，从而提高 AllToAll 聚合吞吐。

因此，当前 16 MiB/rank 的 V3 和 matrix 结果已经比较接近网络极限；继续增加每 rank 数据量大概率主要拉长 makespan，带宽不会显著上升。真正还能讨论的优化点更多在 HBM copy、HCCL buffer、真实执行空泡和算子流水，而不是 clos 网络本身。
