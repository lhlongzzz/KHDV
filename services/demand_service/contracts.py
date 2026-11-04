"""Contracts cho Dịch vụ Phát sinh Nhu cầu (Demand Service)."""
from dataclasses import dataclass
from typing import Optional

from services.contracts import TripRequestDTO

DEFAULT_TIME_MULTIPLIERS = (
    0.35, 0.35, 0.35, 0.35, 0.35, 0.45,
    0.70, 1.60, 1.90, 1.40, 1.10, 1.10,
    1.20, 1.10, 1.05, 1.10, 1.50, 1.90,
    2.00, 1.60, 1.25, 1.10, 0.80, 0.55,
)


@dataclass
class DemandConfigDTO:
    arrival_rate: float = 0.04    # λ: số yêu cầu/giây
    max_wait_time: float = 300.0  # Thời gian chờ tối đa (giây)
    seed: Optional[int] = 42
    start_hour: int = 7
    use_time_profile: bool = True
    use_spatial_profile: bool = True
    time_multipliers: tuple[float, ...] = DEFAULT_TIME_MULTIPLIERS
    core_zone_weight: float = 0.7
    cross_zone_probability: float = 0.7
