"""
=============================================================================
AUTH MODULE — Quản lý tài khoản người dùng
=============================================================================
- Lưu trữ user vào JSON file (data/users.json)
- Mật khẩu băm bằng bcrypt (passlib)
- Xác thực qua JWT token (python-jose)
- Avatar upload + resize
=============================================================================
"""

import os
import json
import uuid
import shutil
import bcrypt
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from jose import JWTError, jwt

# ============================================================================
# CONSTANTS
# ============================================================================
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
AVATARS_DIR = DATA_DIR / "avatars"

# JWT config
SECRET_KEY = os.environ.get("CMT_SECRET_KEY", "comment-generator-secret-key-change-in-production-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


# Password hashing helpers using bcrypt directly
def _hash_password(password: str) -> str:
    """Băm mật khẩu bằng bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    """Xác minh mật khẩu so với hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ============================================================================
# HELPERS
# ============================================================================

def _ensure_dirs():
    """Tạo thư mục data nếu chưa có."""
    DATA_DIR.mkdir(exist_ok=True)
    AVATARS_DIR.mkdir(exist_ok=True)


def _load_users() -> list[dict]:
    """Đọc danh sách user từ file JSON."""
    _ensure_dirs()
    if not USERS_FILE.exists():
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_users(users: list[dict]):
    """Ghi danh sách user ra file JSON."""
    _ensure_dirs()
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _ensure_user_dir(user_id: str):
    """Tạo thư mục riêng cho user."""
    user_dir = DATA_DIR / user_id
    user_dir.mkdir(exist_ok=True)
    return user_dir


# ============================================================================
# USER CRUD
# ============================================================================

def find_user_by_username(username: str) -> Optional[dict]:
    """Tìm user theo username (case-insensitive)."""
    users = _load_users()
    for u in users:
        if u["username"].lower() == username.lower():
            return u
    return None


def find_user_by_email(email: str) -> Optional[dict]:
    """Tìm user theo email (case-insensitive)."""
    users = _load_users()
    for u in users:
        if u.get("email", "").lower() == email.lower():
            return u
    return None


def find_user_by_id(user_id: str) -> Optional[dict]:
    """Tìm user theo ID."""
    users = _load_users()
    for u in users:
        if u["user_id"] == user_id:
            return u
    return None


def register_user(
    username: str,
    email: str,
    password: str,
    display_name: str,
) -> dict:
    """
    Đăng ký user mới.
    Raises ValueError nếu username hoặc email đã tồn tại.
    """
    # Validate
    username = username.strip()
    email = email.strip().lower()
    display_name = display_name.strip()

    if len(username) < 3 or len(username) > 20:
        raise ValueError("Username phải từ 3–20 ký tự.")
    if not username.replace("_", "").isalnum():
        raise ValueError("Username chỉ chấp nhận chữ, số và dấu gạch dưới.")
    if len(display_name) < 2 or len(display_name) > 30:
        raise ValueError("Tên hiển thị phải từ 2–30 ký tự.")
    if "@" not in email or "." not in email:
        raise ValueError("Email không hợp lệ.")
    if len(password) < 6:
        raise ValueError("Mật khẩu phải từ 6 ký tự trở lên.")

    # Kiểm tra trùng
    if find_user_by_username(username):
        raise ValueError("Username đã được sử dụng.")
    if find_user_by_email(email):
        raise ValueError("Email đã được sử dụng.")

    # Tạo user
    user = {
        "user_id": str(uuid.uuid4())[:8],
        "username": username,
        "email": email,
        "display_name": display_name,
        "password_hash": _hash_password(password),
        "avatar_path": None,
        "created_at": datetime.now().isoformat(),
        "last_login": None,
    }

    # Lưu
    users = _load_users()
    users.append(user)
    _save_users(users)

    # Tạo thư mục riêng
    _ensure_user_dir(user["user_id"])

    return {k: v for k, v in user.items() if k != "password_hash"}


def authenticate_user(username_or_email: str, password: str) -> Optional[dict]:
    """
    Xác thực user bằng username/email + password.
    Trả về user dict (không có password_hash) nếu đúng, None nếu sai.
    """
    # Tìm theo username hoặc email
    user = find_user_by_username(username_or_email)
    if not user:
        user = find_user_by_email(username_or_email)
    if not user:
        return None

    # Kiểm tra mật khẩu
    if not _verify_password(password, user["password_hash"]):
        return None

    # Cập nhật last_login
    users = _load_users()
    for u in users:
        if u["user_id"] == user["user_id"]:
            u["last_login"] = datetime.now().isoformat()
            break
    _save_users(users)

    return {k: v for k, v in user.items() if k != "password_hash"}


def update_user_profile(user_id: str, display_name: str = None, email: str = None) -> dict:
    """Cập nhật profile user."""
    users = _load_users()
    user = None
    for u in users:
        if u["user_id"] == user_id:
            user = u
            break

    if not user:
        raise ValueError("User không tồn tại.")

    if display_name is not None:
        display_name = display_name.strip()
        if len(display_name) < 2 or len(display_name) > 30:
            raise ValueError("Tên hiển thị phải từ 2–30 ký tự.")
        user["display_name"] = display_name

    if email is not None:
        email = email.strip().lower()
        if "@" not in email or "." not in email:
            raise ValueError("Email không hợp lệ.")
        # Kiểm tra trùng email
        existing = find_user_by_email(email)
        if existing and existing["user_id"] != user_id:
            raise ValueError("Email đã được sử dụng bởi tài khoản khác.")
        user["email"] = email

    _save_users(users)
    return {k: v for k, v in user.items() if k != "password_hash"}


def change_password(user_id: str, old_password: str, new_password: str) -> bool:
    """Đổi mật khẩu. Trả về True nếu thành công."""
    users = _load_users()
    user = None
    for u in users:
        if u["user_id"] == user_id:
            user = u
            break

    if not user:
        raise ValueError("User không tồn tại.")

    if not _verify_password(old_password, user["password_hash"]):
        raise ValueError("Mật khẩu hiện tại không đúng.")

    if len(new_password) < 6:
        raise ValueError("Mật khẩu mới phải từ 6 ký tự trở lên.")

    user["password_hash"] = _hash_password(new_password)
    _save_users(users)
    return True


def save_avatar(user_id: str, file_bytes: bytes, filename: str) -> str:
    """
    Lưu avatar cho user. Trả về đường dẫn tương đối.
    """
    _ensure_dirs()

    # Xác định extension
    ext = Path(filename).suffix.lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        ext = ".png"

    avatar_filename = f"{user_id}{ext}"
    avatar_path = AVATARS_DIR / avatar_filename

    # Xoá avatar cũ nếu có
    for old_file in AVATARS_DIR.glob(f"{user_id}.*"):
        old_file.unlink(missing_ok=True)

    # Lưu file
    with open(avatar_path, "wb") as f:
        f.write(file_bytes)

    # Cập nhật path trong user data
    relative_path = f"data/avatars/{avatar_filename}"
    users = _load_users()
    for u in users:
        if u["user_id"] == user_id:
            u["avatar_path"] = relative_path
            break
    _save_users(users)

    return relative_path


# ============================================================================
# JWT TOKEN
# ============================================================================

def create_access_token(user_id: str, username: str) -> str:
    """Tạo JWT access token."""
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode = {
        "sub": user_id,
        "username": username,
        "exp": expire,
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """
    Giải mã JWT token.
    Trả về {"user_id": ..., "username": ...} nếu hợp lệ, None nếu không.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        username = payload.get("username")
        if user_id is None:
            return None
        return {"user_id": user_id, "username": username}
    except JWTError:
        return None


# ============================================================================
# USER DATA PATHS
# ============================================================================

def get_user_data_dir(user_id: str) -> Path:
    """Lấy đường dẫn thư mục dữ liệu riêng của user."""
    user_dir = DATA_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_tasks_file(user_id: str) -> Path:
    """Lấy đường dẫn file tasks.json của user."""
    return get_user_data_dir(user_id) / "tasks.json"
