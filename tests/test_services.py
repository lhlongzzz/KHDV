"""Contract-level tests for independently replaceable application services."""
import asyncio

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
from core_engine.entities import Driver, DriverState, Request, RequestState
from core_engine.simulator import SimulationEngine
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


def test_demand_service_repeats_request_stream_for_same_seed():
    class CyclingNodeProvider:
        def __init__(self):
            self.node = 101

        def get_random_node(self):
            self.node = 202 if self.node == 101 else 101
            return self.node

    config = DemandConfigDTO(
        arrival_rate=0.1,
        seed=7,
        use_time_profile=False,
        use_spatial_profile=False,
    )
    first = DemandService(CyclingNodeProvider(), config).generate_tick_requests(60.0)
    second = DemandService(CyclingNodeProvider(), config).generate_tick_requests(60.0)

    assert first == second


def test_snapshot_identifies_pickup_request_without_listing_it_as_onboard():
    class SnapshotMap:
        @staticmethod
        def get_node_latlon(node_id):
            return (float(node_id), float(node_id))

        @staticmethod
        def get_shortest_path(origin, destination):
            return [origin, destination]

    request = Request(
        "R_0001",
        202,
        303,
        request_time=0.0,
        status=RequestState.MATCHED,
        matched_driver_id="D_01",
    )
    driver = Driver(
        "D_01",
        101,
        status=DriverState.EN_ROUTE_PICKUP,
        assigned_request=request,
    )
    simulation = SimulationEngine(SnapshotMap(), [driver])
    simulation.active_requests[request.request_id] = request

    snapshot = server.build_snapshot(simulation, [request])

    assert snapshot["drivers"][0]["pickup_requests"] == ["R_0001"]
    assert snapshot["drivers"][0]["passengers"] == []
    assert snapshot["active"][0]["dropoff_lat"] == 303.0
    assert snapshot["active"][0]["route"] == [[202.0, 202.0], [303.0, 303.0]]
    assert snapshot["active"][0]["status"] == "MATCHED"


def test_snapshot_identifies_driver_for_picked_up_request_and_lists_passenger():
    class SnapshotMap:
        @staticmethod
        def get_node_latlon(node_id):
            return (float(node_id), float(node_id))

        @staticmethod
        def get_shortest_path(origin, destination):
            return [origin, destination]

    request = Request(
        "R_0002",
        202,
        303,
        request_time=0.0,
        status=RequestState.PICKED_UP,
        matched_driver_id="D_02",
        pickup_time=10.0,
    )
    driver = Driver(
        "D_02",
        202,
        status=DriverState.OCCUPIED,
        assigned_request=request,
    )
    simulation = SimulationEngine(SnapshotMap(), [driver])
    simulation.active_requests[request.request_id] = request

    snapshot = server.build_snapshot(simulation, [request])

    assert snapshot["active"][0]["driver_id"] == "D_02"
    assert snapshot["active"][0]["status"] == "PICKED_UP"
    assert snapshot["drivers"][0]["passengers"] == ["R_0002"]
    assert snapshot["drivers"][0]["pickup_requests"] == []


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


def test_pause_endpoint_pauses_and_resumes_existing_runtime(monkeypatch):
    events = []
    monkeypatch.setattr(server, "send_ws_event", events.append)
    server.sim_state["running"] = True
    server.pause_event.clear()

    paused = asyncio.run(server.pause_simulation())
    resumed = asyncio.run(server.pause_simulation())

    assert paused == {"status": "paused"}
    assert resumed == {"status": "resumed"}
    assert events == [{"type": "sim_paused"}, {"type": "sim_resumed"}]
    assert server.pause_event.is_set() is False
    assert server.sim_state["paused"] is False
    server.sim_state["running"] = False


def test_benchmark_endpoint_runs_heavy_work_off_event_loop_and_rejects_bad_config(monkeypatch):
    import threading

    request_thread = threading.current_thread()
    benchmark_threads = []
    monkeypatch.setitem(server.sim_state, "running", False)
    monkeypatch.setattr(server, "road_map", object())
    monkeypatch.setattr(server, "map_service", object())

    def fake_benchmark(*_args, **_kwargs):
        benchmark_threads.append(threading.current_thread())
        return {"configuration": {}, "results": [{"key": "greedy"}]}

    monkeypatch.setattr(server, "run_benchmark", fake_benchmark)
    result = asyncio.run(server.run_benchmark_api({
        "sim_seconds": 300,
        "warmup_seconds": 0,
        "num_runs": 1,
        "num_drivers": 3,
        "arrival_rate": 0.03,
    }))

    assert result["results"][0]["name"] == server.STRATEGY_LABELS["greedy"]
    assert benchmark_threads[0] is not request_thread

    invalid = asyncio.run(server.run_benchmark_api({"num_runs": "invalid"}))

    assert invalid.status_code == 422


def test_demand_profile_changes_rate_by_hour_and_spatial_od_uses_separate_zones():
    from services.base import ServiceResult
    from services.map_service.contracts import NodeDTO

    class ZonedNodeProvider:
        nodes = [
            NodeDTO(1, 21.0000, 105.0000),
            NodeDTO(2, 21.0001, 105.0001),
            NodeDTO(3, 21.0002, 105.0002),
            NodeDTO(4, 21.0100, 105.0100),
            NodeDTO(5, 21.0101, 105.0101),
            NodeDTO(6, 21.0102, 105.0102),
            NodeDTO(7, 21.0103, 105.0103),
            NodeDTO(8, 21.0104, 105.0104),
            NodeDTO(9, 21.0105, 105.0105),
            NodeDTO(10, 21.0106, 105.0106),
        ]

        @classmethod
        def get_all_nodes(cls):
            return ServiceResult.ok(cls.nodes)

        @classmethod
        def get_random_node(cls):
            return cls.nodes[0].id

        @classmethod
        def get_node_latlon(cls, node_id):
            node = next(node for node in cls.nodes if node.id == node_id)
            return node.lat, node.lon

    config = DemandConfigDTO(
        arrival_rate=0.1,
        seed=3,
        start_hour=0,
        core_zone_weight=1.0,
        cross_zone_probability=1.0,
    )
    service = DemandService(ZonedNodeProvider(), config)

    origin, destination = service._sample_origin_and_destination()

    assert service._time_multiplier(0) == 0.35
    assert service._time_multiplier(7 * 3600) == 1.6
    assert origin in service._zones[0]
    assert destination in service._zones[1]


def test_hungarian_dispatch_waits_for_configured_batch_boundary(monkeypatch):
    class FakeMapService:
        is_ready = True
        road_map = object()

        @staticmethod
        def get_random_node():
            return 101

    monkeypatch.setattr("services.simulation_service.SimulationEngine", lambda *_args, **_kwargs: object())
    runtime = SimulationService(
        FakeMapService(),
        num_drivers=1,
        strategy=DispatchStrategy.HUNGARIAN,
        seed=None,
        batch_window_seconds=5,
    )
    calls = []

    class FakeMatchingService:
        @staticmethod
        def dispatch(drivers, requests, strategy):
            calls.append((drivers, requests, strategy))
            return ServiceResult.ok(DispatchResultDTO(
                strategy=strategy.value,
                assignments=[],
                matched_count=0,
                unmatched_count=len(requests),
                execution_time_ms=0.1,
            ))

    runtime.matching_service = FakeMatchingService()
    driver = runtime._dispatch([], [], None, 4.0)
    batch = runtime._dispatch([], [], None, 5.0)

    assert driver == []
    assert batch == []
    assert len(calls) == 1
