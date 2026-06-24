# UBX16 AllToAllV Snake Recv-Balanced Fixed-Plane Case

This case evaluates the newer snake-style AllToAllV traffic that tries to reduce receiver-side plane conflicts.

## Configuration

- Topology: UBX16, 16 ranks, 4 ranks per group.
- Cross-group fixed planes:
  - priority 3 -> plane 0 -> device port 3 -> switch 16
  - priority 4 -> plane 1 -> device port 4 -> switch 17
  - priority 5 -> plane 2 -> device port 5 -> switch 18
  - priority 6 -> plane 3 -> device port 6 -> switch 19
  - priority 7 -> intra-group mesh traffic
- Traffic:
  - 240 URMA_WRITE tasks.
  - 48 tasks per priority 3/4/5/6/7.
  - Total network payload: 271,096,416 bytes.
  - Per-rank payload: 16,943,526 bytes.
  - Source-side total payload is exactly balanced across all 16 ranks.

The case reuses the fixed-plane transport-channel setup: each cross-group pair keeps 4 TP rows, and task priority selects the plane.

## Simulation Result

| Case | Makespan | Single-rank GB/s | Rank0 GB/s | Notes |
|---|---:|---:|---:|---|
| baseline custom | 104.474 us | 164.35 | 193.40 | Full TP baseline for the same earlier traffic family |
| previous fixed-plane snake | 201.096 us | 85.38 | 84.26 | Plane bytes balanced, but first-step receiver conflicts up to 4-way |
| recv-balanced fixed-plane snake | 193.338 us | 87.64 | 91.07 | Receiver conflict improved, but still not a per-plane matching |

Rank0 profile:

- `../../profiles/ns3ub-ubx16-a2av-snake-recvbalanced-fixedplane-rank0-profile.html`

## What Improved

Compared with the previous fixed-plane snake case:

- Makespan improves from 201.096 us to 193.338 us.
- Single-rank bandwidth improves from 85.38 GB/s to 87.64 GB/s.
- Rank0 bandwidth improves from 84.26 GB/s to 91.07 GB/s.
- Worst cross-flow bandwidth improves from 8.22 GB/s to 13.48 GB/s.

The new traffic reduces the worst first-step same-destination conflict from 4-way to 3-way.

## Remaining Bottleneck

The first cross-group root step still launches 64 tasks at time 0:

- 16 tasks on priority 3.
- 16 tasks on priority 4.
- 16 tasks on priority 5.
- 16 tasks on priority 6.

For each plane, the maximum same-destination duplication in the first step is still 3:

| Plane priority | Active first-step flows | Max same-dst duplication |
|---|---:|---:|
| 3 | 16 | 3 |
| 4 | 16 | 3 |
| 5 | 16 | 3 |
| 6 | 16 | 3 |

This means the schedule is still not a clean `(src, plane)` and `(dst, plane)` matching. Multiple sources can still target the same destination over the same plane in the same step, so they contend on the destination device port.

## Conclusion

The receiver-balanced version moves in the right direction, but the gain is small because it only reduces the worst receiver conflict. It does not fully eliminate per-plane step conflicts.

The next optimization should make each plane/step closer to a bipartite matching:

- Each `(src, plane)` should appear at most once per step.
- Each `(dst, plane)` should appear at most once per step.
- Large AllToAllV flows should be split or placed to avoid blocking a whole source-plane dependency chain.

In short: balancing total bytes per plane is not enough. The important constraint for fixed-plane AllToAllV is step-level matching on each plane.
