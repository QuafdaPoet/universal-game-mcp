# Hướng Dẫn Tích Hợp Hearts of Iron IV (HOI4) - Universal Game MCP V1.5

Tài liệu này cung cấp hướng dẫn đầy đủ về bộ tích hợp prebuilt dành cho **Hearts of Iron IV (HOI4)** trên nền tảng **Universal Game MCP (UGM) V1.5**.

Hệ thống cho phép các mô hình AI (Claude, Gemini, GPT) quan sát, phân tích chiến lược, điều hướng giao diện và thực thi hành động trong HOI4 hoàn toàn tự động, an toàn và ngoại tuyến.

---

## 1. Kiến Trúc Tích Hợp

```
                                +-----------------------------+
                                |      AI Client / Agent      |
                                +--------------+--------------+
                                               |
                                     (JSON-RPC MCP Tools)
                                               v
+-----------------------------------------------------------------------------------------+
|                               Universal Game MCP Server                                 |
|                                                                                         |
|  +--------------------+   +-----------------------+   +------------------------------+  |
|  | HOI4 Knowledge     |   | HOI4 Perception Engine|   | 12-State Navigation Graph    |  |
|  | - Rules & Modes    |   | - Differential Tooltip|   | - main_menu -> start_game    |  |
|  | - Operating Loop   |   | - Paper Mask Popups   |   | - main_map <-> panels        |  |
|  | - Strategy Core    |   | - Resilient Date OCR  |   | - Dijkstra Shortest Path     |  |
|  | - Country Playbooks|   | - Hazard Pause/Speed  |   | - Verified State Transitions |  |
|  +--------------------+   +-----------------------+   +------------------------------+  |
|                                                                                         |
|  +-----------------------------------------------------------------------------------+  |
|  |                   Prebuilt Game Skill Pack (game_skills/hoi4/)                    |  |
|  |   15 Selectors (Visual/OCR) | 25 Verified Skills (Tier A, Tier B, Tier C)          |  |
|  +-----------------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------------+
                                               |
                          (DirectInput / BetterCam / RapidOCR)
                                               v
                                +-----------------------------+
                                |   Hearts of Iron IV (Game)  |
                                +-----------------------------+
```

---

## 2. 12 Trạng Thái Giao Diện (Observable UI States)

Hệ thống nhận diện tự động `UIStateRecognizer` hỗ trợ 12 trạng thái tiêu chuẩn:

| Trạng thái | Tên hiển thị | Cơ chế nhận dạng chính | Phím chuyển đổi |
|---|---|---|---|
| `main_menu` | Main Menu | OCR phát hiện "Single Player", "Multiplayer", "Options", không có topbar | Click "Single Player" |
| `country_selection` | Country Selection | OCR phát hiện "Select Country", "1936", "Gathering Storm" | Click "Start" |
| `main_map` | Main Map | Topbar hiển thị đầy đủ tài nguyên, không có ngăn kéo/popup đè | Trạng thái gốc |
| `politics` | Politics Panel | Ngăn kéo chứa "Laws & Government", "Political Parties", "Ruling Party" | `Shift + Q` / `Escape` |
| `national_focus` | Focus Tree | Khung cây tiêu điểm toàn màn hình chứa "National Focus Tree" | Click slot / `Escape` |
| `production` | Production | Ngăn kéo chứa "Military Factories", "Dockyards", dây chuyền sản xuất | `Y` / `Escape` |
| `construction` | Construction | Ngăn kéo chứa "Civilian Factories", "Infrastructure", hàng đợi xây | `T` / `Escape` |
| `research` | Research | Ngăn kéo chứa các ô nghiên cứu "Research Slot", cây công nghệ | `W` / `Escape` |
| `diplomacy` | Diplomacy | Ngăn kéo chứa "World Tension", danh sách quốc gia, liên minh | `E` / `Escape` |
| `recruitment` | Recruitment | Ngăn kéo chứa "Division Designer", danh sách huấn luyện quân | `U` / `Escape` |
| `selected_army` | Selected Army | Thanh quân đoàn ở đáy màn hình có tướng/sư đoàn được chọn | Click quân đoàn / `Escape` |
| `event_popup` | Event Popup | Phân đoạn mặt nạ giấy (parchment/news) hoặc hộp thoại sự kiện | Click nút / `Enter` |

---

## 3. Danh Mục Kỹ Năng (Game Skill Pack - 25 Skills)

### Nhóm Kỹ Năng Cốt Lõi (Tier A - Essential)
- `pause_unpause`: Tạm dừng hoặc tiếp tục mô phỏng (Phím cách).
- `set_speed`: Thiết lập tốc độ game từ 1 đến 5.
- `decrease_speed` / `increase_speed`: Giảm/Tăng tốc độ game (`num-` / `num+`).
- `close_dialog`: Đóng ngăn kéo, menu hoặc popup đang mở (`Escape`).
- `open_production` / `open_research` / `open_construction` / `open_politics`: Mở các bảng chức năng tương ứng.
- `open_national_focus`: Mở cây tiêu điểm quốc gia 70 ngày.
- `read_topbar`: Đọc tài nguyên, nhân lực, nhà máy, độ ổn định và ngày giờ.
- `read_tooltip`: Rê chuột qua phần tử UI và đọc tooltip giải thích.

### Nhóm Kỹ Năng Kinh Tế & Nghiên Cứu (Tier B - Economy & State)
- `inspect_research_slots`: Quét danh sách các ô nghiên cứu và tiến độ hiện tại.
- `start_research`: Chọn một ô nghiên cứu trống để bắt đầu đề tài mới.
- `inspect_national_focus`: Kiểm tra tiêu điểm quốc gia đang chạy và nhánh kế tiếp.
- `select_national_focus`: Bấm chọn tiêu điểm mới trên cây focus.
- `inspect_production_lines`: Đọc sản lượng súng, pháo, xe tăng, máy bay và hiệu suất dây chuyền.
- `adjust_factory_allocation`: Tăng/giảm số nhà máy quân sự gán cho dây chuyền.
- `inspect_construction_queue`: Quét hàng đợi xây dựng nhà máy dân sự và cơ sở hạ tầng.
- `queue_construction`: Đặt thêm công trình vào hàng đợi xây dựng.

### Nhóm Kỹ Năng Quân Sự (Tier C - Military)
- `list_visible_armies`: Quét danh sách tập đoàn quân và số lượng sư đoàn.
- `select_army`: Bấm chọn thẻ tập đoàn quân trên thanh chỉ huy.
- `assign_commander`: Bấm chọn chân dung tướng lĩnh hoặc thống chế.
- `inspect_army_orders`: Kiểm tra kế hoạch tác chiến (mặt trận, tiến công, phòng ngự, đồn trú).

---

## 4. Kho Tri Thức Chiến Lược (On-Demand Knowledge Base)

Tri thức chiến lược được phân tách hoàn toàn khỏi các lệnh bấm chuột, lưu trữ dạng JSON tại `game_skills/hoi4/knowledge/` và truy xuất qua công cụ `hoi4_get_knowledge(topic, subtopic)`:

1. **`rules_and_modes`**: Định nghĩa luật chơi công bằng (FAIR - cấm space marine, cấm bug lặp đồ, cấm order-66 phản bội) và META.
2. **`operating_loop`**: Chu kỳ vận hành tiêu chuẩn (thời bình kiểm tra hàng tháng, thời chiến kiểm tra hàng ngày; chỉ dừng game khi có quyết định phân nhánh).
3. **`strategy_core`**:
   - *Chu kỳ hoàn vốn công nghiệp:* Nhà máy dân sự cần 2.5 - 3 năm mới hoàn vốn. Đức có thể xây dân sự sớm (1936-1937); Trung Quốc/Ba Lan đối mặt chiến tranh sớm phải dồn nhà máy quân sự ngay.
   - *Thứ tự sửa chữa thiếu hụt:* 1. Dừng tấn công lãng phí; 2. Nhập khẩu tài nguyên thiếu; 3. Dồn nhà máy vào súng/pháo cốt lõi; 4. Đơn giản hóa bảng sư đoàn.
4. **Cẩm Nang Quốc Gia (`country`)**:
   - `germany_fair`: Lộ trình 5 giai đoạn, Anschluss, Danzig, Fall Gelb, Sư tử biển, Barbarossa.
   - `china_fair`: Chiến lược phòng thủ chiều sâu (Sông Vàng, Sông Dương Tử), thống nhất quân phiệt, phòng thủ bờ biển.
   - `poland_meta`: Liên minh quân chủ Baltic, chốt chặn cầu Romania (Romanian bridgehead), bao vây cơ động kỵ binh.

---

## 5. Danh Sách Công Cụ MCP Dành Riêng Cho HOI4

| Tên Công Cụ | Tham Số | Chức Năng |
|---|---|---|
| `hoi4_get_knowledge` | `topic`, `subtopic` | Tải tri thức chiến lược theo chủ đề (`meta`, `rules_and_modes`, `operating_loop`, `strategy_core`, `country`). |
| `hoi4_detect_environment` | `custom_game_path` | Tự động phát hiện thư mục cài đặt Steam, trạng thái tiến trình `hoi4.exe`, và engine OCR sẵn sàng. |
| `hoi4_read_topbar` | `detailed` | OCR tài nguyên topbar; bóc tách ngày giờ, trạng thái pause và tốc độ game khi `detailed=True`. |
| `hoi4_read_tooltip` | `target`, `x`, `y` | Rê chuột và phát hiện hộp tooltip vi phân, đọc nội dung bằng OCR. |
| `hoi4_detect_popups` | *(không có)* | Quét cửa sổ sự kiện hoặc tin tức dạng giấy báo, trả về tọa độ và các nút lựa chọn. |
| `index_game_assets` | `game_path`, `game_id` | Tự động quét file `.gui`, `.gfx`, `.yml` của game vào cache nội bộ (hoàn toàn read-only). |

---

## 6. Lệnh Kiểm Thử Toàn Diện (Pytest)

Tất cả các tính năng đều có bài kiểm thử tự động ngoại tuyến (không cần mở game thật):

```powershell
# Chạy toàn bộ 137 bài kiểm thử của dự án
uv run pytest -v

# Chỉ chạy bài kiểm thử tích hợp HOI4
uv run pytest tests/test_hoi4_prebuilt_integration.py -v

# Chỉ chạy bài kiểm thử chống hồi quy của V1.5
uv run pytest tests/test_v15_audit_regressions.py -v
```
