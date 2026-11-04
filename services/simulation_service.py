"""Application service that composes map, demand, matching, and simulation capabilities."""
from __future__ import annotations

import random
from typing import List, Optional, Tuple, Union

import numpy as np

from core_engine.entities import Driver, Request
from core_engine.simulator import SimulationEngine
from services.contracts import DriverDTO, TripRequestDTO
from services.demand_service.contracts import DemandConfigDTO
from services.demand_service.service import DemandService
from services.map_service.service import MapService
from services.matching_service.contracts import DispatchStrategy
from services.matching_service.service import MatchingService

HUNGARIAN_STRATEGIES = {
    DispatchStrategy.HUNGARIAN,
    DispatchStrategy.HUNGARIAN_POOLING,
}


class SimulationService:
    """Translate service contracts to simulation entities and coordinate each tick."""

    def __init__(
        self,
        map_service: MapService,
        num_drivers: int = 8,
        arrival_rate: float = 0.03,
        strategy: DispatchStrategy = DispatchStrategy.GREEDY,
        seed: Optional[int] = 42,
        default_speed: float = 8.33,
        start_hour: int = 7,
        batch_window_seconds: float = 30.0,
        use_spatiotemporal_demand: bool = True,
        warmup_seconds: float = 0.0,
    ):
        if not map_service.is_ready:
            raise RuntimeError("Không thể tạo mô phỏng khi Map Service chưa sẵn sàng")
        if not 0 <= start_hour <= 23:
            raise ValueError("start_hour phải nằm trong khoảng 0–23")
        if batch_window_seconds <= 0:
            raise ValueError("batch_window_seconds phải lớn hơn 0")

        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        drivers = [
            Driver(
                driver_id=f"D_{index + 1:02d}",
                current_node=map_service.get_random_node(),
            )
            for index in range(num_drivers)
        ]
        self.engine = SimulationEngine(
            map_service.road_map,
            drivers,
            default_speed=default_speed,
            warmup_seconds=warmup_seconds,
        )
        self.demand_service = DemandService(
            map_service,
            DemandConfigDTO(
                arrival_rate=arrival_rate,
                seed=seed,
                start_hour=start_hour,
                use_time_profile=use_spatiotemporal_demand,
                use_spatial_profile=use_spatiotemporal_demand,
            ),
        )
        self.matching_service = MatchingService(map_service)
        self.strategy = strategy
        self.batch_window_seconds = batch_window_seconds
        self._next_batch_dispatch_time = batch_window_seconds

    @staticmethod
    def _to_request(dto: TripRequestDTO) -> Request:
        return Request(
            request_id=dto.request_id,
            origin_node=dto.origin_node,
            destination_node=dto.destination_node,
            request_time=dto.request_time,
            max_wait_time=dto.max_wait_time,
            num_passengers=dto.num_passengers,
        )

    def _dispatch(
        self,
        idle_drivers: List[Driver],
        pending_requests: List[Request],
        _road_map,
        _current_time: float,
    ) -> List[Tuple[str, Union[str, List[Tuple[str, str]]]]]:
        if (
            self.strategy in HUNGARIAN_STRATEGIES
            and _current_time + 1e-9 < self._next_batch_dispatch_time
        ):
            return []
        if self.strategy in HUNGARIAN_STRATEGIES:
            self._next_batch_dispatch_time = (
                int(_current_time // self.batch_window_seconds) + 1
            ) * self.batch_window_seconds

        driver_contracts = [
            DriverDTO(
                driver_id=driver.driver_id,
                current_node=driver.current_node,
                capacity=driver.capacity,
            )
            for driver in idle_drivers
        ]
        request_contracts = [
            TripRequestDTO(
                request_id=request.request_id,
                origin_node=request.origin_node,
                destination_node=request.destination_node,
                request_time=request.request_time,
                max_wait_time=request.max_wait_time,
                num_passengers=request.num_passengers,
            )
            for request in pending_requests
        ]

        result = self.matching_service.dispatch(
            driver_contracts,
            request_contracts,
            strategy=self.strategy,
        )
        if not result.success or result.data is None:
            raise RuntimeError(result.error or "Matching Service không trả về kết quả")

        matches: List[Tuple[str, Union[str, List[Tuple[str, str]]]]] = []
        for assignment in result.data.assignments:
            value = (
                assignment.request_ids[0]
                if len(assignment.request_ids) == 1
                else assignment.stops
            )
            matches.append((assignment.driver_id, value))
        return matches

    def step(self, dt: float = 1.0) -> List[Request]:
        """Generate demand through its service, then advance one simulation tick."""
        request_contracts = self.demand_service.generate_tick_requests(
            self.engine.current_time
        )
        requests = [self._to_request(dto) for dto in request_contracts]
        if requests:
            self.engine.add_requests(requests)

        self.engine.step(dt, matching_function=self._dispatch)
        return requests
