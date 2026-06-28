from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


class ProxyApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyApiResult:
    proxies: list[str]
    response: dict[str, Any]


def normalize_proxy_api_protocol(protocol: str | None) -> str:
    text = str(protocol or "http").strip().lower()
    if text in {"socks", "socks5"}:
        return "socks5"
    if text in {"https"}:
        return "https"
    return "http"


def build_proxy_api_url(api_url: str, *, count: int, protocol: str) -> str:
    target = str(api_url or "").strip()
    if not target:
        raise ProxyApiError("请先填写代理 API 地址")

    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["count"] = str(max(1, int(count)))
    query["format"] = "json"
    query["protocol"] = normalize_proxy_api_protocol(protocol)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query, doseq=True),
            parts.fragment,
        )
    )


def build_plain_proxy_url(api_url: str) -> str:
    """
    纯文本模式：返回原始 URL 不做任何修改（参数已内置在 URL 中）。
    """
    target = str(api_url or "").strip()
    if not target:
        raise ProxyApiError("请先填写代理 API 地址")
    return target


def _iter_proxy_items(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("proxy_list", "list", "proxies", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            if any(key in data for key in ("ip", "host", "port", "proxy")):
                return [data]
        elif isinstance(data, list):
            return data

        for key in ("proxy_list", "list", "proxies", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    if isinstance(payload, list):
        return payload
    return []


def _extract_host_port(item: Any) -> tuple[str, str] | None:
    if isinstance(item, dict):
        proxy_value = item.get("proxy") or item.get("addr") or item.get("address")
        if proxy_value:
            return _extract_host_port(str(proxy_value))

        host = item.get("ip") or item.get("host")
        port = item.get("port")
        if host and port:
            return str(host).strip(), str(port).strip()
        return None

    text = str(item or "").strip()
    if not text:
        return None
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if ":" not in text:
        return None
    host, port = text.rsplit(":", 1)
    return host.strip(), port.strip()


def parse_proxy_api_response(
    payload: dict[str, Any],
    *,
    protocol: str,
    username: str = "",
    password: str = "",
) -> list[str]:
    code = payload.get("code", payload.get("errno", 0))
    success = payload.get("success")
    if success is False or str(code) not in {"0", "200", "None"}:
        message = payload.get("msg") or payload.get("message") or payload
        raise ProxyApiError(f"代理 API 返回失败: {message}")

    norm = normalize_proxy_api_protocol(protocol)
    if norm == "socks5":
        scheme = "socks5"
    elif norm == "https":
        scheme = "https"
    else:
        scheme = "http"

    proxies: list[str] = []
    seen_hp: set[str] = set()
    for item in _iter_proxy_items(payload):
        host_port = _extract_host_port(item)
        if not host_port:
            continue
        host, port = host_port
        if not host or not port.isdigit():
            continue
        auth = f"{username}:{password}@" if username and password else ""
        proxy = f"{scheme}://{auth}{host}:{port}"
        hp_key = _hostport_key(proxy)
        if hp_key in seen_hp:
            continue
        seen_hp.add(hp_key)
        proxies.append(proxy)

    if not proxies:
        raise ProxyApiError("代理 API 返回成功，但没有解析到代理 IP 和端口")
    return proxies


def _hostport_key(proxy_url: str) -> str:
    """提取 scheme://host:port 作为去重键，忽略认证信息。"""
    text = proxy_url.strip().lower()
    if "://" not in text:
        return text
    scheme, remainder = text.split("://", 1)
    if "@" in remainder:
        remainder = remainder.rsplit("@", 1)[1]
    hostport = remainder.split("/")[0]  # 去掉路径
    return f"{scheme}://{hostport}"


def parse_plain_proxy_response(text: str) -> list[str]:
    """
    纯文本模式：每行一个完整代理 URL（如 socks5://user:pass@host:port）。
    按 host:port 去重（相同出口保留第一条）。
    """
    proxies: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # 验证是否有 scheme:// 结构
        if "://" not in line:
            continue
        key = _hostport_key(line)
        if key in seen:
            continue
        seen.add(key)
        proxies.append(line)
    if not proxies:
        raise ProxyApiError("纯文本代理 API 未返回有效的代理 URL")
    return proxies


def fetch_proxy_api(
    api_url: str,
    *,
    count: int,
    protocol: str,
    username: str = "",
    password: str = "",
    timeout: int = 15,
    format_type: str = "json",
) -> ProxyApiResult:
    if format_type == "plain":
        request_url = build_plain_proxy_url(api_url)
    else:
        request_url = build_proxy_api_url(api_url, count=count, protocol=protocol)

    response = requests.request(
        "GET", request_url, headers={}, data={}, timeout=timeout
    )
    response.raise_for_status()

    if format_type == "plain":
        proxies = parse_plain_proxy_response(response.text)
        return ProxyApiResult(proxies=proxies, response={"_raw": response.text})

    payload = response.json()
    if not isinstance(payload, dict):
        raise ProxyApiError("代理 API 未返回 JSON 对象")
    return ProxyApiResult(
        proxies=parse_proxy_api_response(
            payload, protocol=protocol, username=username, password=password
        ),
        response=payload,
    )
