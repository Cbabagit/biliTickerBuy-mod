"""代理连通性测试工具 — 优化版

优化点:
- B站延迟 + 出口 IP 并行获取，消除串行等待
- 缩短超时：B站 5s / IP 2s
- 使用 httpx 代替 requests，原生支持 SOCKS5 带认证代理
- 高并发：默认 max_workers=10
- 排序 O(n)：哈希表映射，避免 index() 遍历
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from util.proxy.ProxyManager import ProxyManager


class ProxyTester:
    """代理连通性测试工具"""

    VALID_PROTOCOLS = ("http://", "https://", "socks5://", "socks4://")
    BILI_URL = "https://api.bilibili.com/x/web-interface/nav"
    BILI_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
        ),
        "Accept": "application/json",
    }

    def __init__(self, timeout: int = 5, ip_timeout: int = 2):
        self.timeout = timeout
        self.ip_timeout = ip_timeout

    @staticmethod
    def _validate_proxy_format(proxy: str) -> bool:
        proxy = proxy.strip()
        if not proxy:
            return False
        if not any(proxy.startswith(p) for p in ProxyTester.VALID_PROTOCOLS):
            return False
        remainder = proxy.split("://", 1)[1]
        return ":" in remainder

    def test_single_proxy(self, proxy: str) -> dict[str, Any]:
        """测试单个代理：并行跑 B站延迟 + 出口 IP 获取"""
        display = ProxyManager.mask_proxy_value(proxy) or proxy
        normalized = ProxyManager.normalize_proxy_value(proxy)
        # 解析代理主机名，绕过 Clash fake-IP
        proxy_for_conn = ProxyManager._resolve_proxy_host(normalized)
        if proxy_for_conn != normalized:
            display = ProxyManager.mask_proxy_value(proxy_for_conn) or proxy

        # 预创建结果容器
        result: dict[str, Any] = {
            "proxy": display,
            "status": "failed",
            "response_time": None,
            "error": None,
            "ip_info": None,
        }

        if normalized != "none" and not self._validate_proxy_format(normalized):
            result["error"] = "代理格式无效"
            return result

        # ---- 内部任务：B站延迟 (使用 httpx) ----
        def task_bili():
            nonlocal result
            try:
                client_kwargs: dict[str, Any] = {
                    "timeout": httpx.Timeout(self.timeout),
                }
                if proxy_for_conn != "none":
                    # httpx 原生支持 socks5:// 带认证
                    client_kwargs["proxy"] = proxy_for_conn

                with httpx.Client(**client_kwargs) as client:
                    start = time.monotonic()
                    resp = client.get(self.BILI_URL, headers=self.BILI_HEADERS)
                    elapsed = round((time.monotonic() - start) * 1000, 2)
                    if resp.status_code == 200:
                        result["status"] = "success"
                    else:
                        result["status"] = "partial"
                        result["error"] = f"B站连接失败 HTTP {resp.status_code}"
                    result["response_time"] = elapsed
            except httpx.TimeoutException:
                result["error"] = f"连接超时 (>{self.timeout}s)"
            except httpx.ProxyError:
                result["error"] = "代理服务器错误或无法连接"
            except httpx.ConnectError as e:
                err_str = str(e).lower()
                if "proxy" in err_str:
                    result["error"] = "代理连接失败"
                else:
                    result["error"] = "网络连接失败"
            except Exception as e:
                result["error"] = f"未知错误: {e}"

        # ---- 内部任务：出口 IP (使用 httpx) ----
        def task_ip():
            nonlocal result
            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(self.ip_timeout),
            }
            if proxy_for_conn != "none":
                client_kwargs["proxy"] = proxy_for_conn

            # httpbin.org 最轻量，优先用
            try:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get(
                        "https://httpbin.org/ip",
                        headers={"User-Agent": "curl/8.0"},
                    )
                    if resp.status_code == 200:
                        ip_raw = resp.json().get("origin", "")
                        result["ip_info"] = ip_raw
                        return
            except Exception:
                pass

            # fallback: ip-api.com（带位置/ISP 信息）
            try:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get("http://ip-api.com/json/")
                    if resp.status_code == 200:
                        data = resp.json()
                        ip = data.get("query", "未知")
                        parts = [ip]
                        if data.get("city"):
                            parts.append(data["city"])
                        if data.get("isp") and data["isp"] not in ("", ip):
                            parts.append(data["isp"])
                        result["ip_info"] = " ({})".format(", ".join(parts[1:])) if len(parts) > 1 else ip
                        return
            except Exception:
                pass

            if result.get("ip_info") is None:
                result["ip_info"] = "IP获取失败"

        # 并行执行
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_bili = pool.submit(task_bili)
            f_ip = pool.submit(task_ip)
            f_bili.result()
            f_ip.result()

        return result

    def test_proxy_list(self, proxy_string: str, max_workers: int = 10) -> list[dict[str, Any]]:
        """并发测试代理列表，保持输入顺序"""
        proxy_list = ProxyManager.parse_proxy_list(proxy_string, include_direct_fallback=True)
        if not proxy_list:
            proxy_list = ["none"]

        # 哈希表保存结果，保持输入顺序
        results: dict[str, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_map: dict[Any, str] = {
                executor.submit(self.test_single_proxy, p): p for p in proxy_list
            }
            for future in as_completed(fut_map):
                proxy = fut_map[future]
                try:
                    results[proxy] = future.result()
                except Exception as e:
                    results[proxy] = {
                        "proxy": ProxyManager.mask_proxy_value(proxy) or proxy,
                        "status": "failed",
                        "response_time": None,
                        "error": f"测试异常: {e}",
                        "ip_info": None,
                    }

        # 按输入顺序输出（O(n) 哈希查找）
        output: list[dict[str, Any]] = []
        for p in proxy_list:
            r = results.get(p)
            if r:
                output.append(r)

        return output

    @staticmethod
    def format_test_results(results: list[dict[str, Any]]) -> str:
        """格式化为可读文本"""
        lines: list[str] = []
        lines.append("代理连通性测试结果")
        lines.append("=" * 50)

        ok = 0
        for i, r in enumerate(results, 1):
            proxy = r["proxy"]
            status = r["status"]
            rt = r["response_time"]
            err = r["error"]
            ip = r["ip_info"]

            if status == "success":
                lines.append(f"✅ [{i}] {proxy}")
                lines.append(f"    响应时间: {rt}ms")
                if ip:
                    lines.append(f"    出口 IP: {ip}")
                ok += 1
            elif status == "partial":
                lines.append(f"⚠️  [{i}] {proxy}")
                lines.append(f"    响应时间: {rt}ms")
                if ip:
                    lines.append(f"    出口 IP: {ip}")
                lines.append(f"    警告: {err}")
            else:
                lines.append(f"❌ [{i}] {proxy}")
                lines.append(f"    错误: {err}")
            lines.append("")

        lines.append("=" * 50)
        lines.append(f"测试统计: {ok}/{len(results)} 个代理可用")
        return "\n".join(lines)


def test_proxy_connectivity(proxy_string: str = "none", timeout: int = 5) -> str:
    """便捷入口"""
    tester = ProxyTester(timeout=timeout)
    results = tester.test_proxy_list(proxy_string)
    return tester.format_test_results(results)
