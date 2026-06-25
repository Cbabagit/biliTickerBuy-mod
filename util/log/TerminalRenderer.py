from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Iterable


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
    cooldown: str = "-"


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


def _extract_message_meta(item) -> tuple[str, str, int | None, int | None]:
    message = getattr(item, "message", item)
    kind = getattr(item, "kind", "normal")
    state = getattr(item, "state", None)
    attempt_current = getattr(state, "attempt_current", None)
    attempt_total = getattr(state, "attempt_total", None)
    return str(message), str(kind), attempt_current, attempt_total


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


class RichLiveTerminalRenderer(BaseTerminalRenderer):
    """使用 rich.live 渲染全面板终端 UI：配置 + 状态 + 事件日志"""

    def __init__(self, context: TerminalRenderContext):
        super().__init__(context)
        self.state = TerminalViewState()
        self._live = None
        self._config_info: list[tuple[str, str]] = []  # (label, value)
        self._log_messages: list[tuple[str, str]] = []  # (style_key, text)
        self._max_log = 25
        self._load_config()

    def _load_config(self) -> None:
        """从 config.json 加载配置信息"""
        import json
        from pathlib import Path

        # 向上查找 config.json
        cwd = Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            cfg = parent / "config.json"
            if cfg.exists():
                try:
                    with open(cfg, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if "_default" in raw:
                        conf = {}
                        for v in raw["_default"].values():
                            if isinstance(v, dict) and "key" in v:
                                conf[v["key"]] = v["value"]

                        items = [
                            ("抢票间隔", f"{conf.get('requestInterval', '?')}ms"),
                            ("重试限制", str(conf.get("createRetryLimit", "?"))),
                            ("批量大小", str(conf.get("createRequestBatchSize", "?"))),
                            ("代理池", str(len((conf.get("https_proxy", "") or "").split(","))) + " 个"),
                            ("Clash 实例", str(conf.get("clash.instance_count", "?"))),
                        ]
                        self._config_info = items
                        return
                except Exception:
                    pass

        # 从环境变量或上下文获取
        self._config_info = [
            ("配置", self.context.config_name),
        ]

    def render_header(self) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.layout import Layout

        self._console = Console()
        self._layout = Layout()
        self._layout.split_column(
            Layout(name="header", size=3),
            Layout(name="config", size=4),
            Layout(name="status", size=4),
            Layout(name="events"),
        )
        self._live = Live(
            self._build_layout(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
            auto_refresh=False,
        )
        self._live.__enter__()

    def _build_layout(self) -> "Layout":
        from rich.console import Group, Text
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text as RichText

        # ── 标题行 ──
        now_str = time.strftime("%H:%M:%S")
        title = RichText()
        title.append(f" biliTickerBuy ", style="bold white on blue")
        title.append(f" {self.context.config_name} ", style="bold cyan")
        title.append(f"  {now_str}  ", style="dim white")
        self._layout["header"].update(Panel(title, style="blue", padding=(0, 1)))

        # ── 配置面板 ──
        cfg_table = Table.grid(padding=(0, 2))
        cfg_table.add_column(style="dim cyan", width=14)
        cfg_table.add_column(style="white")
        for label, value in self._config_info:
            cfg_table.add_row(label, value)
        self._layout["config"].update(
            Panel(cfg_table, title="[bold]配置[/bold]", border_style="cyan", padding=(0, 1))
        )

        # ── 状态面板 ──
        st = self.state
        st_table = Table.grid(padding=(0, 2))
        st_table.add_column(width=12)
        st_table.add_column()

        stage_style = "bold yellow" if "等待" in st.stage else "bold green"
        st_table.add_row("阶段", f"[{stage_style}]{st.stage}[/]")
        st_table.add_row("倒计时", f"[bold]{st.countdown}[/]")

        proxy_display = st.current_proxy if len(st.current_proxy) <= 50 else st.current_proxy[:47] + "..."
        st_table.add_row("代理", proxy_display)
        st_table.add_row("冷却", st.cooldown if st.cooldown and st.cooldown != "-" else "[dim]-[/]")

        self._layout["status"].update(
            Panel(st_table, title="[bold]状态[/bold]", border_style="green", padding=(0, 1))
        )

        # ── 事件日志 ──
        if not self._log_messages:
            event_content = RichText("等待事件...", style="dim")
        else:
            lines: list[Text | str] = []
            for style_key, text in self._log_messages:
                style_map = {
                    "info": "",
                    "success": "green",
                    "error": "bold red",
                    "warning": "yellow",
                    "dim": "dim",
                    "attempt": "dim white",
                    "countdown": "cyan",
                    "proxy": "yellow",
                    "blocked": "bold yellow",
                }
                s = style_map.get(style_key, "")
                t = RichText(text, style=s) if s else RichText(text)
                lines.append(t)
            event_content = Group(*lines)

        self._layout["events"].update(
            Panel(
                event_content,
                title="[bold]事件[/bold]",
                border_style="dim",
                padding=(0, 1),
                height=None,
            )
        )

        if self._live:
            self._live.update(self._layout)
            self._live.refresh()

        return self._layout

    def _classify_message(self, msg: str) -> str:
        """根据消息内容分类颜色样式"""
        if any(k in msg for k in ["异常", "错误", "失败", "✕"]):
            return "error"
        if any(k in msg for k in ["成功", "✓"]):
            return "success"
        if any(k in msg for k in ["风控", "412", "403", "blocked"]):
            return "blocked"
        if msg.startswith("距离开始抢票还有"):
            return "countdown"
        if any(msg.startswith(p) for p in ["当前代理", "切换代理", "代理冷却", "代理池", "所有代理"]):
            return "proxy"
        if "429" in msg or "限流" in msg:
            return "warning"
        return "normal"

    def render_state(self, state) -> None:
        self.state.stage = getattr(state, "stage", self.state.stage)
        self.state.countdown = getattr(state, "countdown", self.state.countdown)
        self.state.current_proxy = getattr(state, "current_proxy", self.state.current_proxy)
        cooldown_remaining = getattr(state, "cooldown_remaining", None)
        self.state.cooldown = (
            f"{cooldown_remaining}s"
            if isinstance(cooldown_remaining, (int, float)) and cooldown_remaining > 0
            else "-"
        )
        self._build_layout()

    def render_message(self, item) -> None:
        message = getattr(item, "message", item)
        msg_str = str(message)

        # 分类并添加到日志缓冲区
        style = self._classify_message(msg_str)
        self._log_messages.append((style, msg_str))
        if len(self._log_messages) > self._max_log:
            self._log_messages = self._log_messages[-self._max_log:]

        self._build_layout()

    def close(self) -> None:
        if self._live:
            try:
                self._live.__exit__(None, None, None)
            except Exception:
                pass
            self._live = None

        # 退出前打印最终摘要
        import sys as _sys
        _sys.stdout.write("\n")
        print("━━━ 抢票结束 ━━━", flush=True)
        if self._log_messages:
            for style, text in self._log_messages[-5:]:
                print(f"  {text}", flush=True)


class PlainTerminalRenderer(BaseTerminalRenderer):
    """Stable fallback for terminals where Textual cannot render reliably."""

    def __init__(self, context: TerminalRenderContext):
        super().__init__(context)
        self.state = TerminalViewState()
        self._last_snapshot: tuple[str, str, str, str] | None = None
        self._header_printed = False

    def render_header(self) -> None:
        print(
            f"[抢票终端] 配置: {self.context.config_name} | 日志: {self.context.log_file}",
            flush=True,
        )
        self._header_printed = True
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
        available_proxies = getattr(state, "available_proxies", None)
        cooldown_remaining = getattr(state, "cooldown_remaining", None)

        state_parts = [
            f"[状态] 阶段: {self.state.stage}",
            f"倒计时: {self.state.countdown}",
            f"代理: {self.state.current_proxy}",
        ]
        if available_proxies is not None:
            state_parts.append(f"可用 {available_proxies}")
        self.state.cooldown = (
            f"{cooldown_remaining} 秒"
            if isinstance(cooldown_remaining, int) and cooldown_remaining > 0
            else "-"
        )
        state_parts.append(f"冷却: {self.state.cooldown}")
        self.state._status_line = " | ".join(state_parts)
        self._print_snapshot()

    def _print_snapshot(self, *, force: bool = False) -> None:
        snapshot = (
            self.state.stage,
            self.state.countdown,
            self.state.current_proxy,
            getattr(self.state, "cooldown", "-"),
        )
        if not force and snapshot == self._last_snapshot:
            return

        status_line = getattr(self.state, "_status_line",
            f"[状态] 阶段: {self.state.stage} | "
            f"倒计时: {self.state.countdown} | "
            f"代理: {self.state.current_proxy} | "
            f"冷却: {self.state.cooldown}"
        )

        header_line = f"[抢票终端] 配置: {self.context.config_name} | 日志: {self.context.log_file}"

        if self._header_printed:
            import sys
            is_tty = sys.stdout.isatty()
            if is_tty:
                sys.stdout.write("\033[2A")
                sys.stdout.write("\033[2K")
                sys.stdout.write("\r")
                sys.stdout.write(header_line)
                sys.stdout.write("\n")
                sys.stdout.write("\033[2K")
                sys.stdout.write("\r")
                sys.stdout.write(status_line)
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._last_snapshot = snapshot
                return

        print(header_line, flush=True)
        print(status_line, flush=True)
        self._last_snapshot = snapshot


def _make_log_item(item) -> LogItem:
    message, kind, attempt_current, attempt_total = _extract_message_meta(item)

    if kind != "attempt" or attempt_current is None or attempt_total is None:
        return LogItem(
            raw_message=message,
            display_message=message,
            count=1,
            kind="normal",
        )

    return LogItem(
        raw_message=message,
        display_message=message,
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
        item.display_message = (
            f"[{item.attempt_start}/{attempt_total}] {message}".rstrip()
        )
    else:
        item.display_message = f"[{item.attempt_start}-{item.attempt_end}/{attempt_total}] {message}".rstrip()


class TextualTerminalRenderer(BaseTerminalRenderer):
    def __init__(self, context: TerminalRenderContext):
        super().__init__(context)

        import threading

        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from textual.app import App, ComposeResult
        from textual.containers import Vertical, VerticalScroll
        from textual.widgets import Static

        self.threading = threading
        self.ready = threading.Event()

        ready = self.ready

        class TicketTerminalApp(App):
            CSS = """
            Screen {
                background: #0f1117;
            }

            #root {
                height: 100%;
                padding: 1 2;
            }

            #status {
                height: 5;
                margin-bottom: 1;
            }

            #log_container {
                height: 1fr;
                border: round #3b4252;
                background: #111827;
            }

            #log {
                height: auto;
                min-height: 100%;
                padding: 0 1;
            }
            """

            BINDINGS = [
                ("q", "quit", "退出"),
                ("ctrl+c", "quit", "退出"),
            ]

            def __init__(self):
                super().__init__()

                self.state = TerminalViewState()
                self.status_widget: Static | None = None
                self.log_container: VerticalScroll | None = None
                self.log_widget: Static | None = None

                self.message_count = 0
                self.log_items: list[LogItem] = []

            def compose(self) -> ComposeResult:
                # 不显示 Header / Footer，所以不会出现标题栏和底部快捷键栏。
                # 退出键位放在顶部状态区里显示。
                with Vertical(id="root"):
                    self.status_widget = Static(id="status")
                    yield self.status_widget

                    with VerticalScroll(id="log_container") as log_container:
                        self.log_container = log_container
                        self.log_widget = Static(id="log")
                        yield self.log_widget

            def on_mount(self) -> None:
                self.title = ""
                self.sub_title = ""

                self.update_status()
                self.update_log()

                ready.set()

            def update_status(self) -> None:
                table = Table.grid(expand=True)
                table.add_column(style="dim", ratio=1)
                table.add_column(style="bold white", ratio=3)

                table.add_row(
                    "倒计时",
                    self.state.countdown,
                )
                table.add_row(
                    "代理状态",
                    self._shorten(self.state.current_proxy, 96),
                )
                table.add_row(
                    "冷却",
                    self.state.cooldown,
                )

                panel = Panel(
                    table,
                    border_style="cyan",
                    padding=(0, 1),
                    expand=True,
                )

                if self.status_widget is not None:
                    self.status_widget.update(panel)

            def update_log(self) -> None:
                if self.log_widget is None:
                    return

                if not self.log_items:
                    self.log_widget.update(Text("等待日志输出...", style="dim"))
                    return

                rendered = [self.render_log_item(item) for item in self.log_items]
                self.log_widget.update(Group(*rendered))
                if self.log_container is not None:
                    self.log_container.scroll_end(animate=False)

            @staticmethod
            def _shorten(text: str, width: int = 60) -> str:
                if not text or text == "-":
                    return "-"
                return text if len(text) <= width else text[: width - 1] + "…"

            def sync_state(self, state) -> None:
                self.state.stage = getattr(state, "stage", self.state.stage)
                self.state.countdown = getattr(state, "countdown", self.state.countdown)
                self.state.current_proxy = getattr(
                    state, "current_proxy", self.state.current_proxy
                )
                cooldown_remaining = getattr(state, "cooldown_remaining", None)
                self.state.cooldown = (
                    f"{cooldown_remaining} 秒"
                    if isinstance(cooldown_remaining, int) and cooldown_remaining > 0
                    else "-"
                )
                self.update_status()

            def render_log_message(self, message: str, item: LogItem) -> Text:
                text = Text()

                if message.startswith(("0)", "1）", "2）", "3）")):
                    text.append("● ", style="bold cyan")
                    text.append(message, style="bold white")
                    return text

                if message.startswith("距离开始抢票还有"):
                    text.append("⏱ ", style="cyan")
                    text.append(message, style="cyan")
                    return text

                if "412风控" in message:
                    text.append("⚠ ", style="bold yellow")
                    text.append(message, style="bold yellow")
                    return text

                if (
                    message.startswith("当前代理:")
                    or message.startswith("目前已配置代理")
                    or message.startswith("切换代理到 ")
                    or message.startswith("代理冷却:")
                    or message.startswith("代理池状态:")
                    or message.startswith("所有代理当前不可用")
                ):
                    text.append("⇄ ", style="yellow")
                    text.append(message, style="yellow")
                    return text

                if "抢票成功" in message or "创建订单成功" in message:
                    text.append("✓ ", style="bold green")
                    text.append(message, style="bold green")
                    return text

                if (
                    "接口异常" in message
                    or "请求异常" in message
                    or "程序异常" in message
                ):
                    text.append("✕ ", style="bold red")
                    text.append(message, style="bold red")
                    return text

                if item.kind == "attempt":
                    if "[900001]" in message or "[900002]" in message:
                        text.append("… ", style="yellow")
                        text.append(message, style="yellow")
                    elif "[100041]" in message or "[100009]" in message:
                        text.append("… ", style="magenta")
                        text.append(message, style="magenta")
                    else:
                        text.append("… ", style="dim")
                        text.append(message, style="white")
                    return text

                text.append("  ", style="dim")
                text.append(message, style="white")
                return text

            def render_log_item(self, item: LogItem) -> Text:
                line = self.render_log_message(item.display_message, item)

                if item.count > 1:
                    line.append(f"  x{item.count}", style="bold dim")

                return line

            def add_message(self, event) -> None:
                self.message_count += 1

                if self.log_items and _can_merge_log_item(self.log_items[-1], event):
                    _merge_log_item(self.log_items[-1], event)
                else:
                    self.log_items.append(_make_log_item(event))

                self.update_log()

        self.app = TicketTerminalApp()
        self.thread = None

    def _dump_final_snapshot(self) -> None:
        state = self.app.state
        print(
            f"[抢票终端] 配置: {self.context.config_name} | 日志: {self.context.log_file}",
            flush=True,
        )
        print(
            (
                "[状态] "
                f"阶段: {state.stage} | "
                f"倒计时: {state.countdown} | "
                f"代理: {state.current_proxy} | "
                f"冷却: {state.cooldown}"
            ),
            flush=True,
        )
        if not self.app.log_items:
            print("等待日志输出...", flush=True)
            return
        for item in self.app.log_items:
            print(item.display_message, flush=True)

    def render_header(self) -> None:
        def run_app() -> None:
            try:
                signature = inspect.signature(self.app.run)
                params = signature.parameters

                run_kwargs = {}

                # Textual 新版本支持 inline 模式。
                # inline=True 可以避免进入全屏 alternate screen；
                # inline_no_clear=True 可以在退出后保留最后的界面输出，方便继续看日志。
                if "inline" in params:
                    run_kwargs["inline"] = True

                if "inline_no_clear" in params:
                    run_kwargs["inline_no_clear"] = True

                self.app.run(**run_kwargs)
            except TypeError:
                self.app.run()

        self.thread = self.threading.Thread(
            target=run_app,
            daemon=True,
        )
        self.thread.start()

        if not self.ready.wait(timeout=5):
            raise RuntimeError("Textual terminal renderer failed to start")

    def render_message(self, item) -> None:
        self.app.call_from_thread(self.app.add_message, item)

    def render_state(self, state) -> None:
        self.app.call_from_thread(self.app.sync_state, state)

    def close(self) -> None:
        try:
            self.app.call_from_thread(self.app.exit)
        except Exception:
            pass
        try:
            if self.thread is not None:
                self.thread.join(timeout=2)
        except Exception:
            pass
        self._dump_final_snapshot()


def create_terminal_renderer(
    context: TerminalRenderContext,
    *,
    prefer_rich: bool = True,
) -> BaseTerminalRenderer:
    # 优先：RichLiveTerminalRenderer（使用 rich，无需 textual）
    if context.platform_name == "nt":
        try:
            return RichLiveTerminalRenderer(context)
        except Exception:
            pass

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
                renderer.render_state(state)

            if message is None:
                continue

            if on_message is not None:
                on_message(message)

            if renderer is not None:
                renderer.render_message(item)

    finally:
        if renderer is not None:
            renderer.close()
