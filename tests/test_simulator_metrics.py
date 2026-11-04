from core_engine.entities import Request
from core_engine.simulator import SimulationEngine


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
