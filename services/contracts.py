"""Shared, transport-neutral data contracts exchanged by application services."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DriverDTO:
    driver_id: str
    current_node: int
    capacity: int


@dataclass(frozen=True)
class TripRequestDTO:
    request_id: str
    origin_node: int
    destination_node: int
    request_time: float
    max_wait_time: float = 300.0
    num_passengers: int = 1
