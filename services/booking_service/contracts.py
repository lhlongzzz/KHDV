"""
Hợp đồng dữ liệu (Data Transfer Objects - DTO) cho Dịch vụ Đặt xe (Booking Service).
Tuân thủ kiến trúc Hướng dịch vụ (SOA).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class BookingState(str, Enum):
    WAITING = "WAITING"               # Khách vừa đặt, đang chờ tài xế nhận cuốc
    MATCHED = "MATCHED"               # Tài xế đã bấm NHẬN, đang di chuyển tới điểm đón
    PICKING_UP = "PICKING_UP"         # Tài xế đã tới điểm đón chờ khách
    IN_TRANSIT = "IN_TRANSIT"         # Khách đã lên xe, đang di chuyển tới điểm đến
    COMPLETED = "COMPLETED"           # Hoàn thành chuyến đi, thu tiền
    CANCELLED = "CANCELLED"           # Hủy chuyến (do quá 5 phút hoặc khách/tài xế hủy)


class ServiceType(str, Enum):
    GRAB_CAR = "grab_car"             # UETransportCar 4 chỗ tiêu chuẩn
    GRAB_BIKE = "grab_bike"           # UETransportBike xe máy linh hoạt
    GRAB_CAR_PLUS = "grab_car_plus"   # UETransportCar Plus xe rộng, tài xế đánh giá cao
    GRAB_CAR_SHARE = "grab_car_share" # UETransportCar Ghép chuyến (Ride-Pooling) tiết kiệm cước


SERVICE_INFO = {
    ServiceType.GRAB_CAR.value: {
        "name": "UETransportCar",
        "description": "Xe 4 chỗ tiêu chuẩn, di chuyển an toàn & thoải mái",
        "capacity": 4,
        "base_fare": 25000,
        "per_km_fare": 10000,
        "icon": "🚗",
    },
    ServiceType.GRAB_BIKE.value: {
        "name": "UETransportBike",
        "description": "Xe máy nhanh chóng, tiết kiệm, len lỏi phố cổ",
        "capacity": 1,
        "base_fare": 12500,
        "per_km_fare": 4500,
        "icon": "🛵",
    },
    ServiceType.GRAB_CAR_PLUS.value: {
        "name": "UETransportCar Plus",
        "description": "Xe cao cấp, đời mới, tài xế đánh giá 4.9+ sao",
        "capacity": 4,
        "base_fare": 32000,
        "per_km_fare": 13000,
        "icon": "🚙",
    },
    ServiceType.GRAB_CAR_SHARE.value: {
        "name": "UETransportCar Share",
        "description": "Ghép chuyến thông minh cùng lộ trình, tiết kiệm 25%",
        "capacity": 2,
        "base_fare": 19000,
        "per_km_fare": 7500,
        "icon": "👥",
    },
}


@dataclass
class CustomerProfileDTO:
    customer_id: str
    name: str
    phone: str
    email: Optional[str] = None
    created_at: float = 0.0


@dataclass
class DriverProfileDTO:
    driver_id: str
    name: str
    phone: str
    vehicle_type: str = ServiceType.GRAB_CAR.value
    vehicle_plate: str = "29A-888.88"
    vehicle_model: str = "Toyota Vios"
    rating: float = 4.9
    current_node: int = 0
    current_lat: float = 21.0285
    current_lon: float = 105.8542
    status: str = "ONLINE"  # ONLINE, BUSY, OFFLINE
    total_trips: int = 0
    total_income: int = 0


@dataclass
class BookingDTO:
    booking_id: str
    customer: CustomerProfileDTO
    origin_node: int
    destination_node: int
    origin_address: str
    destination_address: str
    origin_lat: float
    origin_lon: float
    destination_lat: float
    destination_lon: float
    service_type: str = ServiceType.GRAB_CAR.value
    service_name: str = "UETransportCar"
    distance_meters: float = 0.0
    estimated_duration_seconds: float = 0.0
    estimated_price_vnd: int = 0
    created_at_time: float = 0.0
    max_wait_time_seconds: float = 300.0  # Tự động hủy nếu sau 5 phút (300s) không có tài xế nhận
    status: BookingState = BookingState.WAITING
    driver: Optional[DriverProfileDTO] = None
    pickup_route_coords: List[List[float]] = field(default_factory=list)
    trip_route_coords: List[List[float]] = field(default_factory=list)
    current_driver_lat: Optional[float] = None
    current_driver_lon: Optional[float] = None
    matched_time: Optional[float] = None
    picked_up_time: Optional[float] = None
    completed_time: Optional[float] = None
    cancel_reason: Optional[str] = None


@dataclass
class DriverDecisionDTO:
    driver_id: str
    booking_id: str
    decision: str  # "accept" hoặc "decline"


@dataclass
class TripActionDTO:
    driver_id: str
    booking_id: str
    action: str  # "arrived_pickup", "start_trip", "complete_trip"


@dataclass
class EstimateResultDTO:
    origin_node: int
    destination_node: int
    distance_meters: float
    estimated_duration_seconds: float
    route_coords: List[List[float]]
    services: Dict[str, dict]
