from math import inf
from typing import List, Tuple, Dict
from matching.base import Matcher
from matching.models import Driver as AlgoDriver, Request as AlgoRequest
from core_engine.entities import Driver, Request
from core_engine.map_network import RealRoadMap


def create_osm_cost_fn(road_map: RealRoadMap):
    """
    Tạo hàm cost_fn tính khoảng cách thực tế (mét) giữa 2 Node trên bản đồ OpenStreetMap.
    Có lưu Cache để không phải tính lại Dijkstra nhiều lần cho cùng một cặp Node.
    """
    cost_cache: Dict[Tuple[int, int], float] = {}

    def cost_fn(node_a: int, node_b: int) -> float:
        if node_a == node_b:
            return 0.0
        
        pair = (node_a, node_b)
        if pair in cost_cache:
            return cost_cache[pair]

        path = road_map.get_shortest_path(node_a, node_b)
        if not path:
            cost_cache[pair] = inf
            return inf

        dist = road_map.get_path_distance(path)
        cost_cache[pair] = dist
        return dist

    return cost_fn


def run_matcher_on_sim(
    matcher: Matcher,
    idle_drivers: List[Driver],
    pending_requests: List[Request],
    road_map: RealRoadMap,
    current_time: float
) -> List[Tuple[str, str]]:
    """
    Hàm Wrapper bọc thuật toán để truyền trực tiếp vào `sim.step()`.
    
    Trả về: Danh sách các cặp [(driver_id, request_id), ...]
    """
    # 1. Chuyển đổi Driver -> AlgoDriver
    algo_drivers = [
        AlgoDriver(
            id=d.driver_id,
            location=d.current_node,
            capacity=4,
            available=True
        )
        for d in idle_drivers
    ]

    # 2. Chuyển đổi Request -> AlgoRequest
    algo_requests = [
        AlgoRequest(
            id=r.request_id,
            pickup=r.origin_node,
            dropoff=r.destination_node,
            time=r.request_time,
            passengers=1
        )
        for r in pending_requests
    ]

    # 3. Khởi tạo hàm cost_fn trên bản đồ OSMnx
    cost_fn = create_osm_cost_fn(road_map)

    # 4. Chạy thuật toán ghép cặp
    match_result = matcher.match(algo_requests, algo_drivers, cost_fn)

    # 5. Chuyển kết quả MatchResult về định dạng [(driver_id, request_id)] cho Simulator
    sim_matches = []
    for assignment in match_result.assignments:
        driver_id = assignment.driver_id
        # Mỗi assignment của Greedy/Hungarian chứa 1 request_id
        for req_id in assignment.request_ids:
            sim_matches.append((driver_id, req_id))

    return sim_matches