import json
import requests

from util.notifer.Notifier import NotifierBase, HTTPDeliveryError


class PushPlusNotifier(NotifierBase):
    def __init__(self, token, title, content, interval_seconds=10, duration_minutes=10):
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.token = token

    def send_message(self, title, message):
        url = "http://www.pushplus.plus/send"
        headers = {"Content-Type": "application/json"}
        data = {"token": self.token, "content": message, "title": title}
        resp = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
        if resp.status_code != 200:
            raise HTTPDeliveryError(
                f"PushPlus HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        # PushPlus 成功: {"code": 200, "msg": "success"}
        if body.get("code") != 200:
            raise HTTPDeliveryError(
                f"PushPlus 业务错误: {body.get('msg', body)}"
            )
