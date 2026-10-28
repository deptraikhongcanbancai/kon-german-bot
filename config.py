import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

# Danh sách các model dự phòng tự động chuyển đổi tức thì nếu model chính bị quá tải (503 / 429 / Timeout)
GEMINI_FALLBACK_MODELS = [
    GEMINI_MODEL,
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
]
# Loại bỏ trùng lặp nhưng giữ nguyên thứ tự ưu tiên
FALLBACK_CHAIN = list(dict.fromkeys(GEMINI_FALLBACK_MODELS))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
KON_EXPORT_GIFS = os.getenv("KON_EXPORT_GIFS", "1").strip() != "0"

DATA_DIR = BASE_DIR / "data"
STUDENTS_DIR = DATA_DIR / "students"
TEMP_DIR = DATA_DIR / "tmp"

for directory in (DATA_DIR, STUDENTS_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)
