# UBX16 Profiling 图生成说明

UBX16 实验里的 profiling HTML 由 `tools/` 下的可复用脚本生成。脚本读取 ns-3-ub case 目录里的 `traffic.csv` 和 `output/task_statistics.csv`，按指定 rank 渲染时间线。

## 输入约定

所有渲染脚本都要求 case 已经跑完仿真，并存在：

```text
<case_dir>/traffic.csv
<case_dir>/output/task_statistics.csv
```

常见输出目录：

```text
experiments/ubx16/alltoall/reports/
experiments/ubx16/alltoallv/*/profiles/
```

## Baseline / Mesh1D

脚本：

```text
tools/render_ns3ub_mesh1d_rank_profile.py
```

用于 HCCL Mesh1D baseline 这类按 Mesh1D 逻辑线程展示的 profile。

示例：

```bash
python3 tools/render_ns3ub_mesh1d_rank_profile.py \
  experiments/ubx16/alltoall/cases/generated_topology_ubx16_hccl_baseline_threadserial_a2a16_128mib \
  --rank 0 \
  -o experiments/ubx16/alltoall/reports/ns3ub-ubx16-baseline-threadserial-a2a16-128mib-rank0-profile.html \
  --title "HCCL UBX Mesh1D baseline threadserial A2A16 128MiB rank0 profile"
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--rank` | 要渲染的 rank。 |
| `--priority` | 只渲染某个 priority；默认渲染 priority 7。用 `-1` 可包含所有 priority。 |
| `--concurrent` | Mesh1D 逻辑并发槽数量；不填时从 traffic 推断。 |
| `--title` | HTML 标题。 |

## MeshClos V3 / Fixed-Plane / Snake / Snack

脚本：

```text
tools/render_ns3ub_rank_profile.py
```

用于 MeshClos V3、priority 选平面、snake/snack fixed-plane 等 profile。

### MeshClos V3 logical 模式

当 case 的平面由 MeshClos V3 逻辑/hash 编排决定，且 `transport_channel.csv` 已经删成每个 pair 只剩一个 TP 时，使用默认 `logical` 模式：

```bash
python3 tools/render_ns3ub_rank_profile.py \
  experiments/ubx16/alltoall/cases/generated_topology_ubx16_hccl_meshclos2d_v3_strict_threadserial_a2a16_16mb \
  --rank 0 \
  --rank-count 16 \
  --group-size 4 \
  --clos-channels 4 \
  -o experiments/ubx16/alltoall/reports/ns3ub-ubx16-v3-strict-threadserial-a2a16-16mb-rank0-profile.html \
  --title "UBX16 MeshClos V3 strict thread-serial A2A16 16MiB rank0 profile"
```

### Priority-plane 模式

当 case 通过 `traffic.csv` 的 priority 选择平面时，使用 `--unit-mode priority-plane`：

```bash
python3 tools/render_ns3ub_rank_profile.py \
  experiments/ubx16/alltoallv/snake/cases/generated_topology_ubx16_a2av_snake_v2_fixedplane_custom \
  --rank 0 \
  --rank-count 16 \
  --group-size 4 \
  --clos-channels 4 \
  --unit-mode priority-plane \
  --priorities 3 4 5 6 7 \
  -o experiments/ubx16/alltoallv/snake/profiles/ns3ub-ubx16-a2av-snake-v2-fixedplane-rank0-profile.html \
  --title "UBX16 AllToAllV snake v2 fixed-plane rank0 profile"
```

priority 与平面约定：

| Priority | 平面 | 端口 |
| ---: | ---: | ---: |
| 3 | plane 0 | port 3 |
| 4 | plane 1 | port 4 |
| 5 | plane 2 | port 5 |
| 6 | plane 3 | port 6 |
| 7 | 组内 mesh | port 0/1/2 |

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--rank` | 要渲染的 rank。 |
| `--rank-count` | rank 总数，UBX16 为 16。 |
| `--group-size` | 每个 mesh 组的 rank 数，UBX16 为 4。 |
| `--clos-channels` | Clos 平面数，UBX16 为 4。 |
| `--unit-mode logical` | 按 MeshClos V3 logical thread 分行。 |
| `--unit-mode priority-plane` | 按 priority 3/4/5/6 映射到 clos plane 分行。 |
| `--priorities` | 要渲染的 priority 列表。 |
| `--marker-us` | 在 HTML 中画竖线标记，可重复传入。 |
| `--marker-label` | 竖线说明。 |

## Matrix

脚本：

```text
tools/render_ns3ub_matrix_rank_profile.py
```

用于 HCCL matrix 算法 profile。

示例：

```bash
python3 tools/render_ns3ub_matrix_rank_profile.py \
  experiments/ubx16/alltoall/cases/generated_topology_ubx16_hccl_matrix_strict_roundbarrier_a2a16_16mb \
  --rank 0 \
  -o experiments/ubx16/alltoall/reports/ns3ub-ubx16-hccl-matrix-strict-roundbarrier-a2a16-16mb-rank0-profile.html \
  --title "HCCL UBX Matrix strict round-barrier A2A16 16MiB rank0 profile"
```

## 选择哪个脚本

| case 类型 | 推荐脚本 | 推荐模式 |
| --- | --- | --- |
| baseline / Mesh1D | `render_ns3ub_mesh1d_rank_profile.py` | Mesh1D logical slots |
| MeshClos V3 alltoall | `render_ns3ub_rank_profile.py` | `logical` |
| AllToAllV closv3 hash | `render_ns3ub_rank_profile.py` | `logical` 或 `priority-plane`，取决于 case 是否用 priority 写平面 |
| snake/snack fixed-plane | `render_ns3ub_rank_profile.py` | `priority-plane` |
| matrix | `render_ns3ub_matrix_rank_profile.py` | matrix slots |

## 注意事项

- profiling 图展示的是 ns-3 task 的开始/结束时间，不会重新推断依赖。
- 如果多个 task 在同一个逻辑行上重叠，`render_ns3ub_rank_profile.py` 会拆成视觉 sublane，不会伪造串行。
- fixed-plane case 中，priority 必须和 `transport_channel.csv` 里的 TP priority 对上，否则图里看到的平面和实际选路会不一致。
- 如果仿真关闭了 packet trace，`firstPacketSends/us` 为空是正常的；profile 只依赖 task start/end 仍可生成。
