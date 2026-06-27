"""
多进程共享速率状态

用于同一账号多进程互相了解对方请求频率，防止同账号同场次短时间
内发送过多 create 请求触发 412。

原理：
  每个进程在 create 前写入自己的状态到共享 JSON 文件。
  读取其他进程的状态决定自己是否跳过本窗口。

文件锁: 使用原子写 (tmp + rename + mkstemp) 避免并发冲突。
"""

import json
import os
import tempfile
import time
from pathlib import Path

from util.Constant import SHARED_STATE_DIR, SHARED_STATE_FILE, SHARED_STATE_MAX_AGE_MS


class ProcessToken:
    """进程标识"""

    def __init__(self, pid: int | None = None):
        self.pid = pid or os.getpid()

    def __str__(self) -> str:
        return f"p{self.pid}"


class BuyerProcessState:
    """一个账号下所有进程的共享状态"""

    def __init__(self, workspace_dir: str | None = None):
        self.workspace_dir = workspace_dir or os.getcwd()
        self.state_dir = os.path.join(self.workspace_dir, SHARED_STATE_DIR)
        Path(self.state_dir).mkdir(parents=True, exist_ok=True)

    def _state_path(self, account_uid: str) -> str:
        return os.path.join(self.state_dir, SHARED_STATE_FILE % account_uid)

    def write_create_attempt(
        self, account_uid: str, pid_mark: str | None = None
    ) -> None:
        """记录一次 create 请求（调用者在发起前调用）"""
        state_path = self._state_path(account_uid)
        pid_mark = pid_mark or str(ProcessToken())
        data = {
            "pids": {pid_mark: {"last_create_ms": int(time.time() * 1000)}},
            "updated_ms": int(time.time() * 1000),
        }
        # 尝试读取已有状态以保留其他进程的记录
        try:
            if os.path.exists(state_path):
                with open(state_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                existing_pids = existing.get("pids", {})
                # 清除过期条目
                now_ms = int(time.time() * 1000)
                existing_pids = {
                    pid: info
                    for pid, info in existing_pids.items()
                    if now_ms - info.get("last_create_ms", 0) < SHARED_STATE_MAX_AGE_MS
                }
                existing_pids[pid_mark] = {"last_create_ms": now_ms}
                data["pids"] = existing_pids
        except (json.JSONDecodeError, OSError):
            pass
        # 原子写
        self._atomic_write(state_path, data)

    def should_skip_window(self, account_uid: str, window_ms: int = 1000) -> bool:
        """检查本窗口内是否有别的进程已经发过 create 请求（排除自己）"""
        state_path = self._state_path(account_uid)
        if not os.path.exists(state_path):
            return False
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        pids = data.get("pids", {})
        own_pid = f"p{os.getpid()}"
        now_ms = int(time.time() * 1000)
        for pid, info in pids.items():
            if pid == own_pid:
                continue
            last_ms = info.get("last_create_ms", 0)
            if now_ms - last_ms < window_ms:
                return True
        return False

    @staticmethod
    def _atomic_write(path: str, data: dict) -> None:
        """先写 tmp 再 rename，避免并发冲突"""
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(path),
            prefix=".tmp_state_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
