# HCCL AllToAll MeshClos2D V2 流量模型

本文记录 `ins_temp_alltoall_mesh_clos_v2.cc` 的建模口径和 ns-3-ub 仿真结果。

## 执行路径

该模板走的是 `HCCL_CMD_ALLTOALL`，不是 `HCCL_CMD_ALLTOALLV`。

启用条件：

```text
ENABLE_HCCL_ALLTOALL_CLOS_MESH_2D=1
或设置 HCCL_A2A_OPT_TOPO
```

选择器会选择 `InsAlltoAllParallelMesh2DClosV2`，注册路径为：

```text
InsV2AlltoAllParallelExecutor<
  TopoMatchUBX,
  InsTempAlltoAllMesh2DV2,
  InsTempAlltoAllMeshClosV2
>
```

## 2D 分解

以 16 rank、每组 8 rank 为例：

```text
xRankSize = 8
yRankSize = 2
rank = y * 8 + x
```

executor 默认把数据按两个方向 50/50 拆分，并分两 stage 执行：

```text
stage0: intra(split0) + inter(split1)
stage1: intra(split1) + inter(split0)
```

若 direct alltoall 每个 rank 发给每个远端 peer 的大小为 `P`，则 16 rank/2 组下：

```text
每个 stage:
  intra: 7 条流，每条 P
  inter: 1 条流，每条 4P

两个 stage 总网络发送:
  2 * (7P + 4P) = 22P
```

直接 alltoall 的应用层远端 payload 是 `15P`，所以该 2D 分解会产生额外中转网络量。

## TP 模型

配套脚本：

```text
tools/generate_hccl_meshclos2d_alltoall_case.py
```

支持两种模式：

```text
strict:
  intra 使用 channels[0]
  inter 使用 (myAlgRank + connectedAlgRank) % channel_count
  每个 rank-pair 只保留 1 条 TP

ideal:
  traffic 完全相同
  每个 rank-pair 保留全部 TP，让 ns-3-ub 多路径
```

strict 模式用于贴近源码行为；ideal 模式用于看拓扑多路径上限。

## 16 Rank 256MiB 结果

命令：

```bash
tools/generate_hccl_meshclos2d_alltoall_case.py \
  -n 16 --group-size 8 -b 256MB --mode strict \
  -s generated_topology \
  -o generated_topology_hccl_meshclos2d_strict_a2a16_256mb

tools/generate_hccl_meshclos2d_alltoall_case.py \
  -n 16 --group-size 8 -b 256MB --mode ideal \
  -s generated_topology \
  -o generated_topology_hccl_meshclos2d_ideal_a2a16_256mb
```

两版 traffic 完全相同：

```text
tasks: 256
phases: 128 + 128
flow sizes:
  intra: 17,895,698 B
  inter: 71,582,792 B
actual network bytes per rank: 393,705,356 B
direct payload per rank: 256 MiB
```

仿真结果：

```text
strict:
  duration: 5202.856 us
  direct payload bandwidth: 412.75 Gbps = 51.59 GB/s
  actual network bandwidth: 605.37 Gbps = 75.67 GB/s

ideal:
  duration: 707.118 us
  direct payload bandwidth: 3036.95 Gbps = 379.62 GB/s
  actual network bandwidth: 4454.20 Gbps = 556.78 GB/s
```

strict 下 node0 只用了两个 Tx 端口；ideal 下 node0 使用了 15 个 Tx 端口。因此该差距主要来自源码级别的单 TP/hash 选链策略，而不是拓扑本身没有足够路径。
