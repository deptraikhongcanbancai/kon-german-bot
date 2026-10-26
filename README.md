# 🤓 Kon German Bot — AI Grader cho Tiếng Đức (Goethe B1 Sprechen & Schreiben)

Bot Telegram độc lập hỗ trợ giảng dạy và chấm bài **Tiếng Đức chuẩn CEFR (A1 – C2, trọng tâm Goethe-Zertifikat B1)** chạy trực tiếp bằng Python + **Google Gemini Multimodal AI** + **Telethon MTProto**.

---

## ✨ Tính năng nổi bật

1. **🎙️ Chấm bài Nói (`Sprechen`) & Sửa phát âm qua Voice / Audio / Video (Lên tới 2 GB)**:
   - Nhận trực tiếp ghi âm giọng nói (`.ogg`, `.mp3`, `.m4a`, `.wav`) và video bài nói (`.mp4`, `.mov`, `.webm`).
   - Tích hợp **Telethon MTProto + `cryptg` (C-extension)** tự động vượt giới hạn 20 MB của Telegram Bot API, kèm thanh tiến trình tải `0% -> 100%` theo thời gian thực và trích xuất âm thanh nhẹ bằng `FFmpeg`.
   - Nhận xét 100% giọng văn giáo viên tự nhiên, dễ đọc trên điện thoại và chấm điểm theo thang **40 điểm Goethe B1**.

2. **✍️ Chấm bài Viết tay (`Schreiben B1`) chuyên trị chữ xấu & gạch xóa nhiều**:
   - Xử lý ảnh kép (**Dual-View Image Enhancement** bằng Pillow + ImageMagick + Tesseract OCR `deu+eng`): tự động khử bóng tối giấy vở, tăng tương phản nét bút và lọc bỏ toàn bộ các từ/câu bị gạch bỏ.
   - Chấm chuẩn cấu trúc **Goethe-Zertifikat B1 Schreiben**:
     - **Teil 1:** 40 điểm
     - **Teil 2:** 40 điểm
     - **Teil 3:** 20 điểm
     - **Tổng điểm:** 100 điểm
   - Tách riêng phần **nhận xét tổng quan sạch sẽ trên khung chat Telegram** và **bản phân tích lỗi từng câu + bài viết mẫu nâng cấp trong file Word (`.docx`)**.

3. **📄 Đọc & Xuất báo cáo Word (`.docx`) tự động**:
   - Đọc trực tiếp bài tập trong file `.docx` học viên gửi lên.
   - Tự động xuất phiếu chấm bài `.docx` trình bày đẹp mắt bằng font `Times New Roman`.

4. **🔐 Khóa bảo mật nội bộ (Passcode Protection & Admin Control)**:
   - Chặn người lạ dùng chùa tốn token bằng mật khẩu mở khóa 1 lần.
   - Hỗ trợ các lệnh quản trị viên ngay trên Telegram: `/users`, `/setpass <mật_khẩu_mới>`, `/revoke <user_id>`.

5. **⚡ Adaptive Multi-Model Routing & Chạy đa luồng**:
   - Xử lý song song nhiều học viên cùng lúc (`concurrent_updates`).
   - Tự động chuyển đổi tức thì giữa chuỗi model Gemini (`gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-2.5-flash`...) khi máy chủ Google quá tải (`503` / `429`).

---

## 🚀 Cài đặt & Chạy Bot

### 1. Cài đặt thư viện
```bash
pip install -r requirements.txt
```

### 2. Cấu hình biến môi trường
Sao chép file `.env.example` thành `.env` và điền thông tin của bạn:
```bash
cp .env.example .env
```
Trong file `.env`:
- `GEMINI_API_KEY`: API Key của Google Gemini.
- `TELEGRAM_BOT_TOKEN`: Token lấy từ `@BotFather`.

### 3. Khởi động Bot
Nhấp đúp vào file `start_bot.bat` (tự động khởi động lại nếu mất mạng) hoặc chạy lệnh:
```bash
python -u bot.py
```
