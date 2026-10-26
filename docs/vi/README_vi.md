# Hướng dẫn Universal Game MCP (UGM) - Phiên bản V1.5 (Tiếng Việt)

Dự án mã nguồn mở **Universal Game MCP (UGM)** xây dựng hạ tầng máy chủ Model Context Protocol (MCP) gọn nhẹ, độc lập và có khả năng tự học cho các trợ lý trí tuệ nhân tạo (AI/LLM). 

Mô hình AI bên ngoài cung cấp khả năng suy luận lý luận. UGM cung cấp:
- **Đôi mắt**: Chụp màn hình DXGI/GDI, nhận diện chữ (RapidOCR theo sự kiện), khớp icon và template OpenCV.
- **Đôi tay**: Điều khiển chuột và bàn phím DirectInput với cơ chế mô phỏng an toàn (Dry-Run).
- **Trí nhớ**: Kho tri thức SQLite bền vững (WAL mode, migrations, bảng băm tri giác dHash).
- **Dẫn đường**: Nhận diện trạng thái giao diện UI và thuật toán Dijkstra tìm đường qua các menu/bảng điều khiển.
- **Kỹ năng**: Bộ máy thực thi kiểm định các kỹ năng macro (Game Skills).
- **Khả năng tự học**: Quy trình tự phát hiện, kiểm chứng, thăng cấp và tái sử dụng workflow mới.

---

## 📊 Kết Quả Đo Đạc Hiệu Năng Thực Tế (CPU AMD Ryzen 5 5500U)

Hệ thống được đo đạc trực tiếp trên vi xử lý **AMD Ryzen 5 5500U** (6 nhân / 12 luồng, GPU tích hợp Radeon, Windows 11 x64):

| Thành phần | Độ trễ đo được | Ghi chú kỹ thuật |
|---|---|---|
| **Chụp ảnh bộ nhớ giả lập (Memcpy)** | **1.79 ms** | Mức cơ sở sao chép buffer trong RAM |
| **Khớp Icon ROI (OpenCV)** | **43.97 ms** | **Nhanh hơn 13.2 lần** so với toàn màn hình |
| **Khớp Icon toàn màn hình 1080p** | **579.94 ms** | Quét đa tỷ lệ (Multi-scale + NMS) |
| **OCR theo sự kiện (Crop ROI)** | **831.89 ms** | RapidOCR PP-OCRv4 CPU trên vùng bounding box |
| **OCR toàn màn hình 1080p** | **2,185.15 ms** | Tốn CPU, tự động tránh dùng nhờ crop ROI |
| **Xác minh thay đổi (Visual Diff)** | **5.48 ms** | Ma trận sai phân thời gian thực `absdiff` |
| **Tiêu thụ RAM ổn định** | **136.12 MB** | Toàn bộ mã nguồn, model OCR & SQLite (< 150 MB) |

> [!NOTE]
> Phép đo chụp bộ nhớ giả lập (synthetic in-memory) được tách biệt rõ ràng với chụp màn hình thực tế (live desktop duplication) để đảm bảo tính minh bạch khoa học.

---

## 🏗️ Kiến Trúc Hệ Thống V1.5

1. **Bộ nhớ bền vững SQLite (`ugm.memory`)**:
   - Sử dụng SQLite chế độ WAL (Write-Ahead Logging) cho hiệu năng đọc/ghi đồng thời.
   - Quản lý phiên bản schema tự động thông qua migrations (`schema_version`).
   - Phân vùng tri thức theo từng tựa game (`game_id`) và phiên bản.
   - Lưu trữ băm tri giác dHash (Hamming distance) nhận diện giao diện tương đồng.
   - Phân biệt minh bạch giữa kinh nghiệm giả lập (`synthetic_fixture`) và kinh nghiệm chơi game thực tế (`live`).
   - Hỗ trợ xuất (`export_bundle`), nhập (`import_bundle`) và dọn dẹp bộ nhớ an toàn.

2. **Nhận diện trạng thái giao diện UI (`ugm.navigation.recognizer`)**:
   - Phân loại trạng thái màn hình (`main_map`, `production`, `research`, `diplomacy`, `army_selected`, `unknown`).
   - Tích hợp đa tín hiệu: băm tri giác dHash, OCR văn bản nổi bật, hình học topbar/ngăn kéo.
   - Báo cáo rõ mức độ tin cậy (`confidence`), bằng chứng quan sát và trạng thái (`known`, `ambiguous`, `unknown`).

3. **Đồ thị dẫn đường giao diện (`ugm.navigation.graph`, `ugm.navigation.navigator`)**:
   - Đỉnh (Node): Trạng thái giao diện UI đã nhận diện.
   - Cạnh (Edge): Thao tác chuyển tiếp có thể thực thi kèm chi phí và độ tin cậy lịch sử.
   - Tìm lộ trình ngắn nhất bằng thuật toán Dijkstra.
   - Dẫn đường từng bước: kiểm tra trạng thái hiện tại -> thực thi thao tác -> kiểm tra trạng thái sau thao tác -> tái lập kế hoạch hoặc dừng lại an toàn nếu phát hiện sai lệch.

4. **Quy trình tự học có kiểm chứng (`ugm.skills.learner`)**:
   - **Quan sát → Nhận diện → Lập kế hoạch → Hành động → Xác minh → Học hỏi → Tái sử dụng.**
   - Kỹ năng mới phát hiện được lưu dưới dạng `candidate` (ứng viên).
   - Chỉ được thăng cấp lên `verified` khi vượt qua tối thiểu 3 lần kiểm chứng độc lập với độ tin cậy ≥ 0.85.
   - **Bảo vệ tuyệt đối Game Skill tuyển chọn**: Nghiêm cấm tự động ghi đè lên các pack có sẵn trong `game_skills/`.

5. **Bộ máy thực thi Game Skill (`ugm.skills.engine`)**:
   - Chỉ cho phép tập lệnh nguyên thủy an toàn: `press_keys`, `click_target`, `locate_and_click`, `click_selector`, `hover`, `wait`, `verify_state`.
   - Ngăn chặn hoàn toàn việc thực thi mã Python hoặc lệnh shell tùy ý.
   - Kiểm tra điều kiện tiên quyết (Preconditions), giới hạn thời gian (Timeouts), xác minh điều kiện sau (Post-verification) và chốt dừng khẩn cấp (Emergency Stop).

6. **Bộ chỉ mục tài nguyên game cục bộ (`ugm.assets.indexer`)**:
   - Đọc các tệp giao diện Clausewitz (`interface/*.gui`, `interface/*.gfx`, `localisation/*.yml`) của HOI4 để trích xuất sprite, tên cửa sổ và nhãn văn bản.
   - Chỉ đọc (Read-only); không can thiệp bộ nhớ tiến trình, không chỉnh sửa file game.

---

## 🛠️ Danh Sách 21 Công Cụ MCP Hoàn Chỉnh

### Công cụ Tri giác, Thao tác & An toàn (V1 Core)
1. `get_game_window`: Gắn kết và lấy thông tin cửa sổ game (tọa độ client, DPI, trạng thái focus).
2. `capture_screen`: Chụp khung hình game theo vùng client chuẩn hóa.
3. `read_text`: Nhận diện chữ nhanh trên vùng ROI bằng RapidOCR.
4. `locate_ui_element`: Định vị phần tử giao diện qua icon, văn bản hoặc selector.
5. `locate_icon`: Khớp template OpenCV đa tỷ lệ kết hợp NMS lọc trùng.
6. `hover_target`: Di chuột đến vị trí và đọc nội dung tooltip.
7. `click_target`: Click chuột theo selector động hoặc tọa độ chuẩn hóa kèm xác minh.
8. `press_keys`: Gửi phím bấm DirectInput (ví dụ phím Space để pause).
9. `verify_state`: So sánh sai phân ảnh và xác minh văn bản dự kiến sau thao tác.
10. `emergency_stop`: Chốt dừng khẩn cấp tức thì, khóa toàn bộ thao tác bàn phím/chuột.
11. `save_training_sample`: Lưu mẫu dữ liệu huấn luyện cục bộ (mặc định tắt).

### Công cụ Bộ nhớ, Dẫn đường & Tự học (V1.5 Mới)
12. `list_game_packs`: Liệt kê các bộ kỹ năng game đã cài đặt và các game trong bộ nhớ.
13. `get_ui_state`: Nhận diện trạng thái màn hình hiện tại kèm độ tin cậy và bằng chứng.
14. `memory_search`: Tìm kiếm tri thức trong bộ nhớ SQLite (trạng thái, chuyển tiếp, kỹ năng, lịch sử).
15. `get_navigation_plan`: Lập kế hoạch lộ trình di chuyển menu bằng Dijkstra.
16. `navigate_to`: Tự động di chuyển qua các menu an toàn từng bước kèm xác minh.
17. `execute_skill`: Thực thi kỹ năng game đã đăng ký với kiểm định điều kiện đầu vào/ra.
18. `learn_skill`: Lưu chuỗi thao tác mới khám phá thành kỹ năng ứng viên.
19. `list_learned_skills`: Xem danh sách kỹ năng đã học và lịch sử xác minh.
20. `get_learning_status`: Thống kê tổng thể về bộ nhớ, số trạng thái và tỷ lệ thành công.
21. `delete_learned_memory`: Xóa an toàn tri thức theo phạm vi hoặc xóa một kỹ năng cụ thể.
22. `index_game_assets`: Trích xuất tài nguyên UI/sprite từ thư mục cài đặt game vào bộ nhớ đệm cục bộ.

---

## 🚀 Hướng Dẫn Cài Đặt & Sử Dụng

### 1. Cài Đặt Môi Trường (Python 3.12)
```powershell
cd universal-game-mcp
uv venv --python 3.12 .venv
.\.venv\Scripts\activate
pip install -e .
```

### 2. Chạy Kiểm Thử Tự Động (99 Bài Test Offline)
```powershell
pytest -v
```
Toàn bộ 99 bài test chạy hoàn toàn độc lập, sử dụng ảnh fixture có sẵn và cơ sở dữ liệu SQLite trong bộ nhớ tạm (`tmp_path`).

### 3. Chạy Đo Đạc Hiệu Năng
```powershell
python scripts/benchmark_cpu_ram.py
```

### 4. Khởi Chạy Máy Chủ MCP
```powershell
# Chế độ thông thường
python -m ugm.server

# Chế độ ép buộc Dry-Run toàn máy chủ (không thể bị override)
python -m ugm.server --dry-run
```

Cấu hình cho Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "universal-game-mcp": {
      "command": "C:\\Users\\<USER>\\...\\universal-game-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "ugm.server"]
    }
  }
}
```

---

## 🛡️ Chính Sách An Toàn & Chống Gian Lận (Anti-Cheat)

- **Chỉ phục vụ game chơi đơn / offline**: Tuyệt đối không can thiệp vào game online có bảo vệ chống gian lận.
- **Chặn tiến trình Anti-Cheat**: Tự động phát hiện các tiến trình EAC, BattlEye, Vanguard, Ricochet và kích hoạt chốt dừng khẩn cấp ngay tức thì.
- **Không thực thi mã nguồn tùy ý**: Bộ máy kỹ năng chỉ thực thi các thao tác giao diện nguyên thủy đã được duyệt trước.
