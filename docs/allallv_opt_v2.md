## 方案二：V3 A+B 拆分

### 原理

把每个 pair 的数据拆成公共基础部分 A 和非等长残差 B：

```text
count[src][dst] = A + B[src][dst]
```

A 部分：
```text
所有 pair 的通信量相同
走 当前alltoall V3 no-memcpy 实现，高性能路径；
```

B 部分：
```text
只处理剩余非等长部分
复用已有 AllToAllV mesh1D / 包喷洒 / 基线变长算法
```

### A 的选择

如果是 AllToAllVC 或等价接口，可以基于全局 count 矩阵精确选择 A/B：

```text
1. 统计 count[src][dst] 的分布
2. 根据 A 路径和 B 路径的可用带宽确定目标数据比例
3. 选择 A，使 sum(min(count[src][dst], A)) 与 B 残差的数据量匹配目标比例
4. B[src][dst] = max(count[src][dst] - A, 0)
```

如果 A 路径使用 V3 no-memcpy，B 路径使用现有 AllToAllV/RR，则可以按两条路径的有效带宽估算：

```text
A_ratio ~= BW_A / (BW_A + BW_B)
B_ratio ~= BW_B / (BW_A + BW_B)
```

例如 A 路径能稳定跑到 200G，B 路径只能跑到 50G，则目标可以接近：

```text
A_ratio ~= 80%
B_ratio ~= 20%
```

AllToAllVC 场景可以用真实 count 精确求出接近该比例的 A。

AllToAllV 场景通常没有全局 count 分布，只能做估算。例如假设不均衡度上限约为 1.3，可以先人为选择：

```text
A_ratio = 80%
B_ratio = 20%
```

如果某些 pair 的真实数据量小于均值的 80%，A 部分不足的区域使用 padding 语义处理。padding 不应被视为真实有效数据；实现上可以选择不真实发送 padding 数据，但调度和 output 有效区间必须仍保持一致（接受padding带来的空泡）。


### 优点

- alltoallvc场景 或 大部分的alltoallv场景，性能收益在 alltoallv基线 - alltoall优化 两者之间，取决于通信不均衡度
- 不需要完整 weighted scheduling，实现复杂度/算法复杂度 低。

### 缺点
- alltoallvc场景 若 count 分布稀疏或有很多 0，性能退化为基线的alltoallv。
- alltoallv场景 若无全局count，极端场景由于padding可能导致性能比基线的alltoallv劣化