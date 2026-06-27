#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
biliTickerBuy 内存清理器 - Tkinter GUI 版
========================================
轻量界面，为抢票保内存而生。
保护: biliTickerBuy(主/子进程) + BHYG + WebView2 + 系统进程
杀: Edge/QQ/微信/网易云等高内存非关键进程
"""

import os
import sys
import json
import time
import ctypes
import threading
import queue
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext
except ImportError:
    tk = None

# ── 路径 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "btb_logs")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "memory_cleaner.json")

# ── 默认配置 ──
DEFAULT_CONFIG = {
    "threshold_mb": 100,
    "interval_sec": 60,
    "protected_names": [
        "bilitickerbuy",
        "main-bhyg-windows",
        "msedgewebview2",
        "system", "idle", "smss", "csrss", "wininit",
        "services", "lsass", "winlogon", "svchost", "spoolsv",
        "conhost", "taskhostw", "sihost", "runtimebroker",
        "dwm", "fontdrvhost", "securityhealthservice",
        "securityhealthsystray", "startmenuexperiencehost",
        "searchapp", "searchindexer", "ctfmon", "explorer",
    ],
    "target_names": [
        "msedge",
        "qq",
        "weixin",
        "wechatappex",
        "cloudmusic",
        "telegram",
        "windowsterminal",
        "textinputhost",
        "wetype_server",
        "spotify",
        "discord",
        "slack",
    ],
    "log_keep_days": 7,
}

# ── 样式 ──
COLOR_BG = "#f0f4f8"
COLOR_CARD = "#ffffff"
COLOR_PRIMARY = "#2196F3"
COLOR_SUCCESS = "#4CAF50"
COLOR_DANGER = "#f44336"
COLOR_WARN = "#FF9800"
COLOR_TEXT = "#333333"
COLOR_MUTED = "#888888"
COLOR_BORDER = "#e0e0e0"


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except:
            pass
    return cfg


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False


def elevate_self():
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            f'"{__file__}"', None, 1
        )
        return True
    except:
        return False


def get_memory_summary():
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / 1024**3, 2),
        "used_gb": round(mem.used / 1024**3, 2),
        "avail_gb": round(mem.available / 1024**3, 2),
        "percent": mem.percent,
    }


def get_protected_pids(cfg=None):
    """
    获取受保护进程 PID 集合。
    三层防护:
      1) 进程名子串匹配 (protected_names)
      2) exe 路径匹配 (运行在 SCRIPT_DIR 下)
      3) 命令行关键字匹配 (bilitickerbuy / bhyg)
    返回 (pid_set, details_list)。
    """
    if cfg is None:
        cfg = load_config()
    prot_names = [n.lower() for n in cfg.get("protected_names", [])]
    bili_dir = SCRIPT_DIR.lower()

    protected_pids = set()
    details = []

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            pid = proc.info["pid"]
            name = (proc.info["name"] or "").lower()
            exe_path = (proc.info["exe"] or "").lower()
            cmdline = " ".join(proc.info["cmdline"] or []).lower()

            # 1) 进程名子串匹配
            if any(np in name for np in prot_names):
                protected_pids.add(pid)
                details.append({"pid": pid, "name": name, "reason": "name_match"})
                continue

            # 2) exe 路径匹配: 运行在 biliTickerBuy 目录下
            if bili_dir in exe_path:
                protected_pids.add(pid)
                details.append({"pid": pid, "name": name, "reason": "exe_path"})
                continue

            # 3) 命令行含 bilitickerbuy / bhyg 关键字
            if "bilitickerbuy" in cmdline or "bhyg" in cmdline:
                protected_pids.add(pid)
                details.append({"pid": pid, "name": name, "reason": "cmdline"})
                continue

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return protected_pids, details


# ══════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════

class MemCleanerApp:
    """Tkinter 主窗口"""

    def __init__(self):
        self.cfg = load_config()
        self.monitor_running = False
        self.monitor_paused = False
        self.log_queue = queue.Queue()

        self._build_ui()
        self._start_refresh()

    # ── UI 构建 ──

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("biliTickerBuy 内存清理器")
        self.root.geometry("620x520")
        self.root.minsize(520, 400)
        self.root.configure(bg=COLOR_BG)
        self.root.resizable(True, True)

        # 协议退出
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.close_flag = False

        # ├─ 标题栏 ─
        header = tk.Frame(self.root, bg=COLOR_PRIMARY, height=42)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="biliTickerBuy 内存清理器",
                 bg=COLOR_PRIMARY, fg="white",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=14, pady=8)

        # ├─ 主内容 (scrollable) ─
        canvas = tk.Canvas(self.root, bg=COLOR_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        self.scroll_frame = tk.Frame(canvas, bg=COLOR_BG)
        self.scroll_frame.bind("<Configure>",
                               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw", tags="inner")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮绑定
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self._canvas = canvas

        sf = self.scroll_frame

        # ├─ 状态卡片 ─
        self.card_status = self._make_card(sf)
        self.lbl_mem = tk.Label(self.card_status["body"],
                                text="??", font=("Consolas", 22, "bold"),
                                fg=COLOR_PRIMARY, bg=COLOR_CARD, anchor="w")
        self.lbl_mem.pack(fill="x", padx=12, pady=(8, 0))
        self.lbl_detail = tk.Label(self.card_status["body"],
                                   text="??", fg=COLOR_MUTED,
                                   bg=COLOR_CARD, anchor="w")
        self.lbl_detail.pack(fill="x", padx=12, pady=(0, 4))

        # 内存条
        self.mem_bar_frame = tk.Frame(self.card_status["body"], bg=COLOR_CARD, height=18)
        self.mem_bar_frame.pack(fill="x", padx=12, pady=(0, 8))
        self.mem_bar_frame.pack_propagate(False)
        self.mem_bar_bg = tk.Canvas(self.mem_bar_frame, bg="#e0e0e0",
                                    highlightthickness=0, height=18)
        self.mem_bar_bg.pack(fill="both", expand=True)
        self.mem_bar_fill = self.mem_bar_bg.create_rectangle(0, 0, 0, 18,
                                                             fill=COLOR_PRIMARY, width=0)

        # ├─ 受保护进程卡片 ─
        self.card_prot = self._make_card(sf)
        self.lbl_prot_title = tk.Label(self.card_prot["body"],
                                       text="受保护 (biliTickerBuy / BHYG / WebView2 / 系统)",
                                       font=("Microsoft YaHei UI", 9),
                                       fg=COLOR_MUTED, bg=COLOR_CARD, anchor="w")
        self.lbl_prot_title.pack(fill="x", padx=12, pady=(4, 0))
        self.lbl_prot_list = tk.Label(self.card_prot["body"], text="加载中...",
                                      font=("Consolas", 9), justify="left",
                                      fg=COLOR_TEXT, bg=COLOR_CARD, anchor="w")
        self.lbl_prot_list.pack(fill="x", padx=12, pady=(0, 6))

        # ├─ 可杀进程卡片 ─
        self.card_kill = self._make_card(sf)
        self.lbl_kill_title = tk.Label(self.card_kill["body"],
                                       text="可清理的高内存进程 (>100MB)",
                                       font=("Microsoft YaHei UI", 9),
                                       fg=COLOR_MUTED, bg=COLOR_CARD, anchor="w")
        self.lbl_kill_title.pack(fill="x", padx=12, pady=(4, 0))
        self.lbl_kill_list = tk.Label(self.card_kill["body"], text="加载中...",
                                      font=("Consolas", 9), justify="left",
                                      fg=COLOR_DANGER, bg=COLOR_CARD, anchor="w")
        self.lbl_kill_list.pack(fill="x", padx=12, pady=(0, 6))

        # ├─ 操作按钮栏 ─
        btn_frame = tk.Frame(sf, bg=COLOR_BG)
        btn_frame.pack(fill="x", padx=12, pady=(8, 0))

        self.btn_clean = self._make_btn(btn_frame, "一键清理", COLOR_DANGER,
                                        self._on_clean, width=10)
        self.btn_clean.pack(side="left", padx=(0, 6))

        self.btn_monitor = self._make_btn(btn_frame, "持续监控", COLOR_PRIMARY,
                                          self._on_toggle_monitor, width=10)
        self.btn_monitor.pack(side="left", padx=6)

        self.btn_elevate = self._make_btn(btn_frame, "提权运行", COLOR_WARN,
                                          self._on_elevate, width=10)
        if not is_admin():
            self.btn_elevate.pack(side="left", padx=6)
        else:
            self.btn_elevate.pack_forget()

        self.btn_refresh_now = self._make_btn(btn_frame, "刷新", COLOR_MUTED,
                                              self._refresh_now, width=6)
        self.btn_refresh_now.pack(side="right", padx=(6, 0))

        # 间隔设置
        interval_frame = tk.Frame(btn_frame, bg=COLOR_BG)
        interval_frame.pack(side="right")
        tk.Label(interval_frame, text="间隔:", bg=COLOR_BG,
                 fg=COLOR_MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.interval_var = tk.StringVar(value=str(self.cfg["interval_sec"]))
        self.interval_spin = tk.Spinbox(interval_frame, from_=10, to=600,
                                        increment=10, width=5,
                                        textvariable=self.interval_var,
                                        font=("Consolas", 9),
                                        state="readonly")
        self.interval_spin.pack(side="left")
        tk.Label(interval_frame, text="秒", bg=COLOR_BG,
                 fg=COLOR_MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")

        # ├─ 日志区 ─
        log_frame = self._make_card(sf)
        tk.Label(log_frame["header"], text="运行日志",
                 font=("Microsoft YaHei UI", 9), fg=COLOR_MUTED).pack(side="left", padx=12, pady=4)
        self.log_text = scrolledtext.ScrolledText(
            log_frame["body"], height=8, font=("Consolas", 9),
            bg="#1e1e1e", fg="#cccccc", insertbackground="#cccccc",
            borderwidth=0, highlightthickness=0,
            state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 底部状态栏
        self.status_bar = tk.Label(self.root, text="就绪",
                                   bg="#e0e0e0", fg=COLOR_MUTED,
                                   font=("Microsoft YaHei UI", 8),
                                   anchor="w", padx=8)
        self.status_bar.pack(fill="x", side="bottom")

    # ── 工具组件 ──

    def _make_card(self, parent):
        frame = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                         highlightthickness=1, bd=0)
        frame.pack(fill="x", padx=12, pady=(6, 0))
        header = tk.Frame(frame, bg=COLOR_CARD)
        header.pack(fill="x")
        return {"frame": frame, "header": header, "body": frame}

    def _make_btn(self, parent, text, color, cmd, width=8):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg="white", activebackground=color,
                         activeforeground="white", bd=0, padx=8, pady=4,
                         font=("Microsoft YaHei UI", 9, "bold"),
                         width=width, cursor="hand2")

    # ── 日志 ──

    def log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"info": "#cccccc", "warn": "#FF9800", "ok": "#4CAF50", "err": "#f44336"}
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] ", "ts")
        self.log_text.insert("end", f"{msg}\n", level)
        self.log_text.tag_config("ts", foreground="#888888")
        self.log_text.tag_config("info", foreground="#cccccc")
        self.log_text.tag_config("warn", foreground="#FF9800")
        self.log_text.tag_config("ok", foreground="#4CAF50")
        self.log_text.tag_config("err", foreground="#f44336")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ── 刷新 ──

    def _start_refresh(self):
        self._refresh_data()
        self._refresh_timer()

    def _refresh_timer(self):
        if self.close_flag:
            return
        if not self.monitor_running or self.monitor_paused:
            self._refresh_data()
        self.root.after(3000, self._refresh_timer)

    def _refresh_now(self):
        self._refresh_data()

    def _refresh_data(self):
        if not psutil:
            self._set_status_text("psutil 未安装!")
            return

        try:
            mem = get_memory_summary()
            admin = is_admin()
            procs = self._scan_processes()

            pct = mem["percent"]
            self.lbl_mem.configure(text=f"{mem['used_gb']}GB / {mem['total_gb']}GB  ({pct}%)")
            color = COLOR_PRIMARY if pct < 70 else (COLOR_WARN if pct < 85 else COLOR_DANGER)
            self.lbl_mem.configure(fg=color)
            bw = max(1, int(self.mem_bar_frame.winfo_width() * pct / 100))
            self.mem_bar_bg.coords(self.mem_bar_fill, 0, 0, bw, 18)
            self.mem_bar_bg.itemconfig(self.mem_bar_fill, fill=color)

            status_parts = [f"可用 {mem['avail_gb']}GB"]
            if admin:
                status_parts.append("管理员")
            else:
                status_parts.append("普通权限 (建议提权)")
            self.lbl_detail.configure(text="  |  ".join(status_parts))

            prot_lines = []
            for p in procs["protected"]:
                prot_lines.append(f"PID={p['pid']:<5}  {p['name']:<24}  {p['mem_mb']:>7.1f}MB")
            if prot_lines:
                self.lbl_prot_list.configure(text="\n".join(prot_lines), fg=COLOR_TEXT)
            else:
                self.lbl_prot_list.configure(text="(无)", fg=COLOR_MUTED)

            kill_lines = []
            total_kill_mb = 0
            for p in procs["killable"]:
                kill_lines.append(
                    f"PID={p['pid']:<5}  {p['name']:<24}  {p['mem_mb']:>7.1f}MB")
                total_kill_mb += p["mem_mb"]
            if kill_lines:
                self.lbl_kill_list.configure(
                    text=f"共 {len(procs['killable'])} 个进程, 可释放约 {total_kill_mb:.0f}MB\n"
                    + "\n".join(kill_lines),
                    fg=COLOR_DANGER)
            else:
                self.lbl_kill_list.configure(text="(无可清理进程)", fg=COLOR_MUTED)

        except Exception as e:
            self._set_status_text(f"刷新失败: {e}")

    def _get_protected(self):
        """获取受保护 PID 集合（基于 exe 路径 + 进程名 + 命令行）"""
        pids, _ = get_protected_pids(self.cfg)
        return pids

    def _scan_processes(self):
        """
        扫描进程，分类返回 protected / killable。
        对 PID 去重，基于快照级保护 PID 集合，
        确保 biliTickerBuy/BHYG/WebView2 必在保护内。
        """
        cfg = self.cfg
        thresh = cfg["threshold_mb"]
        target_names = [t.lower() for t in cfg["target_names"]]

        # 先抓受保护 PID 快照（exe/name/cmdline 三层）
        protected_pids = self._get_protected()

        protected = []
        killable = []
        seen_pids = set()

        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                pid = proc.info["pid"]
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                name = proc.info["name"] or ""
                mem_mb = (proc.info["memory_info"].rss
                          if proc.info["memory_info"] else 0) / 1048576
                name_lower = name.lower()

                if pid in protected_pids:
                    protected.append({"pid": pid, "name": name, "mem_mb": mem_mb})
                elif any(t in name_lower for t in target_names) and mem_mb >= thresh:
                    killable.append({"pid": pid, "name": name, "mem_mb": mem_mb})
            except:
                continue

        protected.sort(key=lambda x: x["mem_mb"], reverse=True)
        killable.sort(key=lambda x: x["mem_mb"], reverse=True)

        return {"protected": protected, "killable": killable}

    def _set_status_text(self, text):
        self.status_bar.configure(text=text)

    # ── 操作 ──

    def _on_clean(self):
        if self.monitor_running:
            self.log("监控模式运行中, 请先停止", "warn")
            return
        self._do_clean(dry_run=False)

    def _do_clean(self, dry_run=False):
        """执行清理（后台线程 + PID 快照保护）"""
        def task():
            cfg = self.cfg
            thresh = cfg["threshold_mb"]
            target_names = [t.lower() for t in cfg["target_names"]]

            # ★ 先抓受保护 PID 快照 — 基于 exe 路径 + 进程名 + 命令行三层匹配
            protected_pids, _ = get_protected_pids(cfg)
            protected_pids.add(os.getpid())  # 把自己也保护

            killed = 0
            freed = 0
            noaccess = 0
            below = 0

            for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    pid = proc.info["pid"]
                    name = (proc.info["name"] or "").lower()
                    mem_mb = (proc.info["memory_info"].rss
                              if proc.info["memory_info"] else 0) / 1048576

                    # ★ PID 快照保护 — 属于保护集则不杀
                    if pid in protected_pids:
                        continue

                    # 不在目标列表 -> 跳过
                    if not any(t in name for t in target_names):
                        continue

                    # 低于阈值 -> 跳过
                    if mem_mb < thresh:
                        below += 1
                        continue

                    if dry_run:
                        self.log_queue.put(
                            ("info", f"[预览] PID={pid} {name} {mem_mb:.0f}MB"))
                        killed += 1
                        freed += mem_mb
                    else:
                        try:
                            proc.kill()
                            self.log_queue.put(
                                ("ok", f"已杀 PID={pid} {name} {mem_mb:.0f}MB"))
                            killed += 1
                            freed += mem_mb
                        except psutil.AccessDenied:
                            self.log_queue.put(
                                ("warn", f"权限不足 PID={pid} {name}"))
                            noaccess += 1
                        except psutil.NoSuchProcess:
                            pass
                except:
                    continue

            mem = get_memory_summary()
            if dry_run:
                self.log_queue.put(("info", f"预览: 可杀 {killed} 进程, "
                                   f"释放 ~{freed:.0f}MB "
                                   f"(跳过 {noaccess} 权限 / {below} 低阈值)"))
            else:
                self.log_queue.put(("ok", f"完成: 已杀 {killed} 进程, "
                                   f"释放 {freed:.0f}MB "
                                   f"(跳过 {noaccess} 权限 / {below} 低阈值)"))
            self.log_queue.put(("info", f"当前内存: {mem['used_gb']}GB / "
                               f"{mem['total_gb']}GB  ({mem['percent']}%)"))
            self.log_queue.put(("flush", None))
            self._refresh_now()

        t = threading.Thread(target=task, daemon=True)
        t.start()
        self._poll_log_queue()

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg[0] == "flush":
                    break
                self.log(msg[1], msg[0])
        except queue.Empty:
            pass
        if self.close_flag:
            return
        self.root.after(100, self._poll_log_queue)

    def _on_toggle_monitor(self):
        if not self.monitor_running:
            self._start_monitor()
        else:
            self._stop_monitor()

    def _start_monitor(self):
        self.monitor_running = True
        self.monitor_paused = False
        self.btn_monitor.configure(text="停止监控", bg=COLOR_DANGER)
        self.btn_clean.configure(state="disabled")
        self.log("监控模式启动", "info")

        def loop():
            while self.monitor_running and not self.close_flag:
                if self.monitor_paused:
                    time.sleep(1)
                    continue

                interval = int(self.interval_var.get())
                cfg = self.cfg
                target_names = [t.lower() for t in cfg["target_names"]]
                thresh = cfg["threshold_mb"]

                # ★ 每轮先抓受保护 PID 快照
                protected_pids, _ = get_protected_pids(cfg)
                protected_pids.add(os.getpid())

                killed = 0

                for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                    try:
                        pid = proc.info["pid"]
                        name = (proc.info["name"] or "").lower()
                        mem_mb = (proc.info["memory_info"].rss
                                  if proc.info["memory_info"] else 0) / 1048576

                        # ★ PID 快照保护
                        if pid in protected_pids:
                            continue
                        if not any(t in name for t in target_names):
                            continue
                        if mem_mb < thresh:
                            continue
                        try:
                            proc.kill()
                            m = f"监控: 已杀 PID={pid} {name} {mem_mb:.0f}MB"
                            self.log_queue.put(("ok", m))
                            killed += 1
                        except:
                            pass
                    except:
                        continue

                if killed > 0:
                    self.log_queue.put(("ok", f"监控: 本轮清理 {killed} 个"))
                self.log_queue.put(("flush", None))
                self.root.after(0, self._refresh_now)

                for _ in range(interval):
                    if not self.monitor_running or self.close_flag:
                        return
                    time.sleep(1)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._poll_log_queue()

    def _stop_monitor(self):
        self.monitor_running = False
        self.btn_monitor.configure(text="持续监控", bg=COLOR_PRIMARY)
        self.btn_clean.configure(state="normal")
        self.log("监控停止", "warn")

    def _on_elevate(self):
        self.log("正在提权...", "warn")
        if elevate_self():
            self.close_flag = True
            self.root.destroy()
            sys.exit(0)
        else:
            self.log("提权失败", "err")

    # ── 关闭 ──

    def _on_close(self):
        self.close_flag = True
        self.monitor_running = False
        try:
            self.root.destroy()
        except:
            pass
        sys.exit(0)

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════

def main():
    if not psutil:
        import tkinter.messagebox as mb
        mb.showerror("错误", "需要 psutil 库\n请运行: pip install psutil")
        sys.exit(1)
    if not tk:
        import tkinter.messagebox as mb
        mb.showerror("错误", "需要 tkinter 支持")
        sys.exit(1)

    app = MemCleanerApp()
    app.run()


if __name__ == "__main__":
    main()
