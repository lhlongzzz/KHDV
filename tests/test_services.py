"""Contract-level tests for independently replaceable application services."""
from services.contracts import DriverDTO, TripRequestDTO
from services.base import ServiceResult
from services.demand_service.contracts import DemandConfigDTO
from services.demand_service.service import DemandService
from services.matching_service.contracts import (
    AssignmentDTO,
    DispatchResultDTO,
    DispatchStrategy,
)
from services.matching_service.service import MatchingService
from services.simulation_service import SimulationService
import server


class FixedNodeProvider:
    def __init__(self):
        self._nodes = iter([101, 202, 101, 202])

    def get_random_node(self):
        return next(self._nodes)


class FixedDistanceProvider:
    @staticmethod
    def get_distance(origin_node, destination_node):
        return 1.0 if origin_node != destination_node else 0.0


def test_demand_service_returns_transport_neutral_request_contract():
    service = DemandService(
        FixedNodeProvider(),
        DemandConfigDTO(arrival_rate=0.04, seed=7),
    )
    service.next_arrival_time = 0.0

    requests = service.generate_tick_requests(0.0)

    assert len(requests) == 1
    assert requests[0] == TripRequestDTO(
        request_id="R_0001",
        origin_node=101,
        destination_node=202,
        request_time=0.0,
    )


def test_matching_service_consumes_contracts_and_returns_dispatch_dto():
    service = MatchingService(FixedDistanceProvider())

    result = service.dispatch(
        [DriverDTO(driver_id="D_01", current_node=101, capacity=4)],
        [TripRequestDTO(
            request_id="R_0001",
            origin_node=202,
            destination_node=101,
            request_time=0.0,
        )],
        strategy=DispatchStrategy.GREEDY,
    )

    assert result.success
    assert result.data is not None
    assert result.data.matched_count == 1
    assert result.data.assignments[0].driver_id == "D_01"
    assert result.data.assignments[0].request_ids == ["R_0001"]


def test_matching_service_rejects_unknown_strategy_instead_of_falling_back():
    service = MatchingService(FixedDistanceProvider())

    result = service.dispatch([], [], strategy="unknown")

    assert not result.success
    assert "không được hỗ trợ" in result.error


def test_simulation_service_translates_service_contracts_at_engine_boundary(monkeypatch):
    class FakeMapService:
        is_ready = True
        road_map = object()

        @staticmethod
        def get_random_node():
            return 101

    class FakeEngine:
        def __init__(self, _road_map, drivers, **_kwargs):
            self.drivers = {driver.driver_id: driver for driver in drivers}
            self.current_time = 0.0
            self.pending_requests = []

        def add_requests(self, requests):
            self.pending_requests.extend(requests)

        def step(self, dt, matching_function):
            self.matches = matching_function(
                list(self.drivers.values()),
                self.pending_requests,
                None,
                self.current_time,
            )
            self.current_time += dt

    monkeypatch.setattr("services.simulation_service.SimulationEngine", FakeEngine)
    runtime = SimulationService(FakeMapService(), num_drivers=1, seed=None)
    runtime.demand_service.generate_tick_requests = lambda _time: [
        TripRequestDTO("R_0001", 202, 303, 0.0)
    ]

    captured = {}

    def dispatch(drivers, requests, strategy):
        captured["drivers"] = drivers
        captured["requests"] = requests
        return ServiceResult.ok(DispatchResultDTO(
            strategy=strategy.value,
            assignments=[AssignmentDTO("D_01", ["R_0001"], [], 1.0)],
            matched_count=1,
            unmatched_count=0,
            execution_time_ms=0.1,
        ))

    runtime.matching_service.dispatch = dispatch
    requests = runtime.step()

    assert requests[0].request_id == "R_0001"
    assert isinstance(captured["drivers"][0], DriverDTO)
    assert isinstance(captured["requests"][0], TripRequestDTO)
    assert runtime.engine.matches == [("D_01", "R_0001")]


def test_simulation_worker_reports_startup_failure_and_resets_running_state(monkeypatch):
    events = []
    monkeypatch.setattr(server, "map_service", None)
    monkeypatch.setattr(server, "send_ws_event", events.append)
    server.sim_state["running"] = False

    server.simulation_worker("greedy", 1, 0.04, 1.0)

    assert server.sim_state["running"] is False
    assert server.sim_state["paused"] is False
    assert events == [{
        "type": "sim_error",
        "error": "Map Service chưa sẵn sàng để khởi chạy mô phỏng",
    }]
