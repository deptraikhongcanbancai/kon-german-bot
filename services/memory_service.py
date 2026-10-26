import json
from datetime import datetime
from pathlib import Path
from config import STUDENTS_DIR

DEFAULT_PROFILE_TEMPLATE = """# Hồ Sơ Học Viên Tiếng Đức (German Student Profile)

## Học viên
- Tên: {name}
- Ngôn ngữ giải thích: Tiếng Việt
- Mục tiêu trình độ CEFR: {level} (Goethe-Zertifikat)

## Đánh giá hiện tại
- Trình độ ước lượng: {level}
- Cập nhật lần cuối: {date}

## Điểm mạnh
- Đang theo dõi qua các bài tập

## Lỗi hay mắc phải (Recurring Mistakes)
- Chưa có dữ liệu

## Ngữ pháp & Chủ đề đã học
- Đang cập nhật

## Nhật ký tiến độ (Update Log)
- {date}: Khởi tạo hồ sơ học viên cho {name}.
"""


def get_student_profile_path(user_id: int) -> Path:
    return STUDENTS_DIR / f"{user_id}_profile.md"


def get_student_state_path(user_id: int) -> Path:
    return STUDENTS_DIR / f"{user_id}_state.json"


def load_student_profile(user_id: int, name: str = "Học viên") -> str:
    profile_path = get_student_profile_path(user_id)
    if not profile_path.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        content = DEFAULT_PROFILE_TEMPLATE.format(name=name, level="B1", date=today)
        profile_path.write_text(content, encoding="utf-8")
        return content
    return profile_path.read_text(encoding="utf-8")


def save_student_profile(user_id: int, content: str):
    profile_path = get_student_profile_path(user_id)
    profile_path.write_text(content.strip() + "\n", encoding="utf-8")


def load_student_state(user_id: int) -> dict:
    state_path = get_student_state_path(user_id)
    if not state_path.exists():
        return {
            "target_level": "B1",
            "history": [],
            "last_grading_markdown": "",
        }
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "target_level": "B1",
            "history": [],
            "last_grading_markdown": "",
        }


def save_student_state(user_id: int, state: dict):
    state_path = get_student_state_path(user_id)
    # Giữ tối đa 20 lượt hội thoại gần nhất để tiết kiệm token và giữ ngữ cảnh mượt
    if "history" in state and len(state["history"]) > 20:
        state["history"] = state["history"][-20:]
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_chat_turn(user_id: int, user_msg: str, assistant_msg: str, is_grading: bool = False):
    state = load_student_state(user_id)
    state.setdefault("history", []).append({"role": "user", "text": user_msg[:3000]})
    state["history"].append({"role": "model", "text": assistant_msg[:4000]})
    if is_grading or "# Bang Diem" in assistant_msg or "Aufgabenerfüllung" in assistant_msg:
        state["last_grading_markdown"] = assistant_msg
    else:
        # Lưu câu trả lời gần nhất phòng khi người dùng gõ /docx
        state["last_response_markdown"] = assistant_msg
    save_student_state(user_id, state)


def set_student_level(user_id: int, level: str, name: str = "Học viên") -> str:
    level = level.upper().strip()
    state = load_student_state(user_id)
    state["target_level"] = level
    save_student_state(user_id, state)

    profile = load_student_profile(user_id, name=name)
    today = datetime.now().strftime("%Y-%m-%d")
    profile += f"\n- {today}: Đổi mục tiêu trình độ CEFR sang **{level}**."
    save_student_profile(user_id, profile)
    return level
