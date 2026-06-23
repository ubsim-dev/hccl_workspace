import net_sim_builder as netsim
import networkx as nx
import argparse
import re  # 用于校验带宽格式


# ==================== 保留原寻路函数（路由表生成需要） ====================
def all_simple_paths(G, source, target):
    try:
        paths = nx.all_simple_paths(G, source, target, cutoff=3)
    except nx.NetworkXNoPath:
        paths = []
    return paths

def all_shortest_paths(G, source, target):
    try:
        paths = nx.all_shortest_paths(G, source, target)
    except nx.NetworkXNoPath:
        paths = []
    return paths

# ==================== 带宽格式校验函数 ====================
def validate_bandwidth(bandwidth_str):
    """校验带宽格式（如 400Gbps、100Mbps、10Gbps）"""
    pattern = r'^\d+(Gbps|Mbps|Kbps|bps)$'
    if not re.match(pattern, bandwidth_str):
        raise argparse.ArgumentTypeError(
            f"无效的带宽格式: {bandwidth_str}，请使用如 400Gbps、100Mbps 格式"
        )
    return bandwidth_str


# ==================== 拓扑参数定义 ====================
# 1024节点网络：16组64p单元，每组64个host + 12个底层switch
# 无上层交换机，底层交换机跨组全互联（同索引switch间全mesh，edge_count=4）
NUM_GROUPS = 16          # 64p单元组数
HOSTS_PER_GROUP = 64     # 每组host数
SWITCHES_PER_GROUP = 12  # 每组底层switch数
INTER_SWITCH_EDGES = 4   # 同索引switch间互联的并行边数（提供4条等价路径）
TOTAL_HOSTS = NUM_GROUPS * HOSTS_PER_GROUP  # 1024
TOTAL_LOWER_SW = NUM_GROUPS * SWITCHES_PER_GROUP  # 192


if __name__ == '__main__':
    # 初始化拓扑图对象
    graph = netsim.NetworkSimulationGraph()

    # 1. 创建参数解析器
    parser = argparse.ArgumentParser(
        description='1024-Node Network (Remove OCS) - 无上层交换机，底层交换机跨组直接全互联',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # ========== 链路可配置参数 ==========
    parser.add_argument('-bw', '--link-bandwidth',
                        type=validate_bandwidth,
                        default='400Gbps',
                        help='链路带宽 (format: XXXGbps/XXXMbps/XXXKbps, e.g. 400Gbps)')
    parser.add_argument('-hd', '--host-switch-delay',
                        type=int,
                        default=100,
                        help='主机-底层交换机时延 (ns)')
    parser.add_argument('-sd', '--switch-switch-delay',
                        type=int,
                        default=20,
                        help='底层交换机-底层交换机跨组时延 (ns)')
    parser.add_argument('-fd', '--forward-delay',
                        type=int,
                        default=1,
                        help='节点转发时延 (ns)')
    parser.add_argument('-ec', '--edge-count',
                        type=int,
                        default=1,
                        help='主机-交换机链路的并行边数')
    parser.add_argument('-ise', '--inter-switch-edge-count',
                        type=int,
                        default=INTER_SWITCH_EDGES,
                        help=f'跨组交换机间互联的并行边数 (默认{INTER_SWITCH_EDGES})')
    parser.add_argument('-pl', '--priority-list',
                        type=int,
                        nargs='+',
                        default=[7, 8],
                        help='传输通道优先级列表 (整数列表), e.g. -pl 5 6 7 8')

    # 2. 解析参数
    args = parser.parse_args()

    link_bandwidth = args.link_bandwidth
    host_switch_delay = args.host_switch_delay
    switch_switch_delay = args.switch_switch_delay
    forward_delay = args.forward_delay
    edge_count = args.edge_count
    inter_switch_edge_count = args.inter_switch_edge_count
    priority_list = args.priority_list

    # ========== 参数合法性校验 ==========
    if host_switch_delay <= 0:
        parser.error("host-switch-delay must be a positive integer")
    if switch_switch_delay <= 0:
        parser.error("switch-switch-delay must be a positive integer")
    if forward_delay <= 0:
        parser.error("forward-delay must be a positive integer")
    if edge_count <= 0:
        parser.error("edge-count must be a positive integer")
    if inter_switch_edge_count <= 0:
        parser.error("inter-switch-edge-count must be a positive integer")
    if not priority_list:
        parser.error("priority-list cannot be empty")
    for p in priority_list:
        if p <= 0:
            parser.error(f"Invalid priority value {p}: must be a positive integer")

    # 3. 生成节点（ID规划：主机在前，底层交换机在后，顺序编号）
    host_ids = []
    lower_switch_ids = []

    # 3.1 创建1024个主机节点
    print(f"=== 创建 {TOTAL_HOSTS} 个主机节点 ===")
    for host_id in range(TOTAL_HOSTS):
        graph.add_netisim_host(host_id, forward_delay=f'{forward_delay}ns')
        host_ids.append(host_id)

    # 3.2 创建192个底层交换机节点
    print(f"=== 创建 {TOTAL_LOWER_SW} 个底层交换机节点 ===")
    switch_base_id = TOTAL_HOSTS  # 1024
    for switch_idx in range(TOTAL_LOWER_SW):
        switch_id = switch_base_id + switch_idx
        graph.add_netisim_node(switch_id, forward_delay=f'{forward_delay}ns')
        lower_switch_ids.append(switch_id)

    # ===================== 辅助函数 =====================
    def get_group_hosts(group_idx):
        """获取第group_idx组(0-based)的64个host ID"""
        start = group_idx * HOSTS_PER_GROUP
        return host_ids[start:start + HOSTS_PER_GROUP]

    def get_group_lower_switch_ids(group_idx):
        """获取第group_idx组(0-based)的12个底层交换机ID"""
        start = group_idx * SWITCHES_PER_GROUP
        return lower_switch_ids[start:start + SWITCHES_PER_GROUP]

    # 4. 建立拓扑连接

    # ========== 4.1 组内连接：每组内64个host与12个底层switch全连接 ==========
    print("=== 建立组内 Host <-> 底层交换机的连接 ===")
    for group_idx in range(NUM_GROUPS):
        group_hosts = get_group_hosts(group_idx)
        group_switches = get_group_lower_switch_ids(group_idx)

        for host_idx, host_id in enumerate(group_hosts):
            for sw_idx, switch_id in enumerate(group_switches):
                graph.add_netisim_edge(
                    host_id, switch_id,
                    bandwidth=link_bandwidth,
                    delay=f'{host_switch_delay}ns',
                    edge_count=edge_count
                )
        print(f"  Group {group_idx}: {len(group_hosts)} hosts <-> {len(group_switches)} switches 连接完成")

    # ========== 4.2 跨组连接：同索引底层交换机全互联 ==========
    # 对于每个switch索引i(0~11)，16个组中索引为i的switch两两互联，每对连接使用inter_switch_edge_count条并行边
    # 这替代了原来的上层交换机，提供直接的switch-to-switch跨组路径
    # 每个底层switch：
    #   - 64个port连接组内host
    #   - 15个其他组的同索引switch × inter_switch_edge_count = 15×4 = 60个port用于跨组互联
    #   - 总计 64 + 60 = 124 个port
    print("\n=== 建立跨组底层交换机直接互联 ===")
    inter_switch_link_count = 0
    for sw_idx in range(SWITCHES_PER_GROUP):
        # 收集所有16个组中索引为sw_idx的switch ID
        same_index_switches = []
        for group_idx in range(NUM_GROUPS):
            group_switches = get_group_lower_switch_ids(group_idx)
            same_index_switches.append(group_switches[sw_idx])

        # 全互联：两两之间建立连接
        for i in range(len(same_index_switches)):
            for j in range(i + 1, len(same_index_switches)):
                sw_a = same_index_switches[i]
                sw_b = same_index_switches[j]
                graph.add_netisim_edge(
                    sw_a, sw_b,
                    bandwidth=link_bandwidth,
                    delay=f'{switch_switch_delay}ns',
                    edge_count=inter_switch_edge_count
                )
                inter_switch_link_count += 1

        print(f"  Switch index {sw_idx}: {len(same_index_switches)} 个同索引switch全互联完成"
              f" ({len(same_index_switches) * (len(same_index_switches) - 1) // 2} 条链路,"
              f" edge_count={inter_switch_edge_count})")

    print(f"\n  跨组互联总计: {inter_switch_link_count} 条逻辑链路"
          f" (每条edge_count={inter_switch_edge_count})")

    # 5. 生成配置文件
    print("\n=== 生成拓扑配置文件 ===")
    total_nodes = TOTAL_HOSTS + TOTAL_LOWER_SW
    print(f"拓扑概要: {TOTAL_HOSTS} hosts + {TOTAL_LOWER_SW} switches = {total_nodes} nodes")
    print(f"  每个底层switch: 64 port(host) + {15 * inter_switch_edge_count} port(inter-switch) = {64 + 15 * inter_switch_edge_count} ports")

    graph.build_graph_config()
    graph.gen_route_table(path_finding_algo=all_shortest_paths, multiple_workers=8)
    graph.config_transport_channel(priority_list=priority_list)
    graph.write_config()
    print("\n=== 1024节点无OCS拓扑配置文件已生成完成 ===")