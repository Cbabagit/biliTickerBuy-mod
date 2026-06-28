from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Iterable

import ntplib
from loguru import logger

# NTP 服务器优先级：阿里云 → 国家授时中心
NTP_SERVERS = ["ntp1.aliyun.com", "ntp.ntsc.ac.cn"]


def sync_ntp(max_retries_per_server: int = 3) -> float:
    """逐次尝试 NTP 服务器，返回 timeoffset（秒，本地时间 - NTP 时间）。"""
    client = ntplib.NTPClient()
    for server in NTP_SERVERS:
        for attempt in range(max_retries_per_server):
            try:
                response = client.request(server, version=4)
                offset = -response.offset  # response.offset = NTP - local, 取反
                logger.info(
                    f"NTP 时间同步成功: {server}，偏差 {offset:.3f} 秒"
                )
                return offset
            except Exception:
                logger.warning(f"NTP {server} 第 {attempt + 1} 次失败")
                if attempt < max_retries_per_server - 1:
                    time.sleep(0.5)
    logger.warning("NTP 时间同步全部失败，使用本地时间")
    return 0.0


def current_time_ms(*, timeoffset: float = 0, base_ms: int | None = None) -> int:
    """
    Return a timeoffset-aware millisecond timestamp.
    """
    if base_ms is None:
        base_ms = int(time.time() * 1000)
    return int(base_ms + timeoffset * 1000)


class TimeUtil:
    def __init__(self, _ntp_server: str | None = None) -> None:
        self.client = ntplib.NTPClient()
        self.timeoffset: float = 0

        if _ntp_server:
            self.ntp_servers = [_ntp_server]
        else:
            self.ntp_servers = list(NTP_SERVERS)

    def compute_timeoffset(self) -> str:
        for server in self.ntp_servers:
            for i in range(3):
                try:
                    response = self.client.request(server, version=4)
                    offset = -response.offset
                    logger.info(f"NTP 时间同步成功: {server}，偏差 {offset:.3f} 秒")
                    return format(offset, ".5f")
                except Exception:
                    logger.warning(
                        f"NTP {server} 第 {i + 1} 次获取失败"
                    )
                    if i == 2:
                        break
                    time.sleep(0.5)
        logger.warning("NTP 时间同步全部失败，使用本地时间")
        return "error"

    def set_timeoffset(self, _timeoffset: str) -> None:
        if _timeoffset == "error":
            self.timeoffset = 0
            logger.warning("NTP时间同步失败, 使用本地时间")
        else:
            self.timeoffset = float(_timeoffset)

    def get_timeoffset(self) -> float:
        return self.timeoffset
