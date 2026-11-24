"""
Dịch vụ Quản lý Đặt xe (Booking Service) theo kiến trúc Hướng dịch vụ (SOA).
Quản lý:
- Hồ sơ khách hàng & Tài xế
- Tạo cuốc xe, tính cước phí và lộ trình thực tế OpenStreetMap Hoàn Kiếm
- Cửa sổ nhận cuốc cho tài xế với quyền LỰA CHỌN CHẤP NHẬN hoặc TỪ CHỐI
- Tự động hủy chuyến sau 5 phút (300 giây) nếu không có tài xế nhận cuốc
- Cung cấp dữ liệu tọa độ đón/trả cho Bản đồ nhỏ của Web quản lý
"""
from __future__ import annotations

import time
import uuid
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from services.base import EventType, ServiceResult, event_bus
from services.booking_service.contracts import (
    SERVICE_INFO,
    BookingDTO,
    BookingState,
    CustomerProfileDTO,
    DriverProfileDTO,
    EstimateResultDTO,
    ServiceType,
)

if TYPE_CHECKING:
    from services.map_service.service import MapService


class BookingService:
    DRIVER_SPEED_METERS_PER_SECOND = 10.0

    def __init__(self, map_service: MapService):
        self.map_service = map_service
        self.customers: Dict[str, CustomerProfileDTO] = {}
        self.drivers: Dict[str, DriverProfileDTO] = {}
        self.bookings: Dict[str, BookingDTO] = {}
        # Theo dõi các cuốc xe mà từng tài xế đã từ chối: driver_id -> Set[booking_id]
        self.declined_trips: Dict[str, Set[str]] = {}

        # Khởi tạo sẵn một số tài xế mẫu đại diện để hệ thống luôn có sẵn xe đối tác
        self._init_default_drivers()

    def _init_default_drivers(self):
        """Tạo sẵn một số tài xế đối tác mẫu trong khu vực Hoàn Kiếm."""
        default_drivers = [
            ("D_01", "Nguyễn Văn Hùng", "0912 345 678", ServiceType.GRAB_CAR.value, "29A-567.89", "Toyota Vios 2024", 4.95),
            ("D_02", "Trần Đình Trọng", "0988 765 432", ServiceType.GRAB_BIKE.value, "29B1-234.56", "Honda Wave Alpha", 4.92),
            ("D_03", "Lê Hoàng Long", "0904 112 233", ServiceType.GRAB_CAR_PLUS.value, "29E-888.68", "Mercedes C200", 5.0),
            ("D_04", "Phạm Quốc Dũng", "0977 445 566", ServiceType.GRAB_CAR_SHARE.value, "29A-345.12", "Hyundai Accent", 4.88),
            ("D_05", "Vũ Minh Tuấn", "0936 998 877", ServiceType.GRAB_CAR.value, "29A-999.28", "Honda City 2023", 4.96),
        ]
        for did, name, phone, vtype, plate, model, rating in default_drivers:
            node = self.map_service.get_random_node() if self.map_service.is_ready else 0
            lat, lon = self.map_service.get_node_latlon(node) if self.map_service.is_ready and node != 0 else (21.0285, 105.8542)
            self.drivers[did] = DriverProfileDTO(
                driver_id=did,
                name=name,
                phone=phone,
                vehicle_type=vtype,
                vehicle_plate=plate,
                vehicle_model=model,
                rating=rating,
                current_node=node,
                current_lat=round(lat, 6),
                current_lon=round(lon, 6),
                status="ONLINE",
            )
            self.declined_trips[did] = set()

    # =========================================================================
    # Quản lý Khách hàng & Tài xế
    # =========================================================================
    def register_customer(self, name: str, phone: str, email: Optional[str] = None) -> ServiceResult[CustomerProfileDTO]:
        """Lưu thông tin cá nhân khách hàng."""
        if not name or not phone:
            return ServiceResult.fail("Họ tên và Số điện thoại khách hàng không được để trống")
        cid = f"CUST_{uuid.uuid4().hex[:6].upper()}"
        cust = CustomerProfileDTO(
            customer_id=cid,
            name=name.strip(),
            phone=phone.strip(),
            email=email.strip() if email else None,
            created_at=time.time(),
        )
        self.customers[cid] = cust
        return ServiceResult.ok(cust)

    def register_driver(
        self,
        name: str,
        phone: str,
        vehicle_type: str = ServiceType.GRAB_CAR.value,
        vehicle_plate: str = "29A-888.88",
        vehicle_model: str = "Toyota Vios",
        start_node: Optional[int] = None,
    ) -> ServiceResult[DriverProfileDTO]:
        """Tài xế nhập thông tin cá nhân và phương tiện để vào ca làm việc."""
        if not name or not phone:
            return ServiceResult.fail("Họ tên và Số điện thoại tài xế không được để trống")
        
        did = f"DRV_{uuid.uuid4().hex[:6].upper()}"
        node = start_node if start_node else self.map_service.get_random_node()
        lat, lon = self.map_service.get_node_latlon(node)

        driver = DriverProfileDTO(
            driver_id=did,
            name=name.strip(),
            phone=phone.strip(),
            vehicle_type=vehicle_type,
            vehicle_plate=vehicle_plate.strip().upper(),
            vehicle_model=vehicle_model.strip(),
            rating=5.0,
            current_node=node,
            current_lat=round(lat, 6),
            current_lon=round(lon, 6),
            status="ONLINE",
        )
        self.drivers[did] = driver
        self.declined_trips[did] = set()
        return ServiceResult.ok(driver)

    def get_driver(self, driver_id: str) -> ServiceResult[DriverProfileDTO]:
        drv = self.drivers.get(driver_id)
        if not drv:
            return ServiceResult.fail("Không tìm thấy tài xế")
        return ServiceResult.ok(drv)

    # =========================================================================
    # Báo giá & Dự tính lộ trình
    # =========================================================================
    def estimate_booking(self, origin_node: int, destination_node: int) -> ServiceResult[EstimateResultDTO]:
        """Tính toán cước phí, khoảng cách và lộ trình cho tất cả các loại xe UETransport."""
        route_res = self.map_service.calculate_route(origin_node, destination_node)
        if not route_res.success or not route_res.data:
            return ServiceResult.fail(route_res.error or "Không tìm được đường đi")

        distance_meters = route_res.data.distance_meters
        # Vận tốc trung bình trong nội thành Hà Nội: 30 km/h ~ 8.33 m/s
        duration_sec = max(60.0, distance_meters / 8.33)
        distance_km = max(0.5, distance_meters / 1000.0)

        coords = [[round(lat, 6), round(lon, 6)] for lat, lon in route_res.data.geometry_coords]

        services_quote = {}
        for stype, info in SERVICE_INFO.items():
            base = info["base_fare"]
            per_km = info["per_km_fare"]
            fare = base + max(0.0, distance_km - 2.0) * per_km
            fare_rounded = int(round(fare / 1000.0) * 1000)

            services_quote[stype] = {
                "service_type": stype,
                "name": info["name"],
                "description": info["description"],
                "icon": info["icon"],
                "capacity": info["capacity"],
                "estimated_price": fare_rounded,
                "distance_km": round(distance_km, 2),
                "estimated_minutes": int(round(duration_sec / 60.0)),
            }

        return ServiceResult.ok(EstimateResultDTO(
            origin_node=origin_node,
            destination_node=destination_node,
            distance_meters=round(distance_meters, 1),
            estimated_duration_seconds=round(duration_sec, 1),
            route_coords=coords,
            services=services_quote,
        ))

    # =========================================================================
    # Tạo cuốc xe (Khách hàng)
    # =========================================================================
    def create_booking(
        self,
        customer_id: str,
        origin_node: int,
        destination_node: int,
        service_type: str = ServiceType.GRAB_CAR.value,
        origin_address: str = "Điểm đón",
        destination_address: str = "Điểm đến",
    ) -> ServiceResult[BookingDTO]:
        """Khách hàng tạo cuốc xe mới sau khi chọn điểm đón, điểm trả và loại xe."""
        cust = self.customers.get(customer_id)
        if not cust:
            return ServiceResult.fail(f"Không tìm thấy khách hàng mã {customer_id}")

        estimate_res = self.estimate_booking(origin_node, destination_node)
        if not estimate_res.success or not estimate_res.data:
            return ServiceResult.fail(estimate_res.error or "Không thể tính toán chi phí chuyến đi")

        est = estimate_res.data
        quote = est.services.get(service_type, est.services[ServiceType.GRAB_CAR.value])
        
        olat, olon = self.map_service.get_node_latlon(origin_node)
        dlat, dlon = self.map_service.get_node_latlon(destination_node)

        booking_id = f"GRAB_{uuid.uuid4().hex[:6].upper()}"
        now = time.time()

        booking = BookingDTO(
            booking_id=booking_id,
            customer=cust,
            origin_node=origin_node,
            destination_node=destination_node,
            origin_address=origin_address,
            destination_address=destination_address,
            origin_lat=round(olat, 6),
            origin_lon=round(olon, 6),
            destination_lat=round(dlat, 6),
            destination_lon=round(dlon, 6),
            service_type=service_type,
            service_name=quote["name"],
            distance_meters=est.distance_meters,
            estimated_duration_seconds=est.estimated_duration_seconds,
            estimated_price_vnd=quote["estimated_price"],
            created_at_time=now,
            max_wait_time_seconds=300.0,  # 5 phút tự động hủy
            status=BookingState.WAITING,
            trip_route_coords=est.route_coords,
        )

        self.bookings[booking_id] = booking
        event_bus.publish(EventType.BOOKING_CREATED, booking)
        return ServiceResult.ok(booking)

    def cancel_booking(self, customer_id: str, booking_id: str) -> ServiceResult[BookingDTO]:
        booking = self.bookings.get(booking_id)
        if not booking:
            return ServiceResult.fail("Không tìm thấy cuốc xe", status_code=404)
        if booking.customer.customer_id != customer_id:
            return ServiceResult.fail("Bạn không có quyền hủy cuốc xe này", status_code=403)
        if booking.status != BookingState.WAITING:
            return ServiceResult.fail(
                "Chỉ có thể hủy cuốc xe khi đang chờ tài xế",
                status_code=409,
            )

        booking.status = BookingState.CANCELLED
        booking.cancel_reason = "Khách hàng chủ động hủy chuyến"
        event_bus.publish(EventType.REQUEST_CANCELLED, booking)
        return ServiceResult.ok(booking)

    # =========================================================================
    # Cửa sổ nhận cuốc cho Tài xế (Driver Job Pool)
    # =========================================================================
    def get_available_bookings_for_driver(self, driver_id: str) -> List[dict]:
        """
        Lấy danh sách các cuốc xe có thể nhận cho tài xế:
        - Cuốc ở trạng thái WAITING
        - Chưa bị tài xế này từ chối
        - Phù hợp với loại phương tiện (Xe máy chỉ thấy cuốc xe máy, Ô tô chỉ thấy cuốc ô tô)
        - Tính khoảng cách từ vị trí hiện tại của tài xế tới điểm đón
        """
        self.check_timeouts()  # Quét tự động hủy các cuốc quá 5 phút trước khi trả danh sách

        driver = self.drivers.get(driver_id)
        declined_set = self.declined_trips.get(driver_id, set())

        # Phân loại tài xế: Xe máy vs Ô tô
        is_motorcycle_driver = False
        if driver:
            v_type = (driver.vehicle_type or "").lower()
            if any(k in v_type for k in ["bike", "motorcycle", "xe_may", "moto", "xe máy", "xe_om", "xe ôm", "om"]):
                is_motorcycle_driver = True
            else:
                is_motorcycle_driver = False

        available = []
        for b in self.bookings.values():
            if b.status != BookingState.WAITING:
                continue
            if b.booking_id in declined_set:
                continue

            # Lọc theo phương tiện: Xe máy chỉ hiện cuốc xe máy, Ô tô chỉ hiện cuốc ô tô
            is_bike_trip = (b.service_type == ServiceType.GRAB_BIKE.value)
            if driver:
                if is_motorcycle_driver and not is_bike_trip:
                    continue
                if not is_motorcycle_driver and is_bike_trip:
                    continue

            # Khoảng cách từ tài xế tới điểm đón
            pickup_dist = 0.0
            pickup_mins = 2
            if driver and self.map_service.is_ready:
                pickup_dist = self.map_service.get_distance(driver.current_node, b.origin_node)
                pickup_mins = max(1, int(round((pickup_dist / 8.33) / 60.0)))

            available.append({
                "booking_id": b.booking_id,
                "customer_name": b.customer.name,
                "customer_phone": b.customer.phone,
                "service_type": b.service_type,
                "service_name": b.service_name,
                "origin_address": b.origin_address,
                "destination_address": b.destination_address,
                "origin_lat": b.origin_lat,
                "origin_lon": b.origin_lon,
                "destination_lat": b.destination_lat,
                "destination_lon": b.destination_lon,
                "trip_distance_km": round(b.distance_meters / 1000.0, 2),
                "estimated_price_vnd": b.estimated_price_vnd,
                "distance_to_pickup_m": round(pickup_dist, 1),
                "pickup_eta_minutes": pickup_mins,
                "wait_seconds_elapsed": round(time.time() - b.created_at_time, 1),
                "seconds_until_auto_cancel": max(0, int(round(300.0 - (time.time() - b.created_at_time)))),
                "route_coords": b.trip_route_coords,
            })

        # Sắp xếp theo khoảng cách đón gần nhất
        available.sort(key=lambda x: x["distance_to_pickup_m"])
        return available

    # =========================================================================
    # Quyết định của Tài xế: Chấp nhận hoặc Từ chối
    # =========================================================================
    def driver_respond_trip(
        self,
        driver_id: str,
        booking_id: str,
        decision: str,
        current_position: Optional[Tuple[float, float]] = None,
    ) -> ServiceResult[BookingDTO]:
        """
        Tài xế có thể lựa chọn đón khách hoặc không:
        - "accept": Nhận cuốc, chuyển trạng thái cuốc sang MATCHED, khóa cuốc khỏi các tài xế khác.
        - "decline": Từ chối cuốc, cuốc vẫn ở trạng thái WAITING để tài xế khác nhận.
        """
        driver = self.drivers.get(driver_id)
        if not driver:
            return ServiceResult.fail("Không tìm thấy thông tin tài xế")

        booking = self.bookings.get(booking_id)
        if not booking:
            return ServiceResult.fail("Không tìm thấy cuốc xe")

        decision_clean = decision.strip().lower()

        if decision_clean == "accept":
            if booking.status != BookingState.WAITING:
                return ServiceResult.fail(f"Cuốc xe không còn khả dụng (Trạng thái hiện tại: {booking.status.value})")
            driver_has_active_booking = any(
                existing.driver
                and existing.driver.driver_id == driver_id
                and existing.status in (
                    BookingState.MATCHED,
                    BookingState.PICKING_UP,
                    BookingState.IN_TRANSIT,
                )
                for existing in self.bookings.values()
            )
            if driver.status == "BUSY" or driver_has_active_booking:
                return ServiceResult.fail("Tài xế đang phục vụ một cuốc xe khác")

            if current_position is not None:
                latitude, longitude = current_position
                nearest_node = self.map_service.find_nearest_node(latitude, longitude)
                if not nearest_node.success or nearest_node.data is None:
                    return ServiceResult.fail(nearest_node.error or "Không thể xác định vị trí tài xế")
                driver.current_node = nearest_node.data
                driver.current_lat = round(latitude, 6)
                driver.current_lon = round(longitude, 6)

            # Tính đường đi đón khách từ vị trí hiện tại của tài xế
            pickup_route = self.map_service.calculate_route(driver.current_node, booking.origin_node)
            pickup_coords = []
            if pickup_route.success and pickup_route.data:
                pickup_coords = [[round(lat, 6), round(lon, 6)] for lat, lon in pickup_route.data.geometry_coords]
                if current_position is not None:
                    start_position = [driver.current_lat, driver.current_lon]
                    if start_position != pickup_coords[0]:
                        pickup_coords.insert(0, start_position)

            # Gán tài xế vào cuốc
            booking.driver = driver
            booking.status = BookingState.MATCHED
            booking.matched_time = time.time()
            booking.pickup_route_coords = pickup_coords
            booking.current_driver_lat = driver.current_lat
            booking.current_driver_lon = driver.current_lon

            driver.status = "BUSY"
            event_bus.publish(EventType.REQUEST_MATCHED, booking)
            return ServiceResult.ok(booking)

        elif decision_clean == "decline":
            # Đánh dấu tài xế từ chối cuốc này
            if driver_id not in self.declined_trips:
                self.declined_trips[driver_id] = set()
            self.declined_trips[driver_id].add(booking_id)
            return ServiceResult.ok(booking)

        return ServiceResult.fail(f"Quyết định không hợp lệ: {decision} (chỉ chấp nhận 'accept' hoặc 'decline')")

    @staticmethod
    def _distance_between_coordinates(start: List[float], end: List[float]) -> float:
        earth_radius_meters = 6_371_000
        lat1, lon1 = radians(start[0]), radians(start[1])
        lat2, lon2 = radians(end[0]), radians(end[1])
        lat_delta = lat2 - lat1
        lon_delta = lon2 - lon1
        haversine = (
            sin(lat_delta / 2) ** 2
            + cos(lat1) * cos(lat2) * sin(lon_delta / 2) ** 2
        )
        return 2 * earth_radius_meters * asin(sqrt(haversine))

    @classmethod
    def _position_along_route(
        cls, route: List[List[float]], distance_meters: float
    ) -> Tuple[Tuple[float, float], bool]:
        if not route:
            raise ValueError("Tuyến đường phải có ít nhất một tọa độ")

        remaining_distance = max(0.0, distance_meters)
        for start, end in zip(route, route[1:]):
            segment_distance = cls._distance_between_coordinates(start, end)
            if segment_distance == 0:
                continue
            if remaining_distance < segment_distance:
                fraction = remaining_distance / segment_distance
                return (
                    (
                        start[0] + (end[0] - start[0]) * fraction,
                        start[1] + (end[1] - start[1]) * fraction,
                    ),
                    False,
                )
            remaining_distance -= segment_distance

        return (route[-1][0], route[-1][1]), True

    def update_driver_positions(self, now: Optional[float] = None) -> List[dict]:
        """Advance matched drivers along their pickup or passenger route."""
        current_time = time.time() if now is None else now
        position_updates = []

        for booking in self.bookings.values():
            if not booking.driver:
                continue

            if booking.status == BookingState.MATCHED:
                route = booking.pickup_route_coords
                started_at = booking.matched_time
                destination_node = booking.origin_node
            elif booking.status == BookingState.IN_TRANSIT:
                route = booking.trip_route_coords
                started_at = booking.picked_up_time
                destination_node = booking.destination_node
            else:
                continue

            if started_at is None or not route:
                continue

            distance_meters = max(0.0, current_time - started_at) * self.DRIVER_SPEED_METERS_PER_SECOND
            (latitude, longitude), reached_destination = self._position_along_route(
                route, distance_meters
            )
            latitude = round(latitude, 6)
            longitude = round(longitude, 6)
            position_changed = (
                booking.current_driver_lat != latitude
                or booking.current_driver_lon != longitude
            )

            booking.current_driver_lat = latitude
            booking.current_driver_lon = longitude
            booking.driver.current_lat = latitude
            booking.driver.current_lon = longitude
            if reached_destination:
                booking.driver.current_node = destination_node

            if position_changed:
                position_updates.append({
                    "booking_id": booking.booking_id,
                    "driver_id": booking.driver.driver_id,
                    "lat": latitude,
                    "lon": longitude,
                })

        return position_updates

    # =========================================================================
    # Tiến trình chuyến đi (Tài xế thực hiện)
    # =========================================================================
    def update_trip_action(self, driver_id: str, booking_id: str, action: str) -> ServiceResult[BookingDTO]:
        """
        Cập nhật trạng thái chuyến đi theo hành động của tài xế:
        - "arrived_pickup": Tài xế đã tới điểm đón khách
        - "start_trip": Khách đã lên xe, bắt đầu chở khách
        - "complete_trip": Đã đến điểm đến, hoàn thành chuyến đi
        """
        booking = self.bookings.get(booking_id)
        if not booking:
            return ServiceResult.fail("Không tìm thấy cuốc xe")
        if not booking.driver or booking.driver.driver_id != driver_id:
            return ServiceResult.fail("Tài xế không phải người phụ trách cuốc xe này")

        action_clean = action.strip().lower()

        if action_clean == "arrived_pickup":
            booking.status = BookingState.PICKING_UP
            booking.current_driver_lat = booking.origin_lat
            booking.current_driver_lon = booking.origin_lon
            booking.driver.current_node = booking.origin_node
            booking.driver.current_lat = booking.origin_lat
            booking.driver.current_lon = booking.origin_lon
            event_bus.publish(EventType.REQUEST_PICKED_UP, booking)
            return ServiceResult.ok(booking)

        elif action_clean == "start_trip":
            booking.status = BookingState.IN_TRANSIT
            booking.picked_up_time = time.time()
            return ServiceResult.ok(booking)

        elif action_clean == "complete_trip":
            booking.status = BookingState.COMPLETED
            booking.completed_time = time.time()
            booking.current_driver_lat = booking.destination_lat
            booking.current_driver_lon = booking.destination_lon

            # Cập nhật thu nhập và trả trạng thái tài xế về ONLINE
            driver = self.drivers.get(driver_id)
            if driver:
                driver.status = "ONLINE"
                driver.total_trips += 1
                driver.total_income += booking.estimated_price_vnd
                driver.current_node = booking.destination_node
                driver.current_lat = booking.destination_lat
                driver.current_lon = booking.destination_lon

            event_bus.publish(EventType.REQUEST_COMPLETED, booking)
            return ServiceResult.ok(booking)

        return ServiceResult.fail(f"Hành động không hợp lệ: {action}")

    # =========================================================================
    # Quy tắc nghiệp vụ: Tự động hủy nếu quá 5 phút không có tài xế nhận
    # =========================================================================
    def check_timeouts(self, now: Optional[float] = None) -> List[BookingDTO]:
        """Quét và tự động hủy các cuốc xe ở trạng thái WAITING quá 5 phút (300 giây)."""
        current_now = now or time.time()
        cancelled = []
        for b in self.bookings.values():
            if b.status == BookingState.WAITING:
                if current_now - b.created_at_time >= b.max_wait_time_seconds:
                    b.status = BookingState.CANCELLED
                    b.cancel_reason = "Tự động hủy chuyến: Không có tài xế nhận cuốc sau 5 phút"
                    cancelled.append(b)
                    event_bus.publish(EventType.REQUEST_CANCELLED, b)
        return cancelled

    # =========================================================================
    # Dữ liệu cho Bản đồ nhỏ của Web quản lý
    # =========================================================================
    def get_management_summary(self) -> dict:
        """
        Cung cấp dữ liệu trực quan cho Web Quản lý:
        - Tọa độ vị trí đón (xanh lá) và vị trí trả (đỏ) của các cuốc xe
        - Danh sách tài xế và trạng thái
        - Thống kê các cuốc chờ, đang chạy, đã hoàn thành, đã hủy
        """
        self.check_timeouts()

        active_trips_data = []
        waiting_trips_data = []
        history_trips_data = []

        for b in self.bookings.values():
            trip_info = {
                "booking_id": b.booking_id,
                "customer_name": b.customer.name,
                "customer_phone": b.customer.phone,
                "service_name": b.service_name,
                "origin_address": b.origin_address,
                "destination_address": b.destination_address,
                "pickup_lat": b.origin_lat,
                "pickup_lon": b.origin_lon,
                "dropoff_lat": b.destination_lat,
                "dropoff_lon": b.destination_lon,
                "distance_km": round(b.distance_meters / 1000.0, 2),
                "price_vnd": b.estimated_price_vnd,
                "status": b.status.value,
                "driver_name": b.driver.name if b.driver else None,
                "driver_plate": b.driver.vehicle_plate if b.driver else None,
                "driver_lat": b.current_driver_lat,
                "driver_lon": b.current_driver_lon,
                "created_at": b.created_at_time,
                "cancel_reason": b.cancel_reason,
            }
            if b.status == BookingState.WAITING:
                waiting_trips_data.append(trip_info)
            elif b.status in (BookingState.MATCHED, BookingState.PICKING_UP, BookingState.IN_TRANSIT):
                active_trips_data.append(trip_info)
            else:
                history_trips_data.append(trip_info)

        drivers_data = [
            {
                "id": d.driver_id,
                "name": d.name,
                "phone": d.phone,
                "plate": d.vehicle_plate,
                "model": d.vehicle_model,
                "rating": d.rating,
                "lat": d.current_lat,
                "lon": d.current_lon,
                "status": d.status,
                "total_trips": d.total_trips,
                "income": d.total_income,
            }
            for d in self.drivers.values()
        ]

        return {
            "total_drivers": len(self.drivers),
            "waiting_trips_count": len(waiting_trips_data),
            "active_trips_count": len(active_trips_data),
            "in_transit_trips_count": sum(
                1 for b in self.bookings.values()
                if b.status == BookingState.IN_TRANSIT
            ),
            "drivers": drivers_data,
            "waiting_trips": waiting_trips_data,
            "active_trips": active_trips_data,
            "history_trips": history_trips_data[-20:],  # 20 cuốc gần nhất
        }
