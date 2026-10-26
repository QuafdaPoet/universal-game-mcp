# Hướng dẫn Universal Game MCP (UGM) - Tiếng Việt

Dự án mã nguồn mở **Universal Game MCP (UGM)** xây dựng máy chủ Model Context Protocol (MCP) cho phép trí tuệ nhân tạo (AI/LLM) quan sát, đọc chữ (OCR), định vị icon, điều khiển chuột/bàn phím an toàn và xác minh kết quả thao tác trong các tựa game offline trên Windows 10/11.

---

## 📊 Kết Quả Đo Đạc Hiệu Năng Thực Tế (CPU Ryzen 5 5500U)

Hệ thống được đo đạc thực tế trên vi xử lý **AMD Ryzen 5 5500U** (6 nhân / 12 luồng) và Windows 11 x64, không đưa ra tuyên bố khi chưa đo lường:

| Thành phần | Độ trễ đo được | Tốc độ / Tăng tốc | Ghi chú kỹ thuật |
|---|---|---|---|
| **Chụp màn hình (DMA/Buffer)** | **2.20 ms** | ~450 FPS | BetterCam / DXGI Desktop Duplication API |
| **Khớp Icon ROI (OpenCV)** | **51.27 ms** | **Nhanh hơn 14.5 lần** | Quét đa tỷ lệ (Multi-scale) kết hợp NMS |
| **Khớp Icon toàn màn hình** | **743.10 ms** | Mức cơ sở | Quét toàn bộ khung hình 1080p |
| **OCR theo sự kiện (Crop ROI)** | **705.05 ms** | **Nhanh hơn 3.0 lần** | RapidOCR PP-OCRv4 CPU (không cần GPU) |
| **OCR toàn màn hình 1080p** | **2,115.99 ms** | Nặng, tốn CPU | Đã loại bỏ ở runtime nhờ cơ chế crop ROI |
| **Xác minh thay đổi (Visual Diff)** | **4.58 ms** | Thời gian thực | Tính ma trận sai phân `absdiff` trên numpy |
| **Tiêu thụ RAM thực tế** | **117.65 MB** | **Dưới 120 MB RAM** | Rất nhẹ, không làm giật lag máy chơi game |

---

## 🎮 Cách Kiểm Thử Khi Máy Không Có Cài Game HOI4

Hệ thống được thiết kế theo nguyên tắc: **Core độc lập hoàn toàn với game**, hỗ trợ kiểm thử 100% offline không cần cài đặt game thương mại.

Có 3 cách kiểm thử trực tiếp trên máy:

### Cách 1: Chạy Demo Tự Động Kết Hợp 10 MCP Tools
Chạy kịch bản chạy thử nghiệm tương tác trọn vẹn 10 công cụ MCP trên mô hình chiến thuật giả lập:
```powershell
.\.venv\Scripts\activate
python scripts/demo_offline_sim.py
```
Kịch bản sẽ tự động chạy qua: tìm cửa sổ -> chụp ảnh -> OCR chỉ số vàng/gỗ -> tìm nút bấm -> di chuột đọc tooltip -> click mua nông trại (dry-run) -> gửi phím Space tạm dừng -> kiểm tra thay đổi hình ảnh -> kích hoạt chốt dừng khẩn cấp (Emergency Stop) -> lưu mẫu dataset.

### Cách 2: Mở Giao Diện Giả Lập Trực Quan (Tkinter GUI)
Chạy ứng dụng game chiến thuật mini tích hợp sẵn:
```powershell
python scripts/offline_game_sim.py
```
Cửa sổ đồ họa sẽ hiện lên với thanh tài nguyên (Gold, Wood, Mana), trạng thái (RUNNING / PAUSED), các nút bấm có Tooltip và phím tắt `[Space]`, `[B]`, `[T]`.

### Cách 3: Chạy Toàn Bộ 36 Bài Kiểm Thử Tự Động
```powershell
pytest -v
```
Toàn bộ 36 test kiểm thử `core`, `capture`, `ocr`, `icon`, `input`, `safety`, `verifier`, `dataset`, `hoi4 adapter` và `mcp server` đều sử dụng ảnh fixture có sẵn, chạy độc lập không cần mở bất kỳ cửa sổ nào.

---

## 🛠 Danh Sách 10 MCP Tools Đã Hiện Thực

Mọi tọa độ trả về đều được **chuẩn hóa từ 0.0 đến 1.0** theo vùng hiển thị (Client Area) của cửa sổ game:

1. `get_game_window(window_title, process_name)`: Định vị cửa sổ game, lấy tọa độ viền, vùng client, trạng thái focus/minimized.
2. `capture_screen(roi, save_path)`: Chụp khung hình hiện tại của game (hỗ trợ cắt ROI, mã hóa Base64 và lưu file).
3. `read_text(roi, event_tag)`: Nhận diện chữ theo sự kiện bằng RapidOCR (PP-OCRv4 CPU), cắt ROI cục bộ giúp giảm 70% thời gian xử lý.
4. `locate_icon(icon_name, template_base64, roi, threshold)`: Tìm vị trí icon bằng OpenCV template matching đa tỷ lệ, tự động lọc trùng NMS và cảnh báo khi kết quả mơ hồ (`is_ambiguous`).
5. `hover_target(x, y, duration_ms, read_tooltip)`: Di chuột đến tọa độ chuẩn hóa `(x, y)`, chờ tooltip xuất hiện và dùng OCR đọc nội dung giải thích.
6. `click_target(x, y, button, clicks, dry_run, verify)`: Click chuột an toàn qua DirectInput (scan code). Hỗ trợ chế độ chạy thử (`dry_run=True`) và tự động so sánh ảnh trước/sau để xác minh kết quả.
7. `press_keys(keys, duration_ms, dry_run)`: Gửi tổ hợp phím hoặc phím đơn (ví dụ phím cách `['space']` để dừng/chạy game).
8. `verify_selection(expected_text, roi, pre_capture_id)`: So sánh sai phân hình ảnh giữa 2 thời điểm, xác minh xem giao diện đã chuyển trạng thái hay chữ mong muốn đã xuất hiện chưa.
9. `emergency_stop(reason)`: Khóa an toàn khẩn cấp tức thì. Chặn mọi thao tác click hay phím bấm tiếp theo cho đến khi được reset có chủ ý.
10. `save_training_sample(tag, roi, metadata)`: Thu thập dữ liệu phục vụ huấn luyện (mặc định tắt để bảo vệ quyền riêng tư; dữ liệu lưu tại `data/samples/` và bị loại trừ khỏi Git).

---

## 🛡️ Nguyên Tắc An Toàn & Bảo Mật

- **Chỉ dùng cho game offline / chơi đơn**: Tuyệt đối không can thiệp, không vượt cơ chế anti-cheat. Hệ thống tự động nhận diện danh sách đen các tiến trình anti-cheat (EAC, BattlEye, Vanguard) và từ chối hoạt động ngay lập tức.
- **Giới hạn không gian cửa sổ**: Tọa độ ngoài phạm vi [0.0 - 1.0] hoặc nằm ngoài viền cửa sổ game sẽ bị hệ thống an toàn chặn lại.
- **Chống thao tác mù quáng**: Nếu độ tin cậy nhận diện icon thấp hoặc có nhiều mục tương đồng gây mơ hồ, hệ thống khuyến nghị AI di chuột (`hover_target`) để đọc tooltip xác nhận trước khi quyết định click.
- **Bảo mật dữ liệu**: Dữ liệu chụp ảnh màn hình và thông tin mẫu huấn luyện được lưu riêng trong thư mục cục bộ `data/samples/` và luôn được cấu hình trong `.gitignore` để không bao giờ bị push lên GitHub.

---

## ⚙️ Hướng Dẫn Kết Nối MCP Server

Để kết nối máy chủ UGM với Claude Desktop hoặc các ứng dụng MCP Client khác, thêm cấu hình sau vào file cấu hình MCP (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "universal-game-mcp": {
      "command": "C:\\Users\\<Tên_User>\\Documents\\Projects\\universal-game-mcp\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "ugm.server"
      ]
    }
  }
}
```
Sau đó khởi động lại client, 10 công cụ của UGM sẽ sẵn sàng phục vụ AI điều khiển và quan sát game.
