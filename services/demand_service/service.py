"""
Dịch vụ Phát sinh Nhu cầu Đi lại (Demand Service).
Quản lý:
- Quá trình Poisson phát sinh yêu cầu theo không-thời gian
- Lựa chọn điểm đón/trả ngẫu nhiên hoặc theo cụm nhu cầu thực tế
- Phát sinh sự kiện REQUEST_CREATED qua EventBus
"""
from __future__ import annotations

import random
from math import hypot
from typing import List, Optional, Protocol
import numpy as np

from services.base import event_bus, EventType
from services.demand_service.contracts import DemandConfigDTO, TripRequestDTO
from services.map_service.contracts import NodeDTO


class NodeProvider(Protocol):
    def get_random_node(self) -> int: ...
    def get_all_nodes(self) -> object: ...
    def get_node_latlon(self, node_id: int) -> tuple[float, float]: ...


class DemandService:
    def __init__(self, node_provider: NodeProvider, config: Optional[DemandConfigDTO] = None):
        self.node_provider = node_provider
        self.config = config or DemandConfigDTO()
        if self.config.arrival_rate <= 0:
            raise ValueError("arrival_rate phải lớn hơn 0")
        if len(self.config.time_multipliers) != 24 or any(
            multiplier < 0 for multiplier in self.config.time_multipliers
        ):
            raise ValueError("time_multipliers phải có 24 giá trị không âm")
        if not 0 <= self.config.start_hour <= 23:
            raise ValueError("start_hour phải nằm trong khoảng 0–23")
        if not 0 <= self.config.core_zone_weight <= 1:
            raise ValueError("core_zone_weight phải nằm trong khoảng 0–1")
        if not 0 <= self.config.cross_zone_probability <= 1:
            raise ValueError("cross_zone_probability phải nằm trong khoảng 0–1")
        self.req_counter = 0
        self._random = random.Random(self.config.seed)
        self._rng = np.random.default_rng(self.config.seed)
        self._zones = self._load_zones()
        self._max_rate = max(self.config.time_multipliers) * self.config.arrival_rate
        self.next_arrival_time = 0.0
        self.reset(self.config.seed)

    def _load_zones(self) -> list[list[int]]:
        if not self.config.use_spatial_profile:
            return []
        get_nodes = getattr(self.node_provider, "get_all_nodes", None)
        if get_nodes is None:
            return []
        result = get_nodes()
        if not result.success or result.data is None:
            raise RuntimeError(result.error or "Map Service không trả về danh sách nút")
        nodes: List[NodeDTO] = result.data
        if len(nodes) < 4:
            return []
        center_lat = sum(node.lat for node in nodes) / len(nodes)
        center_lon = sum(node.lon for node in nodes) / len(nodes)
        ranked = sorted(
            nodes,
            key=lambda node: hypot(
                (node.lat - center_lat) * 111_000,
                (node.lon - center_lon) * 111_000 * np.cos(np.radians(center_lat)),
            ),
        )
        split_at = max(1, min(len(ranked) - 1, round(len(ranked) * 0.4)))
        return [
            [node.id for node in ranked[:split_at]],
            [node.id for node in ranked[split_at:]],
        ]

    def reset(self, seed: Optional[int] = 42):
        self._random = random.Random(seed)
        self._rng = np.random.default_rng(seed)
        self.req_counter = 0
        max_multiplier = max(self.config.time_multipliers) if self.config.use_time_profile else 1.0
        self._max_rate = max(self.config.arrival_rate * max_multiplier, 1e-5)
        self.next_arrival_time = float(self._rng.exponential(1.0 / self._max_rate))

    def update_rate(self, new_rate: float):
        self.config.arrival_rate = max(0.001, new_rate)
        max_multiplier = max(self.config.time_multipliers) if self.config.use_time_profile else 1.0
        self._max_rate = max(self.config.arrival_rate * max_multiplier, 1e-5)

    def _time_multiplier(self, simulation_time: float) -> float:
        if not self.config.use_time_profile:
            return 1.0
        hour = (self.config.start_hour + simulation_time / 3600.0) % 24
        current_hour = int(hour)
        fraction = hour - current_hour
        next_hour = (current_hour + 1) % 24
        current = self.config.time_multipliers[current_hour]
        following = self.config.time_multipliers[next_hour]
        return current + (following - current) * fraction

    def _sample_node(self, zone_index: Optional[int] = None) -> int:
        if self._zones:
            zone = self._random.randrange(len(self._zones)) if zone_index is None else zone_index
            return self._random.choice(self._zones[zone])
        return self.node_provider.get_random_node()

    def _sample_origin_and_destination(self) -> tuple[int, int]:
        if not self._zones:
            origin = self._sample_node()
            destination = self._sample_node()
            while destination == origin:
                destination = self._sample_node()
            return origin, destination

        origin_zone = 0 if self._random.random() < self.config.core_zone_weight else 1
        origin = self._sample_node(origin_zone)
        if self._random.random() < self.config.cross_zone_probability:
            destination_zone = 1 - origin_zone
        else:
            destination_zone = origin_zone
        destination = self._sample_node(destination_zone)
        if len(self._zones[destination_zone]) == 1 and destination == origin:
            destination_zone = 1 - destination_zone
            destination = self._sample_node(destination_zone)
        while destination == origin:
            destination = self._sample_node(destination_zone)
        return origin, destination

    def generate_tick_requests(self, current_time: float) -> List[TripRequestDTO]:
        """Sinh tất cả yêu cầu có thời điểm xuất hiện <= current_time."""
        new_requests: List[TripRequestDTO] = []

        while current_time >= self.next_arrival_time:
            candidate_time = self.next_arrival_time
            acceptance_rate = min(
                1.0,
                self.config.arrival_rate * self._time_multiplier(candidate_time) / self._max_rate,
            )
            accepted = self._random.random() < acceptance_rate
            if accepted:
                self.req_counter += 1
                origin, destination = self._sample_origin_and_destination()

                req = TripRequestDTO(
                    request_id=f"R_{self.req_counter:04d}",
                    origin_node=origin,
                    destination_node=destination,
                    request_time=candidate_time,
                    max_wait_time=self.config.max_wait_time,
                    num_passengers=1,
                )
                new_requests.append(req)

                event_bus.publish(EventType.REQUEST_CREATED, {
                    "id": req.request_id,
                    "origin": req.origin_node,
                    "destination": req.destination_node,
                    "time": req.request_time,
                })

            inter_arrival = self._rng.exponential(1.0 / self._max_rate)
            self.next_arrival_time += inter_arrival

        return new_requests
