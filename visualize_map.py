import os
import webbrowser
import folium
from folium import plugins
from dotenv import load_dotenv

load_dotenv()
CARTO_API_KEY = os.getenv("CARTO_API_KEY")

def generate_interactive_html_map(
    road_map, 
    drivers: list, 
    requests: list, 
    output_file: str = "simulation_map.html",
    carto_api_key: str = None
):
    """
    Tạo bản đồ tương tác HTML hiển thị rõ đường đi thực tế bám theo đường phố
    cho từng Request, hỗ trợ bật/tắt từng layer và click vào đường đi để xem chi tiết.
    """
    if not road_map.nodes:
        print("Lỗi: Bản đồ không có dữ liệu giao lộ.")
        return

    center_coords = road_map.get_node_latlon(road_map.nodes[0])

    # 1. Khởi tạo Tile bản đồ
    if carto_api_key:
        tiles_url = f"https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png?api_key={carto_api_key}"
        attr = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
        m = folium.Map(location=center_coords, zoom_start=14, tiles=tiles_url, attr=attr)
    else:
        m = folium.Map(
            location=center_coords,
            zoom_start=14,
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
            attr="Esri World Street Map"
        )

    # 2. Tạo Layer quản lý Vị trí Tài xế
    fg_drivers = folium.FeatureGroup(name="🚗 Vị trí các Tài xế", show=True)
    for driver in drivers:
        lat, lon = road_map.get_node_latlon(driver.current_node)
        status_val = getattr(driver.status, 'value', str(driver.status))
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(f"<b>Tài xế:</b> {driver.driver_id}<br><b>Trạng thái:</b> {status_val}", max_width=250),
            tooltip=f"Tài xế {driver.driver_id}",
            icon=folium.Icon(color="blue", icon="car", prefix="fa")
        ).add_to(fg_drivers)
    fg_drivers.add_to(m)

    # 3. Định nghĩa bảng màu theo trạng thái chuyến
    status_colors = {
        "COMPLETED": "#2ea44f",   # Xanh lá
        "MATCHED": "#0969da",     # Xanh dương
        "PICKED_UP": "#8a2be2",   # Tím
        "WAITING": "#d97706",     # Cam
        "CANCELLED": "#cf222e"    # Đỏ
    }

    # 4. Hiển thị từng Yêu cầu (Request) kèm Đường đi thực tế
    for req in requests:
        orig_lat, orig_lon = road_map.get_node_latlon(req.origin_node)
        dest_lat, dest_lon = road_map.get_node_latlon(req.destination_node)

        status_str = getattr(req.status, 'value', str(req.status))
        color = status_colors.get(status_str, "#6e7781")
        driver_matched = getattr(req, 'matched_driver_id', None) or "Chưa có"

        # Tạo Layer Group riêng cho từng chuyến xe (để hiển thị trong menu điều khiển)
        req_layer = folium.FeatureGroup(name=f"Chuyến {req.request_id} [{status_str}]", show=False)

        # Điểm Đón (Pickup)
        folium.Marker(
            location=[orig_lat, orig_lon],
            popup=folium.Popup(f"<b>[ĐÓN] Yêu cầu:</b> {req.request_id}<br><b>Node:</b> {req.origin_node}", max_width=200),
            tooltip=f"Đón {req.request_id}",
            icon=folium.Icon(color="green" if status_str == "COMPLETED" else "orange", icon="user", prefix="fa")
        ).add_to(req_layer)

        # Điểm Trả (Dropoff)
        folium.Marker(
            location=[dest_lat, dest_lon],
            popup=folium.Popup(f"<b>[TRẢ] Yêu cầu:</b> {req.request_id}<br><b>Node:</b> {req.destination_node}", max_width=200),
            tooltip=f"Trả {req.request_id}",
            icon=folium.Icon(color="red" if status_str == "CANCELLED" else "darkgreen", icon="flag", prefix="fa")
        ).add_to(req_layer)

        # Tính toán Tuyến đường thực tế bám theo đồ thị đường phố OpenStreetMap
        path_nodes = road_map.get_shortest_path(req.origin_node, req.destination_node)

        if len(path_nodes) > 1:
            # Lấy danh sách tọa độ (lat, lon) dọc theo lộ trình
            route_coords = [road_map.get_node_latlon(node) for node in path_nodes]
            dist_m = road_map.get_path_distance(path_nodes)

            # Tính toán Tuyến đường thực tế bám theo đồ thị đường phố OpenStreetMap
        path_nodes = road_map.get_shortest_path(req.origin_node, req.destination_node)

        if len(path_nodes) > 1:
            # LẤY TỌA ĐỘ HÌNH HỌC CHÍNH XÁC BÁM THEO MẶT ĐƯỜNG (Thay cho get_node_latlon đơn thuần)
            route_coords = road_map.get_route_geometry(path_nodes)
            dist_m = road_map.get_path_distance(path_nodes)

            # Vẽ PolyLine bám sát lòng đường
            folium.PolyLine(
                locations=route_coords,
                color=color,
                weight=5,
                opacity=0.85,
                tooltip=f"Lộ trình {req.request_id} (Click xem chi tiết)",
                popup=folium.Popup(
                    f"<div style='width: 200px;'>"
                    f"<b>Mã chuyến:</b> {req.request_id}<br>"
                    f"<b>Trạng thái:</b> <span style='color:{color};font-weight:bold;'>{status_str}</span><br>"
                    f"<b>Tài xế:</b> {driver_matched}<br>"
                    f"<b>Chiều dài lộ trình:</b> {dist_m:.0f} mét"
                    f"</div>",
                    max_width=250
                )
            ).add_to(req_layer)
        else:
            # Fallback nếu 2 điểm trùng nhau hoặc không thấy đường
            folium.PolyLine(
                locations=[[orig_lat, orig_lon], [dest_lat, dest_lon]],
                color=color, weight=3, opacity=0.5, dash_array='5, 10'
            ).add_to(req_layer)

        req_layer.add_to(m)

    # 5. Thêm bảng Bật/Tắt Layer và nút Xem toàn màn hình
    folium.LayerControl(collapsed=False).add_to(m)
    plugins.Fullscreen().add_to(m)

    m.save(output_file)
    print(f"-> Đã lưu bản đồ tại: {output_file}")
    
    abs_path = os.path.abspath(output_file)
    webbrowser.open(f"file://{abs_path}")