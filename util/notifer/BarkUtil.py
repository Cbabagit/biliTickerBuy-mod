import json
import requests

from urllib.parse import urlparse
from util.notifer.Notifier import NotifierBase, HTTPDeliveryError


class BarkNotifier(NotifierBase):
    def __init__(self, token, title, content, interval_seconds=10, duration_minutes=10):
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.token = token

    def send_message(self, title, message):
        headers = {"Content-Type": "application/json"}
        data = {
            "icon": "https://raw.githubusercontent.com/mikumifa/biliTickerBuy/refs/heads/main/assets/icon.ico",
            "group": "biliTickerBuy",
            "url": "https://mall.bilibili.com/neul/index.html?page=box_me&noTitleBar=1",
            "sound": "telegraph",
            "level": "critical",
            "volume": "10",
        }
        if isinstance(self.token, str) and urlparse(self.token).scheme in {"http", "https"}:
            url = f"{self.token.rstrip('/')}/{title}/{message}"
        else:
            url = f"https://api.day.app/{self.token}/{title}/{message}"

        resp = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
        if resp.status_code not in (200, 201, 202):
            raise HTTPDeliveryError(f"Bark HTTP {resp.status_code}: {resp.text[:200]}")
