# Hướng Dẫn Chi Tiết Plugin Adapter: Hearts of Iron IV (HOI4)

Plugin `ugm.adapters.hoi4` là adapter đầu tiên được tích hợp sẵn trong Universal Game MCP (UGM), phục vụ quan sát và điều khiển tựa game chiến thuật **Hearts of Iron IV** (Paradox Interactive).

---

## 🗺️ Bản Đồ Vùng Giao Diện (Normalized UI Regions)

Tất cả các vùng tọa độ được chuẩn hóa tỉ lệ `[0.0, 1.0]` tương thích với mọi độ phân giải tỷ lệ 16:9 (1080p, 1440p, 4K):

| Tên vùng | Tọa độ chuẩn hóa `[x, y, w, h]` | Ý nghĩa trong game HOI4 |
|---|---|---|
| `topbar` | `[0.0, 0.0, 1.0, 0.05]` | Toàn bộ thanh tài nguyên và chỉ số quốc gia phía trên |
| `political_power` | `[0.040, 0.0, 0.055, 0.04]` | Chỉ số Điểm Chính Trị (Political Power) |
| `stability` | `[0.095, 0.0, 0.055, 0.04]` | Chỉ số Độ Ổn Định Quốc Gia (Stability) |
| `war_support` | `[0.150, 0.0, 0.055, 0.04]` | Chỉ số Ủng Hộ Chiến Tranh (War Support) |
| `manpower` | `[0.205, 0.0, 0.070, 0.04]` | Nhân Lực Quân Sự Hiện Có (Manpower) |
| `factories` | `[0.275, 0.0, 0.065, 0.04]` | Số lượng nhà máy dân sự & quân sự (Factories) |
| `convoys` | `[0.340, 0.0, 0.060, 0.04]` | Đoàn tàu vận tải biển (Convoys) |
| `command_power` | `[0.400, 0.0, 0.060, 0.04]` | Điểm Mệnh Lệnh Quân Sự (Command Power) |
| `date_clock` | `[0.830, 0.0, 0.100, 0.045]` | Ngày tháng và đồng hồ thời gian |
| `speed_controls` | `[0.930, 0.0, 0.070, 0.045]` | Nút tăng giảm tốc độ thời gian (1x - 5x) |
| `pause_indicator`| `[0.850, 0.045, 0.080, 0.035]` | Biển báo trạng thái tạm dừng (PAUSED) màu đỏ |
| `event_dialog` | `[0.280, 0.250, 0.440, 0.500]` | Hộp thoại thông báo sự kiện quốc tế / ngoại giao |

---

## ⌨️ Bảng Phím Tắt DirectInput Tích Hợp Sẵn

| Thao tác | Phím gửi | Ghi chú |
|---|---|---|
| Tạm dừng / Tiếp tục | `["space"]` | Phím cách dừng/chạy game |
| Tăng tốc độ game | `["num+"]` | Tăng 1 nấc tốc độ |
| Giảm tốc độ game | `["num-"]` | Giảm 1 nấc tốc độ |
| Mở Nghiên cứu (Research) | `["w"]` | Bật cây công nghệ |
| Mở Ngoại giao (Diplomacy) | `["e"]` | Danh sách quốc gia |
| Mở Thương mại (Trade) | `["r"]` | Mua bán tài nguyên dầu mỏ, cao su, thép |
| Mở Xây dựng (Construction)| `["t"]` | Xây nhà máy, công sự, radar |
| Mở Sản xuất (Production) | `["y"]` | Dây chuyền sản xuất vũ khí, xe tăng, máy bay |
| Đóng cửa sổ / Thoát menu | `["escape"]` | Hủy chọn hoặc đóng popup hiện tại |

---

## 🔍 Bộ Icon Mẫu Nhận Diện Sẵn
Adapter đi kèm các icon mẫu chuẩn nằm tại thư mục `ugm/adapters/hoi4/icons/`:
- `civilian_factory.png`: Icon nhà máy dân sự.
- `military_factory.png`: Icon nhà máy quân sự.
- `political_power.png`: Biểu tượng chim đại bàng / vòng nguyệt quế.
- `stability.png`: Biểu tượng cán cân công lý (Độ ổn định).
- `pause_btn.png`: Hai vạch pause màu vàng trên nền đỏ.
