import asyncio
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import BASE_DIR, TELEGRAM_BOT_TOKEN

logger = logging.getLogger("KonGermanBot.MTProto")

# Official Telegram Desktop public API ID & Hash for MTProto Bot authentication
TG_API_ID = 2040
TG_API_HASH = "b18441a1ff607e10a989891a5462e627"
STRING_SESSION_FILE = BASE_DIR / "data" / "kon_mtproto_session.txt"

_client_lock = asyncio.Lock()
_persistent_client: Optional[TelegramClient] = None


async def _get_mtproto_client() -> TelegramClient:
    """
    Khởi tạo và giữ kết nối TelegramClient (StringSession trên RAM + tăng tốc C cryptg)
    để tải các video tiếp theo ngay lập tức mà không mất thời gian bắt tay lại.
    """
    global _persistent_client
    async with _client_lock:
        if _persistent_client is not None and _persistent_client.is_connected():
            return _persistent_client

        STRING_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        saved_session_str = ""
        if STRING_SESSION_FILE.exists():
            try:
                saved_session_str = STRING_SESSION_FILE.read_text(encoding="utf-8").strip()
            except Exception:
                saved_session_str = ""

        client = TelegramClient(
            StringSession(saved_session_str),
            TG_API_ID,
            TG_API_HASH,
            request_retries=5,
            connection_retries=5,
        )
        await client.start(bot_token=TELEGRAM_BOT_TOKEN)
        new_session_str = client.session.save()
        if new_session_str and new_session_str != saved_session_str:
            try:
                STRING_SESSION_FILE.write_text(new_session_str, encoding="utf-8")
            except Exception:
                pass
        _persistent_client = client
        return _persistent_client


async def download_media_mtproto(
    chat_id: int,
    message_id: int,
    dest_path: Path,
    status_updater: Optional[Callable[[str], asyncio.Future]] = None,
) -> Path:
    """
    Tải file/video dung lượng lớn (>20MB, lên tới 2GB) trực tiếp qua giao thức MTProto
    kèm báo cáo % tiến độ thời gian thực.
    """
    global _persistent_client
    last_update_time = [0.0]
    start_time = time.monotonic()

    async def _progress_cb(current: int, total: int):
        if not status_updater or not total:
            return
        now = time.monotonic()
        # Cập nhật tin nhắn mỗi 3.5 giây để không bị Telegram giới hạn tốc độ sửa tin nhắn
        if now - last_update_time[0] < 3.5 and current < total:
            return
        last_update_time[0] = now
        pct = int(current * 100 / total)
        filled = max(1, min(10, pct // 10))
        bar = "█" * filled + "░" * (10 - filled)
        cur_mb = current / (1024 * 1024)
        tot_mb = total / (1024 * 1024)
        elapsed = max(0.5, now - start_time)
        speed = cur_mb / elapsed
        text = (
            f"📥 Đang tải video dung lượng lớn qua MTProto:\n"
            f"[{bar}] {pct}% ({cur_mb:.1f} / {tot_mb:.1f} MB — {speed:.1f} MB/s)"
        )
        try:
            await status_updater(text)
        except Exception:
            pass

    for attempt in range(2):
        try:
            client = await _get_mtproto_client()
            msg = await client.get_messages(chat_id, ids=message_id)
            if not msg or not msg.media:
                raise RuntimeError(f"Không tìm thấy media ở message_id={message_id}")
            downloaded = await client.download_media(
                msg,
                file=str(dest_path),
                progress_callback=_progress_cb,
            )
            if not downloaded:
                raise RuntimeError("Không tải được nội dung media qua MTProto.")
            return Path(downloaded)
        except Exception as e:
            logger.warning(f"MTProto tải lần {attempt+1} gặp lỗi: {e}")
            async with _client_lock:
                if _persistent_client:
                    try:
                        await _persistent_client.disconnect()
                    except Exception:
                        pass
                    _persistent_client = None
            if attempt == 1:
                raise


def _extract_audio_sync(input_path: Path) -> tuple[Path, str]:
    """
    Dùng FFmpeg tách âm thanh từ video (.mp4, .mov, .webm) hoặc nén file âm thanh lớn (>10MB)
    thành file MP3 16kHz 64kbps cực nhẹ (~0.5MB/phút) để gửi lên Gemini nhanh và không vượt ngưỡng 20MB.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return input_path, "video/mp4"

    output_mp3 = input_path.with_name(f"{input_path.stem}_extracted.mp3")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(output_mp3),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode == 0 and output_mp3.exists() and output_mp3.stat().st_size > 0:
        return output_mp3, "audio/mp3"
    logger.warning(f"FFmpeg tách audio không thành công: {res.stderr[:200]}")
    return input_path, "video/mp4"


async def prepare_audio_for_gemini(media_path: Path, mime_type: str) -> tuple[Path, str]:
    """
    Nếu là video (.mp4, .mov, .webm) hoặc file nặng >10MB, tự động tách lấy kênh âm thanh MP3 nhẹ.
    """
    suffix = media_path.suffix.lower()
    size_mb = media_path.stat().st_size / (1024 * 1024) if media_path.exists() else 0
    if suffix in {".mp4", ".mov", ".webm", ".mkv"} or mime_type.startswith("video/") or size_mb > 10:
        return await asyncio.to_thread(_extract_audio_sync, media_path)
    return media_path, mime_type
