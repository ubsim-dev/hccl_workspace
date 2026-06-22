# UBX16 AllToAll 算法与 ns-3-ub 仿真小结

本文记录当前保留的 3 组 UBX16 AllToAll 建模结果。仿真拓扑为真实 UBX16 抽象：16 rank，每 4 rank 一个 mesh 组；每张卡有 3 条组内 mesh 直连端口，以及 4 条跨组 clos 平面端口。数据量为 16 MiB/rank，profile 图均展示 rank 0 的任务时间线。

## 结果汇总

| 算法 | case | rank0 makespan | rank0 direct BW | profile |
| --- | --- | ---: | ---: | --- |
| HCCL Mesh1D baseline | `generated_topology_ubx16_hccl_baseline_threadserial_a2a16_16mb` | 73.186 us | 229.24 GB/s | [HTML](../reports/ns3ub-ubx16-baseline-threadserial-a2a16-16mb-rank0-profile.html) |
| HCCL matrix optimized | `generated_topology_ubx16_hccl_matrix_strict_roundbarrier_a2a16_16mb` | 69.829 us | 240.26 GB/s | [HTML](../reports/ns3ub-ubx16-hccl-matrix-strict-roundbarrier-a2a16-16mb-rank0-profile.html) |
| MeshClos V3 optimized | `generated_topology_ubx16_hccl_meshclos2d_v3_strict_threadserial_a2a16_16mb` | 69.830 us | 240.26 GB/s | [HTML](../reports/ns3ub-ubx16-v3-strict-threadserial-a2a16-16mb-rank0-profile.html) |

## 算法形态

### HCCL Mesh1D baseline

对应当前 `hccl/src/ops/all_to_all_v/template/aicpu/ins_temp_all_to_all_v_mesh_1D.h` 的 Mesh1D 基线。当前源码中 `ALLTOALLV_DIRECT_FULLMESH_CONCURRENT_SIZE = 16`，所以 16 rank 时 rank 0 在一轮中发完 15 个 peer：`0->15, 0->1, 0->14, 0->2, ... , 0->8`。

这个 baseline 仍然使用 Mesh1D 的左右对称 peer 顺序，并按 peer 的可用 channel 分片。它没有显式按 UBX 的 4 个 clos 平面组织成 3 轮均衡调度，因此 rank0 结果为 73.186 us / 229.24 GB/s，低于两个优化版本。

### HCCL matrix optimized

对应当前 `hccl` 仓库里的 UBX matrix 优化实现。UBX16 被视为 4x4 矩阵，每轮 rank 0 同时发 5 条流：1 条组内 mesh 流，加 4 条跨组 clos 平面流。rank 0 一共 3 轮完成对 15 个 peer 的发送。

建模时使用 round barrier，因为当前 matrix 路径源码里有按通信轮次同步的行为。profile 上能看到每轮 4 条 clos 平面基本同时跑满，mesh 槽位每轮只承担一个组内 peer。

### MeshClos V3

对应 `hccl-xzw` 里的 MeshClos V3 优化算法建模。它把组内 mesh 任务和跨组 clos 任务拆成独立并行单元：rank 0 有 3 个 mesh thread 和 4 个 clos thread。每个 clos thread 串行发送 3 个跨组目标，4 个 clos 平面并发工作。

这个模型下，clos 是主要瓶颈；3 条 mesh 直连只在开头一段时间有任务，完成后基本空闲。最终性能与 HCCL matrix optimized 非常接近，都是单 TP/单平面 strict 口径下约 240 GB/s。

## 当前结论

在 strict 单 TP/单平面口径下，当前 HCCL Mesh1D baseline 为 229.24 GB/s，matrix optimized 和 MeshClos V3 optimized 都在 240.26 GB/s 左右，优化收益约 4.8%。两个优化版本的核心收益不是让单条 peer 流使用 full TP，而是更直接地把跨组通信组织到 4 条 clos 平面上并发发送，从而提高 AllToAll 聚合吞吐。

因此，当前 16 MiB/rank 的 V3 和 matrix 结果已经比较接近 strict 单平面网络极限；继续增加每 rank 数据量大概率主要拉长 makespan，带宽不会显著上升。真正还能讨论的优化点更多在 HBM copy、HCCL buffer、真实执行空泡和算子流水，而不是 clos 网络本身。
