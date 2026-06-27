# Traffic 平面选择说明

这个 case 的 `traffic.csv` 用 task 的 `priority` 显式指定跨组流走哪个 Clos 平面。

## Priority 到平面的映射

| Priority | 平面 | 设备端口 | 交换机 |
|---:|---:|---:|---:|
| 3 | plane 0 | port 3 | switch 16 |
| 4 | plane 1 | port 4 | switch 17 |
| 5 | plane 2 | port 5 | switch 18 |
| 6 | plane 3 | port 6 | switch 19 |
| 7 | 组内 mesh | port 0/1/2 | 直连 mesh |

因此，跨组流的 `priority` 就是平面选择：

```text
priority 3 -> plane0
priority 4 -> plane1
priority 5 -> plane2
priority 6 -> plane3
```

## 每个 rank 的跨组流平面

下面只列跨组流，组内 mesh 流统一是 `priority 7`。

| Source | 跨组流 -> priority / plane |
|---:|---|
| 0 | `0->12:p3/plane0`, `0->9:p4/plane1`, `0->6:p5/plane2`, `0->15:p6/plane3`, `0->4:p3/plane0`, `0->13:p4/plane1`, `0->10:p5/plane2`, `0->7:p6/plane3`, `0->8:p3/plane0`, `0->5:p4/plane1`, `0->14:p5/plane2`, `0->11:p6/plane3` |
| 1 | `1->13:p3/plane0`, `1->8:p4/plane1`, `1->7:p5/plane2`, `1->14:p6/plane3`, `1->5:p3/plane0`, `1->12:p4/plane1`, `1->11:p5/plane2`, `1->6:p6/plane3`, `1->9:p3/plane0`, `1->4:p4/plane1`, `1->15:p5/plane2`, `1->10:p6/plane3` |
| 2 | `2->14:p3/plane0`, `2->11:p4/plane1`, `2->4:p5/plane2`, `2->13:p6/plane3`, `2->6:p3/plane0`, `2->15:p4/plane1`, `2->8:p5/plane2`, `2->5:p6/plane3`, `2->10:p3/plane0`, `2->7:p4/plane1`, `2->12:p5/plane2`, `2->9:p6/plane3` |
| 3 | `3->15:p3/plane0`, `3->10:p4/plane1`, `3->5:p5/plane2`, `3->12:p6/plane3`, `3->7:p3/plane0`, `3->14:p4/plane1`, `3->9:p5/plane2`, `3->4:p6/plane3`, `3->11:p3/plane0`, `3->6:p4/plane1`, `3->13:p5/plane2`, `3->8:p6/plane3` |
| 4 | `4->8:p3/plane0`, `4->13:p4/plane1`, `4->2:p5/plane2`, `4->11:p6/plane3`, `4->0:p3/plane0`, `4->9:p4/plane1`, `4->14:p5/plane2`, `4->3:p6/plane3`, `4->12:p3/plane0`, `4->1:p4/plane1`, `4->10:p5/plane2`, `4->15:p6/plane3` |
| 5 | `5->9:p3/plane0`, `5->12:p4/plane1`, `5->3:p5/plane2`, `5->10:p6/plane3`, `5->1:p3/plane0`, `5->8:p4/plane1`, `5->15:p5/plane2`, `5->2:p6/plane3`, `5->13:p3/plane0`, `5->0:p4/plane1`, `5->11:p5/plane2`, `5->14:p6/plane3` |
| 6 | `6->10:p3/plane0`, `6->15:p4/plane1`, `6->0:p5/plane2`, `6->9:p6/plane3`, `6->2:p3/plane0`, `6->11:p4/plane1`, `6->12:p5/plane2`, `6->1:p6/plane3`, `6->14:p3/plane0`, `6->3:p4/plane1`, `6->8:p5/plane2`, `6->13:p6/plane3` |
| 7 | `7->11:p3/plane0`, `7->14:p4/plane1`, `7->1:p5/plane2`, `7->8:p6/plane3`, `7->3:p3/plane0`, `7->10:p4/plane1`, `7->13:p5/plane2`, `7->0:p6/plane3`, `7->15:p3/plane0`, `7->2:p4/plane1`, `7->9:p5/plane2`, `7->12:p6/plane3` |
| 8 | `8->4:p3/plane0`, `8->1:p4/plane1`, `8->14:p5/plane2`, `8->7:p6/plane3`, `8->12:p3/plane0`, `8->5:p4/plane1`, `8->2:p5/plane2`, `8->15:p6/plane3`, `8->0:p3/plane0`, `8->13:p4/plane1`, `8->6:p5/plane2`, `8->3:p6/plane3` |
| 9 | `9->5:p3/plane0`, `9->0:p4/plane1`, `9->15:p5/plane2`, `9->6:p6/plane3`, `9->13:p3/plane0`, `9->4:p4/plane1`, `9->3:p5/plane2`, `9->14:p6/plane3`, `9->1:p3/plane0`, `9->12:p4/plane1`, `9->7:p5/plane2`, `9->2:p6/plane3` |
| 10 | `10->6:p3/plane0`, `10->3:p4/plane1`, `10->12:p5/plane2`, `10->5:p6/plane3`, `10->14:p3/plane0`, `10->7:p4/plane1`, `10->0:p5/plane2`, `10->13:p6/plane3`, `10->2:p3/plane0`, `10->15:p4/plane1`, `10->4:p5/plane2`, `10->1:p6/plane3` |
| 11 | `11->7:p3/plane0`, `11->2:p4/plane1`, `11->13:p5/plane2`, `11->4:p6/plane3`, `11->15:p3/plane0`, `11->6:p4/plane1`, `11->1:p5/plane2`, `11->12:p6/plane3`, `11->3:p3/plane0`, `11->14:p4/plane1`, `11->5:p5/plane2`, `11->0:p6/plane3` |
| 12 | `12->0:p3/plane0`, `12->5:p4/plane1`, `12->10:p5/plane2`, `12->3:p6/plane3`, `12->8:p3/plane0`, `12->1:p4/plane1`, `12->6:p5/plane2`, `12->11:p6/plane3`, `12->4:p3/plane0`, `12->9:p4/plane1`, `12->2:p5/plane2`, `12->7:p6/plane3` |
| 13 | `13->1:p3/plane0`, `13->4:p4/plane1`, `13->11:p5/plane2`, `13->2:p6/plane3`, `13->9:p3/plane0`, `13->0:p4/plane1`, `13->7:p5/plane2`, `13->10:p6/plane3`, `13->5:p3/plane0`, `13->8:p4/plane1`, `13->3:p5/plane2`, `13->6:p6/plane3` |
| 14 | `14->2:p3/plane0`, `14->7:p4/plane1`, `14->8:p5/plane2`, `14->1:p6/plane3`, `14->10:p3/plane0`, `14->3:p4/plane1`, `14->4:p5/plane2`, `14->9:p6/plane3`, `14->6:p3/plane0`, `14->11:p4/plane1`, `14->0:p5/plane2`, `14->5:p6/plane3` |
| 15 | `15->3:p3/plane0`, `15->6:p4/plane1`, `15->9:p5/plane2`, `15->0:p6/plane3`, `15->11:p3/plane0`, `15->2:p4/plane1`, `15->5:p5/plane2`, `15->8:p6/plane3`, `15->7:p3/plane0`, `15->10:p4/plane1`, `15->1:p5/plane2`, `15->4:p6/plane3` |

## Rank0 Profile

对应 rank0 profile：

- `../../profiles/ns3ub-ubx16-a2av-gradient-max2p0x-closv3-prioritytp-rank0-profile.html`
