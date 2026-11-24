# HỆ THỐNG ĐIỀU PHỐI VÀ GHÉP CHUYẾN CHO DỊCH VỤ GỌI XE (HÀ NỘI)
> **Đề tài Tiểu luận môn:** Khoa học Dịch vụ (Service Science)  
> **Khu vực mô phỏng thực tế:** Quận Hoàn Kiếm, TP. Hà Nội (Dữ liệu OpenStreetMap: 216 nút giao lộ, 462 đoạn đường)  
> **Giao diện vận hành:** Web Dashboard mô phỏng trung tâm điều phối UETransport Operations (Leaflet.js + Chart.js + FastAPI WebSocket)

---

## 1. Kiến trúc hướng dịch vụ (SOA)

Trước đây `server.py` gọi trực tiếp bộ sinh nhu cầu, thuật toán ghép cặp và lõi mô phỏng; các lớp trong `services/` chưa nằm trên luồng xử lý chính. Cấu trúc đó là modular monolith, chưa thể hiện rõ ranh giới dịch vụ. Luồng ứng dụng hiện được tổ chức theo các dịch vụ có hợp đồng DTO riêng và một lớp điều phối:

```text
Web UI
  │ REST commands / WebSocket events
  ▼
API Gateway (FastAPI: server.py)
  │
  ▼
Simulation Service ───────────────► Simulation Engine
  ├── Demand Service ── TripRequestDTO
  ├── Matching Service ── DriverDTO + TripRequestDTO ──► Matching algorithms
  └── Map Service ── nodes / routes / distance
          │
          └── OpenStreetMap network

Service events: EventBus (REQUEST_CREATED, DISPATCH_BATCH_EXECUTED, ...)
```

`SimulationService` là composition/orchestration layer và là anti-corruption layer giữa DTO của các dịch vụ với entity nội bộ của mô phỏng. Dịch vụ Nhu cầu không trả entity lõi; Dịch vụ Ghép xe nhận DTO và cổng tính khoảng cách, không phụ thuộc trực tiếp vào `core_engine`. API Gateway là điểm vào cho giao diện và chuyển lệnh tới luồng mô phỏng. CLI và benchmark cũng dùng cùng luồng service để tránh hai cách chạy khác nhau.

**Phạm vi triển khai:** các ranh giới và hợp đồng SOA đã được áp dụng trong ứng dụng, nhưng hiện các dịch vụ vẫn được host trong cùng tiến trình Python; EventBus là in-memory, không phải message broker bền vững. Vì vậy đây là SOA-style single-process deployment, chưa phải các dịch vụ phân tán có thể triển khai/scale độc lập. Muốn tách tiến trình, cần thay các cổng service bằng REST/gRPC clients và thay EventBus bằng broker, đồng thời bổ sung xác thực service-to-service, timeout/retry, cấu hình endpoint và observability.

```
KHDV/
├── core_engine/                 # Động cơ mô phỏng không-thời gian (Spatiotemporal Engine)
│   ├── entities.py              # Thực thể: Driver, Request, DriverState, RequestState
│   ├── map_network.py           # Bản đồ OSMnx: tải đồ thị đường phố, tính Dijkstra, nội suy tọa độ
│   └── simulator.py             # Quản lý vòng lặp tick mô phỏng, chuyển động xe, đo lường KPIs
│
├── matching/                    # Các chiến lược ghép cặp (Dispatch & Matching Strategies)
│   ├── base.py                  # Lớp trừu tượng Matcher, memoize_cost_fn, kiểm tra tính hợp lệ
│   ├── models.py                # Mô hình dữ liệu: Driver, Request, Stop, Assignment, MatchResult
│   ├── greedy.py                # Chiến lược 1: Greedy Nearest Matching (Tham lam)
│   ├── hungarian.py             # Chiến lược 2: Batch Matching với thuật toán Hungarian (Kuhn-Munkres)
│   ├── pooling.py               # Chiến lược 3: Greedy Ride-Pooling Matching (Ghép chuyến chung)
│   └── hungarian_pooling.py     # Chiến lược 4: Hungarian Ride-Pooling (Hungary gom lô + Ghép chuyến)
│
├── services/                    # Service layer và hợp đồng trao đổi DTO
│   ├── contracts.py             # Hợp đồng dữ liệu dùng chung giữa các service
│   ├── base.py                  # Kết quả service và EventBus
│   ├── simulation_service.py    # Điều phối và chuyển DTO sang entity lõi
│   ├── map_service/             # Dữ liệu nút, định tuyến, khoảng cách
│   ├── demand_service/          # Sinh yêu cầu và phát REQUEST_CREATED
│   ├── matching_service/        # Ghép chuyến qua DriverDTO/TripRequestDTO
│   └── benchmark_service.py     # Chạy lặp nhiều seed và tổng hợp KPI
│
├── tests/                       # Unit tests cho lõi và các service
│   ├── test_base.py
│   ├── test_greedy.py
│   ├── test_hungarian.py
│   ├── test_pooling.py
│   ├── test_hungarian_pooling.py
│   ├── test_node_ids.py
│   └── test_services.py
│
├── static/                      # Giao diện Web App điều hành
│   └── index.html               # Web UI tương tác: Leaflet bản đồ Hà Nội, Chart.js, KPI real-time
│
├── server.py                    # API Gateway REST/WebSocket
└── start_server.bat             # File khởi chạy nhanh 1-click cho Windows
```

---

## 2. Bốn Chiến lược Ghép Cặp (Matching Strategies)

### Chiến lược 1: Greedy Nearest Matching (S1)
* **Nguyên lý:** Xử lý yêu cầu theo thứ tự đặt (FIFO). Với mỗi yêu cầu, quét toàn bộ tài xế rảnh hợp lệ (khoảng cách đón $\le 3000\text{m}$) và chọn tài xế có chi phí tiếp cận nhỏ nhất.
* **Độ phức tạp:** $O(R \times D)$ lần gọi hàm chi phí (Dijkstra).
* **Đặc điểm:** Tốc độ tính toán nhanh nhất, phản hồi ngay lập tức, nhưng dễ dẫn đến quyết định cận tối ưu (khách đặt trước cướp tài xế gần, đẩy khách sau vào tài xế rất xa).

### Chiến lược 2: Hungarian Batch Matching (S2)
* **Nguyên lý:** Gom các yêu cầu trong một cửa sổ thời gian thành lô (batch). Xây dựng ma trận chi phí hai phía $C_{ij}$ giữa các yêu cầu và các tài xế rảnh, giải bài toán phân công tối ưu toàn cục bằng thuật toán Kuhn-Munkres:
  $$\min \sum_{i} \sum_{j} C_{ij} x_{ij} \quad \text{s.t.} \quad \sum_j x_{ij} \le 1, \quad \sum_i x_{ij} \le 1$$
* **Độ phức tạp:** $O(k^2 \cdot m)$ với $k = \min(R, D), m = \max(R, D)$.
* **Đặc điểm:** Tối thiểu hóa tổng thời gian chờ đón của toàn bộ khách hàng trong lô, loại bỏ hiện tượng tranh chấp tài xế.

### Chiến lược 3: Greedy Ride-Pooling Matching (S3)
* **Nguyên lý:** Cho phép 1 tài xế phục vụ đồng thời nhiều khách cùng hướng đi (carpooling). Với yêu cầu mới, thử chèn (insertion heuristic) điểm đón và điểm trả vào mọi vị trí hợp lệ trong lộ trình của các tài xế, thỏa mãn:
  1. Số khách trên xe không vượt quá sức chứa (`capacity = 4`).
  2. Thời gian đón của mỗi khách $\le \text{max\_pickup\_cost}$.
  3. Tỷ lệ đi vòng của từng khách không vượt quá $\text{max\_detour\_ratio} = 1.5$ lần quãng đường đi thẳng.
* **Đặc điểm:** Nâng cao rõ rệt hiệu suất sử dụng tài xế, giảm lượng xe cần thiết.

### Chiến lược 4: Hungarian Ride-Pooling Matching (S4)
* **Nguyên lý:** Kết hợp tối ưu toàn cục đợt phân bổ 1-1 ban đầu bằng thuật toán Hungarian, sau đó dùng thuật toán chèn tối ưu (insertion) để ghép các yêu cầu còn lại vào xe đang di chuyển.
* **Đặc điểm:** Kết hợp ưu điểm giảm thời gian đón của Hungarian và nâng cao công suất của Pooling.

---

## 3. Các Chỉ số Đánh giá Hiệu năng (KPIs)

1. **Thời gian chờ đón khách trung bình (Average Pickup Wait Time):**
   $$\overline{W}_{\text{pickup}} = \frac{1}{|C|} \sum_{r \in C} (t_{\text{pickup}}^{(r)} - t_{\text{request}}^{(r)}) \quad (\text{giây})$$
2. **Tỷ lệ chuyến bị hủy (Cancellation Rate):**
   $$\text{CR} = \frac{N_{\text{cancelled}}}{N_{\text{total}}} \times 100\%$$
   *(Khách hàng hủy chuyến nếu sau 300 giây = 5 phút chưa được gán tài xế).*
3. **Hiệu suất sử dụng tài xế (Driver Fleet Utilization):**
   $$U = \frac{\sum_{d \in D} T_{\text{busy}}^{(d)}}{|D| \times T_{\text{sim}}} \times 100\%$$
   *(Tỷ lệ phần trăm thời gian tài xế thực sự bận đón hoặc chở khách trên tổng thời gian vận hành).*
4. **Tổng quãng đường di chuyển và quãng đường chạy rỗng (Total Distance & Deadheading Distance).**

**Mô hình nhu cầu hiện tại:** số yêu cầu đến tuân theo Poisson không đồng nhất, với cường độ cơ sở λ được điều chỉnh theo profile 24 giờ; người dùng có thể chọn giờ bắt đầu và bật/tắt profile. Vị trí đón/trả được lấy từ hai vùng tổng hợp trên các nút OSM (vùng lõi và vùng ngoài), có xác suất thiên lệch điểm đón về lõi và chọn điểm trả khác vùng. Đây là giả định tổng hợp có seed để tái lập, không phải dữ liệu chuyến xe quan sát hay ma trận O-D thực tế. Báo cáo cần nêu rõ điều này; nếu có dữ liệu thực, nên thay profile giả định bằng phân bố theo thời gian/vùng được hiệu chỉnh từ dữ liệu.

---

## 4. Thực nghiệm và so sánh định lượng

Benchmark chạy bốn chiến lược trên cùng cấu hình cho từng seed; mỗi chiến lược nhận cùng chuỗi nhu cầu và seed khởi tạo tài xế tương ứng. Hungarian và Hungarian Pooling chỉ dispatch tại ranh giới cửa sổ gom lô; Greedy và Greedy Pooling vẫn dispatch theo tick. Giao diện mặc định 20 phút mô phỏng mỗi chiến lược, 10 lượt và 5 phút warm-up; có thể chọn 5–120 phút, 1–30 lượt và warm-up ngắn hơn thời lượng.

**20 phút hợp lý cho demo/smoke test, chưa đủ để kết luận chiến lược nào tốt hơn.** Với nhiều lượt, bảng báo cáo trung bình ± độ lệch chuẩn mẫu cho các KPI; chỉ số chờ đón tính trên các chuyến đã được đón (kể cả chuyến đang chạy), còn chuyến chưa hoàn tất được báo riêng. Warm-up loại các yêu cầu phát sinh trước mốc đo khỏi KPI theo cohort; mức sử dụng tài xế và quãng đường chỉ tính phần thời gian sau warm-up.

Để theo dõi, nên giữ nguyên số tài xế, λ, profile nhu cầu, mạng đường, thời lượng, cửa sổ gom lô và bộ seed cho cả bốn chiến lược; chạy nhiều seed (ví dụ 20–30 lần), dùng cùng khoảng warm-up, rồi báo cáo trung bình và độ lệch chuẩn/khoảng tin cậy. Ghi cấu hình và seed range mà giao diện trả về; diễn giải các profile không gian/thời gian là giả định tổng hợp, không phải quan sát thực tế.

---

## 5. Hướng dẫn Sử dụng

### Cách 1: Chạy Giao diện Web Điều hành (Khuyến nghị)
1. Nhấp đúp chuột vào file **`start_server.bat`** (hoặc chạy lệnh: `python server.py`).
2. Mở trình duyệt web truy cập: **`http://localhost:8000`**
3. Các tính năng trên giao diện:
   - Bản đồ số Hà Nội với xe di chuyển theo thời gian thực dọc theo các tuyến phố.
   - Chuyển đổi linh hoạt giữa 4 chiến lược ghép chuyến.
   - Tùy chỉnh số lượng tài xế (3 - 30 xe), cường độ Poisson $\lambda$, và thanh trượt tốc độ (1x - 10x).
   - Nút **"So sánh chiến lược"** để cấu hình thời lượng, số lượt, warm-up, seed, giờ bắt đầu, profile nhu cầu và cửa sổ gom lô; benchmark chạy trên máy chủ, khi offline ứng dụng không giả lập số liệu kết quả.

### Cách 2: Chạy Kiểm thử Unit Test
```bash
python -m pytest tests/
```
*(Toàn bộ 212 tests xác thực tính toàn vẹn của đồ thị, thuật toán gán, và các ràng buộc ghép chuyến).*
