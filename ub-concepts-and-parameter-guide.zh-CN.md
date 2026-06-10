# UB 仿真器配置说明

本文面向 `scratch/` 下案例目录的使用者，重点回答四类问题：

1. `jetty`、TP、路由分别是什么
2. `traffic.csv`、`transport_channel.csv`、`routing_table.csv` 各自控制什么
3. `priority`、`metric`、`EnableMultiPath`、`UsePacketSpray`、`UseShortestPaths` 如何配合
4. 不同配置方式通常会带来什么行为结果

本文不展开实验过程，只讲配置逻辑和结果含义。

## 一、先看整体结构

### 1.1 仿真器里有三层逻辑

理解 UB 仿真器，先把下面三层分开：

1. 任务层
   - 代表对象：`UbApp`、`UbFunction`、`UbJetty`
   - 关心的是：一条任务如何被提交、切分、完成

2. 传输层
   - 代表对象：TP、TPN、`UbTransportChannel`
   - 关心的是：任务可以走哪些端到端传输通道

3. 转发层
   - 代表对象：`UbRoutingProcess`、`UbSwitch`
   - 关心的是：包进入网络后，每一跳交换机如何选出端口

这三层之间有联系，但职责不同。

### 1.2 一句话理解三层分工

- `jetty` 是任务在主机侧的逻辑发送上下文
- TP 是主机端口到主机端口的一条传输通道
- 路由是交换机拿到包之后逐跳决定从哪个 `outPort` 转发

只要先把这三个概念拆开，后面的配置就容易看懂。

## 二、核心概念

### 2.1 `jetty` 是什么

`jetty` 可以理解成“一条任务对应的发送上下文”。

它主要负责：

- 持有这条任务对应的 WQE
- 跟踪 WQE segment 的发送与确认进度
- 维护窗口、完成回调等状态

它不是：

- 物理端口
- 交换机队列
- TP 本身

在一条 `URMA_WRITE` 或 `URMA_READ` 任务发送时，通常会先：

1. 创建 `jetty`
2. 查找可用 TP
3. 把 `jetty` 绑定到一个或多个 TP
4. 创建 WQE
5. 把 WQE 推入 `jetty`
6. 由事务层继续切分和调度

因此，“同一个 jetty”本质上就是“同一条任务使用的那一个发送上下文”。

### 2.2 TP 是什么

TP 是传输层的端到端通道，对应 `UbTransportChannel`。

一条 TP 的关键属性包括：

- 源节点
- 源端口
- 目的节点
- 目的端口
- 优先级
- TPN
- metric

TP 的端点是主机端口，而不是交换机。

这意味着：

- `transport_channel.csv` 定义的是端点之间有哪些传输通道
- 它不直接决定中间交换机逐跳怎么走

### 2.3 TP group 是什么

一个 `jetty` 可以绑定一组 TP，而不一定只绑定一条。

这组 TP 可以理解成“这条任务允许使用的传输通道集合”。

是否真的同时使用多条 TP，由 `EnableMultiPath` 决定：

- `false`：按单路径方式使用 TP
- `true`：可以同时使用多个 TP

这是主机侧的多路径机制。

### 2.4 路由是什么

路由回答的问题是：

- 包已经发出来了
- 到了某个交换机
- 这个交换机应该从哪个端口转发出去

这里使用的配置文件是 `routing_table.csv`，而不是 `transport_channel.csv`。

所以必须分清：

- TP 解决“端点间有哪些通道”
- 路由解决“包在网络里怎么逐跳走”

## 三、配置文件分工

### 3.1 `node.csv`：节点和端口清单

这个文件描述：

- 有哪些主机和交换机
- 每个节点有多少端口
- 某些节点的转发延迟等基础属性

它决定了“网络里有什么设备”。

### 3.2 `topology.csv`：物理链路

这个文件描述：

- 哪个节点的哪个端口连到哪个节点的哪个端口
- 链路带宽
- 链路时延

它决定了“物理上怎么连”。

### 3.3 `routing_table.csv`：逐跳转发表

这个文件描述：

- 某个节点面对某个目标时
- 可以从哪些 `outPort` 发出去
- 这些端口各自的 `metric` 是多少

格式如下：

```csv
nodeId,dstNodeId,dstPortId,outPorts,metrics
```

它决定的是交换机逐跳选口，不负责创建 TP。

### 3.4 `transport_channel.csv`：TP 定义

这个文件描述：

- 哪些主机端口之间存在 TP
- 每条 TP 属于哪个优先级类别
- 多条 TP 并存时谁更优先

格式如下：

```csv
nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric
```

它决定的是端点之间的传输通道，而不是交换机路由。

### 3.5 `traffic.csv`：任务定义

这个文件描述：

- 哪些任务要发
- 源和目的节点是谁
- 数据量多大
- 是读还是写
- 优先级是多少
- 何时开始

它决定的是“业务流量怎么进入系统”。

## 四、三个最关键的文件分别怎么理解

### 4.1 `traffic.csv`：任务要什么

在这三个文件里，`traffic.csv` 的作用是告诉仿真器：

- 我现在有一条任务
- 它的源和目的是什么
- 它想使用哪个优先级类别的通道

最关键的字段是：

- `sourceNode`
- `destNode`
- `opType`
- `dataSize(Byte)`
- `priority`

其中 `priority` 的含义是：

- 这条任务要去找哪一类 TP

### 4.2 `transport_channel.csv`：系统里有哪些 TP

这个文件定义“TP 库存”。

你可以把它理解成：

- 系统预先声明了哪些传输通道存在
- 每条通道属于哪个优先级池
- 如果同类通道不止一条，它们之间的优先级关系如何

这一步是主机侧建模，而不是交换机建模。

### 4.3 `routing_table.csv`：包进网络后怎么转发

这个文件定义“交换机在每一跳有哪些候选端口”。

你可以把它理解成：

- TP 决定包从哪个主机端口发出来
- 路由表决定包到了中间节点后往哪转

因此，就算 TP 只有一条，只要中间网络里有多条候选路，交换机侧依然可能存在多路径行为。

## 五、`priority` 怎么起作用

### 5.1 任务里的 `priority`

`traffic.csv` 里的 `priority` 表示：

- 这条任务要使用哪一类 TP

例如：

- 一条任务是 `priority=7`
- 那么它会优先查找 `transport_channel.csv` 里 `priority=7` 的 TP

它不会直接去找别的优先级 TP 来替代。

### 5.2 TP 里的 `priority`

`transport_channel.csv` 里的 `priority` 表示：

- 这条 TP 属于哪个业务优先级池

这样做的结果是：

- 不同优先级的任务可以使用不同的 TP 集合
- 即使源和目的节点相同，也可以按优先级做通道隔离

### 5.3 如果任务优先级匹配不到 TP，会发生什么

这里分两种情况：

1. TP 文件里有记录，但优先级不匹配
2. TP 文件里根本没有记录，或者文件为空/缺失

这两种情况的共同点是：

- 当前任务都查不到匹配 TP
- 仿真器都会进入“按需自动建 TP”的逻辑

差别只在于：

- 第一种情况是“已有 TP 存在，但当前任务不能用”
- 第二种情况是“系统里本来就没有预建 TP”

更具体地说：

- 如果任务是 `priority=7`
- TP 文件里只有 `priority=8` 的记录
- 那么这些 `priority=8` 的 TP 不会被拿来凑合使用
- 系统会尝试按当前任务的优先级重新创建新的 TP

所以“优先级不匹配”不会自动降级复用别的优先级 TP。

## 六、两种 `metric` 不是一回事

### 6.1 TP 的 `metric`

`transport_channel.csv` 里的 `metric` 是 TP 级别的代价。

它的作用是：

- 当一条任务匹配到多条 TP 时，对这些 TP 排序
- 如果当前策略只允许 shortest TP，就只保留最小 `metric` 的 TP
- 如果允许所有 TP，则不同 `metric` 的 TP 都可以保留

它影响的是：

- 任务最终会看到哪些 TP

### 6.2 路由表的 `metrics`

`routing_table.csv` 里的 `metrics` 是交换机出端口级别的代价。

它的作用是：

- 把候选 `outPort` 分成 shortest 组和 non-shortest 组
- 最小 `metric` 的端口属于 shortest 组
- 其余端口属于 non-shortest 组

它影响的是：

- 交换机逐跳时可以从哪些端口里选

### 6.3 最容易记住的区分方式

- TP `metric` 管“选哪条传输通道”
- 路由 `metrics` 管“交换机从哪个出端口走”

## 七、`transport_channel.csv` 到底该不该写

这是一个单独值得说明的问题。

### 7.1 可以不写的情况

如果你的目标是：

- 只想让仿真器自动找可达路径
- 不在意具体 TPN 编号
- 不在意精确的端口到端口映射
- 不需要严格区分不同优先级 TP 池

那么可以不写 `transport_channel.csv`，让系统按需自动建 TP。

### 7.2 建议写的情况

如果你的目标是下面任意一种，就应该写：

1. 你想明确规定哪些主机端口之间存在 TP
2. 你想固定 TPN 编号
3. 你想为不同优先级准备不同 TP 池
4. 你想精确控制单 TP、多 TP、某些端口可达、某些端口不可达
5. 你想控制 TP 的 `metric`
6. 你想让实验更可复现、不同运行结果更稳定可对比

### 7.3 自动建 TP 和手写 TP 的区别

自动建 TP 更像是：

- 系统根据当前任务和路由可达性，临时创建一组可用通道

手写 `transport_channel.csv` 更像是：

- 你显式声明系统里应该存在哪些传输通道

所以它不是“有没有必要”，而是：

- 想省事、只求能跑：可以不写
- 想建模精确、行为可控：应该写

## 八、主机侧多路径和网络侧多路径不是一回事

### 8.1 `EnableMultiPath`：主机侧多 TP

`EnableMultiPath` 是 `UbApp` 侧参数，控制一个 `jetty` 是否同时使用多个 TP。

#### 配成 `false`

结果通常是：

- 一个 `jetty` 按单路径方式使用 TP
- 即使 TP 文件里定义了多条候选 TP，也不会同时全部跑起来

#### 配成 `true`

结果通常是：

- 一个 `jetty` 可以同时绑定多个 TP
- 多个主机端口可能同时发送
- 更容易获得更高吞吐

### 8.2 `UsePacketSpray`：交换机侧逐包分散

`UsePacketSpray` 控制的是：

- 如果某台交换机存在多个候选 `outPort`
- 同一条流是固定走其中一个，还是可以按包分散到多个

#### 配成 `false`

结果通常是：

- 同一条流在一台交换机上会稳定命中一个 `outPort`
- 即使有多条等价路，也不一定会同时用上

#### 配成 `true`

结果通常是：

- 同一条流的不同包可以分散到多个候选 `outPort`
- 如果中间网络存在多条可用路径，就可能一起用起来

### 8.3 这两个参数的区别

最简洁的区分方式是：

- `EnableMultiPath` 管“一个任务能不能同时用多条 TP”
- `UsePacketSpray` 管“一条流在交换机里能不能同时吃到多条候选路”

前者是主机侧显式多通道，后者是网络侧逐包分散。

## 九、`UseShortestPaths` 有两个层次

### 9.1 `ns3::UbApp::UseShortestPaths`

这个参数作用在 TP 选择侧。

它影响的是：

- 查 TP 时是否只保留最小 TP `metric` 的候选
- 自动建 TP 时是否只根据 shortest 路径建 TP

可以理解为：

- 它决定任务会看到哪些 TP

### 9.2 `ns3::UbTransportChannel::UseShortestPaths`

这个参数作用在交换机转发侧。

它影响的是包头里的路由标志，交换机会据此决定：

- 只在 shortest 组端口里选
- 还是 shortest 和 non-shortest 都允许参与

可以理解为：

- 它决定包到交换机以后，允许从哪些候选口里选

### 9.3 配置时如何避免误判

建议把它们分开记：

- `UbApp::UseShortestPaths` 管 TP 候选范围
- `UbTransportChannel::UseShortestPaths` 管交换机候选范围

如果你只改了前者，没有改后者，那么：

- TP 候选范围可能变了
- 但交换机转发行为未必变

## 十、常见配置方式与典型结果

### 10.1 单 TP + 不开 spray

配置特征：

- 任务绑定一条 TP
- 交换机虽然可能有多个候选 `outPort`
- 但 `UsePacketSpray=false`

典型结果：

- 一条任务通常沿一条 TP 发送
- 在每一台交换机上，这条流通常只命中一个 `outPort`
- 即使中间有多条等价路，也未必能同时用上

### 10.2 单 TP + 开 spray

配置特征：

- 任务仍然只有一条 TP
- 但交换机允许逐包分散

典型结果：

- 单条 TP 的流量也可能被分散到多条交换路径上
- 如果中间网络存在多条候选路，整体链路利用率可能提高

### 10.3 多 TP + `EnableMultiPath=false`

配置特征：

- TP 文件里存在多条候选 TP
- 但任务按单路径方式使用 TP

典型结果：

- 多条 TP 只是系统里的“可用资源”
- 但单条任务通常只真正使用其中一条

### 10.4 多 TP + `EnableMultiPath=true`

配置特征：

- 同一优先级下存在多条 TP
- 一个 `jetty` 允许同时使用这些 TP

典型结果：

- 多个主机端口可能同时出流
- 主机侧显式并发更明显
- 更容易得到更高吞吐和更短完成时间

### 10.5 shortest-only

配置特征：

- shortest-path 相关参数设置为只使用最短候选

典型结果：

- 交换机只会在 shortest 组端口里选
- non-shortest 端口不会主动参与

### 10.6 允许 non-shortest

配置特征：

- `UseShortestPaths=false`

典型结果：

- 候选集合会扩大
- 如果再配合 spray，可能把更多端口都利用起来

但需要注意：

- 这不等于一定最好
- 非最短路可能带来更长时延、更高资源开销、更复杂的包序表现

## 十一、如果你想达到某种效果，应该怎么配

### 11.1 想做单通道行为

建议：

- `transport_channel.csv` 每类任务只配一条 TP
- 或者虽然配多条 TP，但 `EnableMultiPath=false`
- `UsePacketSpray=false`

结果：

- 任务行为更接近单通道发送

### 11.2 想做主机侧多 TP 并发

建议：

- 在 `transport_channel.csv` 里为同一优先级配置多条 TP
- `EnableMultiPath=true`

结果：

- 一个任务可以同时利用多个 TP
- 多个主机端口可能同时工作

### 11.3 想做交换机侧多路径

建议：

- 在 `routing_table.csv` 里给同一目标配置多个候选 `outPort`
- `UsePacketSpray=true`

结果：

- 同一条 TP 的流量也可能被摊到多条交换路径上

### 11.4 想做优先级隔离

建议：

- 在 `transport_channel.csv` 里为不同 `priority` 配不同 TP 集合
- 在 `traffic.csv` 里让不同任务使用对应的 `priority`

结果：

- 不同类别的任务会进入不同 TP 池

### 11.5 想做最短路优先但保留绕路能力

建议：

- 在 `routing_table.csv` 里用不同 `metrics` 区分 shortest 和 non-shortest
- 用 `UbTransportChannel::UseShortestPaths` 控制是否允许 non-shortest 参与

结果：

- `true` 时只看 shortest 组
- `false` 时 shortest 和 non-shortest 都可能进入候选集

## 十二、结果应该怎么看

即使不做复杂实验，配置之后通常也可以从输出文件判断行为是否符合预期。

### 12.1 看任务层结果

- `runlog/TaskTrace_node_*.tr`

可以看：

- 任务何时开始
- 任务何时完成

### 12.2 看传输层结果

- `runlog/PacketTrace_node_*.tr`

可以看：

- TP 相关发送接收事件
- 首包、尾包、ACK 等行为

### 12.3 看逐跳路径结果

- `runlog/AllPacketTrace_PKT_node_*.tr`

可以看：

- 包经过了哪些交换节点
- 命中了哪些端口

### 12.4 看吞吐结果

- `output/throughput.csv`

可以看：

- 哪些端口真的在出流
- 多条链路是否被同时利用

### 12.5 一个简单判断方法

- 如果你预期多路径并发，就应该在多个端口上看到明显吞吐
- 如果你预期单路径行为，就应该只有少数端口持续有流量
- 如果你预期 shortest-only，就不应该频繁看到 non-shortest 端口被使用

## 十三、读一个案例目录时，建议按什么顺序看

推荐顺序如下：

1. `node.csv`
   - 先看有哪些节点、多少端口

2. `topology.csv`
   - 再看物理链路如何连接、带宽和时延是多少

3. `routing_table.csv`
   - 看交换机面对每个目标有哪些候选 `outPort`
   - 看 shortest 和 non-shortest 如何区分

4. `transport_channel.csv`
   - 看主机之间有哪些 TP
   - 看不同优先级是否使用不同 TP 池
   - 看同一类 TP 是否存在多条通道和不同 `metric`

5. `traffic.csv`
   - 看任务类型、大小、优先级、起始时间

6. `network_attribute.txt`
   - 最后看全局行为开关，例如 `EnableMultiPath`、`UsePacketSpray`、`UseShortestPaths`

这个顺序比较符合“先看网络骨架，再看传输建模，最后看业务流量”的阅读方式。

## 十四、最后压缩成几句话

如果只记最核心的结论，可以记下面这些：

- `jetty` 是任务的主机侧发送上下文
- TP 是主机端口到主机端口的传输通道
- 路由是交换机拿到包之后逐跳选口
- `traffic.csv` 的 `priority` 决定任务要找哪类 TP
- `transport_channel.csv` 的 `priority` 决定 TP 属于哪类通道
- `transport_channel.csv` 的 `metric` 决定多条 TP 之间谁更优先
- `routing_table.csv` 的 `metrics` 决定交换机哪些端口属于 shortest，哪些属于 non-shortest
- `EnableMultiPath` 决定一个任务是否同时使用多条 TP
- `UsePacketSpray` 决定一条流在交换机里是固定一路还是逐包分散
- `UseShortestPaths` 决定候选集合只看最短路还是允许非最短路
- `transport_channel.csv` 可以省略，但只有在你不需要精确控制 TP 结构时才适合省略

把这些层次分清楚，UB 仿真器的配置行为就基本能直接推出来。
