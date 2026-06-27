"""代理管理器"""

from __future__ import annotations

import json
import re
import socket
import urllib.request

import requests

from util.proxy.ProxyState import ProxyStateRegistry

# Clash fake-IP CIDR 198.18.0.0/15
_FAKE_IP_RE = re.compile(r"^198\.18\.")


class ProxyManager:
    def __init__(
        self,
        proxy_string: str = "none",
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 180.0,
    ):
        self.proxy_list = self.parse_proxy_list(proxy_string)
        if not self.proxy_list:
            raise ValueError("at least have none proxy")
        self.state_registry = ProxyStateRegistry(
            self.proxy_list,
            mask_proxy=self.mask_proxy_value,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    @property
    def now_proxy_idx(self) -> int:
        return self.state_registry.current_index

    @now_proxy_idx.setter
    def now_proxy_idx(self, index: int) -> None:
        self.state_registry.set_current_index(index)

    @staticmethod
    def normalize_proxy_value(proxy: str) -> str:
        proxy = (proxy or "").strip()
        if not proxy:
            return ""
        if proxy.lower() in {"none", "direct"}:
            return "none"
        return proxy

    @classmethod
    def parse_proxy_list(
        cls, proxy_string: str | None, include_direct_fallback: bool = False
    ) -> list[str]:
        proxy_list = []
        if proxy_string:
            proxy_list = [
                cls.normalize_proxy_value(item)
                for item in proxy_string.split(",")
                if item and item.strip()
            ]

        normalized: list[str] = []
        seen: set[str] = set()
        for item in proxy_list:
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

        if include_direct_fallback and "none" not in seen:
            normalized.insert(0, "none")

        return normalized

    @staticmethod
    def mask_proxy_value(proxy: str) -> str:
        proxy = (proxy or "").strip()
        if not proxy:
            return ""
        if proxy.lower() in {"none", "direct"}:
            return "直连"
        if "://" not in proxy:
            return proxy

        scheme, remainder = proxy.split("://", 1)
        if "@" not in remainder:
            return proxy

        _, host_part = remainder.rsplit("@", 1)
        return f"{scheme}://***:***@{host_part}"

    @classmethod
    def mask_proxy_string(cls, proxy_string: str | None) -> str:
        proxies = cls.parse_proxy_list(proxy_string)
        masked = [cls.mask_proxy_value(proxy) for proxy in proxies]
        return ",".join(item for item in masked if item)

    @property
    def current_proxy(self) -> str:
        return self.proxy_list[self.now_proxy_idx]

    @property
    def current_proxy_display(self) -> str:
        return self.mask_proxy_value(self.current_proxy)

    def current_proxy_status(self) -> str:
        return self.state_registry.current_status_text()

    def proxy_pool_status(self) -> str:
        return self.state_registry.describe_all_states()

    def replace_proxy_list(self, proxy_string: str) -> None:
        proxy_list = self.parse_proxy_list(proxy_string)
        if not proxy_list:
            raise ValueError("at least have none proxy")
        self.proxy_list = proxy_list
        self.state_registry = ProxyStateRegistry(
            self.proxy_list,
            mask_proxy=self.mask_proxy_value,
            failure_threshold=self.state_registry.failure_threshold,
            cooldown_seconds=self.state_registry.cooldown_seconds,
        )

    def snapshot(self) -> int:
        return self.now_proxy_idx

    def restore(self, index: int) -> None:
        self.now_proxy_idx = index

    @staticmethod
    def _resolve_via_doh(host: str) -> str | None:
        """通过 DNS-over-HTTPS 解析域名，绕过 Clash fake-IP。"""
        doh_urls = [
            f"https://dns.google/resolve?name={host}&type=A",
            f"https://cloudflare-dns.com/dns-query?name={host}&type=A",
        ]
        for url in doh_urls:
            try:
                req = urllib.request.Request(url)
                if "cloudflare" in url:
                    req.add_header("Accept", "application/dns-json")
                resp = urllib.request.urlopen(req, timeout=3)
                data = json.loads(resp.read().decode())
                for answer in data.get("Answer", []):
                    if answer.get("type") != 1:
                        continue  # 只取 A 记录，跳过 CNAME
                    ip = answer.get("data", "")
                    if ip and not _FAKE_IP_RE.match(ip):
                        return ip
            except Exception:
                continue
        return None

    @staticmethod
    def _resolve_proxy_host(proxy: str) -> str:
        """解析代理 URL 中的主机名：Clash fake-IP 污染时替换为真实 IP。"""
        if not proxy or proxy == "none" or "://" not in proxy:
            return proxy

        scheme, remainder = proxy.split("://", 1)
        userinfo_host, _, _ = remainder.partition("/")
        if "@" in userinfo_host:
            _, real_host_port = userinfo_host.rsplit("@", 1)
            userinfo = remainder.rsplit("@", 1)[0]
            prefix = f"{userinfo}@"
        else:
            real_host_port = userinfo_host
            prefix = ""

        if ":" in real_host_port:
            host, port = real_host_port.rsplit(":", 1)
        else:
            host = real_host_port
            port = None

        # 已经是 IP 地址
        if host and (host[0].isdigit() or host[0] == "["):
            return proxy

        # 检查系统 DNS 是否返回 fake-IP
        fake = False
        try:
            for addr in socket.getaddrinfo(host, None):
                ip = addr[4][0]
                if _FAKE_IP_RE.match(ip):
                    fake = True
                else:
                    fake = False
                    break
        except OSError:
            fake = True

        if not fake:
            return proxy

        # 通过 DoH 拿到真实 IP
        real_ip = ProxyManager._resolve_via_doh(host)
        if real_ip:
            resolved_port = port or "443"
            return f"{scheme}://{prefix}{real_ip}:{resolved_port}"

        # DoH 也失败，只能返回原 URL（会报错但至少不会静默失败）
        return proxy

    def apply_to_session(self, session: requests.Session) -> None:
        session.trust_env = False
        if self.current_proxy == "none":
            session.proxies = {}
            return
        resolved = self._resolve_proxy_host(self.current_proxy)
        session.proxies = {
            "http": resolved,
            "https": resolved,
        }

    def rotate(self) -> bool:
        return self.state_registry.switch_to_next_available()

    def ensure_current_available(self) -> bool:
        return self.state_registry.ensure_current_available()

    def has_available_proxy(self) -> bool:
        return self.state_registry.has_available_proxy()

    def is_current_proxy_available(self) -> bool:
        return self.state_registry.is_current_available()

    def mark_current_success(self) -> None:
        self.state_registry.record_current_success()

    def mark_current_failure(self, reason: str) -> bool:
        return self.state_registry.record_current_failure(reason)
