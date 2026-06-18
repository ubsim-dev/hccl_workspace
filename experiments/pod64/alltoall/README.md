# POD64 AllToAll

当前保留 POD64 AllToAll case，覆盖 16/64/128/256/512/1024MiB/rank。大包 case 使用 MTP 并关闭 port/packet trace，仅保留 task trace 生成 profiling。

| 算法 | case | rank | 数据量 | TP rows | makespan(us) | Global GB/s | Rank0 GB/s | profile |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_16mb` | 64 | 16MiB/rank | 16352 | 93.739980 | 11454.47 | 182.26 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-16mb-rank0-profile.html) |
| baseline full-TP phase-barrier | `cases/generated_topology_pod64_hccl_baseline_phasebarrier_a2a64_16mb` | 64 | 16MiB/rank | 16352 | 95.348160 | 11261.27 | 176.29 | [rank0](reports/ns3ub-pod64-baseline-phasebarrier-a2a64-16mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_16mb` | 64 | 16MiB/rank | 2016 | 80.702060 | 13305.01 | 297.06 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-16mb-rank0-profile.html) |
| matrix strict round-barrier | `cases/generated_topology_pod64_hccl_matrix_strict_roundbarrier_a2a64_16mb` | 64 | 16MiB/rank | 2016 | 44.448600 | 24156.93 | 378.88 | [rank0](reports/ns3ub-pod64-matrix-strict-roundbarrier-a2a64-16mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_16mb` | 64 | 16MiB/rank | 2016 | 71.328480 | 15053.48 | 271.13 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-16mb-rank0-profile.html) |
| MeshClos V3 strict step-barrier | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_stepbarrier_a2a64_16mb` | 64 | 16MiB/rank | 2016 | 44.596040 | 24077.07 | 377.09 | [rank0](reports/ns3ub-pod64-v3-strict-stepbarrier-a2a64-16mb-rank0-profile.html) |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_64mb` | 64 | 64MiB/rank | 16352 | 190.524460 | 22542.87 | 354.66 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-64mb-rank0-profile.html) |
| baseline full-TP phase-barrier | `cases/generated_topology_pod64_hccl_baseline_phasebarrier_a2a64_64mb` | 64 | 64MiB/rank | 16352 | 189.438080 | 22672.14 | 356.15 | [rank0](reports/ns3ub-pod64-baseline-phasebarrier-a2a64-64mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_64mb` | 64 | 64MiB/rank | 2016 | 320.781040 | 13389.09 | 306.43 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-64mb-rank0-profile.html) |
| matrix strict round-barrier | `cases/generated_topology_pod64_hccl_matrix_strict_roundbarrier_a2a64_64mb` | 64 | 64MiB/rank | 2016 | 162.617220 | 26411.52 | 413.79 | [rank0](reports/ns3ub-pod64-matrix-strict-roundbarrier-a2a64-64mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_64mb` | 64 | 64MiB/rank | 2016 | 240.090240 | 17888.97 | 314.83 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-64mb-rank0-profile.html) |
| MeshClos V3 strict step-barrier | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_stepbarrier_a2a64_64mb` | 64 | 64MiB/rank | 2016 | 164.170020 | 26161.70 | 409.55 | [rank0](reports/ns3ub-pod64-v3-strict-stepbarrier-a2a64-64mb-rank0-profile.html) |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_128mb` | 64 | 128MiB/rank | 16352 | 362.207680 | 23715.50 | 374.17 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-128mb-rank0-profile.html) |
| baseline full-TP phase-barrier | `cases/generated_topology_pod64_hccl_baseline_phasebarrier_a2a64_128mb` | 64 | 128MiB/rank | 16352 | 364.354720 | 23575.75 | 371.49 | [rank0](reports/ns3ub-pod64-baseline-phasebarrier-a2a64-128mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_128mb` | 64 | 128MiB/rank | 2016 | 595.123740 | 14433.86 | 298.74 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-128mb-rank0-profile.html) |
| matrix strict round-barrier | `cases/generated_topology_pod64_hccl_matrix_strict_roundbarrier_a2a64_128mb` | 64 | 128MiB/rank | 2016 | 319.560620 | 26880.45 | 420.90 | [rank0](reports/ns3ub-pod64-matrix-strict-roundbarrier-a2a64-128mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_128mb` | 64 | 128MiB/rank | 2016 | 479.937540 | 17898.03 | 311.77 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-128mb-rank0-profile.html) |
| MeshClos V3 strict step-barrier | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_stepbarrier_a2a64_128mb` | 64 | 128MiB/rank | 2016 | 319.073240 | 26921.51 | 421.24 | [rank0](reports/ns3ub-pod64-v3-strict-stepbarrier-a2a64-128mb-rank0-profile.html) |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_256mb` | 64 | 256MiB/rank | 16352 | 709.204860 | 24224.13 | 387.43 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-256mb-rank0-profile.html) |
| baseline full-TP phase-barrier | `cases/generated_topology_pod64_hccl_baseline_phasebarrier_a2a64_256mb` | 64 | 256MiB/rank | 16352 | 744.311020 | 23081.57 | 369.53 | [rank0](reports/ns3ub-pod64-baseline-phasebarrier-a2a64-256mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_256mb` | 64 | 256MiB/rank | 2016 | 1115.206340 | 15405.10 | 291.16 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-256mb-rank0-profile.html) |
| matrix strict round-barrier | `cases/generated_topology_pod64_hccl_matrix_strict_roundbarrier_a2a64_256mb` | 64 | 256MiB/rank | 2016 | 632.230520 | 27173.43 | 425.10 | [rank0](reports/ns3ub-pod64-matrix-strict-roundbarrier-a2a64-256mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_256mb` | 64 | 256MiB/rank | 2016 | 946.533300 | 18150.31 | 316.68 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-256mb-rank0-profile.html) |
| MeshClos V3 strict step-barrier | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_stepbarrier_a2a64_256mb` | 64 | 256MiB/rank | 2016 | 631.528780 | 27203.62 | 425.47 | [rank0](reports/ns3ub-pod64-v3-strict-stepbarrier-a2a64-256mb-rank0-profile.html) |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_512mb` | 64 | 512MiB/rank | 16352 | 1523.070800 | 22559.51 | 368.81 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-512mb-rank0-profile.html) |
| baseline full-TP phase-barrier | `cases/generated_topology_pod64_hccl_baseline_phasebarrier_a2a64_512mb` | 64 | 512MiB/rank | 16352 | 1444.564340 | 23785.54 | 372.29 | [rank0](reports/ns3ub-pod64-baseline-phasebarrier-a2a64-512mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_512mb` | 64 | 512MiB/rank | 2016 | 2476.414380 | 13874.79 | 270.97 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-512mb-rank0-profile.html) |
| matrix strict round-barrier | `cases/generated_topology_pod64_hccl_matrix_strict_roundbarrier_a2a64_512mb` | 64 | 512MiB/rank | 2016 | 1332.747880 | 25781.12 | 405.31 | [rank0](reports/ns3ub-pod64-matrix-strict-roundbarrier-a2a64-512mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_512mb` | 64 | 512MiB/rank | 2016 | 1954.688340 | 17578.12 | 319.29 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-512mb-rank0-profile.html) |
| MeshClos V3 strict step-barrier | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_stepbarrier_a2a64_512mb` | 64 | 512MiB/rank | 2016 | 1251.125180 | 27463.07 | 429.64 | [rank0](reports/ns3ub-pod64-v3-strict-stepbarrier-a2a64-512mb-rank0-profile.html) |
| baseline full-TP | `cases/generated_topology_pod64_hccl_baseline_threadserial_a2a64_1024mb` | 64 | 1024MiB/rank | 16352 | 3105.937660 | 22125.20 | 355.05 | [rank0](reports/ns3ub-pod64-baseline-threadserial-a2a64-1024mb-rank0-profile.html) |
| baseline full-TP phase-barrier | `cases/generated_topology_pod64_hccl_baseline_phasebarrier_a2a64_1024mb` | 64 | 1024MiB/rank | 16352 | 2886.564440 | 23806.67 | 375.63 | [rank0](reports/ns3ub-pod64-baseline-phasebarrier-a2a64-1024mb-rank0-profile.html) |
| matrix strict | `cases/generated_topology_pod64_hccl_matrix_strict_threadserial_a2a64_1024mb` | 64 | 1024MiB/rank | 2016 | 4889.519240 | 14054.44 | 233.90 | [rank0](reports/ns3ub-pod64-matrix-strict-threadserial-a2a64-1024mb-rank0-profile.html) |
| matrix strict round-barrier | `cases/generated_topology_pod64_hccl_matrix_strict_roundbarrier_a2a64_1024mb` | 64 | 1024MiB/rank | 2016 | 3152.185820 | 21800.58 | 344.41 | [rank0](reports/ns3ub-pod64-matrix-strict-roundbarrier-a2a64-1024mb-rank0-profile.html) |
| MeshClos V3 strict | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_threadserial_a2a64_1024mb` | 64 | 1024MiB/rank | 2016 | 3986.795740 | 17236.77 | 301.49 | [rank0](reports/ns3ub-pod64-v3-strict-threadserial-a2a64-1024mb-rank0-profile.html) |
| MeshClos V3 strict step-barrier | `cases/generated_topology_pod64_hccl_meshclos2d_v3_strict_stepbarrier_a2a64_1024mb` | 64 | 1024MiB/rank | 2016 | 2483.951540 | 27665.39 | 432.75 | [rank0](reports/ns3ub-pod64-v3-strict-stepbarrier-a2a64-1024mb-rank0-profile.html) |

建模口径：

- 拓扑：`../../topologies/pod64/generated_topology`。
- POD64：64 rank，每 8 rank 一组；每卡 8 个 clos 平面端口和 7 个组内 mesh 直连端口。
- baseline：HCCL Mesh1D，`concurrent=16`，`tp-mode=full`；phase-barrier 是同步对照。
- matrix strict：8x8 matrix 调度；组内 slot 走 mesh，跨组 slot 走固定 clos 平面；round-barrier 是同步对照。
- MeshClos V3 strict：`group-size=8`，8 个 clos 平面；组内任务走直连 mesh，跨组任务按 V3 shift/link 映射到固定平面。
- MeshClos V3 strict step-barrier：诊断对照。Mesh2D 任务保持并发，只强制每个 MeshClos 逻辑 step 全局完成后再进入下一 step。
- 完整汇总：`reports/ns3ub-pod64-alltoall-summary.csv`。
- HTML 总报告：`reports/ns3ub-pod64-alltoall-report.html`。

关键观察：

- 最好结果是 MeshClos V3 strict step-barrier 1024MiB/rank：全局 27665.39 GB/s，rank0 432.75 GB/s。
- V3 step 1024MiB/rank 相对 baseline phase-barrier 1024MiB/rank 提升 16.2%。
- 按全局理论 28800 GB/s、单卡理论 450 GB/s 估算，V3 step 1024MiB 达成 96.1% / 96.2%。
- V3 step 从 256MiB 到 1024MiB 仍有小幅增长：27203.62 -> 27463.07 -> 27665.39 GB/s，512MiB 之后基本进入平台区。
- matrix round-barrier 在 1024MiB 退化到 21800.58 GB/s，说明同步调度本身不保证稳定，具体 round 内的链路/接收端竞争仍会影响长包结果。
