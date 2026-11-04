from services.benchmark_service import METRICS, run_benchmark
from services.matching_service.contracts import DispatchStrategy


def test_benchmark_repeats_all_strategies_over_same_seed_range(monkeypatch):
    observed_runs = []

    class FakeEngine:
        def __init__(self):
            self.current_time = 0

        def get_kpis(self):
            return {
                **{metric: 0.0 for metric in METRICS},
                "total_requests": self.seed,
            }

    class FakeSimulationService:
        def __init__(self, *, strategy, seed, **kwargs):
            observed_runs.append((strategy, seed, kwargs))
            self.engine = FakeEngine()
            self.engine.seed = seed

        def step(self, _dt):
            self.engine.current_time += 1

    monkeypatch.setattr(
        "services.benchmark_service.SimulationService",
        FakeSimulationService,
    )
    result = run_benchmark(
        object(),
        num_drivers=3,
        arrival_rate=0.03,
        sim_seconds=10,
        base_seed=12,
        num_runs=2,
        warmup_seconds=2,
        batch_window_seconds=5,
        start_hour=7,
        use_spatiotemporal_demand=True,
    )

    assert len(observed_runs) == 2 * len(DispatchStrategy)
    for seed in (12, 13):
        seed_runs = [
            (strategy, kwargs)
            for strategy, run_seed, kwargs in observed_runs
            if run_seed == seed
        ]
        assert {strategy for strategy, _kwargs in seed_runs} == set(DispatchStrategy)
        assert all(kwargs == seed_runs[0][1] for _strategy, kwargs in seed_runs)
        assert seed_runs[0][1]["warmup_seconds"] == 2
    assert result["configuration"]["seed_range"] == [12, 13]
    greedy = next(row for row in result["results"] if row["key"] == "greedy")
    assert greedy["metrics"]["total_requests"] == 12.5
    assert greedy["stddev"]["total_requests"] > 0
