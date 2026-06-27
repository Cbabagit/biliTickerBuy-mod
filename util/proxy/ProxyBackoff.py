from __future__ import annotations

import random


class ProxyBackoff:
    def __init__(
        self,
        *,
        min_seconds: float = 0.25,
        max_seconds: float = 0.45,
    ):
        self.min_seconds = max(0.0, float(min_seconds))
        self.max_seconds = max(self.min_seconds, float(max_seconds))
        self.exhausted_rounds = 0
        self.notification_sent = False

    def next_delay_seconds(self) -> float:
        self.exhausted_rounds += 1
        return random.uniform(self.min_seconds, self.max_seconds)

    def reset(self) -> None:
        self.exhausted_rounds = 0
        self.notification_sent = False

    def should_notify(self) -> bool:
        if self.notification_sent:
            return False
        self.notification_sent = True
        return True
