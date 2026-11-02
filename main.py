import random
import sys
from functools import partial

import numpy as np

from core_engine.entities import Driver
from core_engine.map_network import RealRoadMap
from core_engine.demand_generator import DemandGenerator
from core_engine.simulator import SimulationEngine
from visualize_map import generate_interactive_html_map

from matching import GreedyMatcher, HungarianMatcher, PoolingMatcher
from adapter import run_matcher_on_sim

if __name__ == "__main__":
    # Chọn thuật toán: python main.py greedy | hungarian | pooling
    MATCHERS = {"greedy": GreedyMatcher, "hungarian": HungarianMatcher, "pooling": PoolingMatcher}
    matcher_name = sys.argv[1] if len(sys.argv) > 1 else "greedy"
    if matcher_name not in MATCHERS:
        sys.exit(f"Thuật toán không hợp lệ: {matcher_name}. Chọn một trong {list(MATCHERS)}")

    random.seed(42)      # đặt seed TRƯỚC khi tạo tài xế để mọi lần chạy cùng kịch bản
    np.random.seed(42)

    # 1. Tải bản đồ giao thông thực tế
    road_map = RealRoadMap(place_name="Hoan Kiem, Hanoi, Vietnam")

    # 2. Khởi tạo 8 tài xế tại các vị trí giao lộ ngẫu nhiên
    drivers = [
        Driver(driver_id=f"D_{i+1:02d}", current_node=road_map.get_random_node())
        for i in range(8)
    ]

    # 3. Khởi tạo Động cơ sinh nhu cầu theo thời gian thực ()
    generator = DemandGenerator(road_map, arrival_rate=0.03, seed=42)

    # 4. Khởi tạo Động cơ mô phỏng
    sim = SimulationEngine(road_map, drivers, default_speed=8.33)  # ~30 km/h

    # 5. Khởi tạo Thuật toán GreedyMatcher
    # Giới hạn khoảng cách đón tối đa = 3000 mét (3km)
    matcher = MATCHERS[matcher_name](max_pickup_cost=3000.0)

    # Đóng gói matcher vào hàm callback dùng partial
    matching_callback = partial(run_matcher_on_sim, matcher)

    # 6. Vòng lặp mô phỏng
    dt = 1.0
    total_simulation_time = 1800.0  # 30 phút mô phỏng
    current_sim_time = 0.0
    all_generated_requests = []

    print(f"\n--- BẮT ĐẦU MÔ PHỎNG VỚI {matcher_name.upper()} MATCHER ---")

    while current_sim_time < total_simulation_time:
        # Sinh yêu cầu mới
        new_requests = generator.tick(current_sim_time)
        if new_requests:
            sim.add_requests(new_requests)
            all_generated_requests.extend(new_requests)

        # Chạy bước mô phỏng với Thuật toán GreedyMatcher
        sim.step(dt, matching_function=matching_callback)
        current_sim_time += dt

    # 7. In báo cáo thống kê
    print("\n================ TỔNG KẾT MÔ PHỎNG ================")
    print(f"Tổng số yêu cầu đã phát sinh: {len(all_generated_requests)}")
    print(f"Chuyến hoàn thành (COMPLETED): {len(sim.completed_requests)}")
    print(f"Chuyến bị hủy (CANCELLED):    {len(sim.cancelled_requests)}")
    print(f"Chuyến chưa hoàn thành:       {len(sim.active_requests) + len(sim.pending_requests)}")
    waits = [r.pickup_time - r.request_time for r in sim.completed_requests if r.pickup_time is not None]
    if waits:
        print(f"Thời gian chờ đón trung bình:  {sum(waits) / len(waits):.1f} giây")
    print("===================================================")

    # 8. Xuất bản đồ tương tác Web HTML
    generate_interactive_html_map(
        road_map, 
        drivers, 
        all_generated_requests, 
        output_file="simulation_map.html"
    )