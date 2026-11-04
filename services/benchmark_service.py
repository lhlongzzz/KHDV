"""Repeatable, same-seed comparisons across dispatch strategies."""
from __future__ import annotations

from statistics import mean, stdev
from typing import Dict, List

from services.map_service.service import MapService
from services.matching_service.contracts import DispatchStrategy
from services.simulation_service import SimulationService


METRICS = (
    "total_requests",
    "completed_requests",
    "cancelled_requests",
    "unfinished_requests",
    "completion_rate",
    "cancellation_rate",
    "avg_wait_time",
    "driver_utilization",
    "total_distance_km",
)


def run_benchmark(
    map_service: MapService,
    *,
    num_drivers: int,
    arrival_rate: float,
    sim_seconds: int,
    base_seed: int,
    num_runs: int,
    warmup_seconds: int,
    batch_window_seconds: float,
    start_hour: int,
    use_spatiotemporal_demand: bool,
) -> Dict[str, object]:
    if not 1 <= num_runs <= 30:
        raise ValueError("Số lần chạy phải từ 1 đến 30")
    if not 0 <= warmup_seconds < sim_seconds:
        raise ValueError("Warm-up phải nhỏ hơn thời lượng mô phỏng")

    collected: Dict[DispatchStrategy, List[dict]] = {
        strategy: [] for strategy in DispatchStrategy
    }
    for seed in range(base_seed, base_seed + num_runs):
        for strategy in DispatchStrategy:
            runtime = SimulationService(
                map_service=map_service,
                num_drivers=num_drivers,
                arrival_rate=arrival_rate,
                strategy=strategy,
                seed=seed,
                start_hour=start_hour,
                batch_window_seconds=batch_window_seconds,
                use_spatiotemporal_demand=use_spatiotemporal_demand,
                warmup_seconds=warmup_seconds,
            )
            while runtime.engine.current_time < sim_seconds:
                runtime.step(1.0)
            collected[strategy].append(
                runtime.engine.get_kpis()
            )

    summaries = []
    for strategy, run_results in collected.items():
        metrics = {
            metric: round(mean(result[metric] for result in run_results), 2)
            for metric in METRICS
        }
        deviations = {
            metric: round(stdev(result[metric] for result in run_results), 2)
            if len(run_results) > 1 else 0.0
            for metric in METRICS
        }
        summaries.append({
            "key": strategy.value,
            "metrics": metrics,
            "stddev": deviations,
        })

    return {
        "configuration": {
            "num_drivers": num_drivers,
            "arrival_rate": arrival_rate,
            "sim_seconds": sim_seconds,
            "base_seed": base_seed,
            "num_runs": num_runs,
            "seed_range": [base_seed, base_seed + num_runs - 1],
            "warmup_seconds": warmup_seconds,
            "batch_window_seconds": batch_window_seconds,
            "start_hour": start_hour,
            "demand_profile": "spatiotemporal" if use_spatiotemporal_demand else "uniform",
        },
        "results": summaries,
    }
