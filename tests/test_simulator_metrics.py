from core_engine.entities import Driver, DriverState, Request, RequestState
from core_engine.simulator import SimulationEngine


class FakeRoadMap:
    class Graph:
        @staticmethod
        def get_edge_data(_start, _end):
            return {0: {"length": 100.0}}

    graph = Graph()

    @staticmethod
    def get_node_latlon(node_id):
        return float(node_id), float(node_id)

    @staticmethod
    def get_shortest_path(start, end):
        return [start, end]


def test_driver_stays_en_route_until_reaching_pickup_node():
    request = Request("R_1", 2, 3, request_time=0.0, status=RequestState.MATCHED)
    driver = Driver(
        "D_01",
        1,
        status=DriverState.EN_ROUTE_PICKUP,
        assigned_request=request,
        current_route=[1, 2],
    )
    simulation = SimulationEngine(FakeRoadMap(), [driver])
    simulation.active_requests[request.request_id] = request

    simulation.step(1.0)

    assert driver.current_node == 1
    assert driver.status is DriverState.EN_ROUTE_PICKUP
    assert request.pickup_time is None

    simulation.step(12.0)

    assert driver.current_node == request.origin_node
    assert driver.status is DriverState.OCCUPIED
    assert request.status is RequestState.PICKED_UP
    assert request.pickup_time == simulation.current_time


def test_average_pickup_wait_includes_picked_up_active_requests():
    simulation = SimulationEngine(road_map=None, drivers=[])
    completed = Request("R_1", 1, 2, request_time=10.0, pickup_time=20.0)
    active_picked_up = Request("R_2", 1, 2, request_time=10.0, pickup_time=30.0)
    active_waiting = Request("R_3", 1, 2, request_time=10.0)
    simulation.completed_requests.append(completed)
    simulation.active_requests[active_picked_up.request_id] = active_picked_up
    simulation.active_requests[active_waiting.request_id] = active_waiting

    kpis = simulation.get_kpis(total_generated=3)

    assert kpis["avg_wait_time"] == 15.0
    assert kpis["unfinished_requests"] == 2


def test_warmup_excludes_early_requests_and_busy_time_from_reported_metrics():
    driver = Driver("D_01", 1, status=DriverState.OCCUPIED)
    simulation = SimulationEngine(road_map=None, drivers=[driver], warmup_seconds=5.0)
    simulation.add_requests([
        Request("R_early", 1, 2, request_time=0.0),
        Request("R_measured", 1, 2, request_time=5.0),
    ])

    simulation.step(10.0)

    kpis = simulation.get_kpis(total_generated=2)

    assert kpis["total_requests"] == 1
    assert kpis["pending_requests"] == 1
    assert kpis["unfinished_requests"] == 1
    assert kpis["measurement_seconds"] == 5.0
    assert kpis["driver_utilization"] == 100.0
