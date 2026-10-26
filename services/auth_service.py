import json
from datetime import datetime
from config import DATA_DIR

ADMIN_USER_ID = 6989418861
AUTH_FILE = DATA_DIR / "authorized_users.json"


def _load_auth_data() -> dict:
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    default_data = {
        "passcode": "kon2026",
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


def authorize_user(user_id: int, name: str, username: str = ""):
    data = _load_auth_data()
    users = data.setdefault("users", {})
    users[str(user_id)] = {
        "name": name or "Người dùng",
        "username": username or "",
        "role": "admin" if is_admin(user_id) else "member",
        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
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


def list_authorized_users() -> dict:
    data = _load_auth_data()
    return data.get("users", {})
