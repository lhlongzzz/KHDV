import sys

from core_engine.map_network import RealRoadMap
from visualize_map import generate_interactive_html_map

from matching import (
    GreedyMatcher,
    HungarianMatcher,
    PoolingMatcher,
    HungarianPoolingMatcher,
)
from services.map_service.service import MapService
from services.matching_service.contracts import DispatchStrategy
from services.simulation_service import SimulationService

MATCHERS = {
    "greedy": (GreedyMatcher, "S1: Greedy (Tham lam gần nhất)"),
    "hungarian": (HungarianMatcher, "S2: Hungarian (Gom lô tối ưu toàn cục)"),
    "pooling": (PoolingMatcher, "S3: Greedy Pooling (Tham lam + Ghép chuyến)"),
    "greedy_pool": (PoolingMatcher, "S3: Greedy Pooling (Tham lam + Ghép chuyến)"),
    "hungarian_pooling": (HungarianPoolingMatcher, "S4: Hungarian Pooling (Hungary + Ghép chuyến)"),
    "hungarian_pool": (HungarianPoolingMatcher, "S4: Hungarian Pooling (Hungary + Ghép chuyến)"),
}


def run_single_simulation(
    road_map: RealRoadMap,
    matcher_cls,
    label: str,
    num_drivers: int = 8,
    arrival_rate: float = 0.03,
    total_time: float = 1800.0,
    seed: int = 42,
    dt: float = 1.0,
) -> dict:
    """Chạy mô phỏng đơn lẻ cho một chiến lược ghép cặp."""
    strategy_by_matcher = {
        GreedyMatcher: DispatchStrategy.GREEDY,
        HungarianMatcher: DispatchStrategy.HUNGARIAN,
        PoolingMatcher: DispatchStrategy.POOLING,
        HungarianPoolingMatcher: DispatchStrategy.HUNGARIAN_POOLING,
    }
    strategy = strategy_by_matcher.get(matcher_cls)
    if strategy is None:
        raise ValueError(f"Thuật toán ghép cặp không được hỗ trợ: {matcher_cls.__name__}")

    map_service = MapService(road_map=road_map)
    initialized = map_service.initialize()
    if not initialized.success:
        raise RuntimeError(initialized.error or "Map Service không sẵn sàng")

    runtime = SimulationService(
        map_service=map_service,
        num_drivers=num_drivers,
        arrival_rate=arrival_rate,
        strategy=strategy,
        seed=seed,
    )
    sim = runtime.engine
    drivers = list(sim.drivers.values())
    all_requests = []

    print(f"\n--- BẮT ĐẦU MÔ PHỎNG: {label} ---")

    while sim.current_time < total_time:
        all_requests.extend(runtime.step(dt))

    kpis = sim.get_kpis(len(all_requests))
    kpis["label"] = label
    kpis["drivers"] = drivers
    kpis["requests"] = all_requests
    kpis["sim"] = sim

    # In báo cáo
    print("\n================ TỔNG KẾT MÔ PHỎNG ================")
    print(f"Chiến lược:                   {label}")
    print(f"Tổng số yêu cầu đã phát sinh: {kpis['total_requests']}")
    print(f"Chuyến hoàn thành (COMPLETED): {kpis['completed_requests']} ({kpis['completion_rate']}%)")
    print(f"Chuyến bị hủy (CANCELLED):    {kpis['cancelled_requests']} ({kpis['cancellation_rate']}%)")
    print(f"Chuyến chưa hoàn thành:       {kpis['active_requests'] + kpis['pending_requests']}")
    print(f"Thời gian chờ đón trung bình:  {kpis['avg_wait_time']} giây")
    print(f"Hiệu suất sử dụng tài xế:     {kpis['driver_utilization']}%")
    print(f"Tổng quãng đường di chuyển:   {kpis['total_distance_km']} km")
    print("===================================================")

    return kpis


def run_benchmark_all(road_map: RealRoadMap):
    """Chạy và so sánh cả 4 chiến lược ghép cặp trên cùng một kịch bản nhu cầu."""
    strategies = [
        ("greedy", GreedyMatcher, "S1: Greedy"),
        ("hungarian", HungarianMatcher, "S2: Hungarian"),
        ("pooling", PoolingMatcher, "S3: Greedy+Pool"),
        ("hungarian_pooling", HungarianPoolingMatcher, "S4: HU+Pool"),
    ]

    results = []
    for key, cls, name in strategies:
        res = run_single_simulation(
            road_map=road_map,
            matcher_cls=cls,
            label=name,
            num_drivers=8,
            arrival_rate=0.03,
            total_time=1800.0,
            seed=42,
        )
        results.append(res)

    print("\n" + "=" * 90)
    print("             BẢNG SO SÁNH 4 CHIẾN LƯỢC GHÉP CHUYẾN (HÀ NỘI - HOÀN KIẾM)")
    print("=" * 90)
    header = f"{'Chiến lược':<20} | {'Tổng':<6} | {'Xong':<6} | {'Hủy':<5} | {'Tỷ lệ hủy':<10} | {'Đón TB (s)':<11} | {'Hiệu suất':<10} | {'Tổng km':<8}"
    print(header)
    print("-" * 90)
    for r in results:
        row = f"{r['label']:<20} | {r['total_requests']:<6} | {r['completed_requests']:<6} | {r['cancelled_requests']:<5} | {r['cancellation_rate']:>8.1f}% | {r['avg_wait_time']:>9.1f}s | {r['driver_utilization']:>8.1f}% | {r['total_distance_km']:>7.1f}"
        print(row)
    print("=" * 90)


if __name__ == "__main__":
    matcher_name = sys.argv[1].lower() if len(sys.argv) > 1 else "greedy"

    valid_choices = list(MATCHERS.keys()) + ["compare", "benchmark", "all"]
    if matcher_name not in valid_choices:
        sys.exit(f"Thuật toán không hợp lệ: {matcher_name}.\nChọn một trong: {valid_choices}")

    # 1. Tải bản đồ giao thông thực tế Hà Nội
    road_map = RealRoadMap(place_name="Hoan Kiem, Hanoi, Vietnam")

    if matcher_name in ("compare", "benchmark", "all"):
        run_benchmark_all(road_map)
    else:
        matcher_cls, label = MATCHERS[matcher_name]
        result = run_single_simulation(
            road_map=road_map,
            matcher_cls=matcher_cls,
            label=label,
            num_drivers=8,
            arrival_rate=0.03,
            total_time=1800.0,
            seed=42,
        )

        # Xuất bản đồ tương tác Web HTML cho lần chạy
        generate_interactive_html_map(
            road_map,
            result["drivers"],
            result["requests"],
            output_file="simulation_map.html",
        )
