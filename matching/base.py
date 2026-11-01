"""Lớp cơ sở cho mọi chiến lược ghép cặp.

Các trường hợp biên dùng chung (không có tài xế, không có yêu cầu, id trùng,
tài xế bận) được xử lý một lần ở `match()`. Lớp con chỉ cần cài `_match()`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from math import inf, isfinite
from typing import Sequence

from .models import (Assignment, CostFn, Driver, MatchResult, Request, Stop,
                     StopType, euclidean, route_cost)


class Matcher(ABC):
    def __init__(self, max_pickup_cost: float = inf):
        """max_pickup_cost: chi phí tối đa từ tài xế đến điểm đón.
        Cặp nào vượt ngưỡng (hoặc cost = inf) không được ghép."""
        self.max_pickup_cost = max_pickup_cost

    # ------------------------------------------------------------ API công khai
    def match(self, requests: Sequence[Request], drivers: Sequence[Driver],
              cost_fn: CostFn = euclidean) -> MatchResult:
        self._check_unique_ids(requests, drivers)
        free = [d for d in drivers if d.available]

        if not requests or not free:
            return MatchResult(
                unmatched_requests=[r.id for r in requests],
                idle_drivers=[d.id for d in free],
            )

        result = self._match(list(requests), free, cost_fn)
        self._check_output(result, requests, free)
        return result

    # ------------------------------------------------------------ lớp con cài đặt
    @abstractmethod
    def _match(self, requests: list[Request], drivers: list[Driver],
               cost_fn: CostFn) -> MatchResult:
        """Nhận danh sách KHÔNG rỗng, tài xế đều rảnh. Trả về MatchResult."""

    # ------------------------------------------------------------ tiện ích cho lớp con
    def is_feasible(self, driver: Driver, request: Request, cost_fn: CostFn) -> bool:
        """Cặp (tài xế, yêu cầu) có hợp lệ không: đủ chỗ và trong ngưỡng đón."""
        if request.passengers > driver.capacity:
            return False
        c = cost_fn(driver.location, request.pickup)
        return isfinite(c) and c <= self.max_pickup_cost

    @staticmethod
    def single_assignment(driver: Driver, request: Request, cost_fn: CostFn) -> Assignment:
        """Dựng Assignment 1 khách (dùng cho Greedy và Hungarian)."""
        stops = [Stop(request.id, StopType.PICKUP, request.pickup),
                 Stop(request.id, StopType.DROPOFF, request.dropoff)]
        return Assignment(driver.id, stops, route_cost(driver.location, stops, cost_fn))

    @staticmethod
    def build_result(assignments: list[Assignment], requests: Sequence[Request],
                     drivers: Sequence[Driver]) -> MatchResult:
        """Tự suy ra danh sách chưa ghép / tài xế rảnh từ các assignment."""
        used_req = {rid for a in assignments for rid in a.request_ids}
        used_drv = {a.driver_id for a in assignments}
        return MatchResult(
            assignments=assignments,
            unmatched_requests=[r.id for r in requests if r.id not in used_req],
            idle_drivers=[d.id for d in drivers if d.id not in used_drv],
        )

    # ------------------------------------------------------------ kiểm tra
    @staticmethod
    def _check_unique_ids(requests, drivers):
        for name, items in (("request", requests), ("driver", drivers)):
            ids = [x.id for x in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Có {name} id bị trùng")

    @staticmethod
    def _check_output(result: MatchResult, requests, free_drivers):
        """Bắt lỗi của lớp con: mỗi yêu cầu/tài xế xuất hiện đúng một lần."""
        matched = result.matched_request_ids
        if len(matched) != len(set(matched)):
            raise AssertionError("Một yêu cầu bị giao cho nhiều tài xế")
        if set(matched) | set(result.unmatched_requests) != {r.id for r in requests} \
                or set(matched) & set(result.unmatched_requests):
            raise AssertionError("Yêu cầu không khớp giữa assignments và unmatched")
        used = [a.driver_id for a in result.assignments]
        if len(used) != len(set(used)):
            raise AssertionError("Một tài xế xuất hiện trong nhiều assignment")
        if set(used) | set(result.idle_drivers) != {d.id for d in free_drivers} \
                or set(used) & set(result.idle_drivers):
            raise AssertionError("Tài xế không khớp giữa assignments và idle_drivers")
