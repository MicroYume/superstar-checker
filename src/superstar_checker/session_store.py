"""登录会话（cookie）持久化，跨 MCP 进程重启复用。"""

import json
import os
import time
from pathlib import Path

STORE_DIR = Path.home() / ".superstar-checker"
SESSION_FILE = STORE_DIR / "session.json"


def load() -> dict | None:
    """返回 {'cookies': {...}, 'uid': ..., 'saved_at': ...}，不存在或损坏返回 None。"""
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if data.get("cookies"):
            return data
    except (OSError, ValueError):
        pass
    return None


def save(cookies: dict, uid: str) -> float:
    """保存会话，返回 saved_at 时间戳（供进程间会话一致性比对）。"""
    saved_at = time.time()
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"cookies": cookies, "uid": uid, "saved_at": saved_at},
                         ensure_ascii=False, indent=2)
    temp_file = SESSION_FILE.with_name(f"{SESSION_FILE.name}.{os.getpid()}.tmp")
    try:
        with temp_file.open("w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_file, SESSION_FILE)
    finally:
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass
    return saved_at


def clear() -> None:
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass
