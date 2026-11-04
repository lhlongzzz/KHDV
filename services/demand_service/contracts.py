"""Contracts cho Dịch vụ Phát sinh Nhu cầu (Demand Service)."""
from dataclasses import dataclass
from typing import Optional

from services.contracts import TripRequestDTO

@dataclass
class DemandConfigDTO:
    arrival_rate: float = 0.04    # λ: số yêu cầu/giây
    max_wait_time: float = 300.0  # Thời gian chờ tối đa (giây)
    seed: Optional[int] = 42
