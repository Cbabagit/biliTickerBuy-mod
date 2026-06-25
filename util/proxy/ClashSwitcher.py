"""
Clash 节点智能切换模块
========================
综合评分：延迟 + 历史成功率 + 节点新鲜度 + 地区多样性
支持多实例 (instance_id=0 → VergeRev 主实例 :9097, instance_id=N → 子实例 :18090+N)
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ── 多实例 API 路由 ──

_INSTANCE_PORTS = {0: 9097}  # instance_id → api_port


def _get_instance_port(instance_id: int) -> int:
    """获取实例的 API 端口"""
    if instance_id == 0:
        # 主实例 (VergeRev)
        try:
            from util import ConfigDB

            url = (ConfigDB.get("clash.api_url") or "http://127.0.0.1:9097").rstrip("/")
            import re

            m = re.search(r":(\d+)$", url)
            return int(m.group(1)) if m else 9097
        except (ImportError, Exception):
            return 9097
    else:
        # 子实例 (ClashInstanceManager 分配)
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
                return {}  # 204 No Content / 空 200 body = 成功
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"[ClashSwitcher:{instance_id}] API call failed: {e}")
        return None


# ── 节点历史记录 ──


@dataclass
class NodeHistory:
    """单个节点使用历史"""

    node: str
    successes: int = 0
    failures: int = 0
    last_seen: float = 0.0
    recent_delays: deque[int] = field(default_factory=lambda: deque(maxlen=10))
    failure_timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=5))

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        if total == 0:
            return 0.5
        return self.successes / total

    @property
    def avg_delay(self) -> int:
        if not self.recent_delays:
            return 9999
        return int(sum(self.recent_delays) / len(self.recent_delays))

    def record_success(self, delay: int = 0):
        self.successes += 1
        self.last_seen = time.time()
        if delay > 0:
            self.recent_delays.append(delay)

    def record_failure(self):
        self.failures += 1
        self.last_seen = time.time()
        self.failure_timestamps.append(self.last_seen)

    def recent_failure_count(self, window: float = 300) -> int:
        now = time.time()
        return sum(1 for t in self.failure_timestamps if now - t < window)


class NodeTracker:
    """全局节点追踪器（线程安全，支持多实例）"""

    def __init__(self):
        self._lock = threading.Lock()
        # instance_id -> {node: NodeHistory}
        self._histories: dict[int, dict[str, NodeHistory]] = defaultdict(dict)
        # instance_id -> (group, node)
        self._current: dict[int, tuple[str | None, str | None]] = defaultdict(
            lambda: (None, None)
        )
        self._banned_until: dict[str, float] = {}
        self._last_switch_time: float = 0.0

    def get_or_create(self, instance_id: int, node: str) -> NodeHistory:
        with self._lock:
            if node not in self._histories[instance_id]:
                self._histories[instance_id][node] = NodeHistory(node=node)
            return self._histories[instance_id][node]

    def record_success(self, instance_id: int, node: str, delay: int = 0):
        with self._lock:
            h = self.get_or_create(instance_id, node)
            h.record_success(delay)

    def record_failure(self, instance_id: int, node: str):
        with self._lock:
            h = self.get_or_create(instance_id, node)
            h.record_failure()
            if h.recent_failure_count(600) >= 3:
                self._banned_until[node] = time.time() + 300
                logger.info(
                    f"[ClashSwitcher:{instance_id}] {node} 连续失败 3 次，禁用 5 分钟"
                )

    def set_current(self, instance_id: int, group: str, node: str):
        with self._lock:
            self._current[instance_id] = (group, node)
            self._last_switch_time = time.time()

    def get_current(self, instance_id: int) -> tuple[str | None, str | None]:
        with self._lock:
            return self._current[instance_id]

    def is_banned(self, node: str) -> bool:
        with self._lock:
            expiry = self._banned_until.get(node, 0)
            return time.time() < expiry

    def clear_bans(self):
        with self._lock:
            self._banned_until.clear()

    # 地区权重加分（香港/澳门/韩国/日本优先）
    _REGION_BONUS = {
        "香港": 15,
        "澳门": 15,
        "韩国": 10,
        "日本": 10,
        "新加坡": 8,
        "台湾": 12,
    }

    def score_node(
        self,
        instance_id: int,
        node: str,
        proxy_info: dict | None = None,
        group_proxies: list[str] | None = None,
    ) -> float:
        if self.is_banned(node):
            return 0.0

        if not proxy_info or not proxy_info.get("alive", False):
            return 0.0

        score = 0.0

        # 1. 延迟评分 (0-40)
        delay = 9999
        try:
            extra = proxy_info.get("extra") or {}
            hist = extra.get("history", [])
            if hist:
                delay = int(hist[-1]["delay"])
        except (IndexError, KeyError, ValueError, TypeError):
            pass
        delay_score = max(0, 40 - 40 * (delay / 5000))
        score += delay_score

        # 2. 历史成功率 (0-30)
        h = self.get_or_create(instance_id, node)
        success_rate = h.success_rate
        score += success_rate * 30

        # 3. 新鲜度 (0-20)
        freshness = 20.0
        recent_fails = h.recent_failure_count(600)
        freshness -= recent_fails * 5
        with self._lock:
            current_node = self._current[instance_id][1]
            if node == current_node:
                freshness -= 10
        score += max(0, freshness)

        # 4. 地区多样性 (0-10)
        region_score = 5.0
        with self._lock:
            current_node = self._current[instance_id][1]
        if current_node and group_proxies:
            current_region = _extract_region(current_node)
            candidate_region = _extract_region(node)
            if (
                current_region
                and candidate_region
                and current_region != candidate_region
            ):
                region_score = 10.0
            elif (
                current_region
                and candidate_region
                and current_region == candidate_region
            ):
                region_score = 2.0
        score += region_score

        # 5. 地区偏好加分 — 香港/澳门/韩国/日本优先 (0-15)
        region = _extract_region(node)
        if region and region in self._REGION_BONUS:
            score += self._REGION_BONUS[region]

        return round(min(100, max(0, score)), 1)

    def reset(self):
        with self._lock:
            self._histories.clear()
            self._current.clear()
            self._banned_until.clear()


# ── 单例 ──
_tracker = NodeTracker()


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
    """判断节点名是否为真实代理节点（排除 info 类展示行）"""
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


# ── 公开 API（全部接受 instance_id 参数）──


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
            # 子实例优先使用业务组 (buy-N)，跳过 GLOBAL（自动创建的父组）
            if instance_id > 0 and name == "GLOBAL":
                continue
            enc = urllib.parse.quote(name, safe="")
            detail = _req(instance_id, "GET", f"/proxies/{enc}")
            if detail and detail.get("all"):
                return name, detail["all"]
    return None, []


def get_comprehensive_scores(
    instance_id: int = 0,
    group: str | None = None,
    all_nodes: list[str] | None = None,
) -> list[tuple[str, float, int, bool]]:
    if group is None or all_nodes is None:
        group, all_nodes = get_proxy_group(instance_id, group)
    if not group or not all_nodes:
        return []

    proxies_data = _req(instance_id, "GET", "/proxies")
    if not proxies_data:
        return []

    all_proxies = {}
    for name, info in proxies_data.get("proxies", {}).items():
        if info.get("type") not in (
            "Selector",
            "URLTest",
            "Fallback",
            "Direct",
            "Reject",
            "RejectDrop",
            "Compatible",
            "Pass",
        ):
            all_proxies[name] = info

    sub_groups = [n for n in all_nodes if n not in all_proxies]
    real_nodes = [n for n in all_nodes if n not in sub_groups]
    real_nodes = [n for n in real_nodes if _is_real_proxy_node(n)]

    scored = []
    for node in real_nodes:
        info = all_proxies.get(node)
        score = _tracker.score_node(instance_id, node, info, real_nodes)
        delay = 9999
        alive = info.get("alive", False) if info else False
        if alive and info:
            try:
                hist = info.get("extra", {}).get("history", [])
                if hist:
                    delay = int(hist[-1]["delay"])
            except (IndexError, KeyError, ValueError, TypeError):
                pass
        scored.append((node, score, delay, alive))

    scored.sort(key=lambda x: (-x[1], x[3], x[2]))
    return scored


def switch_node(instance_id: int, group: str, node: str) -> tuple[bool, str]:
    enc = urllib.parse.quote(group, safe="")
    body = {"name": node}
    result = _req(instance_id, "PUT", f"/proxies/{enc}", body)
    if result is None:
        # _req 返回 None = API 请求失败（网络错误 / HTTP 错误）
        return False, "切换失败: Clash API 无响应"
    if result == {}:
        # 空响应 = 切换成功（Clash 返回 204 No Content）
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
    _, node = _tracker.get_current(instance_id)
    if node:
        _tracker.record_success(instance_id, node)


def get_status_text(instance_id: int = 0) -> str:
    group, node = _tracker.get_current(instance_id)
    if not group:
        return f"[{instance_id}] Clash 自动切换: 未使用"
    return f"[{instance_id}] {group} -> {node}"


def get_ranked_list(instance_id: int = 0, group: str | None = None) -> str:
    group, all_nodes = get_proxy_group(instance_id, group)
    if not group or not all_nodes:
        return "无法获取节点列表"

    scored = get_comprehensive_scores(instance_id, group, all_nodes)
    if not scored:
        return "无法评分"

    lines = [f"节点评分排名 (实例{instance_id}, {group}):"]
    for i, (node, score, delay, alive) in enumerate(scored[:10]):
        status = "UP" if alive else "DOWN"
        ban = " BANNED" if _tracker.is_banned(node) else ""
        lines.append(f"  {i + 1}. [{status}] {node} [{delay}ms] {score}分{ban}")
    if len(scored) > 10:
        lines.append(f"  ... 共 {len(scored)} 个节点")
    return "\n".join(lines)


# ── 自动开关配置 ──

CFG_AUTO_MAIN = "clash.auto_switch_main"
CFG_AUTO_CHILD = "clash.auto_switch_child"


def is_auto_switch_enabled(instance_id: int = 0) -> bool:
    """检查该实例的自动切换是否启用（从 ConfigDB 读取）"""
    try:
        from util import ConfigDB

        if instance_id == 0:
            val = ConfigDB.get(CFG_AUTO_MAIN)
        else:
            val = ConfigDB.get(CFG_AUTO_CHILD)
        return val == "1" if val else True  # 默认启用
    except Exception:
        return True


# ── 跨实例反亲和性 ──


def _get_other_instances_current_node(own_instance_id: int) -> set[str]:
    """查询其他所有在线实例当前选中的节点，返回集合
    只遍历有 API 响应的实例，避免查询不存在端口造成 502 日志噪音
    """
    used = set()
    # 动态探测可用实例：从 1 开始，遇到连续 3 个端口无响应就停止
    missing = 0
    max_live_instances = 30  # 安全上限
    for iid in range(1, max_live_instances + 1):
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
        missing = 0  # 在线实例，重置缺失计数
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


def switch_on_failure(
    instance_id: int = 0, group: str | None = None
) -> tuple[bool, str, str]:
    # 检查自动切换开关
    if not is_auto_switch_enabled(instance_id):
        return False, "自动切换已禁用", ""

    _, current_node = _tracker.get_current(instance_id)

    if current_node:
        _tracker.record_failure(instance_id, current_node)
        logger.info(f"[ClashSwitcher:{instance_id}] 标记节点失败: {current_node}")

    group, all_nodes = get_proxy_group(instance_id, group)
    if not group or not all_nodes:
        return False, "未找到可用的代理组", ""

    scored = get_comprehensive_scores(instance_id, group, all_nodes)

    # 反亲和性：跳过已被其他实例占用的节点
    other_used = _get_other_instances_current_node(instance_id)
    if other_used:
        logger.info(f"[ClashSwitcher:{instance_id}] 其他实例占用节点: {other_used}")

    candidates = []
    for node, score, delay, alive in scored:
        if not alive:
            continue
        if node == current_node:
            continue
        if _tracker.is_banned(node):
            continue
        if node in other_used:
            continue  # 跳过其他实例正在用的节点
        candidates.append((node, score, delay))

    # 如果所有可用节点都被占用了，退回到选分最高的
    if not candidates:
        logger.info("[ClashSwitcher] 所有高优选节点被占用，退回普通选择")
        for node, score, delay, alive in scored:
            if not alive or node == current_node or _tracker.is_banned(node):
                continue
            candidates.append((node, score, delay))

    if not candidates:
        return False, "没有替代节点可用", current_node or ""

    best = candidates[0]
    ok, msg = switch_node(instance_id, group, best[0])
    return ok, msg, best[0]


def switch_best_comprehensive(
    instance_id: int = 0, group: str | None = None
) -> tuple[bool, str]:
    group, all_nodes = get_proxy_group(instance_id, group)
    if not group or not all_nodes:
        return False, "未找到可用的代理组"

    scored = get_comprehensive_scores(instance_id, group, all_nodes)
    if not scored:
        return False, "无法获取节点评分"

    _, current_node = _tracker.get_current(instance_id)
    other_used = _get_other_instances_current_node(instance_id)

    candidates = [
        s for s in scored if s[0] != current_node and s[3] and s[0] not in other_used
    ]
    if not candidates:
        candidates = [s for s in scored if s[3] and s[0] != current_node]
    if not candidates:
        candidates = [s for s in scored if s[3]]
    if not candidates:
        return False, "没有可用的存活节点"

    best = candidates[0]
    return switch_node(instance_id, group, best[0])


# ── 实时延迟探测 ──


def test_all_delays(
    instance_id: int = 0,
    group: str | None = None,
    timeout: int = 5000,
    url: str = "http://www.gstatic.com/generate_204",
) -> dict[str, int]:
    """对代理组内所有节点进行实时延迟测试，返回 {节点名: 延迟ms}
    延迟测试是异步的 — 向每个节点发送延迟探测请求
    """
    group, all_nodes = get_proxy_group(instance_id, group)
    if not group or not all_nodes:
        return {}

    proxies_data = _req(instance_id, "GET", "/proxies")
    if not proxies_data:
        return {}

    all_proxies = {}
    for name, info in proxies_data.get("proxies", {}).items():
        if info.get("type") not in (
            "Selector",
            "URLTest",
            "Fallback",
            "Direct",
            "Reject",
            "RejectDrop",
            "Compatible",
            "Pass",
        ):
            all_proxies[name] = info

    sub_groups = [n for n in all_nodes if n not in all_proxies]
    real_nodes = [n for n in all_nodes if n not in sub_groups]
    real_nodes = [n for n in real_nodes if _is_real_proxy_node(n)]

    if not real_nodes:
        return {}

    secret = _get_secret()
    port = _get_instance_port(instance_id)
    results: dict[str, int] = {}

    import concurrent.futures

    def _test_single(name: str) -> tuple[str, int]:
        try:
            enc = urllib.parse.quote(name, safe="")
            test_url = f"http://127.0.0.1:{port}/proxies/{enc}/delay"
            full_url = (
                f"{test_url}?timeout={timeout}&url={urllib.parse.quote(url, safe='')}"
            )
            req = urllib.request.Request(
                full_url,
                headers={
                    "Authorization": f"Bearer {secret}",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout // 1000 + 2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                delay = int(data.get("delay", 9999))
                return name, delay
        except Exception:
            return name, 9999

    # 使用线程池并发测试
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_test_single, n): n for n in real_nodes}
        for future in concurrent.futures.as_completed(futures):
            name, delay = future.result()
            results[name] = delay

    logger.info(
        f"[ClashSwitcher] 延迟测试完成: {len(results)}/{len(real_nodes)} 个节点"
    )
    return results


def reset_tracker():
    """重置所有追踪状态（实例重启时调用）"""
    _tracker.reset()
