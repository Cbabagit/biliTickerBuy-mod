"""
Clash 节点切换模块（B站延迟择优，O(n) 算法）
=============================================
所有选择算法均为 O(n) 线性扫描，无全量排序，无评分系统。
延迟来自实时 B站 ping 测试。
支持多实例 (instance_id=0 -> VergeRev 主实例 :9097, instance_id=N -> 子实例 :18090+N)
"""

import heapq
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from loguru import logger

# B站 ping 端点
BILIBILI_PING_URL = "https://api.bilibili.com/x/web-interface/online"

# ── 多实例 API 路由 ──


def _get_instance_port(instance_id: int) -> int:
    if instance_id == 0:
        try:
            from util import ConfigDB

            url = (ConfigDB.get("clash.api_url") or "http://127.0.0.1:9097").rstrip("/")
            import re

            m = re.search(r":(\d+)$", url)
            return int(m.group(1)) if m else 9097
        except (ImportError, Exception):
            return 9097
    else:
        return 18090 + instance_id


def _get_secret() -> str:
    try:
        from util import ConfigDB

        return ConfigDB.get("clash.api_secret") or "set-your-secret"
    except (ImportError, Exception):
        return "set-your-secret"


def _req(
    instance_id: int, method: str, path: str, body: Any = None, timeout: int = 5
) -> Any:
    port = _get_instance_port(instance_id)
    secret = _get_secret()
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"[ClashSwitcher:{instance_id}] API call failed: {e}")
        return None


# ── 节点追踪器（仅记录：当前节点 + 黑名单）──


class NodeTracker:
    """线程安全的轻量追踪器。只跟踪当前节点和被封禁的节点，无历史/评分。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._current: dict[int, tuple[str | None, str | None]] = {}  # id -> (group, node)
        self._banned_until: dict[str, float] = {}
        self._last_switch_time: float = 0.0

    def record_failure(self, instance_id: int, node: str):
        with self._lock:
            self._banned_until[node] = time.time() + 300
            logger.info(f"[ClashSwitcher:{instance_id}] {node} 失败，禁用 5 分钟")

    def set_current(self, instance_id: int, group: str, node: str):
        with self._lock:
            self._current[instance_id] = (group, node)
            self._last_switch_time = time.time()

    def get_current(self, instance_id: int) -> tuple[str | None, str | None]:
        with self._lock:
            return self._current.get(instance_id, (None, None))

    def is_banned(self, node: str) -> bool:
        with self._lock:
            expiry = self._banned_until.get(node, 0)
            return time.time() < expiry

    def get_banned_snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._banned_until)

    def clear_bans(self):
        with self._lock:
            self._banned_until.clear()

    def reset(self):
        with self._lock:
            self._current.clear()
            self._banned_until.clear()


_tracker = NodeTracker()


# ── 节点过滤辅助 ──


def _extract_region(node: str) -> str | None:
    regions = {
        "日本": ["日本", "东京", "大阪", "樱花"],
        "香港": ["香港", "HKT", "CMI"],
        "韩国": ["韩国", "首尔", "SK"],
        "新加坡": ["新加坡", "SG", "GTT"],
        "美国": ["美国", "洛杉矶", "硅谷", "圣何塞"],
        "台湾": ["台湾", "台北"],
        "英国": ["英国", "伦敦"],
        "德国": ["德国", "法兰克福"],
        "法国": ["法国", "巴黎"],
        "澳门": ["澳门"],
        "泰国": ["泰国", "曼谷"],
        "越南": ["越南", "河内"],
        "俄罗斯": ["俄罗斯", "莫斯科"],
    }
    for region, keywords in regions.items():
        if any(k in node for k in keywords):
            return region
    return None


def _is_real_proxy_node(name: str) -> bool:
    if "：" in name:
        return False
    for c in name:
        cp = ord(c)
        if 0x1F1E6 <= cp <= 0x1F1FF:
            return True
    if _extract_region(name):
        return True
    if any(kw in name for kw in ("-", "|", "·", "IPLC", "IEPL", "BGP", "CN2", "GIA")):
        return True
    return False


# ── 实时延迟探测（B站 ping）──


def _filter_real_nodes(
    instance_id: int, group: str | None = None, all_nodes: list[str] | None = None
) -> tuple[str | None, list[str], dict[str, dict]]:
    """获取代理组、真实节点列表、节点信息 dict。O(n)."""
    if group is None or all_nodes is None:
        group, all_nodes = get_proxy_group(instance_id, group)
    if not group or not all_nodes:
        return None, [], {}

    proxies_data = _req(instance_id, "GET", "/proxies")
    all_proxies = {}
    if proxies_data:
        for name, info in proxies_data.get("proxies", {}).items():
            if info.get("type") not in (
                "Selector", "URLTest", "Fallback", "Direct", "Reject",
                "RejectDrop", "Compatible", "Pass",
            ):
                all_proxies[name] = info

    sub_group_set = {n for n in all_nodes if n not in all_proxies}
    real = [n for n in all_nodes if n not in sub_group_set and _is_real_proxy_node(n)]
    return group, real, all_proxies


def _live_ping(
    instance_id: int,
    nodes: list[str],
    timeout_ms: int = 8000,
    url: str = BILIBILI_PING_URL,
) -> dict[str, int]:
    """并发 ping 所有节点，返回 {节点名: 延迟ms}。并发 O(n)，空间 O(n)。"""
    if not nodes:
        return {}

    import concurrent.futures

    secret = _get_secret()
    port = _get_instance_port(instance_id)
    results: dict[str, int] = {}

    def _test(name: str) -> tuple[str, int]:
        try:
            enc = urllib.parse.quote(name, safe="")
            full_url = (
                f"http://127.0.0.1:{port}/proxies/{enc}/delay"
                f"?timeout={timeout_ms}&url={urllib.parse.quote(url, safe='')}"
            )
            req = urllib.request.Request(
                full_url, headers={"Authorization": f"Bearer {secret}"}
            )
            with urllib.request.urlopen(req, timeout=timeout_ms // 1000 + 2) as resp:
                d = json.loads(resp.read().decode("utf-8")).get("delay", 9999)
                return name, d if d < 9999 else 9999
        except Exception:
            return name, 9999

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(10, len(nodes) // 5 + 1)) as pool:
        for name, delay in pool.map(_test, nodes):
            results[name] = delay

    return results


def test_all_delays(
    instance_id: int = 0,
    group: str | None = None,
    timeout: int = 8000,
    url: str = BILIBILI_PING_URL,
) -> dict[str, int]:
    """对代理组内所有节点进行实时 B站 延迟测试。O(n) 并发。"""
    _, real, _ = _filter_real_nodes(instance_id, group)
    if not real:
        return {}
    return _live_ping(instance_id, real, timeout_ms=timeout, url=url)


# ── 公共查询 ──


def is_api_available(instance_id: int = 0) -> bool:
    data = _req(instance_id, "GET", "/version")
    return data is not None and "version" in data


def get_proxy_group(
    instance_id: int = 0, group_name: str | None = None
) -> tuple[str | None, list[str]]:
    if group_name:
        enc = urllib.parse.quote(group_name, safe="")
        data = _req(instance_id, "GET", f"/proxies/{enc}")
        if data and data.get("all"):
            return group_name, data["all"]
        return None, []

    data = _req(instance_id, "GET", "/proxies")
    if not data:
        return None, []

    for name, proxy in data.get("proxies", {}).items():
        if proxy.get("type") == "Selector":
            if instance_id > 0 and name == "GLOBAL":
                continue
            enc = urllib.parse.quote(name, safe="")
            detail = _req(instance_id, "GET", f"/proxies/{enc}")
            if detail and detail.get("all"):
                return name, detail["all"]
    return None, []


def get_status_text(instance_id: int = 0) -> str:
    group, node = _tracker.get_current(instance_id)
    if not group:
        return f"[{instance_id}] Clash 自动切换: 未使用"
    return f"[{instance_id}] {group} -> {node}"


def get_ranked_list(instance_id: int = 0, group: str | None = None) -> str:
    """返回延迟最低的 10 个节点文本。使用 heapq.nsmallest，O(n) 时间 + O(k) 空间。"""
    _, real, info = _filter_real_nodes(instance_id, group)
    if not real:
        return "无法获取节点列表"

    delays = _live_ping(instance_id, real)
    if not delays:
        return "延迟测试失败"

    # heapq.nsmallest(k, iterable, key) — O(n log k) = O(n) 当 k=10
    alive = [(n, delays.get(n, 9999)) for n in real if delays.get(n, 9999) < 9999]
    top10 = heapq.nsmallest(10, alive, key=lambda x: x[1])

    lines = [f"节点延迟排名 (实例{instance_id}, B站 ping):"]
    for i, (node, d) in enumerate(top10):
        ban = " BANNED" if _tracker.is_banned(node) else ""
        color = "UP" if d <= 200 else "OK" if d <= 500 else "SLOW"
        lines.append(f"  {i + 1}. [{color}] {node} [{d}ms]{ban}")

    dead = len(real) - len(alive)
    if dead:
        lines.append(f"  ... {len(alive)} 可达, {dead} 超时/不可达")
    return "\n".join(lines)


def switch_node(instance_id: int, group: str, node: str) -> tuple[bool, str]:
    enc = urllib.parse.quote(group, safe="")
    result = _req(instance_id, "PUT", f"/proxies/{enc}", {"name": node})
    if result is None:
        return False, "切换失败: Clash API 无响应"
    if result == {}:
        _tracker.set_current(instance_id, group, node)
        return True, f"已切换到: {node}"
    if isinstance(result, dict) and "message" in result:
        return False, f"切换失败: {result['message']}"
    return False, f"切换失败: {result}"


def _get_instance_from_env() -> int:
    try:
        return int(os.environ.get("BTB_CLASH_INSTANCE", "0"))
    except (ValueError, TypeError):
        return 0


def record_success(instance_id: int | None = None):
    if instance_id is None:
        instance_id = _get_instance_from_env()


# ── 自动开关配置 ──

CFG_AUTO_MAIN = "clash.auto_switch_main"
CFG_AUTO_CHILD = "clash.auto_switch_child"


def is_auto_switch_enabled(instance_id: int = 0) -> bool:
    try:
        from util import ConfigDB

        key = CFG_AUTO_MAIN if instance_id == 0 else CFG_AUTO_CHILD
        val = ConfigDB.get(key)
        return val == "1" if val else True
    except Exception:
        return True


# ── 跨实例反亲和性（只用于子实例 > 0）──


def _get_other_instances_current_node(own_instance_id: int) -> set[str]:
    """O(num_instances) 扫描，遇到连续 3 个无响应端口自动停止。"""
    used: set[str] = set()
    missing = 0
    for iid in range(1, 31):
        if iid == own_instance_id:
            continue
        try:
            data = _req(iid, "GET", "/version")
            if not data or "version" not in data:
                missing += 1
                if missing >= 3:
                    break
                continue
        except Exception:
            missing += 1
            if missing >= 3:
                break
            continue
        missing = 0
        try:
            grp, _ = get_proxy_group(iid)
            if grp:
                enc = urllib.parse.quote(grp, safe="")
                data = _req(iid, "GET", f"/proxies/{enc}")
                if data and data.get("now"):
                    used.add(data["now"])
        except Exception:
            pass
    return used


def _best_candidate_by_delay(
    delays: dict[str, int],
    current: str | None,
    other_used: set[str],
    banned_nodes: dict[str, float],
) -> str | None:
    """找 ≤500ms 且未被其他实例占用的最低延迟节点。无回退。"""
    best, best_delay = None, 99999
    now = time.time()
    for node, d in delays.items():
        if d >= 500 or d >= 9999:
            continue
        if node == current:
            continue
        expiry = banned_nodes.get(node, 0)
        if now < expiry:
            continue
        if node in other_used:
            continue
        if d < best_delay:
            best, best_delay = node, d
    return best


def switch_on_failure(
    instance_id: int = 0, group: str | None = None
) -> tuple[bool, str, str]:
    if not is_auto_switch_enabled(instance_id):
        return False, "自动切换已禁用", ""

    _, current_node = _tracker.get_current(instance_id)

    if current_node:
        _tracker.record_failure(instance_id, current_node)
        logger.info(f"[ClashSwitcher:{instance_id}] 标记节点失败: {current_node}")

    group, real, _ = _filter_real_nodes(instance_id, group)
    if not group or not real:
        return False, "未找到可用的代理组", ""

    delays = _live_ping(instance_id, real)
    if not delays:
        return False, "延迟测试失败", current_node or ""

    other_used = _get_other_instances_current_node(instance_id) if instance_id > 0 else set()
    if other_used:
        logger.info(f"[ClashSwitcher:{instance_id}] 其他实例占用节点: {other_used}")

    best = _best_candidate_by_delay(
        delays,
        current_node,
        other_used,
        _tracker.get_banned_snapshot(),
    )
    if not best:
        return False, "没有替代节点可用", current_node or ""

    ok, msg = switch_node(instance_id, group, best)
    return ok, msg, best


def switch_best_comprehensive(
    instance_id: int = 0, group: str | None = None
) -> tuple[bool, str]:
    group, real, _ = _filter_real_nodes(instance_id, group)
    if not group or not real:
        return False, "未找到可用的代理组"

    delays = _live_ping(instance_id, real)
    if not delays:
        return False, "延迟测试失败"

    _, current_node = _tracker.get_current(instance_id)
    other_used = _get_other_instances_current_node(instance_id) if instance_id > 0 else set()

    best = _best_candidate_by_delay(
        delays, current_node, other_used, {})
    if not best:
        return False, "没有可用的节点"

    return switch_node(instance_id, group, best)


def reset_tracker():
    _tracker.reset()
