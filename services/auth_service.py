import json
from datetime import datetime
from config import DATA_DIR

ADMIN_USER_ID = 6989418861
DEFAULT_DAILY_LIMIT = 10
AUTH_FILE = DATA_DIR / "authorized_users.json"


def _load_auth_data() -> dict:
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("daily_limit", DEFAULT_DAILY_LIMIT)
                return data
        except Exception:
            pass
    default_data = {
        "passcode": "kon2026",
        "daily_limit": DEFAULT_DAILY_LIMIT,
        "users": {
            str(ADMIN_USER_ID): {
                "name": "Khoa (Admin)",
                "username": "Admin",
                "role": "admin",
                "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        },
    }
    _save_auth_data(default_data)
    return default_data


def _save_auth_data(data: dict):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_admin(user_id: int) -> bool:
    return int(user_id) == ADMIN_USER_ID


def is_user_authorized(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    data = _load_auth_data()
    return str(user_id) in data.get("users", {})


def get_passcode() -> str:
    data = _load_auth_data()
    return data.get("passcode", "kon2026")


def set_passcode(new_code: str):
    data = _load_auth_data()
    data["passcode"] = new_code.strip()
    _save_auth_data(data)


def get_daily_limit() -> int:
    data = _load_auth_data()
    return int(data.get("daily_limit", DEFAULT_DAILY_LIMIT))


def set_daily_limit(new_limit: int):
    data = _load_auth_data()
    data["daily_limit"] = max(1, int(new_limit))
    _save_auth_data(data)


def authorize_user(user_id: int, name: str, username: str = ""):
    data = _load_auth_data()
    users = data.setdefault("users", {})
    today = datetime.now().strftime("%Y-%m-%d")
    users[str(user_id)] = {
        "name": name or "Người dùng",
        "username": username or "",
        "role": "admin" if is_admin(user_id) else "member",
        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "usage_date": today,
        "usage_count": 0,
    }
    _save_auth_data(data)


def revoke_user(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    data = _load_auth_data()
    users = data.get("users", {})
    if str(user_id) in users:
        del users[str(user_id)]
        _save_auth_data(data)
        return True
    return False


def check_daily_quota(user_id: int) -> tuple[bool, int, int]:
    """
    Kiểm tra hạn mức chấm bài trong ngày của thành viên (không trừ lượt).
    Trả về: (được_phép_chấm, số_bài_đã_dùng_hôm_nay, giới_hạn_ngày)
    - Admin luôn trả về (True, 0, 999999) (Không giới hạn).
    """
    if is_admin(user_id):
        return True, 0, 999999

    data = _load_auth_data()
    limit = int(data.get("daily_limit", DEFAULT_DAILY_LIMIT))
    users = data.setdefault("users", {})
    u_info = users.get(str(user_id))
    if not u_info:
        return False, 0, limit

    today = datetime.now().strftime("%Y-%m-%d")
    if u_info.get("usage_date") != today:
        u_info["usage_date"] = today
        u_info["usage_count"] = 0
        _save_auth_data(data)

    used = int(u_info.get("usage_count", 0))
    return used < limit, used, limit


def consume_daily_quota(user_id: int) -> tuple[int, int]:
    """
    Cộng 1 lượt sử dụng trong ngày cho tài khoản thành viên sau khi kiểm tra hợp lệ.
    Trả về: (số_bài_đã_dùng_mới, giới_hạn_ngày)
    """
    if is_admin(user_id):
        return 0, 999999

    data = _load_auth_data()
    limit = int(data.get("daily_limit", DEFAULT_DAILY_LIMIT))
    users = data.setdefault("users", {})
    u_info = users.get(str(user_id))
    if not u_info:
        return 0, limit

    today = datetime.now().strftime("%Y-%m-%d")
    if u_info.get("usage_date") != today:
        u_info["usage_date"] = today
        u_info["usage_count"] = 0

    u_info["usage_count"] = int(u_info.get("usage_count", 0)) + 1
    _save_auth_data(data)
    return u_info["usage_count"], limit


def reset_user_quota(user_id: int) -> bool:
    """Admin reset số lượt chấm trong ngày của 1 thành viên về 0."""
    data = _load_auth_data()
    users = data.get("users", {})
    u_info = users.get(str(user_id))
    if not u_info:
        return False
    u_info["usage_date"] = datetime.now().strftime("%Y-%m-%d")
    u_info["usage_count"] = 0
    _save_auth_data(data)
    return True


def list_authorized_users() -> dict:
    data = _load_auth_data()
    today = datetime.now().strftime("%Y-%m-%d")
    users = data.get("users", {})
    changed = False
    for u_info in users.values():
        if u_info.get("role") != "admin" and u_info.get("usage_date") != today:
            u_info["usage_date"] = today
            u_info["usage_count"] = 0
            changed = True
    if changed:
        _save_auth_data(data)
    return users
