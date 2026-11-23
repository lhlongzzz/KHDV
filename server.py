"""
FastAPI Server cho Ride-Hailing Platform Simulation (Hà Nội - Hoàn Kiếm)
Hỗ trợ cả 4 chiến lược ghép cặp:
  S1: Greedy (Tham lam gần nhất)
  S2: Hungarian (Gom lô tối ưu toàn cục)
  S3: Greedy Pooling (Ghép chuyến chung)
  S4: Hungarian Pooling (Hungary gom lô + Ghép chuyến)

Expose REST API & WebSocket streaming thời gian thực cho Web UI.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Thêm thư mục hiện tại vào sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from core_engine.entities import Driver, Request, DriverState
from core_engine.map_network import RealRoadMap
from core_engine.simulator import SimulationEngine
from services.base import EventType, event_bus
from services.benchmark_service import run_benchmark
from services.booking_service.service import BookingService
from services.map_service.service import MapService
from services.matching_service.contracts import DispatchStrategy
from services.simulation_service import SimulationService

STRATEGY_LABELS = {
    DispatchStrategy.GREEDY.value: "S1: Greedy (Tham lam)",
    DispatchStrategy.HUNGARIAN.value: "S2: Hungarian (Gom lô tối ưu)",
    DispatchStrategy.POOLING.value: "S3: Greedy Pooling (Ghép chuyến)",
    DispatchStrategy.HUNGARIAN_POOLING.value: "S4: Hungarian Pooling (Hungary + Ghép chuyến)",
}
STRATEGY_ALIASES = {
    "greedy_pool": DispatchStrategy.POOLING.value,
    "hungarian_pool": DispatchStrategy.HUNGARIAN_POOLING.value,
}

# ============================================================
# Trạng thái toàn cục
# ============================================================
road_map: Optional[RealRoadMap] = None
map_service: Optional[MapService] = None
booking_service: Optional[BookingService] = None
map_nodes_cache: Optional[List[dict]] = None
main_loop: Optional[asyncio.AbstractEventLoop] = None

sim_state = {
    "running": False,
    "paused": False,
    "strategy": "greedy",
    "strategy_name": "S1: Greedy",
    "num_drivers": 10,
    "arrival_rate": 0.04,
    "batch_window_seconds": 30.0,
    "start_hour": 7,
    "use_spatiotemporal_demand": True,
    "speed_multiplier": 2.0,
    "current_time": 0.0,
}

active_connections: Set[WebSocket] = set()
sim_thread: Optional[threading.Thread] = None
stop_event = threading.Event()
pause_event = threading.Event()
benchmark_running = False
sim_engine_ref: Optional[SimulationEngine] = None
all_requests_ref: List[Request] = []
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(CURRENT_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# ============================================================
# Lifespan
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global road_map, map_service, booking_service, map_nodes_cache, main_loop
    main_loop = asyncio.get_running_loop()
    print("\n[Server] Đang nạp bản đồ giao thông Hoàn Kiếm, Hà Nội...")
    map_service = MapService(place_name="Hoan Kiem, Hanoi, Vietnam")
    map_result = map_service.initialize()
    if map_result.success:
        road_map = map_service.road_map
        nodes_result = map_service.get_all_nodes()
        if not nodes_result.success or nodes_result.data is None:
            raise RuntimeError(nodes_result.error or "Map Service không trả về danh sách nút")
        map_nodes_cache = [
            {"id": node.id, "lat": node.lat, "lon": node.lon}
            for node in nodes_result.data
        ]
        booking_service = BookingService(map_service)
        print(f"[Server] ✓ Map Service & Booking Service sẵn sàng: {len(map_nodes_cache)} giao lộ.")

        # Đăng ký đồng bộ sự kiện Booking Service tới WebSocket Client thời gian thực
        def _on_booking_created(b):
            event_data = {
                "type": "event_booking_created",
                "booking_id": b.booking_id,
                "service_type": b.service_type,
                "service_name": b.service_name,
                "customer_name": b.customer.name,
                "origin_address": b.origin_address,
                "destination_address": b.destination_address,
                "estimated_price_vnd": b.estimated_price_vnd,
            }
            send_ws_event(event_data)
            send_ws_event({"type": "portal_booking_created", **event_data})

        def _on_booking_matched(b):
            driver_info = {
                "driver_id": b.driver.driver_id,
                "name": b.driver.name,
                "phone": b.driver.phone,
                "vehicle_type": b.driver.vehicle_type,
                "vehicle_plate": b.driver.vehicle_plate,
                "vehicle_model": b.driver.vehicle_model,
                "rating": b.driver.rating,
                "current_lat": b.current_driver_lat or (b.driver.current_lat if b.driver else None),
                "current_lon": b.current_driver_lon or (b.driver.current_lon if b.driver else None),
            } if b.driver else None
            # Gửi cho cả passenger.html và portal.html
            send_ws_event({
                "type": "event_driver_accepted",
                "booking_id": b.booking_id,
                "status": b.status.value,
                "driver": driver_info,
                "pickup_route_coords": b.pickup_route_coords,
                "trip_route_coords": b.trip_route_coords,
            })
            send_ws_event({
                "type": "portal_booking_matched",
                "booking_id": b.booking_id,
                "driver_id": b.driver.driver_id if b.driver else None,
                "driver": driver_info,
            })

        def _on_booking_picked_up(b):
            send_ws_event({"type": "event_trip_action", "booking_id": b.booking_id, "action": "start_trip", "trip_status": b.status.value})
            send_ws_event({"type": "portal_booking_picked_up", "booking_id": b.booking_id})

        def _on_booking_completed(b):
            send_ws_event({"type": "event_trip_action", "booking_id": b.booking_id, "action": "complete_trip", "trip_status": b.status.value})
            send_ws_event({"type": "portal_booking_completed", "booking_id": b.booking_id})

        def _on_booking_cancelled(b):
            send_ws_event({"type": "event_booking_timeout", "booking_id": b.booking_id, "reason": b.cancel_reason})
            send_ws_event({"type": "portal_booking_cancelled", "booking_id": b.booking_id, "reason": b.cancel_reason})

        event_bus.subscribe(EventType.REQUEST_CREATED, _on_booking_created)
        event_bus.subscribe(EventType.REQUEST_MATCHED, _on_booking_matched)
        event_bus.subscribe(EventType.REQUEST_PICKED_UP, _on_booking_picked_up)
        event_bus.subscribe(EventType.REQUEST_COMPLETED, _on_booking_completed)
        event_bus.subscribe(EventType.REQUEST_CANCELLED, _on_booking_cancelled)
    else:
        road_map = None
        booking_service = None
        map_nodes_cache = None
        print(f"[Server] ⚠ Map Service lỗi: {map_result.error}")
    yield

app = FastAPI(title="Ride-Hailing Platform - Hanoi Simulator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ============================================================
# Snapshot Builder
# ============================================================
@lru_cache(maxsize=4096)
def _request_route_coordinates(road_map, origin_node: int, destination_node: int) -> list:
    """Cache the static road geometry for each pickup/dropoff pair."""
    path = road_map.get_shortest_path(origin_node, destination_node)
    if not path:
        return []
    get_geometry = getattr(road_map, "get_route_geometry", None)
    coordinates = (
        get_geometry(path)
        if callable(get_geometry) and len(path) > 1
        else [road_map.get_node_latlon(node_id) for node_id in path]
    )
    return [
        [round(lat, 6), round(lon, 6)]
        for lat, lon in coordinates
    ]


def build_snapshot(sim: SimulationEngine, all_requests: List[Request]) -> dict:
    """Tạo gói dữ liệu đồng bộ thời gian thực gửi qua WebSocket cho Web UI."""
    driver_data = []
    idle_count = 0
    pickup_count = 0
    occupied_count = 0

    for d in sim.drivers.values():
        lat, lon = sim.get_driver_latlon(d.driver_id)
        status_val = d.status.value
        if status_val == DriverState.IDLE.value:
            idle_count += 1
        elif status_val == DriverState.EN_ROUTE_PICKUP.value:
            pickup_count += 1
        else:
            occupied_count += 1

        route_coords = []
        if len(d.current_route) > 1:
            route_coords = [sim.map.get_node_latlon(node) for node in d.current_route[:25]]

        passengers_onboard = []
        pickup_requests = []
        if d.assigned_request:
            if d.assigned_request.status.value == "MATCHED":
                pickup_requests.append(d.assigned_request.request_id)
            elif d.assigned_request.status.value == "PICKED_UP":
                passengers_onboard.append(d.assigned_request.request_id)
        if d.stop_queue:
            for r, kind, _ in d.stop_queue:
                if kind == "pickup" and r.request_id not in pickup_requests:
                    pickup_requests.append(r.request_id)
                elif kind == "dropoff" and r.status.value == "PICKED_UP" and r.request_id not in passengers_onboard:
                    passengers_onboard.append(r.request_id)

        # Vận tốc di chuyển thực tế (km/h)
        # Khi IDLE (dừng đỗ): 0 km/h; khi di chuyển (đón/chở khách): default_speed (~30 km/h)
        current_speed_kmh = 0.0 if status_val == DriverState.IDLE.value else round(sim.default_speed * 3.6, 1)

        driver_data.append({
            "id": d.driver_id,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "status": status_val,
            "speed_kmh": current_speed_kmh,
            "passengers": passengers_onboard,
            "pickup_requests": pickup_requests,
            "route": route_coords,
            "busy_time": round(d.total_busy_time, 1),
            "distance_km": round(d.total_distance / 1000.0, 2),
        })

    pending_data = []
    for r in sim.pending_requests:
        lat, lon = sim.map.get_node_latlon(r.origin_node)
        dlat, dlon = sim.map.get_node_latlon(r.destination_node)
        pending_data.append({
            "id": r.request_id,
            "pickup_lat": round(lat, 6),
            "pickup_lon": round(lon, 6),
            "dropoff_lat": round(dlat, 6),
            "dropoff_lon": round(dlon, 6),
            "route": _request_route_coordinates(sim.map, r.origin_node, r.destination_node),
            "wait_time": round(sim.current_time - r.request_time, 1),
            "request_time": r.request_time,
        })

    active_data = []
    for r in sim.active_requests.values():
        lat, lon = sim.map.get_node_latlon(r.origin_node)
        dlat, dlon = sim.map.get_node_latlon(r.destination_node)
        active_data.append({
            "id": r.request_id,
            "driver_id": r.matched_driver_id,
            "pickup_lat": round(lat, 6),
            "pickup_lon": round(lon, 6),
            "dropoff_lat": round(dlat, 6),
            "dropoff_lon": round(dlon, 6),
            "route": _request_route_coordinates(sim.map, r.origin_node, r.destination_node),
            "status": r.status.value,
            "request_time": r.request_time,
            "wait_time": round(sim.current_time - r.request_time, 1),
        })

    kpis = sim.get_kpis(len(all_requests))

    return {
        "type": "tick",
        "time": round(sim.current_time, 1),
        "drivers": driver_data,
        "pending": pending_data,
        "active": active_data,
        "fleet": {
            "idle": idle_count,
            "pickup": pickup_count,
            "occupied": occupied_count,
            "total": len(sim.drivers),
        },
        "kpis": kpis,
    }


# ============================================================
# WebSocket Broadcast (Thread-Safe)
# ============================================================
async def broadcast(message: dict):
    if not active_connections:
        return
    text = json.dumps(message, ensure_ascii=False)
    dead = set()
    for ws in list(active_connections):
        try:
            await ws.send_text(text)
        except Exception:
            dead.add(ws)
    active_connections.difference_update(dead)


def send_ws_event(message: dict):
    """Gửi message tới WebSocket an toàn qua main event loop từ bất kỳ thread nào."""
    if main_loop and main_loop.is_running() and active_connections:
        asyncio.run_coroutine_threadsafe(broadcast(message), main_loop)


# ============================================================
# Vòng lặp mô phỏng chạy ngầm (Background Simulation Thread)
# ============================================================
def simulation_worker(
    strategy_key: str,
    num_drivers: int,
    arrival_rate: float,
    speed_mult: float,
    seed: int = 42,
    start_hour: int = 7,
    batch_window_seconds: float = 30.0,
    use_spatiotemporal_demand: bool = True,
):
    sim_state["running"] = True
    sim_state["paused"] = False

    try:
        _run_simulation(
            strategy_key, num_drivers, arrival_rate, speed_mult, seed,
            start_hour, batch_window_seconds, use_spatiotemporal_demand,
        )
    except Exception as exc:
        logger.exception("Simulation worker failed")
        send_ws_event({"type": "sim_error", "error": str(exc)})
    finally:
        sim_state["running"] = False
        sim_state["paused"] = False


def _run_simulation(
    strategy_key: str,
    num_drivers: int,
    arrival_rate: float,
    speed_mult: float,
    seed: int,
    start_hour: int,
    batch_window_seconds: float,
    use_spatiotemporal_demand: bool,
):
    global road_map, map_service, sim_engine_ref, all_requests_ref

    if map_service is None or not map_service.is_ready:
        raise RuntimeError("Map Service chưa sẵn sàng để khởi chạy mô phỏng")
    strategy_key = STRATEGY_ALIASES.get(strategy_key, strategy_key)
    strategy = DispatchStrategy(strategy_key)
    label = STRATEGY_LABELS[strategy_key]
    runtime = SimulationService(
        map_service=map_service,
        num_drivers=num_drivers,
        arrival_rate=arrival_rate,
        strategy=strategy,
        seed=seed,
        start_hour=start_hour,
        batch_window_seconds=batch_window_seconds,
        use_spatiotemporal_demand=use_spatiotemporal_demand,
    )
    sim = runtime.engine
    sim_engine_ref = sim
    all_requests_ref = []

    dt = 1.0
    total_time = 3600.0  # Tối đa 1 tiếng mô phỏng
    last_broadcast_sim_time = 0.0

    send_ws_event({
        "type": "sim_started",
        "strategy": strategy_key,
        "strategy_name": label,
        "num_drivers": num_drivers,
        "arrival_rate": arrival_rate,
        "batch_window_seconds": batch_window_seconds,
        "start_hour": start_hour,
        "use_spatiotemporal_demand": use_spatiotemporal_demand,
    })

    while sim.current_time < total_time and not stop_event.is_set():
        if pause_event.is_set():
            time.sleep(0.1)
            continue

        prev_completed = len(sim.completed_requests)
        prev_cancelled = len(sim.cancelled_requests)

        # Demand Service generates DTOs; SimulationService adapts them to engine entities.
        new_reqs = runtime.step(dt)
        if new_reqs:
            all_requests_ref.extend(new_reqs)
            for r in new_reqs:
                plat, plon = road_map.get_node_latlon(r.origin_node)
                dlat, dlon = road_map.get_node_latlon(r.destination_node)
                send_ws_event({
                    "type": "event_new_request",
                    "id": r.request_id,
                    "pickup": [plat, plon],
                    "dropoff": [dlat, dlon],
                    "time": round(r.request_time, 1),
                })

        # Bắn sự kiện chuyến hoàn thành / bị hủy nếu có thay đổi
        if len(sim.completed_requests) > prev_completed:
            for r in sim.completed_requests[prev_completed:]:
                send_ws_event({
                    "type": "event_trip_completed",
                    "id": r.request_id,
                    "driver_id": r.matched_driver_id,
                })
        if len(sim.cancelled_requests) > prev_cancelled:
            for r in sim.cancelled_requests[prev_cancelled:]:
                send_ws_event({
                    "type": "event_trip_cancelled",
                    "id": r.request_id,
                })

        # Gửi snapshot mỗi 1.0 giây thời gian mô phỏng
        if sim.current_time - last_broadcast_sim_time >= 1.0:
            snapshot = build_snapshot(sim, all_requests_ref)
            send_ws_event(snapshot)
            last_broadcast_sim_time = sim.current_time

        # Điều khiển tốc độ thực
        current_mult = sim_state["speed_multiplier"]
        real_sleep = max(0.005, (dt / max(current_mult, 0.1)) * 0.05)
        time.sleep(real_sleep)

    # Kết thúc
    final_snapshot = build_snapshot(sim, all_requests_ref)
    final_snapshot["type"] = "sim_finished"
    send_ws_event(final_snapshot)

# ============================================================
# API Endpoints
# ============================================================
@app.get("/")
async def get_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse({
        "status": "ok",
        "app": "Ride-Hailing Simulator Hanoi",
        "message": "Không tìm thấy static/index.html. Vui lòng kiểm tra thư mục static."
    })


# ============================================================
# CỔNG ĐẶT XE & TÀI XẾ GRAB (PASSENGER, DRIVER, MANAGEMENT)
# ============================================================
@app.get("/portal")
async def get_portal_page():
    portal_file = os.path.join(STATIC_DIR, "portal.html")
    if os.path.exists(portal_file):
        return FileResponse(portal_file)
    return JSONResponse({"error": "Không tìm thấy static/portal.html"}, status_code=404)


@app.get("/passenger")
@app.get("/client")
async def get_passenger_page():
    passenger_file = os.path.join(STATIC_DIR, "passenger.html")
    if os.path.exists(passenger_file):
        return FileResponse(passenger_file)
    return JSONResponse({"error": "Không tìm thấy static/passenger.html"}, status_code=404)


@app.get("/driver")
async def get_driver_page():
    driver_file = os.path.join(STATIC_DIR, "driver.html")
    if os.path.exists(driver_file):
        return FileResponse(driver_file)
    return JSONResponse({"error": "Không tìm thấy static/driver.html"}, status_code=404)


@app.get("/management")
@app.get("/admin")
async def get_management_page():
    mgmt_file = os.path.join(STATIC_DIR, "management.html")
    if os.path.exists(mgmt_file):
        return FileResponse(mgmt_file)
    return JSONResponse({"error": "Không tìm thấy static/management.html"}, status_code=404)


@app.get("/api/geo/landmarks")
async def get_landmarks_api():
    if not map_service or not map_service.is_ready:
        return JSONResponse({"error": "Bản đồ chưa sẵn sàng"}, status_code=503)
    res = map_service.get_landmarks()
    if not res.success:
        return JSONResponse({"error": res.error}, status_code=500)
    return {"landmarks": res.data}


@app.get("/api/geo/nearest-node")
async def get_nearest_node_api(lat: float, lon: float):
    if not map_service or not map_service.is_ready:
        return JSONResponse({"error": "Bản đồ chưa sẵn sàng"}, status_code=503)
    res = map_service.find_nearest_node(lat, lon)
    if not res.success or res.data is None:
        return JSONResponse({"error": res.error or "Không tìm thấy nút"}, status_code=404)
    nlat, nlon = map_service.get_node_latlon(res.data)
    return {
        "node_id": res.data,
        "lat": round(nlat, 6),
        "lon": round(nlon, 6),
    }


@app.post("/api/customer/register")
async def register_customer_api(payload: dict):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    name = payload.get("name", "").strip()
    phone = payload.get("phone", "").strip()
    email = payload.get("email", "").strip() or None
    res = booking_service.register_customer(name, phone, email)
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=400)
    return {
        "status": "ok",
        "customer": {
            "customer_id": res.data.customer_id,
            "name": res.data.name,
            "phone": res.data.phone,
            "email": res.data.email,
        }
    }


@app.post("/api/driver/register")
async def register_driver_api(payload: dict):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    name = payload.get("name", "").strip()
    phone = payload.get("phone", "").strip()
    vehicle_type = payload.get("vehicle_type", "grab_car")
    vehicle_plate = payload.get("vehicle_plate", "29A-888.88").strip()
    vehicle_model = payload.get("vehicle_model", "Toyota Vios").strip()
    start_node = payload.get("start_node")
    if start_node is not None:
        try:
            start_node = int(start_node)
        except ValueError:
            start_node = None

    res = booking_service.register_driver(
        name=name,
        phone=phone,
        vehicle_type=vehicle_type,
        vehicle_plate=vehicle_plate,
        vehicle_model=vehicle_model,
        start_node=start_node,
    )
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=400)
    d = res.data
    return {
        "status": "ok",
        "driver": {
            "driver_id": d.driver_id,
            "name": d.name,
            "phone": d.phone,
            "vehicle_type": d.vehicle_type,
            "vehicle_plate": d.vehicle_plate,
            "vehicle_model": d.vehicle_model,
            "rating": d.rating,
            "current_node": d.current_node,
            "current_lat": d.current_lat,
            "current_lon": d.current_lon,
            "status": d.status,
        }
    }


@app.get("/api/driver/{driver_id}")
async def get_driver_api(driver_id: str):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    res = booking_service.get_driver(driver_id)
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=404)
    d = res.data
    return {
        "driver_id": d.driver_id,
        "name": d.name,
        "phone": d.phone,
        "vehicle_type": d.vehicle_type,
        "vehicle_plate": d.vehicle_plate,
        "vehicle_model": d.vehicle_model,
        "rating": d.rating,
        "current_node": d.current_node,
        "current_lat": d.current_lat,
        "current_lon": d.current_lon,
        "status": d.status,
        "total_trips": d.total_trips,
        "total_income": d.total_income,
    }


@app.get("/api/driver/{driver_id}/available-trips")
async def get_available_trips_api(driver_id: str):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    trips = booking_service.get_available_bookings_for_driver(driver_id)
    return {"trips": trips}


@app.post("/api/trip/estimate")
async def estimate_trip_api(payload: dict):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    try:
        origin_node = int(payload["origin_node"])
        destination_node = int(payload["destination_node"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "origin_node và destination_node phải là số nguyên hợp lệ"}, status_code=400)

    res = booking_service.estimate_booking(origin_node, destination_node)
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=400)
    data = res.data
    return {
        "origin_node": data.origin_node,
        "destination_node": data.destination_node,
        "distance_meters": data.distance_meters,
        "estimated_duration_seconds": data.estimated_duration_seconds,
        "route_coords": data.route_coords,
        "services": data.services,
    }


@app.post("/api/trip/book")
async def book_trip_api(payload: dict):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    try:
        customer_id = payload["customer_id"]
        origin_node = int(payload["origin_node"])
        destination_node = int(payload["destination_node"])
        service_type = payload.get("service_type", "grab_car")
        origin_address = payload.get("origin_address", "Điểm đón")
        destination_address = payload.get("destination_address", "Điểm đến")
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": f"Dữ liệu đặt xe không hợp lệ: {e}"}, status_code=400)

    res = booking_service.create_booking(
        customer_id=customer_id,
        origin_node=origin_node,
        destination_node=destination_node,
        service_type=service_type,
        origin_address=origin_address,
        destination_address=destination_address,
    )
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=400)
    b = res.data
    return {
        "status": "ok",
        "booking": {
            "booking_id": b.booking_id,
            "customer_id": b.customer.customer_id,
            "customer_name": b.customer.name,
            "service_type": b.service_type,
            "service_name": b.service_name,
            "origin_address": b.origin_address,
            "destination_address": b.destination_address,
            "origin_lat": b.origin_lat,
            "origin_lon": b.origin_lon,
            "destination_lat": b.destination_lat,
            "destination_lon": b.destination_lon,
            "distance_km": round(b.distance_meters / 1000.0, 2),
            "estimated_price_vnd": b.estimated_price_vnd,
            "status": b.status.value,
            "created_at_time": b.created_at_time,
            "max_wait_time_seconds": b.max_wait_time_seconds,
            "trip_route_coords": b.trip_route_coords,
        }
    }


@app.get("/api/trip/{booking_id}")
async def get_trip_details_api(booking_id: str):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    booking_service.check_timeouts()
    b = booking_service.bookings.get(booking_id)
    if not b:
        return JSONResponse({"error": "Không tìm thấy thông tin chuyến xe"}, status_code=404)

    return {
        "booking_id": b.booking_id,
        "customer": {
            "customer_id": b.customer.customer_id,
            "name": b.customer.name,
            "phone": b.customer.phone,
        },
        "driver": {
            "driver_id": b.driver.driver_id,
            "name": b.driver.name,
            "phone": b.driver.phone,
            "vehicle_type": b.driver.vehicle_type,
            "vehicle_plate": b.driver.vehicle_plate,
            "vehicle_model": b.driver.vehicle_model,
            "rating": b.driver.rating,
            "current_lat": b.current_driver_lat or (b.driver.current_lat if b.driver else None),
            "current_lon": b.current_driver_lon or (b.driver.current_lon if b.driver else None),
        } if b.driver else None,
        "service_type": b.service_type,
        "service_name": b.service_name,
        "origin_address": b.origin_address,
        "destination_address": b.destination_address,
        "origin_lat": b.origin_lat,
        "origin_lon": b.origin_lon,
        "destination_lat": b.destination_lat,
        "destination_lon": b.destination_lon,
        "distance_km": round(b.distance_meters / 1000.0, 2),
        "estimated_price_vnd": b.estimated_price_vnd,
        "status": b.status.value,
        "cancel_reason": b.cancel_reason,
        "created_at_time": b.created_at_time,
        "elapsed_seconds": round(time.time() - b.created_at_time, 1),
        "seconds_until_auto_cancel": max(0, int(round(300.0 - (time.time() - b.created_at_time)))) if b.status.value == "WAITING" else 0,
        "current_driver_lat": b.current_driver_lat,
        "current_driver_lon": b.current_driver_lon,
        "pickup_route_coords": b.pickup_route_coords,
        "trip_route_coords": b.trip_route_coords,
    }


@app.post("/api/driver/decision")
async def driver_decision_api(payload: dict):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    try:
        driver_id = payload["driver_id"]
        booking_id = payload["booking_id"]
        decision = payload["decision"]
    except KeyError as e:
        return JSONResponse({"error": f"Thiếu trường dữ liệu: {e}"}, status_code=400)

    res = booking_service.driver_respond_trip(driver_id, booking_id, decision)
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=400)
    b = res.data
    return {
        "status": "ok",
        "decision": decision,
        "booking_id": b.booking_id,
        "trip_status": b.status.value,
        "driver_id": driver_id,
    }


@app.post("/api/driver/trip-action")
async def driver_trip_action_api(payload: dict):
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    try:
        driver_id = payload["driver_id"]
        booking_id = payload["booking_id"]
        action = payload["action"]
    except KeyError as e:
        return JSONResponse({"error": f"Thiếu trường dữ liệu: {e}"}, status_code=400)

    res = booking_service.update_trip_action(driver_id, booking_id, action)
    if not res.success or not res.data:
        return JSONResponse({"error": res.error}, status_code=400)
    b = res.data

    send_ws_event({
        "type": "event_trip_action",
        "booking_id": b.booking_id,
        "action": action,
        "trip_status": b.status.value,
    })

    return {
        "status": "ok",
        "action": action,
        "booking_id": b.booking_id,
        "trip_status": b.status.value,
    }


@app.get("/api/management/trips")
async def get_management_trips_api():
    if not booking_service:
        return JSONResponse({"error": "Booking Service chưa khởi tạo"}, status_code=503)
    summary = booking_service.get_management_summary()
    return summary


@app.get("/api/status")
async def get_status():
    return {
        "map_loaded": road_map is not None,
        "nodes_count": len(map_nodes_cache) if map_nodes_cache else 0,
        "running": sim_state["running"],
        "paused": sim_state["paused"],
        "current_time": round(sim_engine_ref.current_time, 1) if sim_engine_ref else 0.0,
        "strategy": sim_state["strategy"],
        "speed_multiplier": sim_state["speed_multiplier"],
        "place": "Hoàn Kiếm, Hà Nội",
    }


@app.get("/api/map-data")
async def get_map_data():
    if not map_nodes_cache:
        return JSONResponse({"error": "Bản đồ chưa sẵn sàng"}, status_code=503)
    return {
        "center": [21.0285, 105.8542],
        "zoom": 15,
        "nodes": map_nodes_cache,
    }


@app.post("/api/start")
async def start_simulation(payload: dict):
    global sim_thread
    if sim_state["running"]:
        return JSONResponse({"error": "Mô phỏng đang chạy"}, status_code=400)
    if benchmark_running:
        return JSONResponse({"error": "Vui lòng đợi benchmark hoàn tất trước khi bắt đầu mô phỏng"}, status_code=409)
    if not road_map or map_service is None:
        return JSONResponse({"error": "Bản đồ chưa được tải"}, status_code=503)

    try:
        strategy = STRATEGY_ALIASES.get(payload.get("strategy", "greedy"), payload.get("strategy", "greedy"))
        num_drivers = int(payload.get("num_drivers", 10))
        arrival_rate = float(payload.get("arrival_rate", 0.04))
        speed_mult = float(payload.get("speed_multiplier", 2.0))
        start_hour = int(payload.get("start_hour", 7))
        batch_window_seconds = float(payload.get("batch_window_seconds", 30.0))
        use_spatiotemporal_demand = bool(payload.get("use_spatiotemporal_demand", True))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": f"Cấu hình mô phỏng không hợp lệ: {exc}"}, status_code=422)

    if strategy not in STRATEGY_LABELS:
        return JSONResponse({"error": f"Chiến lược không được hỗ trợ: {strategy}"}, status_code=422)
    if not 0 <= start_hour <= 23:
        return JSONResponse({"error": "Giờ bắt đầu phải nằm trong khoảng 0–23"}, status_code=422)
    if not 1 <= batch_window_seconds <= 300:
        return JSONResponse({"error": "Cửa sổ gom lô phải từ 1 đến 300 giây"}, status_code=422)
    if not 3 <= num_drivers <= 30 or not 0.001 <= arrival_rate <= 0.2:
        return JSONResponse({"error": "Số tài xế hoặc cường độ nhu cầu nằm ngoài giới hạn"}, status_code=422)

    sim_state.update({
        "strategy": strategy,
        "strategy_name": STRATEGY_LABELS[strategy],
        "num_drivers": num_drivers,
        "arrival_rate": arrival_rate,
        "speed_multiplier": speed_mult,
    })

    stop_event.clear()
    pause_event.clear()
    sim_thread = threading.Thread(
        target=simulation_worker,
        args=(
            strategy, num_drivers, arrival_rate, speed_mult, int(time.time()),
            start_hour, batch_window_seconds, use_spatiotemporal_demand,
        ),
        daemon=True,
    )
    sim_state["running"] = True
    sim_thread.start()
    return {"status": "started", "strategy": strategy}


@app.post("/api/stop")
async def stop_simulation():
    if not sim_state["running"]:
        return {"status": "already_stopped"}
    stop_event.set()
    return {"status": "stopping"}


@app.post("/api/pause")
async def pause_simulation():
    if not sim_state["running"]:
        return {"status": "not_running"}
    if pause_event.is_set():
        pause_event.clear()
        sim_state["paused"] = False
        send_ws_event({"type": "sim_resumed"})
        return {"status": "resumed"}
    else:
        pause_event.set()
        sim_state["paused"] = True
        send_ws_event({"type": "sim_paused"})
        return {"status": "paused"}


@app.post("/api/set-speed")
async def set_speed(payload: dict):
    mult = float(payload.get("multiplier", 1.0))
    sim_state["speed_multiplier"] = max(0.5, min(mult, 20.0))
    return {"speed_multiplier": sim_state["speed_multiplier"]}


@app.post("/api/benchmark")
async def run_benchmark_api(payload: dict):
    """Chạy benchmark 4 chiến lược trên server và trả về kết quả định lượng so sánh."""
    global benchmark_running
    if sim_state["running"]:
        return JSONResponse({"error": "Vui lòng dừng mô phỏng hiện tại trước khi chạy benchmark"}, status_code=400)
    if benchmark_running:
        return JSONResponse({"error": "Benchmark khác đang chạy, vui lòng thử lại sau"}, status_code=409)
    if not road_map or map_service is None:
        return JSONResponse({"error": "Bản đồ chưa sẵn sàng"}, status_code=503)

    try:
        num_drivers = int(payload.get("num_drivers", 8))
        arrival_rate = float(payload.get("arrival_rate", 0.03))
        sim_seconds = int(payload.get("sim_seconds", 1200))
        num_runs = int(payload.get("num_runs", 10))
        warmup_seconds = int(payload.get("warmup_seconds", 300))
        batch_window_seconds = float(payload.get("batch_window_seconds", 30.0))
        start_hour = int(payload.get("start_hour", 7))
        use_spatiotemporal_demand = bool(payload.get("use_spatiotemporal_demand", True))
        seed = int(payload.get("seed", 42))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": f"Cấu hình benchmark không hợp lệ: {exc}"}, status_code=422)
    if not 300 <= sim_seconds <= 7200:
        return JSONResponse(
            {"error": "Thời lượng benchmark phải từ 5 đến 120 phút mô phỏng"},
            status_code=422,
        )
    if not 1 <= num_runs <= 30:
        return JSONResponse({"error": "Số lần chạy phải từ 1 đến 30"}, status_code=422)
    if not 0 <= warmup_seconds < sim_seconds:
        return JSONResponse({"error": "Warm-up phải nhỏ hơn thời lượng mô phỏng"}, status_code=422)
    if not 1 <= batch_window_seconds <= 300:
        return JSONResponse({"error": "Cửa sổ gom lô phải từ 1 đến 300 giây"}, status_code=422)
    if not 0 <= start_hour <= 23:
        return JSONResponse({"error": "Giờ bắt đầu phải nằm trong khoảng 0–23"}, status_code=422)
    if not 3 <= num_drivers <= 30 or not 0.001 <= arrival_rate <= 0.2:
        return JSONResponse({"error": "Số tài xế hoặc cường độ nhu cầu nằm ngoài giới hạn"}, status_code=422)

    benchmark_running = True
    try:
        result = await asyncio.to_thread(
            run_benchmark,
            map_service,
            num_drivers=num_drivers,
            arrival_rate=arrival_rate,
            sim_seconds=sim_seconds,
            base_seed=seed,
            num_runs=num_runs,
            warmup_seconds=warmup_seconds,
            batch_window_seconds=batch_window_seconds,
            start_hour=start_hour,
            use_spatiotemporal_demand=use_spatiotemporal_demand,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    finally:
        benchmark_running = False

    for row in result["results"]:
        row["name"] = STRATEGY_LABELS[row["key"]]
    return result


@app.websocket("/ws/sim")
async def websocket_simulation(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "welcome",
            "map_loaded": road_map is not None,
            "running": sim_state["running"],
            "paused": sim_state["paused"],
            "strategy": sim_state["strategy"],
        }))
        if sim_state["running"] and sim_engine_ref:
            snapshot = build_snapshot(sim_engine_ref, all_requests_ref)
            await websocket.send_text(json.dumps(snapshot, ensure_ascii=False))
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        active_connections.discard(websocket)
    except Exception:
        active_connections.discard(websocket)


@app.websocket("/ws/portal")
async def websocket_portal(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "portal_welcome",
            "map_loaded": road_map is not None,
            "drivers_count": len(booking_service.drivers) if booking_service else 0,
        }))
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        active_connections.discard(websocket)
    except Exception:
        active_connections.discard(websocket)


if __name__ == "__main__":
    print("=" * 70)
    print("  RIDE-HAILING SIMULATOR - HỆ THỐNG ĐIỀU PHỐI GỌI XE HÀ NỘI")
    print("  Truy cập giao diện: http://localhost:8000")
    print("=" * 70)
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
