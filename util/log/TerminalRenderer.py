from __future__ import annotations

import datetime
import inspect
import threading
import time as time_module
from dataclasses import dataclass
from typing import Iterable

from loguru import logger as loguru_logger
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Static

from util.Constant import BEIJING_TZ


# ─── NTP 同步 ──────────────────────────────────────────────

NTP_SERVERS = ["ntp1.aliyun.com", "ntp.ntsc.ac.cn"]


def sync_ntp_once() -> float:
    """尝试 NTP 时间同步，返回偏移秒数（本地 - NTP）。"""
    import ntplib

    client = ntplib.NTPClient()
    for server in NTP_SERVERS:
        for attempt in range(3):
            try:
                resp = client.request(server, version=4)
                offset = -resp.offset
                return offset
            except Exception:
                if attempt < 2:
                    time_module.sleep(0.5)
                continue
    return 0.0


# ─── 数据模型 ──────────────────────────────────────────────


@dataclass(frozen=True)
class TerminalRenderContext:
    config_name: str
    log_file: str
    platform_name: str


@dataclass
class TerminalViewState:
    stage: str = "初始化"
    countdown: str = "-"
    current_proxy: str = "未初始化"
    proxy_pool: str = ""
    cooldown_remaining: int | None = None
    attempt_current: int | None = None
    attempt_total: int | None = None


@dataclass
class LogItem:
    raw_message: str
    display_message: str
    count: int = 1
    kind: str = "normal"
    attempt_start: int | None = None
    attempt_end: int | None = None
    attempt_total: int | None = None
    attempt_body: str | None = None


# ─── 合并逻辑（复用现有） ─────────────────────────────────


def _extract_message_meta(item) -> tuple[str, str, int | None, int | None]:
    message = getattr(item, "message", item)
    kind = getattr(item, "kind", "normal")
    state = getattr(item, "state", None)
    attempt_current = getattr(state, "attempt_current", None)
    attempt_total = getattr(state, "attempt_total", None)
    return str(message), str(kind), attempt_current, attempt_total


def _make_log_item(item) -> LogItem:
    message, kind, attempt_current, attempt_total = _extract_message_meta(item)
    if kind != "attempt" or attempt_current is None or attempt_total is None:
        return LogItem(
            raw_message=message, display_message=message, count=1, kind="normal"
        )
    return LogItem(
        raw_message=message,
        display_message=f"[{attempt_current}/{attempt_total}] {message}".rstrip(),
        count=1,
        kind="attempt",
        attempt_start=attempt_current,
        attempt_end=attempt_current,
        attempt_total=attempt_total,
        attempt_body=message,
    )


def _can_merge_log_item(item: LogItem, next_item) -> bool:
    message, kind, attempt_current, attempt_total = _extract_message_meta(next_item)
    if item.kind == "normal":
        return item.raw_message == message
    if kind != "attempt" or attempt_current is None or attempt_total is None:
        return False
    if item.attempt_end is None:
        return False
    return (
        item.kind == "attempt"
        and item.attempt_total == attempt_total
        and item.attempt_body == message
        and attempt_current == item.attempt_end + 1
    )


def _merge_log_item(item: LogItem, next_item) -> None:
    message, kind, attempt_current, attempt_total = _extract_message_meta(next_item)
    if item.kind == "normal":
        item.count += 1
        return
    if kind != "attempt" or attempt_current is None or attempt_total is None:
        item.count += 1
        return
    item.raw_message = message
    item.count += 1
    item.attempt_end = attempt_current
    item.attempt_total = attempt_total
    item.attempt_body = message
    if item.attempt_start == item.attempt_end:
        item.display_message = f"[{item.attempt_start}/{attempt_total}] {message}".rstrip()
    else:
        item.display_message = f"[{item.attempt_start}-{item.attempt_end}/{attempt_total}] {message}".rstrip()


# ─── 基础渲染器 ─────────────────────────────────────────────


class BaseTerminalRenderer:
    def __init__(self, context: TerminalRenderContext):
        self.context = context

    def render_header(self) -> None:
        raise NotImplementedError

    def render_message(self, message: str) -> None:
        raise NotImplementedError

    def render_state(self, state) -> None:
        return None

    def close(self) -> None:
        return None


# ─── 纯文本备选 ─────────────────────────────────────────────


class PlainTerminalRenderer(BaseTerminalRenderer):
    def __init__(self, context: TerminalRenderContext):
        super().__init__(context)
        self.state = TerminalViewState()
        self._last_snapshot: tuple[str, str, str, str, int | None] | None = None

    def render_header(self) -> None:
        print(
            f"[抢票终端] 配置: {self.context.config_name} | 日志: {self.context.log_file}",
            flush=True,
        )
        self._print_snapshot(force=True)

    def render_message(self, item) -> None:
        message = getattr(item, "message", item)
        self._print_snapshot()
        print(message, flush=True)

    def render_state(self, state) -> None:
        self.state.stage = getattr(state, "stage", self.state.stage)
        self.state.countdown = getattr(state, "countdown", self.state.countdown)
        self.state.current_proxy = getattr(
            state, "current_proxy", self.state.current_proxy
        )
        cr = getattr(state, "cooldown_remaining", None)
        self.state.cooldown_remaining = cr
        self._print_snapshot()

    def _print_snapshot(self, *, force: bool = False) -> None:
        cr = self.state.cooldown_remaining
        snapshot = (
            self.state.stage,
            self.state.countdown,
            self.state.current_proxy,
            str(cr) if cr and cr > 0 else "-",
        )
        if not force and snapshot == self._last_snapshot:
            return
        now_str = datetime.datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
        print(
            (
                f"[状态] {now_str} | "
                f"阶段: {self.state.stage} | "
                f"倒计时: {self.state.countdown} | "
                f"代理: {self.state.current_proxy} | "
                f"冷却: {cr}秒" if cr and cr > 0 else "-"
            ),
            flush=True,
        )
        self._last_snapshot = snapshot


# ─── Textual 全屏 TUI ──────────────────────────────────────


class TicketTerminalApp(App):
    """全屏抢票终端 UI —— 顶部状态栏 + 滚动日志 + 底部提示。"""

    CSS = """
    Screen {
        background: #0f1117;
    }
    #root {
        height: 100%;
        padding: 1 2;
    }
    #status_panel {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
    }
    #log_container {
        height: 1fr;
        border: round #3b4252;
        background: #111827;
    }
    #log_widget {
        height: auto;
        min-height: 100%;
        padding: 0 1;
    }
    Footer {
        background: #1e2233;
        color: #7e88a3;
    }
    """

    BINDINGS = [("q", "quit", "退出"), ("ctrl+c", "quit", "退出")]

    def __init__(self):
        super().__init__()
        self.state = TerminalViewState()
        self.log_items: list[LogItem] = []
        self._ready_event = threading.Event()
        self._title_name = ""
        self._clock_str = reactive("--:--:--")

    def set_title_name(self, name: str) -> None:
        self._title_name = name

    def compose(self) -> ComposeResult:
        yield Container(id="root")
        yield Footer()

    def on_mount(self) -> None:
        root = self.query_one("#root")
        # 状态面板
        from textual.widgets import Static

        self.status_widget = Static(id="status_panel")
        root.mount(self.status_widget)

        # 日志容器
        from textual.containers import VerticalScroll

        lc = VerticalScroll(id="log_container")
        root.mount(lc)

        self.log_widget = Static(id="log_widget")
        lc.mount(self.log_widget)

        self.log_container = lc

        # 时钟定时器
        self.set_interval(1.0, self._tick_clock)
        self._tick_clock()

        # 初始渲染
        self.update_status()
        self.update_log()

        self._ready_event.set()

    def _tick_clock(self) -> None:
        self._clock_str = datetime.datetime.now(BEIJING_TZ).strftime("%H:%M:%S")

    def watch__clock_str(self, val: str) -> None:
        self.update_status()

    def update_status(self) -> None:
        if not hasattr(self, "status_widget"):
            return
        grid = Table.grid(expand=True)
        grid.add_column(style="dim", width=10)
        grid.add_column(style="bold white", width=28)
        grid.add_column(style="dim", width=8)
        grid.add_column(style="bold white", width=28)

        # 第一行：倒计时 + 系统时间
        cd = self.state.countdown or "-"
        grid.add_row("⏱ 倒计时", cd, "🕒 时间", self._clock_str)

        # 第二行：代理 + 冷却
        proxy = self._shorten(self.state.current_proxy, 40) or "-"
        cr = self.state.cooldown_remaining
        cooldown_str = f"{cr} 秒" if isinstance(cr, int) and cr > 0 else "-"
        grid.add_row("🌐 代理", proxy, "❄ 冷却", cooldown_str)

        # 第三行：阶段 + 重试
        stage = self.state.stage or "-"
        ap = self.state.attempt_current
        at = self.state.attempt_total
        attempt_str = f"{ap}/{at}" if ap is not None and at is not None else "-"
        pool = self._shorten(self.state.proxy_pool, 30) or "-"
        grid.add_row("📌 阶段", stage, "🔄 重试", attempt_str)

        panel = Panel(
            grid,
            title=f"🎫 {self._title_name}" if self._title_name else "🎫 biliTickerBuy",
            border_style="cyan",
            padding=(0, 1),
            expand=True,
        )
        self.status_widget.update(panel)

    def update_log(self) -> None:
        if not hasattr(self, "log_widget"):
            return
        if not self.log_items:
            self.log_widget.update(Text("等待日志输出...", style="dim"))
            return
        rendered = [self._render_log_item(item) for item in self.log_items]
        self.log_widget.update(Group(*rendered))
        try:
            if hasattr(self, "log_container") and self.log_container:
                self.log_container.scroll_end(animate=False)
        except Exception:
            pass

    @staticmethod
    def _shorten(text: str, width: int = 60) -> str:
        if not text or text == "-":
            return "-"
        return text if len(text) <= width else text[: width - 1] + "…"

    def sync_state(self, state) -> None:
        self.state.stage = getattr(state, "stage", self.state.stage)
        self.state.countdown = getattr(state, "countdown", self.state.countdown)
        self.state.current_proxy = getattr(state, "current_proxy", self.state.current_proxy)
        self.state.proxy_pool = getattr(state, "proxy_pool", self.state.proxy_pool)
        cr = getattr(state, "cooldown_remaining", None)
        self.state.cooldown_remaining = cr
        ap = getattr(state, "attempt_current", None)
        at = getattr(state, "attempt_total", None)
        self.state.attempt_current = ap
        self.state.attempt_total = at
        self.update_status()

    def _render_log_line(self, message: str, item: LogItem) -> Text:
        """一行日志带前缀颜色。"""
        t = Text()
        # 特殊消息着色
        if message.startswith(("0)", "1）", "2）", "3）")):
            t.append("● ", style="bold cyan")
            t.append(message, style="bold white")
        elif "距离开始抢票还有" in message or "倒计时" in message:
            t.append("⏱ ", style="cyan")
            t.append(message, style="cyan")
        elif "412" in message and "风控" in message:
            t.append("⚠ ", style="bold yellow")
            t.append(message, style="bold yellow")
        elif message.startswith("当前代理:") or message.startswith("目前已配置代理") or \
             message.startswith("切换代理到 ") or message.startswith("代理冷却:") or \
             "不可用" in message:
            t.append("⇄ ", style="yellow")
            t.append(message, style="yellow")
        elif "抢票成功" in message or "创建订单成功" in message:
            t.append("✓ ", style="bold green")
            t.append(message, style="bold green")
        elif "接口异常" in message or "请求异常" in message or "程序异常" in message or "触发" in message:
            t.append("✕ ", style="bold red")
            t.append(message, style="bold red")
        elif item.kind == "attempt":
            if "[900001]" in message or "[900002]" in message:
                t.append("… ", style="yellow")
                t.append(message, style="yellow")
            elif "[100041]" in message or "[100009]" in message:
                t.append("… ", style="magenta")
                t.append(message, style="magenta")
            elif "触发" in message:
                t.append("… ", style="bold yellow")
                t.append(message, style="bold yellow")
            else:
                t.append("… ", style="dim")
                t.append(message, style="white")
        elif "收到停止信号" in message or "退出" in message:
            t.append("⏹ ", style="bold red")
            t.append(message, style="bold red")
        else:
            t.append("  ", style="dim")
            t.append(message, style="white")
        return t

    def _render_log_item(self, item: LogItem) -> Text:
        line = self._render_log_line(item.display_message, item)
        if item.count > 1:
            line.append(f"  ×{item.count}", style="bold yellow")
        return line

    def add_message(self, event) -> None:
        if self.log_items and _can_merge_log_item(self.log_items[-1], event):
            _merge_log_item(self.log_items[-1], event)
        else:
            self.log_items.append(_make_log_item(event))
        self.update_log()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready_event.wait(timeout=timeout)


# ─── Textual 渲染器包装 ──────────────────────────────────────


class TextualTerminalRenderer(BaseTerminalRenderer):
    def __init__(self, context: TerminalRenderContext):
        super().__init__(context)
        self.app = TicketTerminalApp()
        self.app.set_title_name(context.config_name)
        self.thread: threading.Thread | None = None
        self._running = False
        self._fallback: PlainTerminalRenderer | None = None

    def _ensure_fallback(self) -> PlainTerminalRenderer:
        if self._fallback is None:
            self._fallback = PlainTerminalRenderer(self.context)
            self._fallback.render_header()
        return self._fallback

    def _try_call(self, fn, *args, **kwargs):
        """尝试在 Textual 线程中调用 fn，失败则回退到 plain print。"""
        if not self._running:
            return self._ensure_fallback()
        try:
            self.app.call_from_thread(fn, *args, **kwargs)
        except RuntimeError as e:
            if "App is not running" in str(e):
                self._running = False
                fb = self._ensure_fallback()
                # 回放已有日志到 fallback 防止丢失
                for item in self.app.log_items:
                    fb.render_message(item)
                fb.render_state(self.app.state)
            else:
                raise

    def _dump_final_snapshot(self) -> None:
        state = self.app.state
        now_str = datetime.datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
        print(
            f"[{now_str}] 🎫 {self.context.config_name} | 日志: {self.context.log_file}",
            flush=True,
        )
        cr = state.cooldown_remaining
        print(
            (
                f"[状态] 阶段: {state.stage} | "
                f"倒计时: {state.countdown} | "
                f"代理: {state.current_proxy} | "
                f"冷却: {cr}秒" if cr and cr > 0 else "- [状态]"
            ),
            flush=True,
        )
        if not self.app.log_items:
            print("等待日志输出...", flush=True)
            return
        for item in self.app.log_items:
            line = item.display_message
            if item.count > 1:
                line += f"  ×{item.count}"
            print(line, flush=True)

    def render_header(self) -> None:
        def run_app() -> None:
            try:
                sig = inspect.signature(self.app.run)
                kw = {}
                if "mouse" in sig.parameters:
                    kw["mouse"] = False
                self.app.run(**kw)
            except Exception:
                self.app.run()

        self.thread = threading.Thread(target=run_app, daemon=True)
        self.thread.start()
        if not self.app.wait_ready(timeout=5):
            raise RuntimeError("Textual TUI 启动超时")
        self._running = True

    def render_message(self, item) -> None:
        if not self._running:
            self._ensure_fallback().render_message(item)
            return
        self._try_call(self.app.add_message, item)

    def render_state(self, state) -> None:
        if not self._running:
            self._ensure_fallback().render_state(state)
            return
        self._try_call(self.app.sync_state, state)

    def close(self) -> None:
        self._running = False
        try:
            self.app.call_from_thread(self.app.exit)
        except Exception:
            pass
        try:
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=2)
        except Exception:
            pass
        self._dump_final_snapshot()


# ─── 工厂函数 ──────────────────────────────────────────────


def create_terminal_renderer(
    context: TerminalRenderContext, *, prefer_rich: bool = True
) -> BaseTerminalRenderer:
    if context.platform_name == "nt":
        try:
            if prefer_rich:
                return TextualTerminalRenderer(context)
        except Exception:
            pass
        return PlainTerminalRenderer(context)
    if prefer_rich:
        try:
            return TextualTerminalRenderer(context)
        except Exception:
            pass
    return PlainTerminalRenderer(context)


# ─── 流式渲染 ──────────────────────────────────────────────


def render_message_stream(
    renderer: BaseTerminalRenderer | None,
    messages: Iterable,
    on_message=None,
) -> None:
    if renderer is not None:
        renderer.render_header()
    try:
        for item in messages:
            state = getattr(item, "state", None)
            message = getattr(item, "message", item)
            if renderer is not None and state is not None:
                try:
                    renderer.render_state(state)
                except Exception as exc:
                    loguru_logger.warning(f"渲染器状态更新异常（已忽略）: {exc}")
            if message is None:
                continue
            if on_message is not None:
                on_message(message)
            if renderer is not None:
                try:
                    renderer.render_message(item)
                except Exception as exc:
                    loguru_logger.warning(f"渲染器日志渲染异常（已忽略）: {exc}")
    finally:
        if renderer is not None:
            renderer.close()
