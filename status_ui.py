#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
biliTickerBuy 主控面板 - 终端类 UI
实时显示配置 / 进程状态 / 共享状态 / 最近活动
每 2 秒刷新，窗口自动置顶
"""

import json
import os
import sys
import time
import subprocess
from pathlib import Path


# ANSI color codes
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"

C_BG_BLUE = "\033[44m"
C_BG_GREEN = "\033[42m"
C_BG_RED = "\033[41m"
C_BG_YELLOW = "\033[43m"
C_BG_GRAY = "\033[100m"

C_FG_GREEN = "\033[92m"
C_FG_YELLOW = "\033[93m"
C_FG_RED = "\033[91m"
C_FG_CYAN = "\033[96m"
C_FG_GRAY = "\033[90m"
C_FG_WHITE = "\033[97m"
C_FG_BLUE = "\033[94m"
C_FG_BLACK = "\033[30m"

# Paths
BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_DIR = BASE / "shared_buyer_state"
LOG_DIR = BASE / "btp_logs"


def enable_ansi():
    """Windows 10+ enable ANSI + UTF-8"""
    if sys.platform == "win32":
        import ctypes as _ct

        _ct.windll.kernel32.SetConsoleMode(_ct.windll.kernel32.GetStdHandle(-11), 7)
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def set_always_on_top():
    """Set terminal window always-on-top (Windows)"""
    try:
        import ctypes as _ct

        _ct.windll.user32.SetWindowPos(
            _ct.windll.user32.GetForegroundWindow(), -1, 0, 0, 0, 0, 0x0001 | 0x0002
        )
    except Exception:
        pass


def console_title(title: str):
    if sys.platform == "win32":
        import ctypes as _ct

        _ct.windll.kernel32.SetConsoleTitleW(title)
    else:
        print(f"\033]0;{title}\007", end="", flush=True)


def read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_process_info() -> dict:
    """Detect running biliTickerBuy processes"""
    result = {"running": False, "pids": []}
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    "IMAGENAME eq biliTickerBuy.exe",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in out.stdout.strip().splitlines():
                parts = line.strip('"').split('","')
                if len(parts) >= 2 and parts[0] == "biliTickerBuy.exe":
                    result["running"] = True
                    result["pids"].append(int(parts[1]))
        else:
            out = subprocess.run(
                ["pgrep", "-x", "biliTickerBuy"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in out.stdout.strip().splitlines():
                if line:
                    result["running"] = True
                    result["pids"].append(int(line))
    except Exception:
        pass
    return result


def read_shared_state() -> dict:
    if not STATE_DIR.is_dir():
        return {}
    files = list(STATE_DIR.glob("*.json"))
    if not files:
        return {}
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return read_json(latest) or {}


def time_ago(ms: int | None) -> str:
    if ms is None:
        return "-"
    age_s = (time.time() * 1000 - ms) / 1000
    if age_s < 0:
        return "0s"
    if age_s < 120:
        return f"{age_s:.0f}s ago"
    if age_s < 7200:
        return f"{age_s / 60:.0f}m ago"
    return f"{age_s / 3600:.1f}h ago"


def fmt_time(ms: int | None) -> str:
    if ms is None:
        return "-"
    ts = time.localtime(ms / 1000)
    return time.strftime("%H:%M:%S", ts)


def colored(text: str, color: str, bold: bool = False) -> str:
    b = C_BOLD if bold else ""
    return f"{b}{color}{text}{C_RESET}"


def label_value(label: str, value: str, vcolor: str = C_FG_WHITE) -> str:
    return f"  {C_DIM}{label}:{C_RESET} {vcolor}{value}{C_RESET}"


def badge(text: str, bg: str, fg: str = C_FG_BLACK) -> str:
    return f"{bg}{fg} {text} {C_RESET}"


def section(title: str, width: int) -> str:
    line = "-" * (width - len(title) - 2)
    return f"\n  {C_BOLD}{C_FG_CYAN}{title}{C_RESET} {C_DIM}{line}{C_RESET}"


# Config display helpers

KEY_LABELS = {
    "createRetryLimit": "Retry limit",
    "createRequestBatchSize": "Batch size",
    "requestInterval": "Req interval(ms)",
    "queueConcurrencyLimit": "Concurrency",
    "proxyMaxConsecutiveFailures": "Max proxy fails",
    "proxyCooldownSeconds": "Proxy cooldown(s)",
    "proxyBackoffMaxSeconds": "Proxy backoff max(s)",
    "rateLimitDelayMs": "Rate limit delay(ms)",
    "clash.instance_count": "Clash instances",
    "clash.auto_switch_main": "Auto-switch main",
    "clash.auto_switch_child": "Auto-switch child",
    "clash.api_url": "Clash API URL",
    "clash.api_secret": "Clash secret",
    "clash.selected_node": "Selected node",
    "serverchanKey": "ServerChan",
    "barkToken": "Bark",
    "logLevel": "Log level",
    "https_proxy": "Proxy pool",
    "notifyProxyExhausted": "Notify exhausted",
}


def render_config(conf: dict) -> list[str]:
    if not conf:
        return ["  (no config)"]
    rows = []
    for db_key in [
        "clash.selected_node",
        "clash.instance_count",
        "createRetryLimit",
        "createRequestBatchSize",
        "requestInterval",
        "proxyMaxConsecutiveFailures",
        "proxyCooldownSeconds",
        "proxyBackoffMaxSeconds",
        "clash.auto_switch_main",
        "clash.auto_switch_child",
        "logLevel",
        "https_proxy",
    ]:
        if db_key in conf:
            val = str(conf[db_key])
            label = KEY_LABELS.get(db_key, db_key)
            if db_key == "https_proxy":
                parts = val.split(",")
                val = f"{len(parts)} proxies"
            rows.append(label_value(label, val))
    return rows


def render_process(proc: dict) -> list[str]:
    if not proc["running"]:
        return [f"  {badge('STOPPED', C_BG_RED, C_FG_WHITE)}"]
    lines = [
        f"  {badge('RUNNING', C_BG_GREEN)}  PID(s): {', '.join(map(str, proc['pids']))}"
    ]
    return lines


def render_shared_state(state: dict) -> list[str]:
    if not state:
        return ["  (no shared state)"]
    lines = []
    pids = state.get("pids", {})
    updated = state.get("updated_ms")
    if updated:
        ts = fmt_time(updated)
        ago = time_ago(updated)
        lines.append(label_value("Last activity", f"{ts} ({ago})"))
    for pid_key, pid_data in pids.items():
        pid = pid_key.lstrip("p")
        last_create = pid_data.get("last_create_ms")
        last_try = pid_data.get("last_try_ms")
        attempts = pid_data.get("attempts")
        extra = pid_data.get("extra", {})
        lines.append(f"  {C_DIM}|-{C_RESET} Worker {C_FG_YELLOW}{pid}{C_RESET}:")
        if last_create:
            lines.append(
                f"  {C_DIM}|   {C_RESET}create: {colored(fmt_time(last_create), C_FG_GREEN)}"
                f" ({colored(time_ago(last_create), C_FG_GRAY)})"
            )
        if last_try:
            lines.append(f"  {C_DIM}|   {C_RESET}try: {fmt_time(last_try)}")
        if attempts is not None:
            lines.append(
                f"  {C_DIM}|   {C_RESET}attempts: {colored(str(attempts), C_FG_YELLOW)}"
            )
        if extra:
            for ek, ev in extra.items():
                lines.append(f"  {C_DIM}|   {C_RESET}{ek}={ev}")
    return lines


def render_logs() -> list[str]:
    if not LOG_DIR.is_dir():
        return ["  (no logs)"]
    files = sorted(LOG_DIR.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return ["  (no logs)"]
    try:
        with open(files[0], "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception:
        return ["  (can't read log)"]

    keywords = {
        "error",
        "warn",
        "fail",
        "429",
        "412",
        "blocked",
        "exception",
        "traceback",
    }
    recent = [
        line.strip()
        for line in all_lines[-60:]
        if any(k in line.lower() for k in keywords)
    ]
    recent = recent[-10:]  # last 10 relevant lines
    if not recent:
        recent = all_lines[-3:]
    return [f"  {C_DIM}{line[:78]}{C_RESET}" for line in recent]


def render(conf: dict, proc: dict, state: dict, log_entries: list[str]) -> str:
    width = 74

    # Header
    hdr_text = "[ biliTickerBuy Monitor ]"
    hdr = f"{C_BOLD}{C_BG_BLUE}{C_FG_WHITE}{'':^{width}}{C_RESET}\n"
    hdr += f"{C_BOLD}{C_BG_BLUE}{C_FG_WHITE}{hdr_text:^{width}}{C_RESET}\n"
    hdr += f"{C_BOLD}{C_BG_BLUE}{C_FG_WHITE}{'':^{width}}{C_RESET}"
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    hdr += f"\n  {C_DIM}{now_str}  |  refresh 2s{C_RESET}"

    lines = [hdr]

    # Summary bar
    alive = proc.get("running", False)
    pcount = len(proc.get("pids", []))
    worker_count = len(state.get("pids", {}))
    parts = []
    if alive:
        parts.append(colored(f"Active ({pcount} proc)", C_FG_GREEN, bold=True))
    else:
        parts.append(colored("Idle", C_FG_RED, bold=True))
    parts.append(f"State workers: {colored(str(worker_count), C_FG_YELLOW)}")
    lines.append(f"\n  {'  |  '.join(parts)}")

    # Config section
    lines.append(section("Config", width))
    lines.extend(render_config(conf))

    # Process section
    lines.append(section("Process", width))
    lines.extend(render_process(proc))

    # Shared state
    lines.append(section("Shared State", width))
    lines.extend(render_shared_state(state))

    # Recent events
    lines.append(section("Recent Events", width))
    if log_entries:
        lines.extend(log_entries)
    else:
        lines.append("  (none)")

    # Footer hint
    lines.append(f"\n  {C_DIM}[Ctrl+C quit] [Window topmost]{C_RESET}")

    return "\n".join(lines)


def main():
    enable_ansi()
    time.sleep(0.3)
    set_always_on_top()
    console_title("biliTickerBuy Monitor")

    try:
        while True:
            # Read config
            conf_raw = read_json(CONFIG_PATH)
            conf = {}
            if conf_raw and "_default" in conf_raw:
                for v in conf_raw["_default"].values():
                    if isinstance(v, dict) and "key" in v:
                        conf[v["key"]] = v["value"]

            proc = get_process_info()
            state = read_shared_state()
            log_entries = render_logs()

            os.system("cls" if sys.platform == "win32" else "clear")
            print(render(conf, proc, state, log_entries), end="", flush=True)

            time.sleep(2)
    except KeyboardInterrupt:
        print()
        print("  Monitor stopped.")
        console_title("")


if __name__ == "__main__":
    main()
