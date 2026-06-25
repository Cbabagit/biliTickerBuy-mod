import time

from util.Constant import (
    RATE_LIMIT_BACKOFF_BASE_MS,
    RATE_LIMIT_BACKOFF_MAX_MS,
)


class BiliRateLimitError(RuntimeError):
    """429 限流异常，携带退避信息。"""

    def __init__(self, message: str, *, response=None, consecutive_count: int = 1):
        super().__init__(message)
        self.response = response
        self.consecutive_count = consecutive_count  # 连续 429 次数
        self.backoff_ms = self._calc_backoff()

    def _calc_backoff(self) -> int:
        """指数退避：base * 2^(count-1), capped at max"""
        delay = RATE_LIMIT_BACKOFF_BASE_MS * (2 ** (self.consecutive_count - 1))
        return min(delay, RATE_LIMIT_BACKOFF_MAX_MS)

    @property
    def backoff_seconds(self) -> float:
        return self.backoff_ms / 1000.0

    def sleep_and_retry(self) -> None:
        time.sleep(self.backoff_seconds)
