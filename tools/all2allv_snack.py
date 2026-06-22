#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_snack_v0_v1_csv_config_with_plane_debug.py

功能：
    生成 UBX 仿真器 traffic CSV，用于表示优化方案0 / 优化方案1。

输出两个文件：
    1. 仿真器输入 CSV：
       只包含仿真器要求的 9 列：
       taskId,sourceNodeId,destNodeId,dataSize(Byte),opType,priority,delay,phaseId,dependOnPhases

    2. 复盘调试 CSV：
       在 9 列基础上额外增加：
       isCrossPod, sourcePod, destPod, logicalRound, basePlane, actualPlane,
       sourcePlanePrevPhase, recvPlanePrevPhase, scheduleNote

       这个文件不用于仿真器，只用于检查 v0/v1 的 Plane 编排和依赖来源。

当前建模口径：
    - 16 个 rank；
    - 4 个 Pod；
    - 每个 Pod 4 个 rank；
    - Pod 内通信走 Mesh，不参与 4 Plane 蛇形调度；
    - 跨 Pod 通信进入 snack-v0 / snack-v1；
    - CSV 输出不显式给仿真器传 Plane，但 debug CSV 会保留内部计算的 Plane；
    - dependOnPhases 用空格分隔多个 phaseId，例如 "10 154"。

方案0：
    actualPlane = basePlane

方案1：
    actualPlane = (basePlane + sourceNodeId % NUM_PLANES) % NUM_PLANES

依赖构造：
    1. 发送端 Plane 队列依赖：
       同一个 sourceNodeId + actualPlane 内部按蛇形队列串行。

    2. 接收端 Plane 互斥依赖：
       同一个 destNodeId + actualPlane 不能同时接收多个发送端任务。
       当前采用静态编排方式，按确定顺序提前串成依赖链。

注意：
    如果仿真器本身已经支持 dst + plane 资源互斥，可将
    ENABLE_RECV_PLANE_EXCLUSIVE_DEP = False。
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# 你主要修改这里
# ============================================================

# 方案选择："v0" 或 "v1"
SCHEME = "v1"

# 单条通信任务 dataSize 的最大值 / 最小值比例。
# 可改为 1.2、1.5、2、3。
DATA_RATIO = 1.2

# 最小 dataSize，单位 Byte。 最小的数据量是1MB
MIN_DATA_SIZE_BYTE = 1_048_576

# 随机种子。
RANDOM_SEED = 4

# 输出给仿真器使用的 CSV，不包含 Plane 字段。
OUTPUT_CSV = "snack_v1_ratio1p5_sim.csv"

# 复盘用 CSV，包含 Plane 字段和依赖来源。
OUTPUT_DEBUG_CSV = "snack_v1_ratio1p5_with_plane_debug.csv"

# UBX 拓扑参数。
NUM_RANKS = 16
POD_SIZE = 4
NUM_PLANES = 4

# CSV 默认字段。
OP_TYPE = "URMA_WRITE"
PRIORITY = 7
DELAY = "0ns"

# Pod 内任务依赖模式。
# 当前方案：Pod 内走 Mesh，不参与跨 Pod 4 Plane 蛇形调度，不额外加依赖。
# "none" 表示 Pod 内任务 dependOnPhases 为空。
INTRA_POD_DEP_MODE = "none"

# 是否用 dependOnPhases 静态表达接收端 dest + Plane 互斥。
ENABLE_RECV_PLANE_EXCLUSIVE_DEP = True

# 多依赖分隔符：仿真器要求多个 phaseId 用空格隔开，例如 "10 154"。
MULTI_DEP_DELIMITER = " "


@dataclass
class CsvTask:
    taskId: int
    sourceNodeId: int
    destNodeId: int
    dataSizeByte: int
    opType: str = OP_TYPE
    priority: int = PRIORITY
    delay: str = DELAY
    phaseId: int = 0
    dependOnPhases: List[int] = field(default_factory=list)

    # 以下字段只用于内部编排和 debug 输出，不写入主仿真器 CSV。
    isCrossPod: bool = False
    sourcePod: int = -1
    destPod: int = -1
    logicalRound: Optional[int] = None
    basePlane: Optional[int] = None
    actualPlane: Optional[int] = None

    # debug：记录依赖来源。
    sourcePlanePrevPhase: Optional[int] = None
    recvPlanePrevPhase: Optional[int] = None
    scheduleNote: str = ""


def rank_to_pod(rank: int) -> int:
    """根据 rank id 计算 Pod 编号。"""
    return rank // POD_SIZE


def is_cross_pod(source: int, dest: int) -> bool:
    """判断 source -> dest 是否跨 Pod。"""
    return rank_to_pod(source) != rank_to_pod(dest)


def add_unique_dependency(task: CsvTask, phase_id: int) -> None:
    """
    给任务添加依赖，避免重复依赖。

    dependOnPhases 里放的是 phaseId，不是 taskId。
    当前脚本中 phaseId 默认等于 taskId。
    """
    if phase_id not in task.dependOnPhases:
        task.dependOnPhases.append(phase_id)


def format_depends(depends: List[int]) -> str:
    """将依赖列表转换为仿真器需要的字符串。多个依赖用空格隔开。"""
    if not depends:
        return ""
    return MULTI_DEP_DELIMITER.join(str(x) for x in depends)


def generate_random_matrix() -> List[List[int]]:
    """
    生成 16×16 随机通信矩阵。

    当前口径：
        控制单条通信任务 dataSize 的最大值 / 最小值 = DATA_RATIO。

    matrix[src][dst]:
        src 发给 dst 的数据量，单位 Byte。

    自通信：
        matrix[i][i] = 0。
    """
    rng = random.Random(RANDOM_SEED)

    max_data_size = int(round(MIN_DATA_SIZE_BYTE * DATA_RATIO))

    matrix: List[List[int]] = []
    for src in range(NUM_RANKS):
        row = []
        for dst in range(NUM_RANKS):
            if src == dst:
                row.append(0)
            else:
                row.append(rng.randint(MIN_DATA_SIZE_BYTE, max_data_size))
        matrix.append(row)

    # 固定两个值，保证全局单条任务 min/max 更明确。
    if NUM_RANKS >= 9:
        matrix[0][4] = MIN_DATA_SIZE_BYTE
        matrix[0][8] = max_data_size

    return matrix


def build_tasks(matrix: List[List[int]]) -> List[CsvTask]:
    """
    根据 16×16 矩阵生成 16×15=240 条任务。

    taskId 和 phaseId 默认一一对应：
        phaseId = taskId
    """
    tasks: List[CsvTask] = []
    task_id = 0

    for src in range(NUM_RANKS):
        for dst in range(NUM_RANKS):
            if src == dst:
                continue

            src_pod = rank_to_pod(src)
            dst_pod = rank_to_pod(dst)
            cross = src_pod != dst_pod

            task = CsvTask(
                taskId=task_id,
                sourceNodeId=src,
                destNodeId=dst,
                dataSizeByte=matrix[src][dst],
                phaseId=task_id,
                isCrossPod=cross,
                sourcePod=src_pod,
                destPod=dst_pod,
                scheduleNote="cross-pod snack schedule" if cross else "intra-pod Mesh, no snack plane dependency",
            )
            tasks.append(task)
            task_id += 1

    return tasks


def snake_position(order_in_source: int) -> Tuple[int, int]:
    """
    对单个 source rank 的跨 Pod 任务进行蛇形位置映射。

    输入：
        order_in_source:
            该 source 的跨 Pod 任务按 dataSize 降序排列后的下标。
            0 对应最大任务 M12，11 对应最小任务 M1。

    输出：
        logicalRound:
            逻辑执行轮次，只表示静态编排位置，不是强同步屏障。

        basePlane:
            基础蛇形分配 Plane。

    映射：
        Plane0: M12 -> M5 -> M4
        Plane1: M11 -> M6 -> M3
        Plane2: M10 -> M7 -> M2
        Plane3: M9  -> M8 -> M1
    """
    logical_round = order_in_source // NUM_PLANES
    pos = order_in_source % NUM_PLANES

    if logical_round % 2 == 0:
        base_plane = pos
    else:
        base_plane = NUM_PLANES - 1 - pos

    return logical_round, base_plane


def calc_actual_plane(base_plane: int, source: int) -> int:
    """
    计算最终实际逻辑 Plane。

    v0:
        actualPlane = basePlane

    v1:
        actualPlane = (basePlane + source % NUM_PLANES) % NUM_PLANES
    """
    if SCHEME == "v0":
        return base_plane
    if SCHEME == "v1":
        return (base_plane + source % NUM_PLANES) % NUM_PLANES
    raise ValueError(f"SCHEME 必须是 v0 或 v1，当前为: {SCHEME}")


def assign_planes_for_cross_pod_tasks(tasks: List[CsvTask]) -> None:
    """
    只对跨 Pod 任务分配 basePlane / actualPlane / logicalRound。

    Pod 内任务：
        不参与蛇形；
        basePlane/actualPlane/logicalRound 保持为空；
        dependOnPhases 默认不添加。
    """
    for src in range(NUM_RANKS):
        source_cross_tasks = [
            t for t in tasks
            if t.sourceNodeId == src and t.isCrossPod
        ]

        # 每个 source rank 内部，12 个跨 Pod 任务按 dataSize 降序。
        # tie-breaker 用 destNodeId 和 taskId，保证可复现。
        source_cross_tasks.sort(
            key=lambda t: (-t.dataSizeByte, t.destNodeId, t.taskId)
        )

        for order, task in enumerate(source_cross_tasks):
            logical_round, base_plane = snake_position(order)
            actual_plane = calc_actual_plane(base_plane, src)

            task.logicalRound = logical_round
            task.basePlane = base_plane
            task.actualPlane = actual_plane


def build_source_plane_dependencies(tasks: List[CsvTask]) -> None:
    """
    构造发送端 Plane 队列依赖。

    约束：
        同一个 sourceNodeId + actualPlane 内部串行。

    例如：
        R0 Plane0: T0 -> T1 -> T2

    则：
        T0.dependOnPhases = 空
        T1.dependOnPhases 增加 T0.phaseId
        T2.dependOnPhases 增加 T1.phaseId

    该约束表达：
        同一个发送端 Plane 的后续任务不能越过当前任务。
    """
    queues: Dict[Tuple[int, int], List[CsvTask]] = {}

    for task in tasks:
        if not task.isCrossPod:
            continue
        assert task.actualPlane is not None
        key = (task.sourceNodeId, task.actualPlane)
        queues.setdefault(key, []).append(task)

    for (source, plane), queue in queues.items():
        queue.sort(key=lambda t: (t.logicalRound, t.taskId))

        prev: Optional[CsvTask] = None
        for task in queue:
            if prev is not None:
                add_unique_dependency(task, prev.phaseId)
                task.sourcePlanePrevPhase = prev.phaseId
            prev = task


def build_receiver_plane_dependencies(tasks: List[CsvTask]) -> None:
    """
    构造接收端 Plane 互斥依赖。

    约束：
        同一个 destNodeId + actualPlane 不能同时接收多个发送端任务。

    当前采用静态编排：
        提前规定同一个 dest + actualPlane 上的任务顺序，并通过 dependOnPhases 串起来。

    排序规则：
        1. logicalRound 小的在前；
        2. dataSizeByte 大的在前；
        3. sourceNodeId 小的在前；
        4. taskId 小的在前。

    这个排序规则的含义：
        - 优先保证逻辑编排靠前的任务先占用接收端 Plane；
        - 同一逻辑位置上，大任务优先；
        - 仍有并列时按 sourceNodeId / taskId 保证确定性。
    """
    if not ENABLE_RECV_PLANE_EXCLUSIVE_DEP:
        return

    recv_queues: Dict[Tuple[int, int], List[CsvTask]] = {}

    for task in tasks:
        if not task.isCrossPod:
            continue
        assert task.actualPlane is not None
        key = (task.destNodeId, task.actualPlane)
        recv_queues.setdefault(key, []).append(task)

    for (dest, plane), queue in recv_queues.items():
        queue.sort(
            key=lambda t: (
                t.logicalRound if t.logicalRound is not None else 10**9,
                -t.dataSizeByte,
                t.sourceNodeId,
                t.taskId,
            )
        )

        prev: Optional[CsvTask] = None
        for task in queue:
            if prev is not None:
                add_unique_dependency(task, prev.phaseId)
                task.recvPlanePrevPhase = prev.phaseId
            prev = task


def apply_intra_pod_policy(tasks: List[CsvTask]) -> None:
    """
    处理 Pod 内任务依赖。

    当前策略：
        INTRA_POD_DEP_MODE = "none"

    含义：
        Pod 内通信走 Mesh，不参与跨 Pod 4 Plane 调度；
        脚本不为 Pod 内任务额外添加 dependOnPhases。

    因此，例如 R0 -> R1、R0 -> R2、R0 -> R3 这些 Pod 内任务：
        dependOnPhases 默认为空；
        它们是否在仿真器里发生 Mesh 资源竞争，由仿真器的 Mesh 资源模型决定。
    """
    if INTRA_POD_DEP_MODE != "none":
        raise ValueError(f"当前只实现 INTRA_POD_DEP_MODE='none'，当前为 {INTRA_POD_DEP_MODE}")

    # 不做任何处理。
    return


def write_simulator_csv(tasks: List[CsvTask], output_path: Path) -> None:
    """
    写出仿真器输入 CSV。

    注意：
        不包含 Plane 字段。
    """
    fields = [
        "taskId",
        "sourceNodeId",
        "destNodeId",
        "dataSize(Byte)",
        "opType",
        "priority",
        "delay",
        "phaseId",
        "dependOnPhases",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for task in sorted(tasks, key=lambda t: t.taskId):
            writer.writerow({
                "taskId": task.taskId,
                "sourceNodeId": task.sourceNodeId,
                "destNodeId": task.destNodeId,
                "dataSize(Byte)": task.dataSizeByte,
                "opType": task.opType,
                "priority": task.priority,
                "delay": task.delay,
                "phaseId": task.phaseId,
                "dependOnPhases": format_depends(task.dependOnPhases),
            })


def write_debug_csv(tasks: List[CsvTask], output_path: Path) -> None:
    """
    写出复盘 debug CSV。

    这个文件比仿真器输入多了 Plane 和依赖来源字段。
    只用于人工检查，不建议直接喂给仿真器。
    """
    fields = [
        "taskId",
        "sourceNodeId",
        "destNodeId",
        "sourcePod",
        "destPod",
        "isCrossPod",
        "dataSize(Byte)",
        "opType",
        "priority",
        "delay",
        "phaseId",
        "dependOnPhases",
        "logicalRound",
        "basePlane",
        "actualPlane",
        "sourcePlanePrevPhase",
        "recvPlanePrevPhase",
        "scheduleNote",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for task in sorted(tasks, key=lambda t: t.taskId):
            writer.writerow({
                "taskId": task.taskId,
                "sourceNodeId": task.sourceNodeId,
                "destNodeId": task.destNodeId,
                "sourcePod": task.sourcePod,
                "destPod": task.destPod,
                "isCrossPod": int(task.isCrossPod),
                "dataSize(Byte)": task.dataSizeByte,
                "opType": task.opType,
                "priority": task.priority,
                "delay": task.delay,
                "phaseId": task.phaseId,
                "dependOnPhases": format_depends(task.dependOnPhases),
                "logicalRound": "" if task.logicalRound is None else task.logicalRound,
                "basePlane": "" if task.basePlane is None else task.basePlane,
                "actualPlane": "" if task.actualPlane is None else task.actualPlane,
                "sourcePlanePrevPhase": "" if task.sourcePlanePrevPhase is None else task.sourcePlanePrevPhase,
                "recvPlanePrevPhase": "" if task.recvPlanePrevPhase is None else task.recvPlanePrevPhase,
                "scheduleNote": task.scheduleNote,
            })


def print_summary(matrix: List[List[int]], tasks: List[CsvTask]) -> None:
    """打印摘要信息，便于快速检查。"""
    nonzero = [matrix[i][j] for i in range(NUM_RANKS) for j in range(NUM_RANKS) if i != j]
    cross_tasks = [t for t in tasks if t.isCrossPod]
    intra_tasks = [t for t in tasks if not t.isCrossPod]

    print("========== Traffic Generate Summary ==========")
    print(f"SCHEME                         = {SCHEME}")
    print(f"DATA_RATIO                     = {DATA_RATIO}")
    print(f"single-task min dataSize       = {min(nonzero)}")
    print(f"single-task max dataSize       = {max(nonzero)}")
    print(f"single-task max/min            = {max(nonzero) / min(nonzero):.4f}x")
    print(f"NUM_RANKS                      = {NUM_RANKS}")
    print(f"POD_SIZE                       = {POD_SIZE}")
    print(f"NUM_PODS                       = {NUM_RANKS // POD_SIZE}")
    print(f"NUM_PLANES                     = {NUM_PLANES}")
    print(f"total tasks                    = {len(tasks)}")
    print(f"intra-pod Mesh tasks           = {len(intra_tasks)}")
    print(f"cross-pod snack tasks          = {len(cross_tasks)}")
    print(f"INTRA_POD_DEP_MODE             = {INTRA_POD_DEP_MODE}")
    print(f"ENABLE_RECV_PLANE_EXCLUSIVE_DEP= {ENABLE_RECV_PLANE_EXCLUSIVE_DEP}")
    print(f"OUTPUT_CSV                     = {OUTPUT_CSV}")
    print(f"OUTPUT_DEBUG_CSV               = {OUTPUT_DEBUG_CSV}")
    print("----------------------------------------------")

    # 每个 source 的跨 Pod 队列长度检查。
    for src in range(NUM_RANKS):
        source_cross = [t for t in cross_tasks if t.sourceNodeId == src]
        plane_counts = {p: 0 for p in range(NUM_PLANES)}
        for t in source_cross:
            if t.actualPlane is not None:
                plane_counts[t.actualPlane] += 1
        print(f"R{src:02d}: cross_pod_tasks={len(source_cross):2d}, queue_lengths={plane_counts}")

    print("----------------------------------------------")
    multi_dep = [t for t in tasks if len(t.dependOnPhases) > 1]
    print("Example tasks with multiple dependencies:")
    for t in multi_dep[:10]:
        print(
            f"taskId={t.taskId}, src={t.sourceNodeId}, dst={t.destNodeId}, "
            f"actualPlane={t.actualPlane}, deps={format_depends(t.dependOnPhases)}, "
            f"srcPrev={t.sourcePlanePrevPhase}, recvPrev={t.recvPlanePrevPhase}"
        )
    print("==============================================")


def main() -> None:
    """主流程。"""
    if NUM_RANKS % POD_SIZE != 0:
        raise ValueError("NUM_RANKS 必须能被 POD_SIZE 整除。")

    if SCHEME not in {"v0", "v1"}:
        raise ValueError("SCHEME 必须是 'v0' 或 'v1'。")

    matrix = generate_random_matrix()
    tasks = build_tasks(matrix)

    apply_intra_pod_policy(tasks)
    assign_planes_for_cross_pod_tasks(tasks)
    build_source_plane_dependencies(tasks)
    build_receiver_plane_dependencies(tasks)

    write_simulator_csv(tasks, Path(OUTPUT_CSV))
    write_debug_csv(tasks, Path(OUTPUT_DEBUG_CSV))
    print_summary(matrix, tasks)


if __name__ == "__main__":
    main()