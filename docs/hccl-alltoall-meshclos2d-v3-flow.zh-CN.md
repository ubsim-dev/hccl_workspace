# HCCL AllToAll MeshClos2D V3 流量模型

本文记录 `ins_temp_alltoall_mesh_clos_v3.cc` 的源码调度、ns-3-ub 建模方式和 16 rank 仿真结果。

## 执行路径

V3 通常由 `HCCL_A2A_OPT_TOPO` 或 `HCCL_ENABLE_A2A_ASYMMETRIC_OPT=1` 触发。

注册路径：

```text
InsV2AlltoAllParallelOptExecutor<
  TopoMatchUBX_V2,
  InsTempAlltoAllMesh2DV3,
  InsTempAlltoAllMeshClosV3
>
```

对应注册名：

```text
InsAlltoAllParallelMesh2DClosV3
```

## 相对 V2 的差异

V2 是两阶段 50/50 数据转发：

```text
stage0: intra(50%) + inter(50%)
stage1: intra(50%) + inter(50%)
```

16 rank、2 组下，每 rank 实际网络发送约 `22P`，大于 direct alltoall 的 `15P`。

V3 opt executor 只有一个 stage：

```text
intra.KernelRun(full data)
inter.KernelRun(full data)
```

因此 16 rank 下每 rank 网络发送量就是 direct alltoall 的 `15P`。

## 选链逻辑

### Intra

`InsTempAlltoAllMesh2DV3` 仍然固定取：

```cpp
channels.at(connectedRank)[0]
```

所以组内 peer 仍然挤在第 0 条 TP 上。

### Inter

`InsTempAlltoAllMeshClosV3` 改为 group-pairwise + shift 调度。

16 rank、2 组、每组 8 rank 时：

```text
rank0:
  link0 -> rank8
  link1 -> rank9
  link2 -> rank10
  ...
  link7 -> rank15
```

这意味着跨组 8 个 peer 被分散到 8 条 TP，比 V2 strict 好很多。

## ns-3 建模

配套脚本：

```text
tools/generate_hccl_meshclos2d_v3_alltoall_case.py
```

模式：

```text
strict:
  intra pair 只保留第 0 条 TP
  inter pair 按 V3 shift/linkIdx 只保留 1 条 TP

ideal:
  traffic 相同
  每个 pair 保留全部 TP
```

traffic：

```text
1 个 phase
每个 rank -> 其他 15 个 rank
每 rank direct payload = 256 MiB
每条流约 17.9 MiB
```

## 16 Rank 256MiB 结果

```text
V3 strict:
  tasks: 240
  duration: 2975.088 us
  单 rank 带宽: 721.82 Gbps = 90.23 GB/s

V3 ideal:
  tasks: 240
  duration: 631.716 us
  单 rank 带宽: 3399.45 Gbps = 424.93 GB/s
```

对比：

```text
V2 strict: 75.67 GB/s
V3 strict: 90.23 GB/s
Mesh1D alltoallv baseline: 404.33 GB/s
V3 ideal: 424.93 GB/s
```

结论：V3 修复了 V2 跨组大流单 TP 的问题，但组内 `Mesh2DV3` 仍然固定 `channels[0]`。
因此 strict 版只提升到 90GB/s 左右；真正接近拓扑上限需要组内也能多 TP 分散。
