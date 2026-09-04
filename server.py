"""
=============================================================================
FASTAPI SERVER — Comment Generator Web App
=============================================================================
Máy chủ web phục vụ:
- REST API cho quản lý task sinh comment
- WebSocket cho real-time progress
- Auth endpoints (đăng ký / đăng nhập)
- Static files (frontend)

Chạy: python server.py
       → http://localhost:8000
=============================================================================
"""

import os
import json
import uuid
import asyncio
import threading
import csv
import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

import auth
import storage
from generate_comments import CommentGenerator, DEFAULT_CONFIG

# ============================================================================
# APP SETUP
# ============================================================================

app = FastAPI(title="Comment Generator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# IN-MEMORY TASK STORAGE
# ============================================================================

# {task_id: TaskInfo}
tasks: dict[str, dict] = {}
# {task_id: threading.Event} — cancel flags
cancel_flags: dict[str, threading.Event] = {}
# {task_id: list[callback_data]} — progress log
progress_logs: dict[str, list] = {}
# {task_id: set[WebSocket]} — active websocket connections
ws_connections: dict[str, set] = {}
# Lock for thread-safe task operations
task_lock = threading.Lock()
history_lock = threading.Lock()
HISTORY_DIR = storage.DATA_DIR / "history"


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    display_name: str

class LoginRequest(BaseModel):
    username_or_email: str
    password: str

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class CreateTaskRequest(BaseModel):
    topic: str
    num_comments: int = Field(default=200, ge=5, le=1000)
    language: str = "Tiếng Việt"
    api_provider: str = "groq"
    api_model: str = "qwen/qwen3-32b"
    batch_size: int = Field(default=15, ge=5, le=50)
    word_count: int = Field(default=10, ge=3, le=30)
    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


def _topic_group(topic: str) -> str:
    """Tạo khóa nhóm chủ đề ổn định để dùng chung lịch sử."""
    normalized = unicodedata.normalize("NFD", topic.lower())
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    words = set(normalized.split())
    groups = []
    if words & {"gai", "xinh", "dep", "trai", "nhan", "sac"}:
        groups.append("appearance")
    if words & {"tiktok", "video", "clip", "reel", "short"}:
        groups.append("video")
    if words & {"dia", "diem", "canh", "view", "du", "lich", "quan", "cafe", "bien"}:
        groups.append("place")
    if words & {"mon", "an", "am", "thuc", "nha", "hang", "quan"}:
        groups.append("food")
    if not groups:
        groups = ["generic"]
    return "+".join(sorted(set(groups)))


def _load_user_history(user_id: str) -> dict:
    """Đọc lịch sử của user từ SQLite."""
    history = storage.load_document(user_id, "history", None)
    if isinstance(history, dict) and isinstance(history.get("topics"), dict):
        return history

    history = {"user_id": user_id, "topics": {}}
    for old_task in _get_user_tasks(user_id):
        _add_task_to_history(history, old_task)
    _save_user_history(user_id, history)
    return history


def _save_user_history(user_id: str, history: dict):
    storage.save_document(user_id, "history", history)


def _add_task_to_history(history: dict, task: dict):
    """Gộp metadata task và comment vào đúng nhóm chủ đề."""
    group = task.get("topic_group") or _topic_group(task.get("topic", ""))
    bucket = history["topics"].setdefault(group, {"tasks": [], "comments": []})
    task_record = {k: v for k, v in task.items() if k != "comments"}
    existing_task = next((item for item in bucket["tasks"]
                          if item.get("task_id") == task_record.get("task_id")), None)
    if existing_task is None:
        bucket["tasks"].append(task_record)
    else:
        existing_task.update(task_record)
    known_ids = {comment.get("id") for comment in bucket["comments"]}
    for comment in task.get("comments", []):
        if comment.get("id") not in known_ids:
            bucket["comments"].append(comment)
            known_ids.add(comment.get("id"))


def _record_task_history(user_id: str, task: dict):
    with history_lock:
        history = _load_user_history(user_id)
        _add_task_to_history(history, task)
        _save_user_history(user_id, history)


def _get_topic_history(user_id: str, topic: str) -> list[dict]:
    with history_lock:
        history = _load_user_history(user_id)
        group = _topic_group(topic)
        return [comment.copy() for comment in history["topics"].get(group, {}).get("comments", [])]


def task_info_for_history(task_id: str, user_id: str, config: dict,
                          status: str, comments: list[dict], error: str = None) -> dict:
    return {
        "task_id": task_id,
        "user_id": user_id,
        "topic": config["topic"],
        "topic_group": _topic_group(config["topic"]),
        "num_comments": config["num_comments"],
        "language": config["language"],
        "api_provider": config["api_provider"],
        "api_model": config["api_model"],
        "batch_size": config["batch_size"],
        "word_count": config.get("word_count", 10),
        "similarity_threshold": config["similarity_threshold"],
        "status": status,
        "comments": comments,
        "error": error,
        "updated_at": datetime.now().isoformat(),
    }


# ============================================================================
# AUTH DEPENDENCY
# ============================================================================

def get_current_user(authorization: str = Header(None)) -> dict:
    """Dependency: lấy user từ JWT token trong header Authorization."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập.")

    # Hỗ trợ cả "Bearer <token>" và "<token>"
    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]

    user_info = auth.verify_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc đã hết hạn.")
    
    user = auth.find_user_by_id(user_info["user_id"])
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
    user_info["role"] = user.get("role", "user")

    return user_info


def require_admin(user_info: dict = Depends(get_current_user)) -> dict:
    if user_info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền truy cập.")
    return user_info


# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    try:
        user = auth.register_user(
            username=req.username,
            email=req.email,
            password=req.password,
            display_name=req.display_name,
        )
        token = auth.create_access_token(user["user_id"], user["username"])
        return {"user": user, "token": token}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = auth.authenticate_user(req.username_or_email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu.")
    token = auth.create_access_token(user["user_id"], user["username"])
    return {"user": user, "token": token}


@app.get("/api/auth/me")
async def get_me(user_info: dict = Depends(get_current_user)):
    user = auth.find_user_by_id(user_info["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User không tồn tại.")
    return {k: v for k, v in user.items() if k != "password_hash"}


@app.put("/api/auth/me")
async def update_me(req: UpdateProfileRequest, user_info: dict = Depends(get_current_user)):
    try:
        updated = auth.update_user_profile(
            user_id=user_info["user_id"],
            display_name=req.display_name,
            email=req.email,
        )
        return updated
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/auth/me/password")
async def change_pwd(req: ChangePasswordRequest, user_info: dict = Depends(get_current_user)):
    try:
        auth.change_password(user_info["user_id"], req.old_password, req.new_password)
        return {"message": "Đổi mật khẩu thành công."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/me/avatar")
async def upload_avatar(file: UploadFile = File(...), user_info: dict = Depends(get_current_user)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File phải là ảnh (jpg, png, gif, webp).")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:  # 5MB max
        raise HTTPException(status_code=400, detail="Ảnh quá lớn (tối đa 5MB).")

    avatar_path = auth.save_avatar(user_info["user_id"], content, file.filename)
    return {"avatar_path": avatar_path}


# ============================================================================
# ADMIN ENDPOINTS
# ============================================================================

@app.get("/api/admin/summary")
async def admin_summary(_: dict = Depends(require_admin)):
    summary = storage.usage_summary()
    with task_lock:
        running_tasks = [task for task in tasks.values()
                         if task.get("status") in {"pending", "running"}]
        summary["running_tasks"] = len(running_tasks)
        summary["running_comments"] = sum(task.get("current_count", 0) for task in running_tasks)
        summary["generated_count"] += summary["running_comments"]
    return summary


@app.get("/api/admin/users")
async def admin_users(_: dict = Depends(require_admin)):
    users = storage.list_admin_users()
    runtime_by_user = {}
    with task_lock:
        for task in tasks.values():
            if task.get("status") not in {"pending", "running"}:
                continue
            user_runtime = runtime_by_user.setdefault(task["user_id"], {
                "running_tasks": 0,
                "running_comments": 0,
            })
            user_runtime["running_tasks"] += 1
            user_runtime["running_comments"] += task.get("current_count", 0)

    for user in users:
        runtime = runtime_by_user.get(user["user_id"], {})
        user["running_tasks"] = runtime.get("running_tasks", 0)
        user["running_comments"] = runtime.get("running_comments", 0)
        user["generated_count"] = (user.get("generated_count", 0) or 0) + user["running_comments"]
        user["task_count"] = (user.get("task_count", 0) or 0) + user["running_tasks"]
        user["is_generating"] = user["running_tasks"] > 0
    return users


@app.put("/api/admin/users/{user_id}/status")
async def admin_user_status(user_id: str, is_active: bool, _: dict = Depends(require_admin)):
    if not storage.update_user_status(user_id, is_active):
        raise HTTPException(status_code=404, detail="Không thể thay đổi tài khoản này.")
    return {"message": "Đã cập nhật trạng thái tài khoản."}


# ============================================================================
# AVATAR SERVING
# ============================================================================

@app.get("/data/avatars/{filename}")
async def serve_avatar(filename: str):
    filepath = storage.DATA_DIR / "avatars" / filename
    if not filepath.exists():
        raise HTTPException(status_code=404)
    return FileResponse(filepath)


# ============================================================================
# PROVIDER INFO
# ============================================================================

PROVIDER_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    "anthropic": ["claude-sonnet-4-20250514", "claude-3-haiku-20240307", "claude-3-opus-20240229"],
    "groq": ["qwen/qwen3-32b", "openai/gpt-oss-20b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"],
    "ollama": ["llama3.1", "mistral", "gemma2", "phi3", "qwen2"],
}


@app.get("/api/providers")
async def get_providers():
    return PROVIDER_MODELS


# ============================================================================
# TASK MANAGEMENT
# ============================================================================

def _get_user_tasks(user_id: str) -> list[dict]:
    """Lấy danh sách task của user từ SQLite."""
    tasks = storage.load_document(user_id, "tasks", [])
    return tasks if isinstance(tasks, list) else []


def _save_user_tasks(user_id: str, user_tasks: list[dict]):
    """Lưu danh sách task của user vào SQLite."""
    storage.save_document(user_id, "tasks", user_tasks)


def _update_task_in_file(user_id: str, task_id: str, updates: dict):
    """Cập nhật 1 task cụ thể trong file."""
    user_tasks = _get_user_tasks(user_id)
    for t in user_tasks:
        if t["task_id"] == task_id:
            t.update(updates)
            break
    _save_user_tasks(user_id, user_tasks)


def _run_generator_task(task_id: str, user_id: str, config: dict,
                        cancel_flag: threading.Event, existing_comments: list[dict]):
    """Chạy CommentGenerator trong background thread."""
    loop = asyncio.new_event_loop()

    def on_progress(batch_num, total, target, new_comments, log_message):
        """Callback nhận tiến trình từ engine."""
        progress_entry = {
            "batch_num": batch_num,
            "total": total,
            "target": target,
            "new_comments": [c.copy() for c in new_comments],
            "log_message": log_message,
            "timestamp": datetime.now().isoformat(),
        }

        # Lưu vào progress log
        if task_id in progress_logs:
            progress_logs[task_id].append(progress_entry)

        # Cập nhật task state trong memory
        with task_lock:
            if task_id in tasks:
                tasks[task_id]["current_count"] = total
                tasks[task_id]["progress_pct"] = round(total / target * 100, 1) if target > 0 else 0
                tasks[task_id]["comments"].extend(c.copy() for c in new_comments)

        # Broadcast qua WebSocket
        if task_id in ws_connections:
            for ws in list(ws_connections[task_id]):
                try:
                    loop.run_until_complete(ws.send_json(progress_entry))
                except Exception:
                    ws_connections[task_id].discard(ws)

    try:
        # Cập nhật trạng thái
        with task_lock:
            if task_id in tasks:
                tasks[task_id]["status"] = "running"
                tasks[task_id]["started_at"] = datetime.now().isoformat()
        _update_task_in_file(user_id, task_id, {"status": "running", "started_at": datetime.now().isoformat()})

        # Khởi tạo generator với callback
        generator = CommentGenerator(
            config=config,
            on_progress=on_progress,
            cancel_flag=cancel_flag,
            existing_comments=existing_comments,
        )

        # Chạy
        comments = generator.run()

        # Xác định trạng thái cuối
        if cancel_flag.is_set():
            final_status = "cancelled"
        elif len(comments) >= config["num_comments"]:
            final_status = "completed"
        else:
            final_status = "failed"

        final_error = generator.last_error if final_status == "failed" else None

        # Lưu kết quả
        with task_lock:
            if task_id in tasks:
                tasks[task_id]["status"] = final_status
                tasks[task_id]["completed_at"] = datetime.now().isoformat()
                tasks[task_id]["current_count"] = len(comments)
                tasks[task_id]["progress_pct"] = 100 if final_status == "completed" else tasks[task_id].get("progress_pct", 0)
                tasks[task_id]["comments"] = comments
                tasks[task_id]["error"] = final_error

        _update_task_in_file(user_id, task_id, {
            "status": final_status,
            "completed_at": datetime.now().isoformat(),
            "current_count": len(comments),
            "comments": comments,
            "error": final_error,
        })
        storage.record_usage(
            user_id, task_id, config["num_comments"], len(comments), final_status,
            config["api_provider"], config["api_model"], datetime.now().isoformat()
        )
        _record_task_history(user_id, {
            **task_info_for_history(task_id, user_id, config, final_status, comments),
        })

        # Gửi thông báo hoàn thành qua WebSocket
        final_msg = {
            "batch_num": -1,
            "total": len(comments),
            "target": config["num_comments"],
            "new_comments": [],
            "log_message": f"✅ Task {final_status}. Tổng: {len(comments)} comment.",
            "status": final_status,
            "timestamp": datetime.now().isoformat(),
        }
        if task_id in ws_connections:
            for ws in list(ws_connections[task_id]):
                try:
                    loop.run_until_complete(ws.send_json(final_msg))
                except Exception:
                    pass

    except Exception as e:
        error_message = str(e)
        partial_comments = []
        with task_lock:
            if task_id in tasks:
                partial_comments = tasks[task_id].get("comments", [])
        with task_lock:
            if task_id in tasks:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = error_message
                tasks[task_id]["completed_at"] = datetime.now().isoformat()
                tasks[task_id]["comments"] = partial_comments
        _update_task_in_file(user_id, task_id, {
            "status": "failed",
            "error": error_message,
            "completed_at": datetime.now().isoformat(),
            "comments": partial_comments,
        })
        storage.record_usage(
            user_id, task_id, config["num_comments"], len(partial_comments), "failed",
            config["api_provider"], config["api_model"], datetime.now().isoformat()
        )
        _record_task_history(user_id, task_info_for_history(
            task_id, user_id, config, "failed", [], error_message
        ))

        failure_entry = {
            "batch_num": -1,
            "total": 0,
            "target": config["num_comments"],
            "new_comments": [],
            "log_message": f"❌ Task thất bại: {error_message}",
            "status": "failed",
            "error": error_message,
            "timestamp": datetime.now().isoformat(),
        }
        progress_logs.setdefault(task_id, []).append(failure_entry)
        if task_id in ws_connections:
            for ws in list(ws_connections[task_id]):
                try:
                    loop.run_until_complete(ws.send_json(failure_entry))
                except Exception:
                    ws_connections[task_id].discard(ws)

    finally:
        loop.close()
        # Cleanup cancel flag
        cancel_flags.pop(task_id, None)


@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest, user_info: dict = Depends(get_current_user)):
    user_id = user_info["user_id"]

    if req.api_provider == "groq":
        try:
            from generate_comments import GroqClient
            GroqClient(req.api_model, 1, 0).validate()
        except (ImportError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error))

    task_id = str(uuid.uuid4())[:8]

    # Build config
    config = DEFAULT_CONFIG.copy()
    config.update({
        "topic": req.topic,
        "topic_group": _topic_group(req.topic),
        "num_comments": req.num_comments,
        "language": req.language,
        "api_provider": req.api_provider,
        "api_model": req.api_model,
        "batch_size": req.batch_size,
        "word_count": req.word_count,
        "similarity_threshold": req.similarity_threshold,
    })

    task_info = {
        "task_id": task_id,
        "user_id": user_id,
        "topic": req.topic,
        "num_comments": req.num_comments,
        "language": req.language,
        "api_provider": req.api_provider,
        "api_model": req.api_model,
        "batch_size": req.batch_size,
        "word_count": req.word_count,
        "similarity_threshold": req.similarity_threshold,
        "status": "pending",
        "current_count": 0,
        "progress_pct": 0,
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "comments": [],
        "error": None,
    }

    # Lưu vào memory
    with task_lock:
        tasks[task_id] = task_info.copy()

    # Lưu vào file
    user_tasks = _get_user_tasks(user_id)
    user_tasks.append(task_info)
    _save_user_tasks(user_id, user_tasks)
    _record_task_history(user_id, task_info)

    # Tạo cancel flag và progress log
    cancel_flag = threading.Event()
    cancel_flags[task_id] = cancel_flag
    progress_logs[task_id] = []

    # Chạy trong background thread
    thread = threading.Thread(
        target=_run_generator_task,
        args=(task_id, user_id, config, cancel_flag, _get_topic_history(user_id, req.topic)),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@app.get("/api/tasks")
async def list_tasks(user_info: dict = Depends(get_current_user)):
    user_id = user_info["user_id"]

    # Lấy từ file (persistent) và merge trạng thái runtime
    user_tasks = _get_user_tasks(user_id)

    # Update runtime info
    with task_lock:
        for t in user_tasks:
            tid = t["task_id"]
            if tid in tasks:
                t["status"] = tasks[tid]["status"]
                t["current_count"] = tasks[tid]["current_count"]
                t["progress_pct"] = tasks[tid]["progress_pct"]

    # Trả về không kèm comments (nhẹ hơn)
    result = []
    for t in user_tasks:
        task_summary = {k: v for k, v in t.items() if k != "comments"}
        task_summary["comment_count"] = len(t.get("comments", []))
        result.append(task_summary)

    return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, user_info: dict = Depends(get_current_user)):
    user_id = user_info["user_id"]

    # Tìm trong file
    user_tasks = _get_user_tasks(user_id)
    task = None
    for t in user_tasks:
        if t["task_id"] == task_id:
            task = t
            break

    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")

    # Merge runtime state
    with task_lock:
        if task_id in tasks:
            task["status"] = tasks[task_id]["status"]
            task["current_count"] = tasks[task_id]["current_count"]
            task["progress_pct"] = tasks[task_id]["progress_pct"]
            if tasks[task_id].get("comments"):
                task["comments"] = tasks[task_id]["comments"]

    return task


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, user_info: dict = Depends(get_current_user)):
    user_id = user_info["user_id"]

    # Huỷ nếu đang chạy
    if task_id in cancel_flags:
        cancel_flags[task_id].set()

    # Xoá khỏi memory
    with task_lock:
        tasks.pop(task_id, None)
    cancel_flags.pop(task_id, None)
    progress_logs.pop(task_id, None)

    # Xoá khỏi file
    user_tasks = _get_user_tasks(user_id)
    user_tasks = [t for t in user_tasks if t["task_id"] != task_id]
    _save_user_tasks(user_id, user_tasks)

    return {"message": "Đã xoá task."}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, user_info: dict = Depends(get_current_user)):
    if task_id in cancel_flags:
        cancel_flags[task_id].set()
        return {"message": "Đã gửi lệnh huỷ task."}
    else:
        raise HTTPException(status_code=400, detail="Task không đang chạy.")


@app.get("/api/tasks/{task_id}/comments")
async def get_task_comments(
    task_id: str,
    tone: str = None,
    style: str = None,
    search: str = None,
    page: int = 1,
    page_size: int = 50,
    user_info: dict = Depends(get_current_user),
):
    user_id = user_info["user_id"]

    # Lấy task
    user_tasks = _get_user_tasks(user_id)
    task = None
    for t in user_tasks:
        if t["task_id"] == task_id:
            task = t
            break

    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")

    comments = task.get("comments", [])

    # Merge runtime comments nếu có
    with task_lock:
        if task_id in tasks and tasks[task_id].get("comments"):
            comments = tasks[task_id]["comments"]

    # Apply filters
    if tone:
        comments = [c for c in comments if c.get("tone") == tone]
    if style:
        comments = [c for c in comments if c.get("style") == style]
    if search:
        search_lower = search.lower()
        comments = [c for c in comments if search_lower in c.get("content", "").lower()]

    # Pagination
    total = len(comments)
    start = (page - 1) * page_size
    end = start + page_size
    page_comments = comments[start:end]

    return {
        "comments": page_comments,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@app.get("/api/tasks/{task_id}/download")
async def download_task(task_id: str, format: str = "json", authorization: str = Header(None), token: str = Query(None)):
    # Hỗ trợ cả Header lẫn Query param (vì window.open không gửi header được)
    auth_value = authorization
    if not auth_value and token:
        auth_value = f"Bearer {token}"
    if not auth_value:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập.")
    token_str = auth_value.replace("Bearer ", "") if auth_value.startswith("Bearer ") else auth_value
    user_info = auth.verify_token(token_str)
    if not user_info:
        raise HTTPException(status_code=401, detail="Token không hợp lệ.")
    user_id = user_info["user_id"]

    # Lấy task
    user_tasks = _get_user_tasks(user_id)
    task = None
    for t in user_tasks:
        if t["task_id"] == task_id:
            task = t
            break

    if not task:
        raise HTTPException(status_code=404, detail="Task không tồn tại.")

    comments = task.get("comments", [])

    # Merge runtime
    with task_lock:
        if task_id in tasks and tasks[task_id].get("comments"):
            comments = tasks[task_id]["comments"]

    safe_topic = "".join(c if c.isalnum() or c in " _-" else "_" for c in task.get("topic", "comments"))[:30]

    if format == "txt":
        content = "\n".join(c.get("content", "") for c in comments)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_topic}.txt"'}
        )
    elif format == "csv":
        output = io.StringIO()
        fieldnames = ["id", "content", "length_category", "tone", "style", "word_count"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for c in comments:
            writer.writerow({k: c.get(k, "") for k in fieldnames})

        content = output.getvalue()
        # Prepend BOM for Excel compatibility
        content_bytes = b'\xef\xbb\xbf' + content.encode("utf-8")

        return StreamingResponse(
            io.BytesIO(content_bytes),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_topic}.csv"'}
        )
    else:
        content = json.dumps(comments, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_topic}.json"'}
        )


# ============================================================================
# WEBSOCKET — Real-time progress
# ============================================================================

@app.websocket("/ws/tasks/{task_id}")
async def websocket_task_progress(websocket: WebSocket, task_id: str, token: str = None):
    """WebSocket endpoint cho real-time progress tracking."""
    # Xác thực
    if token:
        user_info = auth.verify_token(token)
        if not user_info:
            await websocket.close(code=4001, reason="Token không hợp lệ.")
            return
    else:
        await websocket.close(code=4001, reason="Thiếu token.")
        return

    await websocket.accept()

    # Đăng ký connection
    if task_id not in ws_connections:
        ws_connections[task_id] = set()
    ws_connections[task_id].add(websocket)

    try:
        # Gửi log history đã có
        if task_id in progress_logs:
            for entry in progress_logs[task_id]:
                await websocket.send_json(entry)

        # Giữ kết nối
        while True:
            try:
                # Nhận ping/pong từ client để giữ kết nối
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Gửi ping từ server
                try:
                    await websocket.send_text("ping")
                except Exception:
                    break
            except WebSocketDisconnect:
                break
    finally:
        if task_id in ws_connections:
            ws_connections[task_id].discard(websocket)


# ============================================================================
# STATIC FILES — Frontend
# ============================================================================

# Serve frontend static files
frontend_dir = Path(__file__).parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def root():
    """Redirect to auth or dashboard."""
    return FileResponse(str(frontend_dir / "auth.html"))


@app.get("/dashboard")
async def dashboard():
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/admin")
async def admin_page():
    return FileResponse(str(frontend_dir / "admin.html"))


@app.get("/auth")
async def auth_page():
    return FileResponse(str(frontend_dir / "auth.html"))


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import sys
    # Fix Windows console encoding for emoji/unicode
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # Ensure data dir
    Path("data").mkdir(exist_ok=True)
    (storage.DATA_DIR / "avatars").mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  COMMENT GENERATOR WEB APP")
    print("=" * 60)
    print("  Open browser: http://localhost:8000")
    print("  Frontend: ./frontend/")
    print("  Data: ./data/")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
