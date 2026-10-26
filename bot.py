import asyncio
import logging
import re
import socket
from datetime import datetime
from pathlib import Path

# Ưu tiên IPv4 kèm bộ nhớ đệm DNS để chống đứt kết nối khi mạng chập chờn
_orig_getaddrinfo = socket.getaddrinfo
_DNS_CACHE = {}


def _ipv4_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    cache_key = (host, port, type, proto)
    try:
        res = _orig_getaddrinfo(host, port, family, type, proto, flags)
        ipv4 = [r for r in res if r[0] == socket.AF_INET]
        result = ipv4 if ipv4 else res
        if result:
            _DNS_CACHE[cache_key] = result
        return result
    except socket.gaierror:
        if cache_key in _DNS_CACHE:
            return _DNS_CACHE[cache_key]
        raise


socket.getaddrinfo = _ipv4_first_getaddrinfo

from google import genai
from google.genai import types
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import FALLBACK_CHAIN, GEMINI_API_KEY, GEMINI_MODEL, TELEGRAM_BOT_TOKEN, TEMP_DIR
from prompts import HANDWRITING_OCR_PROMPT, KON_SYSTEM_PROMPT, SPRECHEN_AUDIO_PROMPT
from services.docx_service import markdown_to_docx, read_docx_text
from services.memory_service import (
    append_chat_turn,
    load_student_profile,
    load_student_state,
    save_student_profile,
    save_student_state,
    set_student_level,
)
from services.ocr_service import enhance_handwriting_image, run_local_ocr_hint
from services.mtproto_service import download_media_mtproto, prepare_audio_for_gemini
from services.auth_service import (
    ADMIN_USER_ID,
    authorize_user,
    get_passcode,
    is_admin,
    is_user_authorized,
    list_authorized_users,
    revoke_user,
    set_passcode,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("KonGermanBot")

client = genai.Client(api_key=GEMINI_API_KEY)

# Bộ đệm gom nhiều ảnh gửi cùng lúc (Album / Media Group) để chấm gộp thành 1 bài duy nhất
MEDIA_GROUP_BUFFER = {}

AUDIO_MIME_MAP = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".mp3": "audio/mp3",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}


_MODEL_COOLDOWN_UNTIL = {}


async def generate_with_fallback(contents, config=None):
    """
    Gọi Gemini API với cơ chế Định tuyến Model Thông minh (Adaptive Model Routing):
    - Nếu một model gặp lỗi quá tải tạm thời (503 UNAVAILABLE / 429), tạm đưa model đó vào cooldown 10 phút
      để các yêu cầu tiếp theo gọi thẳng vào model đang khỏe mà không mất thời gian chờ retry.
    """
    import time
    now = time.monotonic()
    healthy_models = [m for m in FALLBACK_CHAIN if _MODEL_COOLDOWN_UNTIL.get(m, 0) <= now]
    cooldown_models = [m for m in FALLBACK_CHAIN if _MODEL_COOLDOWN_UNTIL.get(m, 0) > now]
    ordered_chain = healthy_models + cooldown_models

    last_error = None
    for model_name in ordered_chain:
        for attempt in range(2):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                if response and response.text:
                    _MODEL_COOLDOWN_UNTIL.pop(model_name, None)
                    if model_name != FALLBACK_CHAIN[0]:
                        logger.info(f"Đã dùng model dự phòng thành công: {model_name}")
                    return response
            except Exception as e:
                last_error = e
                err_str = str(e)
                logger.warning(f"Model {model_name} (lần {attempt+1}) gặp lỗi: {err_str[:160]}")
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str or "overloaded" in err_str.lower():
                    if attempt == 1:
                        # Đưa model đang quá tải vào cooldown 10 phút (600s)
                        _MODEL_COOLDOWN_UNTIL[model_name] = time.monotonic() + 600
                        logger.info(f"Tạm thời chuyển hướng khỏi {model_name} trong 10 phút do quá tải 503.")
                    await asyncio.sleep(0.6)
                    continue
                break
    raise last_error


def wants_docx_export(user_text: str) -> bool:
    if not user_text:
        return False
    lowered = user_text.lower()
    keywords = [
        "docx",
        "file word",
        "xuất word",
        "xuat word",
        "ra word",
        "gửi file",
        "gui file",
        "đóng gói",
        "dong goi",
    ]
    return any(k in lowered for k in keywords)


def is_grading_response(text: str) -> bool:
    lowered = text.lower()
    return (
        "===phan_cach_docx===" in lowered
        or "aufgabenerfüllung" in lowered
        or "aufgabenerfullung" in lowered
        or "aussprache & intonation" in lowered
        or ("bang diem" in lowered and ("/40" in lowered or "/20" in lowered or "/100" in lowered))
        or ("bảng điểm" in lowered and ("/40" in lowered or "/20" in lowered or "/100" in lowered))
    )


def split_chat_and_docx_parts(raw_text: str) -> tuple[str, str]:
    """
    Tách kết quả chấm bài Kiểu 3:
    - Phần 1 (gửi lên khung chat Telegram): Làm sạch dấu *, (), -, # ở phần nhận xét để giống 100% người thật viết.
    - Phần 2 (xuất ra file Word .docx): Giữ nguyên cấu trúc bảng điểm và sửa chi tiết từng câu.
    """
    if "===PHAN_CACH_DOCX===" in raw_text:
        parts = raw_text.split("===PHAN_CACH_DOCX===", 1)
        chat_part = parts[0].strip()
        docx_part = parts[1].strip() or chat_part
    else:
        chat_part = raw_text.strip()
        docx_part = raw_text.strip()

    # Làm sạch các ký tự máy móc (*, #, (), gạch đầu dòng) ở phần gửi lên khung chat
    chat_part = chat_part.replace("*", "").replace("#", "")
    chat_part = chat_part.replace("(", "").replace(")", "")
    cleaned_lines = []
    for line in chat_part.splitlines():
        s = re.sub(r"^\s*[-•–—]+\s*", "", line).strip()
        s = s.replace(" - ", ", ").replace(" – ", ", ")
        cleaned_lines.append(s)
    chat_part = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    return chat_part, docx_part


async def send_long_message(update: Update, text: str):
    """Chia nhỏ tin nhắn nếu vượt quá giới hạn 4096 ký tự của Telegram."""
    max_len = 4000
    if len(text) <= max_len:
        await update.message.reply_text(text)
        return

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        await update.message.reply_text(chunk)


async def export_and_send_docx(update: Update, markdown_text: str, filename_prefix: str = "Cham_Bai_Tieng_Duc_Kon"):
    user_id = update.effective_user.id
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = TEMP_DIR / f"{filename_prefix}_{user_id}_{timestamp}.docx"

    try:
        await update.message.chat.send_action(action=ChatAction.UPLOAD_DOCUMENT)
        await asyncio.to_thread(markdown_to_docx, markdown_text, out_file)
        with open(out_file, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{filename_prefix}.docx",
                caption="📄 Em đã đóng gói kết quả chấm bài chi tiết kèm Meme/GIF vào file Word (.docx) ở đây ạ 🤓!",
            )
    except Exception as e:
        logger.error(f"Lỗi xuất DOCX: {e}")
        await update.message.reply_text(f"⚠️ Có lỗi nhỏ khi đóng gói file Word: {e}")
    finally:
        try:
            if out_file.exists():
                out_file.unlink()
        except Exception:
            pass


async def auto_update_student_memory(user_id: int, user_name: str, grading_text: str):
    """Đã tắt tự động gọi AI cập nhật hồ sơ ngầm sau mỗi bài chấm để tiết kiệm ~1.000 token/bài và tránh lẫn hồ sơ giữa nhiều học viên."""
    return


def build_system_instruction(user_id: int, user_name: str, extra_text: str = "", mode: str = "general") -> str:
    state = load_student_state(user_id)
    target_level = state.get("target_level", "B1")

    # Phát hiện nếu người dùng yêu cầu trình độ cụ thể ngay trong tin nhắn/caption (VD: "trình độ CEFR B2")
    level_match = re.search(r"\b(A1|A2|B1|B2|C1|C2)\b", extra_text.upper())
    if level_match:
        target_level = level_match.group(1)

    if mode == "speaking":
        return (
            "Bạn là giáo viên chấm thi Nói tiếng Đức (Sprechen) chuẩn CEFR/Goethe giàu kinh nghiệm. "
            f"Trình độ áp dụng cho bài nói này: CEFR {target_level}. "
            "Hãy nhận xét sát thực tế bài nói của học viên bằng tiếng Việt tự nhiên như người thật viết."
        )

    return (
        f"{KON_SYSTEM_PROMPT}\n\n"
        f"## THÔNG TIN BÀI CHẤM HIỆN TẠI\n"
        f"- Trình độ mục tiêu cần áp dụng: **Goethe / CEFR {target_level}**"
    )


# ==================== ACCESS CONTROL & ADMIN COMMANDS ====================

async def check_access_or_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Kiểm tra quyền sử dụng bot:
    - Admin (6989418861) và những người đã nhập đúng mật khẩu mở khóa -> cho phép dùng ngay.
    - Người lạ -> yêu cầu nhập mật khẩu mở khóa 1 lần duy nhất.
    """
    user = update.effective_user
    if not user:
        return False

    if is_user_authorized(user.id):
        return True

    msg_text = (update.message.text or "").strip() if update.message else ""
    current_pass = get_passcode()

    if msg_text and msg_text.lower() == current_pass.lower():
        authorize_user(user.id, user.full_name or user.first_name or "Người dùng", user.username or "")
        await update.message.reply_text(
            f"✅ **Mở khóa thành công!** Chào mừng **{user.first_name or 'bạn'}** sử dụng **Kon German Bot** 🤓.\n\n"
            "Bây giờ bạn có thể gửi trực tiếp:\n"
            "• 🎙️ Video / Ghi âm bài nói tiếng Đức (Sprechen)\n"
            "• 📸 Ảnh chụp bài viết tay (Schreiben B1 Teil 1, 2, 3)\n"
            "• 📄 File Word (.docx) hoặc câu hỏi tiếng Đức bất kỳ!"
        )
        try:
            uname = f" (@{user.username})" if user.username else ""
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=(
                    f"🔔 **Thông báo Admin:** Người dùng **{user.full_name}**{uname} "
                    f"(ID: `{user.id}`) vừa nhập đúng mật khẩu và được cấp quyền dùng Bot!\n"
                    f"👉 Nếu muốn thu hồi quyền bất kỳ lúc nào, gõ: `/revoke {user.id}`"
                ),
            )
        except Exception:
            pass
        return False

    await update.message.reply_text(
        "🔒 **Kon German Bot — Chế độ Nội bộ!**\n\n"
        "Bạn chưa được mở khóa quyền sử dụng bot này. "
        "Vui lòng nhắn **Mật khẩu mở khóa** do Admin cấp vào đây (chỉ cần nhập 1 lần duy nhất) nhé 🤓!"
    )
    return False


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Lệnh này chỉ dành riêng cho Admin của Bot!")
        return
    users = list_authorized_users()
    cur_pass = get_passcode()
    lines = [
        f"🔐 **QUẢN LÝ NGƯỜI DÙNG BOT (Tổng: {len(users)} người)**",
        f"🔑 Mật khẩu mở khóa hiện tại: `{cur_pass}`",
        "*(Đổi mật khẩu bằng lệnh: `/setpass <mật_khẩu_mới>`)*\n",
    ]
    for uid, info in users.items():
        role_badge = "👑 Admin" if info.get("role") == "admin" else "👤 Thành viên"
        uname = f" (@{info['username']})" if info.get("username") else ""
        lines.append(f"• {role_badge}: **{info.get('name')}**{uname} — ID: `{uid}` (Từ: {info.get('joined_at', '')})")
    lines.append("\n💡 *Để xóa quyền 1 người, gõ:* `/revoke <ID>`")
    await update.message.reply_text("\n".join(lines))


async def setpass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Lệnh này chỉ dành riêng cho Admin của Bot!")
        return
    if not context.args:
        await update.message.reply_text(
            f"🔑 Mật khẩu mở khóa hiện tại là: `{get_passcode()}`\n"
            "Để đổi mật khẩu mới, hãy gõ: `/setpass <mật_khẩu_mới>` (VD: `/setpass dmf2026`)"
        )
        return
    new_pass = context.args[0].strip()
    set_passcode(new_pass)
    await update.message.reply_text(f"✅ Đã đổi mật khẩu mở khóa Bot thành: `{new_pass}`")


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Lệnh này chỉ dành riêng cho Admin của Bot!")
        return
    if not context.args:
        await update.message.reply_text("⚠️ Vui lòng nhập ID người dùng cần thu hồi quyền. VD: `/revoke 123456789` (Xem ID bằng lệnh `/users`).")
        return
    target_id = context.args[0].strip()
    if not target_id.isdigit():
        await update.message.reply_text("⚠️ ID người dùng phải là dãy số!")
        return
    ok = revoke_user(int(target_id))
    if ok:
        await update.message.reply_text(f"✅ Đã thu hồi quyền sử dụng Bot của ID `{target_id}`!")
    else:
        await update.message.reply_text(f"⚠️ Không tìm thấy ID `{target_id}` trong danh sách hoặc không thể xóa Admin.")


# ==================== TELEGRAM COMMAND HANDLERS ====================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access_or_unlock(update, context):
        return
    user = update.effective_user
    name = user.first_name or "Ông chủ"
    load_student_profile(user.id, name=name)
    msg = (
        f"Chào **{name}**! Em là **Kon** 🤓 — Trợ lý & Gia sư tiếng Đức hơi bựa nhưng cực kỳ có tâm đây ạ!\n\n"
        "🛠 **Bộ kỹ năng em đã sẵn sàng phục vụ:**\n"
        "1️⃣ **Chấm bài viết tiếng Đức chuẩn Goethe (A1–C2, mặc định B1)**: Chấm gắt thang 40 điểm, sửa từng lỗi ngữ pháp, vị trí động từ, Kasus.\n"
        "2️⃣ **Chấm bài chữ viết tay qua ảnh chụp (1 ảnh hoặc Album nhiều trang)**: Chụp ảnh vở viết tay gửi vào đây, em đọc chữ viết tay và chấm từ A-Z.\n"
        "3️⃣ **🎙️ Chấm thi Nói (Sprechen) & Sửa phát âm qua Voice / Audio**: Bấm giữ nút Micro ghi âm giọng đọc tiếng Đức (hoặc gửi file `.m4a`, `.mp3`, `.ogg`), em sẽ bóc băng, soi từng lỗi phát âm (Umlaut, âm đuôi, trọng âm), chấm điểm Sprechen và gửi kèm bài nói mẫu + file Word!\n"
        "4️⃣ **Đọc & Xuất file Word (.docx)**: Gửi file `.docx` để em chấm, hoặc gõ `/docx` để nhận file `.docx` có chèn sẵn Meme/GIF siêu nét.\n"
        "5️⃣ **Lưu hồ sơ tiến độ học viên**: Tự động nhớ các lỗi hay sai và trình độ của riêng bạn.\n\n"
        "📌 **Các lệnh nhanh:**\n"
        "• `/profile` — Xem hồ sơ học tập & các lỗi hay mắc phải\n"
        "• `/setlevel B1` — Đổi mục tiêu trình độ (A1, A2, B1, B2, C1, C2)\n"
        "• `/docx` — Xuất bài chấm gần nhất ra file Word (.docx)\n"
        "• `/clear` — Xóa bộ nhớ đệm hội thoại\n"
        "• `/help` — Xem hướng dẫn chi tiết"
    )
    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 **HƯỚNG DẪN SỬ DỤNG KON GERMAN BOT 🤓**\n\n"
        "1. **Hỏi ngữ pháp / từ vựng / luyện giao tiếp**:\n"
        "   Nhắn trực tiếp bất kỳ câu hỏi tiếng Đức nào (VD: *\"Giải thích cách dùng weil và obwohl ở B1\"*).\n\n"
        "2. **Chấm bài viết tiếng Đức (Schreiben)**:\n"
        "   Dán đoạn văn tiếng Đức vào chat, hoặc nhắn kèm *\"chấm bài này xuất file word\"*.\n\n"
        "3. **Chấm bài chụp ảnh viết tay (1 trang hoặc nhiều trang)**:\n"
        "   Chụp ảnh trang vở viết tay gửi trực tiếp vào chat. Nếu bài dài nhiều trang, cứ chọn gửi nhiều ảnh cùng lúc — em sẽ tự gộp tất cả các trang lại để chấm thành 1 bài hoàn chỉnh và gửi kèm file `.docx`.\n\n"
        "4. **🎙️ Chấm thi Nói & Sửa phát âm (Sprechen / Aussprache)**:\n"
        "   Bấm nút Micro trên Telegram đọc 1 câu, 1 đoạn văn hoặc bài thuyết trình tiếng Đức (hoặc gửi file âm thanh `.m4a`, `.mp3`, `.wav`). Em sẽ nghe trực tiếp phát âm của bạn, chỉ rõ từ nào phát âm sai, cách đặt môi/lưỡi, chấm điểm thang 40 và xuất file `.docx`.\n\n"
        "5. **Chấm file Word (.docx)**:\n"
        "   Gửi file `.docx` bài làm của bạn vào chat, em sẽ đọc nội dung và trả kết quả chấm chi tiết.\n\n"
        "6. **Quản lý hồ sơ cá nhân**:\n"
        "   • `/profile` — Xem hồ sơ tiến độ học viên\n"
        "   • `/setlevel <A1..C2>` — Đặt trình độ mục tiêu (VD: `/setlevel B2`)\n"
        "   • `/docx` — Tạo file Word từ câu trả lời/bài chấm gần nhất\n"
        "   • `/clear` — Làm mới phiên trò chuyện"
    )
    await update.message.reply_text(msg)


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    profile = load_student_profile(user.id, name=user.first_name or "Học viên")
    await send_long_message(update, f"📋 **HỒ SƠ HỌC TẬP CỦA BẠN:**\n\n{profile}")


async def setlevel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    valid_levels = {"A1", "A2", "B1", "B2", "C1", "C2"}
    if not context.args:
        state = load_student_state(user.id)
        cur = state.get("target_level", "B1")
        await update.message.reply_text(
            f"🎯 Trình độ mục tiêu hiện tại đang là: **{cur}**.\n"
            "Để đổi trình độ, hãy gõ: `/setlevel B1` (Hỗ trợ: A1, A2, B1, B2, C1, C2)."
        )
        return

    lvl = context.args[0].upper().strip()
    if lvl not in valid_levels:
        await update.message.reply_text("⚠️ Trình độ không hợp lệ! Vui lòng chọn một trong các mức: A1, A2, B1, B2, C1, C2.")
        return

    set_student_level(user.id, lvl, name=user.first_name or "Học viên")
    await update.message.reply_text(f"✅ Đã cập nhật mục tiêu giảng dạy & chấm bài sang trình độ **Goethe {lvl}** 🤓!")


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = load_student_state(user.id)
    state["history"] = []
    save_student_state(user.id, state)
    await update.message.reply_text("🧹 Đã dọn sạch bộ nhớ đệm cuộc trò chuyện! (Hồ sơ học tập dài hạn của bạn vẫn được giữ nguyên 🤓).")


async def docx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = load_student_state(user.id)
    md_text = state.get("last_grading_markdown") or state.get("last_response_markdown")
    if not md_text:
        await update.message.reply_text("⚠️ Chưa có bài chấm hoặc bài giảng nào gần đây để xuất file Word. Hãy gửi bài viết, ảnh chụp hoặc ghi âm trước nhé!")
        return

    await export_and_send_docx(update, md_text)


# ==================== MESSAGE HANDLERS ====================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access_or_unlock(update, context):
        return
    user = update.effective_user
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    await update.message.chat.send_action(action=ChatAction.TYPING)
    user_name = user.first_name or "Ông chủ"
    state = load_student_state(user.id)
    sys_instruction = build_system_instruction(user.id, user_name, extra_text=user_text)

    contents = []
    for turn in state.get("history", [])[-12:]:
        contents.append(
            types.Content(
                role=turn["role"],
                parts=[types.Part.from_text(text=turn["text"])],
            )
        )
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

    try:
        response = await generate_with_fallback(
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                temperature=0.4,
            ),
        )
        reply_text = (response.text or "Ủa tự nhiên não em lag ngang, thử hỏi lại giúp em nhé 🤓!").strip()
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        await update.message.reply_text(f"⚠️ Lỗi kết nối AI: {e}")
        return

    grading_detected = is_grading_response(reply_text)
    if grading_detected:
        chat_part, docx_part = split_chat_and_docx_parts(reply_text)
    else:
        chat_part, docx_part = reply_text, reply_text

    append_chat_turn(user.id, user_text, docx_part, is_grading=grading_detected)

    export_requested = wants_docx_export(user_text)
    if export_requested:
        await export_and_send_docx(update, docx_part)
    else:
        await send_long_message(update, chat_part)
        if grading_detected:
            await export_and_send_docx(update, docx_part)


async def process_speaking_audio(update: Update, audio_path: Path, mime_type: str = "audio/ogg", caption: str = ""):
    user = update.effective_user
    user_name = user.first_name or "Ông chủ"
    await update.message.chat.send_action(action=ChatAction.TYPING)
    status_msg = await update.message.reply_text(
        "🎧 Đang tách âm thanh & nghe kỹ từng âm tiết tiếng Đức để chấm phát âm / Sprechen, đợi em vài giây nhé 🤓..."
    )

    prepared_path = audio_path
    try:
        prepared_path, prepared_mime = await prepare_audio_for_gemini(audio_path, mime_type)
        audio_bytes = prepared_path.read_bytes()
        prompt_parts = [SPRECHEN_AUDIO_PROMPT]
        if caption:
            prompt_parts.append(f"\nYêu cầu/Ghi chú cụ thể của người dùng: {caption}")

        sys_instruction = build_system_instruction(user.id, user_name, extra_text=caption, mode="speaking")

        response = await generate_with_fallback(
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type=prepared_mime),
                "\n".join(prompt_parts),
            ],
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                temperature=0.3,
            ),
        )
        reply_text = (response.text or "Em chưa nghe rõ đoạn ghi âm, thử thu âm lại gần micro hơn giúp em nhé!").strip()
        # Làm sạch triệt để các ký tự *, (), -, # để giống 100% người thật gõ
        reply_text = reply_text.replace("*", "").replace("#", "")
        reply_text = reply_text.replace("(", "").replace(")", "")
        cleaned_lines = []
        for line in reply_text.splitlines():
            s = re.sub(r"^\s*[-•–—]+\s*", "", line).strip()
            s = s.replace(" - ", ", ").replace(" – ", ", ")
            if s:
                cleaned_lines.append(s)
        reply_text = "\n".join(cleaned_lines)

        try:
            await status_msg.delete()
        except Exception:
            pass

        append_chat_turn(user.id, f"[Ghi âm giọng nói / Sprechen] {caption}", reply_text, is_grading=True)
        await send_long_message(update, reply_text)

        if wants_docx_export(caption):
            await export_and_send_docx(update, reply_text, filename_prefix="Cham_Bai_Noi_Sprechen_Kon")
    except Exception as e:
        logger.error(f"Lỗi xử lý ghi âm: {e}")
        await update.message.reply_text(f"⚠️ Lỗi khi nghe và chấm file ghi âm: {e}")
    finally:
        for p in {audio_path, prepared_path}:
            try:
                if p and p.exists():
                    p.unlink()
            except Exception:
                pass


async def handle_voice_or_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access_or_unlock(update, context):
        return
    caption = (update.message.caption or "").strip()
    voice = update.message.voice
    audio = update.message.audio
    video_note = update.message.video_note
    video = update.message.video

    media_obj = voice or audio or video_note or video
    if not media_obj:
        return

    file_size = getattr(media_obj, "file_size", 0) or 0
    size_mb = file_size / (1024 * 1024) if file_size else 0

    if voice:
        ext = ".ogg"
        mime_type = voice.mime_type or "audio/ogg"
    elif audio:
        ext = Path(audio.file_name or "audio.mp3").suffix.lower() or ".mp3"
        mime_type = audio.mime_type or AUDIO_MIME_MAP.get(ext, "audio/mp3")
    elif video_note:
        ext = ".mp4"
        mime_type = "video/mp4"
    elif video:
        ext = Path(video.file_name or "video.mp4").suffix.lower() or ".mp4"
        mime_type = video.mime_type or AUDIO_MIME_MAP.get(ext, "video/mp4")
    else:
        return

    unique_id = getattr(media_obj, "file_unique_id", str(update.message.message_id))
    temp_audio = TEMP_DIR / f"audio_{update.effective_user.id}_{unique_id}{ext}"

    try:
        tg_file = await media_obj.get_file()
        await tg_file.download_to_drive(custom_path=str(temp_audio))
    except Exception as e:
        err_str = str(e)
        if "File is too big" in err_str or size_mb > 19.5:
            logger.info(f"File {size_mb:.1f} MB vượt ngưỡng 20MB HTTP API, chuyển sang tải qua MTProto...")
            mtproto_msg = await update.message.reply_text(
                f"📥 Video khá nặng (**{size_mb:.1f} MB** > 20 MB), em đang bật luồng tải tốc độ cao MTProto để lấy video và tách âm thanh, đợi em khoảng 15–30 giây nhé 🤓..."
            )
            try:
                temp_audio = await download_media_mtproto(
                    update.effective_chat.id,
                    update.message.message_id,
                    temp_audio,
                    status_updater=mtproto_msg.edit_text,
                )
            except Exception as mt_err:
                logger.error(f"Lỗi tải MTProto: {mt_err}")
                await update.message.reply_text(f"⚠️ Lỗi khi tải video dung lượng lớn: {mt_err}")
                return
            finally:
                try:
                    await mtproto_msg.delete()
                except Exception:
                    pass
        else:
            logger.error(f"Lỗi tải file voice/video ({size_mb:.1f} MB): {e}")
            await update.message.reply_text(f"⚠️ Lỗi khi tải file ghi âm/video từ Telegram: {e}")
            return

    await process_speaking_audio(update, temp_audio, mime_type=mime_type, caption=caption)


async def process_handwriting_images(update: Update, image_paths: list[Path], caption: str = ""):
    user = update.effective_user
    user_name = user.first_name or "Ông chủ"
    await update.message.chat.send_action(action=ChatAction.TYPING)
    page_count = len(image_paths)
    page_label = f"{page_count} trang chữ viết tay" if page_count > 1 else "chữ viết tay"
    status_msg = await update.message.reply_text(
        f"🔍 Đang bật Kính lúp kép soi {page_label} (lọc chữ gạch xóa & chấm chuẩn Schreiben B1), đợi em vài giây nhé 🤓..."
    )

    enhanced_paths = []
    try:
        contents_parts = []
        ocr_hints = []

        for idx, img_path in enumerate(image_paths, start=1):
            # 1. Đưa ảnh gốc vào
            img_bytes = img_path.read_bytes()
            mime_type = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            contents_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))

            # 2. Tạo bản ảnh Kính lúp kép (khử bóng râm, làm nét chữ xấu & nét gạch xóa)
            enhanced_path = await asyncio.to_thread(enhance_handwriting_image, img_path)
            if enhanced_path and enhanced_path.exists():
                enhanced_paths.append(enhanced_path)
                contents_parts.append(
                    types.Part.from_bytes(data=enhanced_path.read_bytes(), mime_type="image/jpeg")
                )

            # 3. Gợi ý phụ trợ từ Tesseract local (nếu có)
            hint = await asyncio.to_thread(run_local_ocr_hint, img_path)
            if hint:
                ocr_hints.append(f"--- Trang {idx} ---\n{hint}")

        prompt_parts = [HANDWRITING_OCR_PROMPT]
        if page_count > 1:
            prompt_parts.append(
                f"\nLưu ý: Học viên gửi {page_count} trang ảnh liên tiếp của cùng 1 bài viết. Hãy ghép nội dung các trang theo đúng thứ tự thành 1 bài hoàn chỉnh để chép lại và chấm điểm."
            )
        if caption:
            prompt_parts.append(f"\nYêu cầu/Ghi chú cụ thể của giáo viên/học viên: {caption} (Hãy chấm đúng theo Teil/trình độ yêu cầu này!).")
        if ocr_hints:
            joined_hints = "\n".join(ocr_hints)
            prompt_parts.append(
                f"\n[Gợi ý văn bản thô từ OCR local (chỉ tham khảo phụ, BẮT BUỘC ưu tiên nhìn trực tiếp chữ viết tay trong ảnh và lọc bỏ hoàn toàn các chữ đã bị học viên gạch xóa)]:\n{joined_hints}"
            )

        contents_parts.append("\n".join(prompt_parts))
        sys_instruction = build_system_instruction(user.id, user_name, extra_text=caption)

        response = await generate_with_fallback(
            contents=contents_parts,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                temperature=0.25,
            ),
        )
        reply_text = (response.text or "Không đọc được chữ trong ảnh, chụp rõ nét hơn giúp em nhé 🤓!").strip()

        try:
            await status_msg.delete()
        except Exception:
            pass

        chat_part, docx_part = split_chat_and_docx_parts(reply_text)
        append_chat_turn(user.id, f"[Ảnh bài tập viết tay ({page_count} trang)] {caption}", docx_part, is_grading=True)
        await send_long_message(update, chat_part)
        await export_and_send_docx(update, docx_part, filename_prefix="Cham_Bai_Viet_Tay_Kon")
    except Exception as e:
        logger.error(f"Lỗi xử lý ảnh viết tay: {e}")
        await update.message.reply_text(f"⚠️ Lỗi khi nhận diện và chấm ảnh bài tập: {e}")
    finally:
        for p in list(image_paths) + enhanced_paths:
            try:
                if p and p.exists():
                    p.unlink()
            except Exception:
                pass


async def _flush_media_group(media_group_id: str):
    await asyncio.sleep(1.8)
    group_data = MEDIA_GROUP_BUFFER.pop(media_group_id, None)
    if not group_data:
        return
    await process_handwriting_images(
        group_data["update"],
        group_data["paths"],
        caption=group_data["caption"],
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access_or_unlock(update, context):
        return
    photo = update.message.photo[-1]
    caption = (update.message.caption or "").strip()
    media_group_id = update.message.media_group_id
    file = await photo.get_file()
    temp_img = TEMP_DIR / f"photo_{update.effective_user.id}_{photo.file_unique_id}.jpg"
    await file.download_to_drive(custom_path=str(temp_img))

    if media_group_id:
        if media_group_id not in MEDIA_GROUP_BUFFER:
            MEDIA_GROUP_BUFFER[media_group_id] = {
                "update": update,
                "paths": [temp_img],
                "caption": caption,
            }
            asyncio.create_task(_flush_media_group(media_group_id))
        else:
            MEDIA_GROUP_BUFFER[media_group_id]["paths"].append(temp_img)
            if caption and not MEDIA_GROUP_BUFFER[media_group_id]["caption"]:
                MEDIA_GROUP_BUFFER[media_group_id]["caption"] = caption
    else:
        await process_handwriting_images(update, [temp_img], caption=caption)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access_or_unlock(update, context):
        return
    doc = update.message.document
    if not doc:
        return

    file_name = doc.file_name or "document"
    suffix = Path(file_name).suffix.lower()
    caption = (update.message.caption or "").strip()
    temp_path = TEMP_DIR / f"doc_{update.effective_user.id}_{doc.file_unique_id}{suffix}"

    try:
        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=str(temp_path))
        except Exception as dl_err:
            if "File is too big" in str(dl_err):
                size_mb = (doc.file_size or 0) / (1024 * 1024)
                mt_msg = await update.message.reply_text(
                    f"📥 File `{file_name}` khá nặng (**{size_mb:.1f} MB** > 20 MB), em đang tải qua luồng MTProto tốc độ cao, đợi em 15–30 giây nhé 🤓..."
                )
                try:
                    temp_path = await download_media_mtproto(
                        update.effective_chat.id,
                        update.message.message_id,
                        temp_path,
                        status_updater=mt_msg.edit_text,
                    )
                finally:
                    try:
                        await mt_msg.delete()
                    except Exception:
                        pass
            else:
                raise dl_err

        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            await process_handwriting_images(update, [temp_path], caption=caption)
            return

        if suffix in AUDIO_MIME_MAP:
            await process_speaking_audio(update, temp_path, mime_type=AUDIO_MIME_MAP[suffix], caption=caption)
            return

        if suffix == ".docx":
            await update.message.chat.send_action(action=ChatAction.TYPING)
            extracted_text = await asyncio.to_thread(read_docx_text, temp_path)
            if not extracted_text:
                await update.message.reply_text("⚠️ File Word (.docx) này trống hoặc không có văn bản đọc được ạ!")
                return

            user = update.effective_user
            user_name = user.first_name or "Ông chủ"
            sys_instruction = build_system_instruction(user.id, user_name, extra_text=caption)
            user_prompt = (
                f"Người dùng vừa gửi file Word `{file_name}`.\n"
                f"Yêu cầu kèm theo: {caption or 'Hãy chấm chi tiết bài tập tiếng Đức này hoặc giải đáp nội dung trong file.'}\n\n"
                f"--- NỘI DUNG FILE DOCX ---\n{extracted_text}"
            )

            response = await generate_with_fallback(
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruction,
                    temperature=0.3,
                ),
            )
            reply_text = (response.text or "Em đã đọc file nhưng chưa xử lý được nội dung.").strip()
            grading_detected = is_grading_response(reply_text)
            if grading_detected:
                chat_part, docx_part = split_chat_and_docx_parts(reply_text)
            else:
                chat_part, docx_part = reply_text, reply_text
            append_chat_turn(user.id, f"[File DOCX: {file_name}] {caption}", docx_part, is_grading=grading_detected)

            await send_long_message(update, chat_part)
            await export_and_send_docx(update, docx_part, filename_prefix=f"Kon_Cham_{Path(file_name).stem}")
            return

        await update.message.reply_text(
            "⚠️ Hiện tại em hỗ trợ trực tiếp: **Ghi âm giọng nói (.ogg, .m4a, .mp3, .wav, .mp4)**, **File Word (.docx)** và **Ảnh chụp bài tập (.jpg, .png)**. "
            "Bạn vui lòng gửi đúng định dạng nhé 🤓!"
        )
    except Exception as e:
        logger.error(f"Lỗi xử lý tài liệu: {e}")
        await update.message.reply_text(f"⚠️ Có lỗi khi đọc file `{file_name}`: {e}")
    finally:
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"} and suffix not in AUDIO_MIME_MAP:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass


def _cleanup_stale_temp_files():
    """Tự động dọn sạch các file tạm bị kẹt trong thư mục data/tmp khi khởi động."""
    try:
        if TEMP_DIR.exists():
            for f in TEMP_DIR.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except Exception:
                        pass
    except Exception:
        pass


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Bắt mọi lỗi phát sinh bất ngờ (rớt mạng tạm thời, timeout) để bot không bao giờ bị văng."""
    logger.warning(f"Đã xử lý ngoại lệ toàn cục: {context.error}")


def _ensure_single_instance():
    """Đảm bảo chỉ có duy nhất 1 tiến trình bot.py chạy tại một thời điểm để tránh xung đột 409 & khóa file."""
    import os
    import subprocess
    my_pid = os.getpid()
    ps_cmd = (
        f"Get-CimInstance Win32_Process | Where-Object {{ $_.Name -match 'python' -and $_.CommandLine -match 'bot\\.py' -and $_.ProcessId -ne {my_pid} }} "
        f"| ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=8)
    except Exception:
        pass


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("LỖI: Chưa cấu hình TELEGRAM_BOT_TOKEN trong file .env!")
        return
    if not GEMINI_API_KEY:
        print("LỖI: Chưa cấu hình GEMINI_API_KEY trong file .env!")
        return

    _ensure_single_instance()
    _cleanup_stale_temp_files()

    print("======================================================")
    print("🤓 KON GERMAN BOT ĐANG KHỞI ĐỘNG...")
    print(f"🤖 Model AI chính: {GEMINI_MODEL} (Adaptive Routing {len(FALLBACK_CHAIN)} models chống lỗi 503)")
    print("⚡ Chế độ: Concurrent Processing (4 luồng song song) + MTProto C-Speed (cryptg)")
    print("📚 Kỹ năng: CEFR Teacher + Multi-page Handwriting OCR + Sprechen Audio Grading + DOCX Export")
    print("======================================================")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(4)
        .connect_timeout(30.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(30.0)
        .build()
    )

    app.add_error_handler(global_error_handler)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("setlevel", setlevel_cmd))
    app.add_handler(CommandHandler("docx", docx_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("setpass", setpass_cmd))
    app.add_handler(CommandHandler("revoke", revoke_cmd))

    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.VIDEO, handle_voice_or_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
