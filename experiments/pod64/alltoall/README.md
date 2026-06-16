# POD64 AllToAll

当前保留 3 个 POD64 AllToAll case：

| 算法 | case | rank | 数据量 | TP rows | makespan(us) | Global GB/s | Rank0 GB/s | profile |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_16mb` | 64 | 16MiB/rank | 16352 | 93.739980 | 11454.47 | 182.26 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-16mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_16mb` | 64 | 16MiB/rank | 2016 | 80.702060 | 13305.01 | 297.06 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-16mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_16mb` | 64 | 16MiB/rank | 2016 | 71.328480 | 15053.48 | 271.13 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-16mb-rank0-profile.html) |

建模口径：

- 拓扑：`../../topologies/pod64/generated_topology`
- POD64：64 rank，每 8 rank 一组；每卡 8 个 clos 平面端口和 7 个组内 mesh 直连端口。
- baseline：HCCL Mesh1D，`concurrent=16`，`dependency-mode=thread-serial`，`tp-mode=full`。
- matrix strict：8x8 matrix 调度；组内 slot 选择 host-host 直连 mesh，跨组 slot 选择固定 clos 平面。
- MeshClos V3 strict：`group-size=8`，8 个 clos 平面；组内任务走直连 mesh，跨组任务按 V3 shift/link 映射到固定平面。
- 完整汇总：`reports/ns3ub-pod64-alltoall-summary.csv`。
- HTML 总报告：`reports/ns3ub-pod64-alltoall-report.html`。
