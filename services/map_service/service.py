"""
Dịch vụ Bản đồ & Định tuyến (Map & Routing Service).
Chịu trách nhiệm:
- Quản lý mạng lưới giao thông OpenStreetMap Hoàn Kiếm, Hà Nội
- Tìm đường đi ngắn nhất (Shortest Path / Dijkstra) có bộ nhớ đệm (LRU Cost Cache)
- Trích xuất hình học tuyến đường uốn lượn theo mặt phố
- Cung cấp tọa độ địa lý cho các dịch vụ khác
"""
from __future__ import annotations

import random
from math import inf
from typing import Dict, List, Optional, Tuple

from core_engine.map_network import RealRoadMap
from services.base import ServiceResult
from services.map_service.contracts import NodeDTO, RouteResponseDTO


class MapService:
    def __init__(
        self,
        place_name: str = "Hoan Kiem, Hanoi, Vietnam",
        road_map: Optional[RealRoadMap] = None,
    ):
        self.place_name = place_name
        self._road_map = road_map
        self._cost_cache: Dict[Tuple[int, int], float] = {}
        self._path_cache: Dict[Tuple[int, int], List[int]] = {}

    def initialize(self) -> ServiceResult[int]:
        """Tải hoặc khôi phục đồ thị OpenStreetMap."""
        try:
            if self._road_map is None:
                self._road_map = RealRoadMap(place_name=self.place_name)
            node_count = len(self._road_map.nodes)
            return ServiceResult.ok(node_count, place=self.place_name)
        except Exception as e:
            return ServiceResult.fail(f"Lỗi khởi tạo bản đồ: {e}")

    @property
    def road_map(self) -> RealRoadMap:
        if self._road_map is None:
            raise RuntimeError("MapService chưa được khởi tạo!")
        return self._road_map

    @property
    def is_ready(self) -> bool:
        return self._road_map is not None and len(self._road_map.nodes) > 0

    def get_all_nodes(self) -> ServiceResult[List[NodeDTO]]:
        """Trả về toàn bộ nút giao lộ để hiển thị lên bản đồ client."""
        if not self.is_ready:
            return ServiceResult.fail("Bản đồ chưa sẵn sàng")
        nodes = []
        for nid in self.road_map.nodes:
            lat, lon = self.road_map.get_node_latlon(nid)
            nodes.append(NodeDTO(id=nid, lat=round(lat, 6), lon=round(lon, 6)))
        return ServiceResult.ok(nodes)

    def get_random_node(self) -> int:
        """Lấy ngẫu nhiên 1 giao lộ."""
        return self.road_map.get_random_node()

    def get_node_latlon(self, node_id: int) -> Tuple[float, float]:
        """Lấy tọa độ (lat, lon) của giao lộ."""
        return self.road_map.get_node_latlon(node_id)

    def find_nearest_node(self, lat: float, lon: float) -> ServiceResult[int]:
        """Tìm nút giao lộ OSM gần nhất sử dụng đồ thị OpenStreetMap/OSMnx."""
        if not self.is_ready:
            return ServiceResult.fail("Bản đồ chưa sẵn sàng")
        try:
            node_id = self.road_map.get_nearest_node(lat, lon)
            return ServiceResult.ok(node_id)
        except Exception as e:
            return ServiceResult.fail(f"Lỗi tìm kiếm giao lộ: {e}")

    def get_landmarks(self) -> ServiceResult[List[dict]]:
        """Danh sách các địa danh trọng điểm tại Quận Hoàn Kiếm, Hà Nội."""
        if not self.is_ready:
            return ServiceResult.fail("Bản đồ chưa sẵn sàng")
        raw_landmarks = [
            {"name": "Hồ Hoàn Kiếm (Đền Ngọc Sơn)", "address": "Phố Đinh Tiên Hoàng, P. Hàng Bạc", "lat": 21.0307, "lon": 105.8524},
            {"name": "Nhà hát Lớn Hà Nội", "address": "Số 1 Tràng Tiền, P. Phan Chu Trinh", "lat": 21.0243, "lon": 105.8576},
            {"name": "Chợ Đồng Xuân", "address": "Phố Đồng Xuân, P. Đồng Xuân", "lat": 21.0381, "lon": 105.8497},
            {"name": "Nhà thờ Lớn Hà Nội", "address": "40 Nhà Chung, P. Hàng Trống", "lat": 21.0288, "lon": 105.8495},
            {"name": "Bệnh viện Hữu nghị Việt Đức", "address": "40 Tràng Thi, P. Hàng Bông", "lat": 21.0275, "lon": 105.8475},
            {"name": "Tràng Tiền Plaza", "address": "24 Hai Bà Trưng, P. Tràng Tiền", "lat": 21.0258, "lon": 105.8542},
            {"name": "Phố ẩm thực Tạ Hiện", "address": "Phố Tạ Hiện, P. Hàng Buồm", "lat": 21.0350, "lon": 105.8522},
            {"name": "Ga Hà Nội", "address": "120 Lê Duẩn, P. Cửa Nam", "lat": 21.0245, "lon": 105.8415},
            {"name": "Cầu Long Biên (Đầu cầu Hoàn Kiếm)", "address": "Phố Trần Nhật Duật, P. Đồng Xuân", "lat": 21.0425, "lon": 105.8520},
            {"name": "Bưu điện Hà Nội", "address": "75 Đinh Tiên Hoàng, P. Tràng Tiền", "lat": 21.0268, "lon": 105.8535},
        ]
        result = []
        for lm in raw_landmarks:
            node = self.road_map.get_nearest_node(lm["lat"], lm["lon"])
            nlat, nlon = self.road_map.get_node_latlon(node)
            result.append({
                "name": lm["name"],
                "address": lm["address"],
                "node_id": node,
                "lat": round(nlat, 6),
                "lon": round(nlon, 6),
            })
        return ServiceResult.ok(result)

    def calculate_route(self, origin_node: int, destination_node: int) -> ServiceResult[RouteResponseDTO]:
        """Tính đường đi ngắn nhất giữa 2 điểm có cache tối ưu."""
        if origin_node == destination_node:
            lat, lon = self.get_node_latlon(origin_node)
            return ServiceResult.ok(RouteResponseDTO(
                path_nodes=[origin_node],
                distance_meters=0.0,
                geometry_coords=[(lat, lon)],
            ))

        pair = (origin_node, destination_node)
        if pair in self._path_cache:
            path = self._path_cache[pair]
            dist = self._cost_cache[pair]
        else:
            path = self.road_map.get_shortest_path(origin_node, destination_node)
            if not path:
                return ServiceResult.fail(f"Không tìm thấy đường từ {origin_node} tới {destination_node}")
            dist = self.road_map.get_path_distance(path)
            self._path_cache[pair] = path
            self._cost_cache[pair] = dist

        coords = self.road_map.get_route_geometry(path)
        return ServiceResult.ok(RouteResponseDTO(
            path_nodes=path,
            distance_meters=dist,
            geometry_coords=coords,
        ))

    def get_distance(self, origin_node: int, destination_node: int) -> float:
        """Hàm tính khoảng cách (mét) siêu tốc phục vụ ma trận chi phí."""
        if origin_node == destination_node:
            return 0.0
        pair = (origin_node, destination_node)
        if pair in self._cost_cache:
            return self._cost_cache[pair]
        path = self.road_map.get_shortest_path(origin_node, destination_node)
        if not path:
            self._cost_cache[pair] = inf
            return inf
        dist = self.road_map.get_path_distance(path)
        self._cost_cache[pair] = dist
        self._path_cache[pair] = path
        return dist
