# HCCL AlltoAllV Mesh1D 基线流量模型

本文记录 `hccl/src/ops/all_to_all_v/template/aicpu/ins_temp_all_to_all_v_mesh_1D.h`
对应 AICPU alltoallv 基线算子的执行路径，以及在 ns-3-ub 中生成近似流量时应采用的通信轮次。

## 源码执行路径

在 UBX / `MESH_1D_CLOS` 场景下，`AlltoAllVAutoSelector` 会选择
`InsAlltoAllVMesh1DUBX`。该算法最终注册到
`InsV2AlltoAllVSoleExecutor<TopoMatchUBX1d, InsTempAlltoAllVMesh1D>`，因此主体调度仍由
`InsTempAlltoAllVMesh1D` 完成。

关键路径：

1. `alltoallv_auto_selector.cc`
   - AICPU 模式选择 `InsAlltoAllVMesh1D` 或 `InsAlltoAllVMesh1DUBX`。
2. `ins_v2_all_to_all_v_sole_executor.cc`
   - 构造 `remoteRank -> channels`。
   - 读取 `sendCounts/recvCounts/sdispls/rdispls`。
   - 根据 CCL buffer 和 `UB_MAX_DATA_SIZE` 把超大 peer 数据切成外层 chunk。
3. `ins_temp_all_to_all_v_mesh_1D.cc`
   - 每个 chunk 内部执行 Mesh1D all-to-all-v 调度。
   - 每轮选最多 `ALLTOALLV_DIRECT_FULLMESH_CONCURRENT_SIZE` 个远端 rank。
   - 每个 rank-peer 的 payload 再按可用 channel 的 `portGroupSize` 比例拆分。

## 通信轮次

当前 `hccl` 模板里固定：

```cpp
ALLTOALLV_DIRECT_FULLMESH_CONCURRENT_SIZE = 16
```

因此每个 rank 每轮最多同时和 16 个远端 rank 通信：

```text
concurrent = min(16, rank_size - 1)
comm_loops = ceil((rank_size - 1) / concurrent)
```

每轮 peer 选择以本 rank 为中心，按距离左右对称展开。以 rank0 为例：

```text
8 rank:
round0: 7, 1, 6, 2, 5, 3, 4

16 rank:
round0: 15, 1, 14, 2, 13, 3, 12, 4, 11, 5, 10, 6, 9, 7, 8
```

全局 directed rank-pair 数量：

```text
8 rank:  56
16 rank: 240
```

这和 traffic_maker 里的 `a2a_pairwise` 不同。`a2a_pairwise` 是每轮一个距离，
16 rank 需要 15 个 phase；该 HCCL Mesh1D 基线会把所有距离合并到一轮。

历史说明：`hccl-xzw` 中这个常量曾被改成 4，因此旧实验里的
`generated_topology_ubx16_hccl_mesh1d_threadserial_a2a16_16mb` 是 4 并发模型，
不再作为当前 `hccl` baseline。

## rank-pair 内部切分

对每个远端 rank，模板会取当前 peer 的 channels：

```cpp
curValidChannelsSize = min(curChannels.size(), channelsPerRank_)
```

然后调用 `CalcDataSplitByPortGroupCommon`，按 channel 的 `portGroupSize` 加权切分
`sendCounts[remoteRank]` 和 `recvCounts[remoteRank]`。因此更精细的 ns-3 建模可以把一个
rank-peer 拆成多个 channel 子流。

当前建议分两层建模：

1. 一阶模型：每个 rank-peer 生成一条 `URMA_WRITE` task，让 ns-3-ub 根据 TP 和 packet spray 使用多路径。
2. 细粒度模型：按 HCCL channel/portGroupSize 把 rank-peer payload 拆成多条 task。

本文配套脚本 `tools/generate_hccl_mesh1d_alltoallv_case.py` 采用一阶模型，默认
`--concurrent 16`。如需复现历史 4 并发模型，需要显式传 `--concurrent 4`。

## ns-3 traffic 生成规则

对于 uniform alltoallv，如果每个 rank 总通信量为 `per_rank_bytes`，排除本地 copy 后：

```text
per_peer_bytes = per_rank_bytes / (rank_size - 1)
```

如果不能整除，脚本按 alltoallv 的变长语义把余数分配给该 rank 的前几个 peer，
保证每个 rank 的发送总量仍严格等于 `per_rank_bytes`。

每个 phase 内，对每个 rank 和该 phase 的每个 peer 生成一条 directed flow：

```text
sourceNodeId = rank
destNodeId   = peer
dataSize     = per_peer_bytes
opType       = URMA_WRITE
priority     = 7
```

依赖关系：

```text
thread-serial: 同一个 source 的同一逻辑 slot 跨轮串行
phase-barrier: 每轮之间加全局 barrier，仅用于保守对照
```

当前 16 rank / concurrent=16 的 baseline 只有一轮，因此 rank0 的 15 条 task
没有跨轮依赖。

注意：真实算子还存在本地 self-copy、DMA read/write 模式和外层 chunk loop。对网络带宽评估来说，
self-copy 不进网络；当单 peer 数据量低于外层 chunk 上限时，可以先忽略 chunk loop。
