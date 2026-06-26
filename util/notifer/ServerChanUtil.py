import json
import requests

from util.notifer.Notifier import NotifierBase, HTTPDeliveryError


class ServerChanTurboNotifier(NotifierBase):
    def __init__(self, token, title, content, interval_seconds=10, duration_minutes=10):
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.token = token

    def send_message(self, title, message):
        url = f"https://sctapi.ftqq.com/{self.token}.send"
        headers = {"Content-Type": "application/json"}
        data = {"desp": message, "title": title}
        resp = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
        if resp.status_code != 200:
            raise HTTPDeliveryError(
                f"ServerChanTurbo HTTP {resp.status_code}: {resp.text[:200]}"
            )
        # ServerChanTurbo 成功响应包含 {"code": 0}
        body = resp.json()
        if body.get("code") != 0:
            raise HTTPDeliveryError(
                f"ServerChanTurbo 业务错误: {body.get('message', body)}"
            )


class ServerChan3Notifier(NotifierBase):
    def __init__(self, api_url, title, content, interval_seconds=10, duration_minutes=10):
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.api_url = api_url

    def send_message(self, title, message):
        headers = {"Content-Type": "application/json"}
        data = {"title": title, "desp": message}
        resp = requests.post(self.api_url, headers=headers, data=json.dumps(data), timeout=10)
        if resp.status_code not in (200, 201, 202):
            raise HTTPDeliveryError(
                f"ServerChan3 HTTP {resp.status_code}: {resp.text[:200]}"
            )
