"""
推送器框架
==========
同步首发 + 后台重试策略：
  1. send_first() — 同步发送一次（含重试），抢到票后必须确保至少有 1 次推送到达
  2. run() — 后台线程做重复推送（Ntfy 等场景），作为补偿
  3. 所有 HTTP 请求带 timeout，检查状态码
"""

from abc import ABC, abstractmethod
import threading
import loguru
import time

from app_cmd.config.NotifierConfig import NotifierConfig


class HTTPDeliveryError(Exception):
    """发送成功但服务器返回非 2xx 状态码"""
    pass


class NotifierBase(ABC):
    """推送器基类。

    修改要点（修复不推送 bug）：
    - send_message() 子类必须带 timeout + 检查 HTTP 响应状态码
    - send_first() 同步重试至多 3 次，确保首发到达
    - run() 后台线程持续推送，为重复推送场景保留
    """

    def __init__(
        self,
        title: str,
        content: str,
        interval_seconds=10,
        duration_minutes=10,
    ):
        super().__init__()
        self.title = title
        self.content = content
        self.interval_seconds = interval_seconds
        self.duration_minutes = duration_minutes
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self._last_delivered: bool = False  # True = 至少一次成功发送

    # ── 同步首发（保证至少一次送达）──

    def send_first(self) -> bool:
        """同步发送，最多重试 3 次（指数退避），保证至少一次送达。
        返回 True 表示成功，False 表示所有重试均失败。
        """
        last_error = None
        for attempt in range(1, 4):
            try:
                self.send_message(self.title, self.content)
                self._last_delivered = True
                loguru.logger.info(
                    f"[推送:{type(self).__name__}] 首发成功 (attempt={attempt})"
                )
                return True
            except Exception as e:
                last_error = e
                loguru.logger.error(
                    f"[推送:{type(self).__name__}] 发送失败 (attempt={attempt}): {e}"
                )
                if attempt < 3:
                    time.sleep(attempt * 2)  # 2s, 4s
        loguru.logger.error(
            f"[推送:{type(self).__name__}] 首发失败（3次重试用完）: {last_error}"
        )
        return False

    # ── 后台重复推送 ──

    def run(self):
        """线程运行函数 — 后台重复推送（持续时间窗口内）。
        send_first() 已经保证至少 1 次送达，后台线程做额外补偿。
        """
        start_time = time.time()
        end_time = start_time + (self.duration_minutes * 60)
        count = 0

        while time.time() < end_time and not self.stop_event.is_set():
            try:
                count += 1
                remaining_minutes = int((end_time - time.time()) / 60)
                remaining_seconds = int((end_time - time.time()) % 60)
                message = (
                    f"{self.content} [#{count}, 剩余 {remaining_minutes}分{remaining_seconds}秒]"
                )
                self.send_message(self.title, message)
                loguru.logger.info(
                    f"[推送:{type(self).__name__}] 后台推送 #{count} 成功"
                )
                # 已成功发送过一次，后台重复推送正常退出即可
                self._last_delivered = True
                break
            except Exception as e:
                loguru.logger.error(
                    f"[推送:{type(self).__name__}] 后台推送 #{count} 失败: {e}"
                )
                time.sleep(self.interval_seconds)

        if count == 0:
            loguru.logger.warning(
                f"[推送:{type(self).__name__}] 后台线程未发送任何消息"
            )
        else:
            loguru.logger.info(
                f"[推送:{type(self).__name__}] 后台推送结束，共发送 {count} 条"
            )

    # ── 生命周期 ──

    def start(self):
        if not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    @property
    def is_delivered(self) -> bool:
        return self._last_delivered

    # ── 子类必须实现 ──

    @abstractmethod
    def send_message(self, title, message):
        """发送一次推送消息。
        必须带 HTTP timeout，必须检查响应状态码（非 2xx 抛 HTTPDeliveryError）。
        """
        pass


class NotifierManager:
    def __init__(self):
        self.notifier_dict: dict[str, NotifierBase] = {}

    def register_notifier(self, name: str, notifier: NotifierBase):
        if name in self.notifier_dict:
            loguru.logger.error(f"推送器添加失败: 已存在名为{name}的推送器")
        else:
            self.notifier_dict[name] = notifier
            loguru.logger.info(f"成功添加推送器: {name}")

    def remove_notifier(self, name: str):
        if name not in self.notifier_dict:
            loguru.logger.error(f"推送器删除失败: 不存在名为{name}的推送器")
        else:
            self.notifier_dict.pop(name)
            loguru.logger.info(f"成功删除推送器: {name}")

    # ── 核心改进：同步首发 + 后台重试 ──

    def deliver_sync(self) -> bool:
        """同步发送所有推送器，重试确保送达。返回 True 表示至少一个渠道成功。"""
        any_ok = False
        for name, notifier in self.notifier_dict.items():
            loguru.logger.info(f"[推送] {name}: 同步首发...")
            ok = notifier.send_first()
            if ok:
                any_ok = True
                loguru.logger.info(f"[推送] {name}: 首发成功 ✓")
            else:
                loguru.logger.error(f"[推送] {name}: 首发失败 ✗")
        return any_ok

    def deliver_and_keep_alive(self, join_timeout: float = 30.0) -> bool:
        """先同步首发，再启动后台线程，最后 join 等待线程结束。
        返回 True 表示至少一个渠道同步发送成功。
        """
        # 1) 同步首发（确保至少一次送达）
        any_ok = self.deliver_sync()

        # 2) 启动后台重复推送线程（补偿 / Ntfy 等）
        self.start_all()

        # 3) 等待线程完成
        self.join_all(timeout=join_timeout)

        return any_ok

    # ── 旧 API 保留兼容 ──

    def start_all(self):
        for notifier in self.notifier_dict.values():
            notifier.start()

    def join_all(self, timeout: float = 30.0):
        """等待所有推送线程结束。
        timeout 默认 30s（足够 HTTP 请求带 timeout=10 完成 + 一些缓冲）。
        """
        for notifier in self.notifier_dict.values():
            notifier.thread.join(timeout=timeout)

    def stop_all(self):
        for notifier in self.notifier_dict.values():
            notifier.stop()

    def start_notifier(self, name: str):
        notifier = self.notifier_dict.get(name)
        if notifier:
            notifier.start()
        else:
            loguru.logger.error(f"推送器启动失败: 不存在名为{name}的推送器")

    def stop_notifier(self, name: str):
        notifier = self.notifier_dict.get(name)
        if notifier:
            notifier.stop()
        else:
            loguru.logger.error(f"推送器停止失败: 不存在名为{name}的推送器")

    def list_notifiers(self):
        return list(self.notifier_dict.keys())

    @staticmethod
    def create_from_config(
        config: NotifierConfig,
        title: str,
        content: str,
        interval_seconds: int = 10,
        duration_minutes: int = 10,
        include_audio: bool = True,
    ) -> "NotifierManager":
        """通过配置创建NotifierManager"""
        manager = NotifierManager()

        # ServerChan Turbo
        if config.serverchan_key:
            try:
                from util.notifer.ServerChanUtil import ServerChanTurboNotifier

                notifier = ServerChanTurboNotifier(
                    token=config.serverchan_key,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("ServerChanTurbo", notifier)
            except ImportError as e:
                loguru.logger.error(f"ServerChanTurbo导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"ServerChanTurbo创建失败: {e}")

        # ServerChan3
        if config.serverchan3_api_url:
            try:
                from util.notifer.ServerChanUtil import ServerChan3Notifier

                notifier = ServerChan3Notifier(
                    api_url=config.serverchan3_api_url,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("ServerChan3", notifier)
            except ImportError as e:
                loguru.logger.error(f"ServerChan3导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"ServerChan3创建失败: {e}")

        # PushPlus
        if config.pushplus_token:
            try:
                from util.proxy.PushPlusUtil import PushPlusNotifier

                notifier = PushPlusNotifier(
                    token=config.pushplus_token,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("PushPlus", notifier)
            except ImportError as e:
                loguru.logger.error(f"PushPlus导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"PushPlus创建失败: {e}")

        # Bark
        if config.bark_token:
            try:
                from util.notifer.BarkUtil import BarkNotifier

                notifier = BarkNotifier(
                    token=config.bark_token,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Bark", notifier)
            except ImportError as e:
                loguru.logger.error(f"Bark导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Bark创建失败: {e}")

        # Ntfy
        if config.ntfy_url:
            try:
                from util.notifer.NtfyUtil import NtfyNotifier

                notifier = NtfyNotifier(
                    url=config.ntfy_url,
                    username=config.ntfy_username,
                    password=config.ntfy_password,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Ntfy", notifier)
            except ImportError as e:
                loguru.logger.error(f"Ntfy导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Ntfy创建失败: {e}")

        # MeoW
        if config.meow_nickname:
            try:
                from util.notifer.MeoWUtil import MeoWNotifier

                notifier = MeoWNotifier(
                    nickname=config.meow_nickname,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("MeoW", notifier)
            except ImportError as e:
                loguru.logger.error(f"MeoW导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"MeoW创建失败: {e}")

        # Audio
        if include_audio and config.audio_path:
            try:
                from util.notifer.AudioUtil import AudioNotifier

                notifier = AudioNotifier(
                    audio_path=config.audio_path,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Audio", notifier)
            except ImportError as e:
                loguru.logger.error(f"Audio导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Audio创建失败: {e}")

        return manager

    @staticmethod
    def test_all_notifiers(include_audio: bool = True) -> str:
        config = NotifierConfig.from_config_db()
        results = []

        test_manager = NotifierManager.create_from_config(
            config=config,
            title="抢票提醒",
            content="测试推送",
            include_audio=include_audio,
        )

        test_cases = [
            ("ServerChanTurbo", config.serverchan_key, "Server酱Turbo"),
            ("ServerChan3", config.serverchan3_api_url, "Server酱3"),
            ("PushPlus", config.pushplus_token, "PushPlus"),
            ("Bark", config.bark_token, "Bark"),
            ("Ntfy", config.ntfy_url, "Ntfy"),
            ("MeoW", config.meow_nickname, "MeoW"),
        ]
        if include_audio:
            test_cases.append(("Audio", config.audio_path, "音频通知"))

        for notifier_name, config_value, display_name in test_cases:
            if not config_value:
                results.append(f"⚠️ {display_name}: 未配置")
                continue

            if notifier_name in test_manager.notifier_dict:
                try:
                    notifier = test_manager.notifier_dict[notifier_name]
                    notifier.send_message(
                        "🎫 抢票测试", f"这是一条{display_name}测试推送消息"
                    )
                    results.append(f"✅ {display_name}: 测试推送已发送")
                except Exception as e:
                    results.append(f"❌ {display_name}: 推送失败 -> {e}")
            else:
                results.append(f"❌ {display_name}: 创建失败")

        return "\n".join(results)
