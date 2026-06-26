"""
Clash 多实例管理器
===================
管理 N 个独立 Clash 子进程，每个进程监听不同端口，使用独立的 Selector 组。
Worker N → HTTP_PROXY=http://127.0.0.1:17890+N
Worker N → API port 18090+N

Config 模板自动从 VergeRev 的 profile 生成，仅包含 proxies + proxy-groups + rules。
"""

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse
import yaml  # PyYAML

from loguru import logger


# ── 路径常量 ──
CLASH_CORE = r"C:\Program Files\Clash Verge\verge-mihomo.exe"
CLASH_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "clash_instances"
)
CLASH_DIR = os.path.normpath(CLASH_DIR)

VERGE_CONFIG_DIR = os.path.expandvars(
    r"%APPDATA%\io.github.clash-verge-rev.clash-verge-rev"
)
SOURCE_PROFILE = os.path.join(VERGE_CONFIG_DIR, "profiles", "RjtesGLAaZHM.yaml")
COUNTRY_MMDB = os.path.join(VERGE_CONFIG_DIR, "Country.mmdb")
GEOIP_DAT = os.path.join(VERGE_CONFIG_DIR, "geoip.dat")
GEOSITE_DAT = os.path.join(VERGE_CONFIG_DIR, "geosite.dat")

# ── 实例端口分配 ──
INSTANCE_COUNT = 10  # 默认 10 个 Worker，可调整
PORT_BASE_MIXED = 17890  # mixed-port 起点
PORT_BASE_API = 18090  # external-controller 起点
# 从 ConfigDB 读取密钥和主实例地址，带硬编码 fallback
_CONFIG_READ = None  # lazy


def _get_config(key: str, fallback: str) -> str:
    global _CONFIG_READ
    if _CONFIG_READ is None:
        try:
            from util import ConfigDB

            _CONFIG_READ = ConfigDB
        except (ImportError, Exception):
            _CONFIG_READ = object()  # sentinel
    if _CONFIG_READ is not object():
        try:
            val = _CONFIG_READ.get(key)
            if val:
                return str(val)
        except Exception:
            pass
    return fallback


SECRET = _get_config("clash.api_secret", "set-your-secret")

# 主实例 API（VergeRev，用来查询节点延迟排名）
MASTER_HOST = _get_config("clash.api_url", "http://127.0.0.1:9097")
_parsed = urlparse(MASTER_HOST)
MASTER_API_HOST = (
    _parsed.hostname + (":" + str(_parsed.port) if _parsed.port else "")
    if _parsed.hostname
    else "127.0.0.1:9097"
)





# B站 ping 测试地址（轻量端点）
BILIBILI_PING_URL = "https://api.bilibili.com/x/web-interface/online"


def _live_filter_nodes(candidates: list[str], need: int) -> list[str]:
    """
    通过主实例 API 对候选节点做实时 B站 延迟测试，返回可达节点按延迟排序。
    使用 heapq.nsmallest(need) 替代全量 sort，O(n log need) 空间 O(n)。
    并发测试所有候选节点，只保留 5000ms 内响应的节点。
    """
    import concurrent.futures
    import heapq
    import urllib.parse

    results: dict[str, int | None] = {}

    def _test(name: str):
        try:
            enc = urllib.parse.quote(name, safe="")
            url = (
                f"http://{MASTER_API_HOST}/proxies/{enc}/delay"
                f"?timeout=8000&url={urllib.parse.quote(BILIBILI_PING_URL, safe='')}"
            )
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {SECRET}"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                d = data.get("delay", 9999)
                return name, d if d < 9999 else None
        except Exception:
            return name, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=need + 2) as pool:
        fs = {pool.submit(_test, n): n for n in candidates}
        concurrent.futures.wait(fs.keys(), timeout=12)
        for f in fs:
            try:
                n, d = f.result(timeout=0)
                results[n] = d
            except Exception:
                results[fs[f]] = None

    # 用 heapq.nsmallest 替代全量 sort：O(n log need) 而非 O(n log n)
    alive_all = [(n, d) for n, d in results.items() if d is not None and d <= 5000]
    alive = heapq.nsmallest(need, alive_all, key=lambda x: x[1])
    slow_or_dead = [n for n, d in results.items() if d is None or d > 5000]

    if len(alive) < need:
        logger.warning(
            f"[ClashInstance] B站 延迟测试: 仅 {len(alive_all)} 个可用 (≤5000ms), {len(slow_or_dead)} 个超时/不可达, 可用数不足 {need}"
        )
    else:
        logger.info(
            f"[ClashInstance] B站 延迟测试: {len(alive_all)} 个可用 (≤5000ms), {len(slow_or_dead)} 个超时/不可达"
        )
    for n, d in alive[:5]:
        logger.debug(f"    {n}: {d}ms")
    # 只返回 top need 个节点
    return [n for n, _ in alive]


def _assign_nodes(count: int) -> dict[int, str]:
    """为 N 个实例分配不同的最优节点
    按 B站 实时 ping 延迟升序排列，每个实例分到不同的最低延迟节点。
    最低延迟 → 实例1，次低 → 实例2，依此类推。
    无评分，无地区偏好，纯延迟排序。
    """
    real_names = _get_real_proxy_names()

    if not real_names:
        logger.warning("[ClashInstance] 没有可用的节点进行分配")
        return {}

    logger.info(f"[ClashInstance] 对 {len(real_names)} 个节点进行 B站 实时延迟测试...")
    verified = _live_filter_nodes(real_names, count)

    if not verified:
        logger.error("[ClashInstance] B站 延迟测试后无可用节点，无法分配")
        return {}

    if len(verified) < count:
        logger.warning(
            f"[ClashInstance] B站 延迟测试后可用节点 ({len(verified)}) < 实例数 ({count})，仅分配前 {len(verified)} 个实例"
        )
        count = len(verified)

    assignments = {}
    for i in range(1, count + 1):
        assignments[i] = verified[i - 1]

    logger.info(
        f"[ClashInstance] 节点分配完成: {len(assignments)} 个实例（B站 ping 延迟排序）"
    )
    for i, node in assignments.items():
        logger.info(f"  Instance {i} -> {node}")

    return assignments


def _get_proxies_from_profile() -> list[dict]:
    """从订阅配置中提取所有代理节点定义"""
    with open(SOURCE_PROFILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    all_proxies = config.get("proxies", [])
    # 过滤 info 节点
    real = []
    for p in all_proxies:
        name = p.get("name", "")
        # 跳过 info 行：含全角冒号、"剩余"、"到期"、"重置"、"更新订阅"、"重启网络"
        skip_keywords = [":", "剩余", "到期", "重置", "更新订阅", "重启网络"]
        if any(kw in name for kw in skip_keywords):
            continue
        real.append(p)
    return real


def _get_real_proxy_names() -> list[str]:
    """获取真实代理节点名列表"""
    proxies = _get_proxies_from_profile()
    # 去重（同名只取一次）
    seen = set()
    names = []
    for p in proxies:
        n = p["name"]
        if n not in seen:
            seen.add(n)
            names.append(n)
    return names


def _reorder_proxies(proxies: list[dict], assigned_node: str | None) -> list[dict]:
    """把指定节点排到列表最前面，实现 Clash 自动预选该节点"""
    if not assigned_node:
        return proxies
    # O(1) 构建：assigned 放首位，avoid insert(0) O(n) 移位
    assigned = next((p for p in proxies if p["name"] == assigned_node), None)
    if not assigned:
        return proxies
    others = [p for p in proxies if p["name"] != assigned_node]
    return [assigned] + others


def generate_child_config(instance_id: int, assigned_node: str | None = None) -> dict:
    """
    生成第 N 个子实例的 Clash 配置。
    - 从订阅读取所有代理节点
    - 创建一个 Selector 组 buy-N，包含所有节点
    - 如果指定了 assigned_node，将其排在组列表最前面（Clash 自动选第一个）
    """
    proxies = _get_proxies_from_profile()
    if assigned_node:
        proxies = _reorder_proxies(proxies, assigned_node)
    proxy_names = [p["name"] for p in proxies]

    group_name = f"buy-{instance_id}"

    config = {
        "mixed-port": PORT_BASE_MIXED + instance_id,
        "log-level": "warning",
        "allow-lan": False,
        "mode": "Rule",
        "external-controller": f"127.0.0.1:{PORT_BASE_API + instance_id}",
        "secret": SECRET,
        "unified-delay": True,
        # 代理节点
        "proxies": proxies,
        # 代理组：所有节点可用，但 assigned_node 排第一
        "proxy-groups": [
            {
                "name": group_name,
                "type": "select",
                "proxies": proxy_names,
            },
        ],
        # 规则：所有流量走买票组
        "rules": [
            "MATCH," + group_name,
        ],
    }

    return config


def write_child_config(instance_id: int, assigned_node: str | None = None) -> str:
    """写入配置文件，返回路径"""
    instance_dir = os.path.join(CLASH_DIR, str(instance_id))
    os.makedirs(instance_dir, exist_ok=True)

    config = generate_child_config(instance_id, assigned_node=assigned_node)
    config_path = os.path.join(instance_dir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            config, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    # 复制 mmdb / geoip / geosite
    for src in [COUNTRY_MMDB, GEOIP_DAT, GEOSITE_DAT]:
        dst = os.path.join(instance_dir, os.path.basename(src))
        if not os.path.exists(dst):
            shutil.copy2(src, dst)

    logger.info(f"[ClashInstance] 已生成配置: {config_path} (assigned={assigned_node})")
    return config_path


def start_instance(
    instance_id: int, wait_ready: bool = True
) -> subprocess.Popen | None:
    """启动第 N 个 Clash 子实例
    - 确保配置已生成
    - 使用 subprocess.Popen 启动 verge-mihomo.exe
    - wait_ready=True 时等待 API 就绪
    - 如果已运行则直接返回 None
    """
    # 先检查是否已在运行
    if _api_req(instance_id, "GET", "/version"):
        logger.info(f"[ClashInstance] 实例 {instance_id} 已在运行")
        return None

    instance_dir = os.path.join(CLASH_DIR, str(instance_id))
    config_path = os.path.join(instance_dir, "config.yaml")

    if not os.path.exists(config_path):
        write_child_config(instance_id)

    api_port = PORT_BASE_API + instance_id
    mixed_port = PORT_BASE_MIXED + instance_id

    try:
        proc = subprocess.Popen(
            [CLASH_CORE, "-d", instance_dir, "-f", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        logger.info(
            f"[ClashInstance] 实例 {instance_id} 已启动 (PID={proc.pid}, mixed:{mixed_port}, api:{api_port})"
        )

        if wait_ready:
            ready = _wait_for_api(api_port, timeout=20)
            if not ready:
                logger.warning(
                    f"[ClashInstance] 实例 {instance_id} API 未就绪，终止进程"
                )
                try:
                    proc.terminate()
                except Exception:
                    pass
                return None
            # 就绪后自动切换到分配节点
            if instance_id in _assigned_nodes:
                _force_select_node(instance_id, _assigned_nodes[instance_id])

        return proc
    except Exception as e:
        logger.error(f"[ClashInstance] 启动实例 {instance_id} 失败: {e}")
        return None


def stop_instance(instance_id: int):
    """停止第 N 个 Clash 实例"""
    api_port = PORT_BASE_API + instance_id
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}/stop",
            method="GET",
            headers={"Authorization": f"Bearer {SECRET}"},
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # 可能已经停了或用进程 kill


# ── 智能启动 / 保活 ──

_assigned_nodes: dict[int, str] = {}


def ensure_instance(instance_id: int, force_reconfig: bool = False) -> bool:
    """确保第 N 个实例正在运行，未运行则自动启动
    如果实例已在线但被分配了新节点，通过 API 强制切换
    """
    global _assigned_nodes

    # 如果没分配过节点，自动分配
    if instance_id not in _assigned_nodes:
        need_count = max(INSTANCE_COUNT, instance_id + 1)
        _assigned_nodes = _assign_nodes(need_count)

    assigned = _assigned_nodes.get(instance_id)

    data = _api_req(instance_id, "GET", "/version")
    if data and "version" in data:
        # 实例已在运行，通过 API 切换到分配节点
        if assigned:
            _force_select_node(instance_id, assigned)
        return True

    # 未运行，生成配置并启动
    write_child_config(instance_id, assigned_node=assigned)
    proc = start_instance(instance_id, wait_ready=True)
    if proc:
        with _lock:
            _running_instances[instance_id] = proc
        return True
    return False


def _force_select_node(instance_id: int, node: str) -> bool:
    """强制切换到指定节点（如果当前不是该节点），返回是否切换成功"""
    try:
        grp, _ = get_proxy_group(instance_id)
        if not grp:
            return False
        enc = urllib.parse.quote(grp, safe="")
        data = _api_req(instance_id, "GET", f"/proxies/{enc}")
        if data:
            now = data.get("now", "")
            if now == node:
                return True  # 已经是目标节点
        switch_node(instance_id, grp, node)
        logger.info(f"[ClashInstance] 实例 {instance_id} 已通过 API 切换到: {node}")
        return True
    except Exception as e:
        logger.warning(f"[ClashInstance] 无法切换实例 {instance_id} 节点: {e}")
        return False


def ensure_all(count: int | None = None) -> list[int]:
    """确保所有实例都在运行，返回启动了的实例列表
    使用 INCREASING_STAGGER 策略：每启动一个实例，等待时间递增
    避免同时启动太多进程导致系统过载
    """
    global _assigned_nodes

    if count is None:
        count = INSTANCE_COUNT

    # 先分配节点（排序好的）
    _assigned_nodes = _assign_nodes(count)

    # 分配完后实际可用的实例数受限于可用节点数
    actual_count = len(_assigned_nodes)
    started = []
    stagger = 2.0  # 初始间隔 2 秒
    for i in range(1, actual_count + 1):
        if ensure_instance(i):
            started.append(i)
            logger.info(
                f"[ClashInstance] 实例 {i} 已就绪 ✓ ({len(started)}/{actual_count})"
            )
        else:
            logger.warning(f"[ClashInstance] 实例 {i} 启动失败 ✗")
        # 递增间隔：第1个2s, 第2个2.5s, 第3个3s...
        time.sleep(stagger)
        stagger = min(stagger + 0.5, 5.0)

    logger.info(f"[ClashInstance] 确保 {len(started)}/{actual_count} 个实例在线")
    return started


def auto_start(count: int | None = None) -> list[int]:
    """一键分配节点 + 生成配置 + 启动所有实例"""
    return ensure_all(count)


def add_instance() -> int:
    """动态新增一个实例，返回其 instance_id"""
    global INSTANCE_COUNT
    with _lock:
        new_id = INSTANCE_COUNT
        INSTANCE_COUNT += 1
    ensure_instance(new_id)
    return new_id


def remove_instance(instance_id: int) -> bool:
    """移除一个实例"""
    stop_instance(instance_id)
    instance_dir = os.path.join(CLASH_DIR, str(instance_id))
    if os.path.exists(instance_dir):
        import shutil

        shutil.rmtree(instance_dir, ignore_errors=True)
    return True


def get_assignments() -> dict[int, str]:
    """获取当前节点分配表"""
    return dict(_assigned_nodes)


def _wait_for_api(api_port: int, timeout: int = 20) -> bool:
    """等待 Clash API 就绪，带指数退避轮询"""
    deadline = time.time() + timeout
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{api_port}/version",
                headers={"Authorization": f"Bearer {SECRET}"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "version" in data:
                    elapsed = timeout - (deadline - time.time())
                    logger.info(
                        f"[ClashInstance] API :{api_port} 就绪 (v{data['version']}, {attempts}次尝试, {elapsed:.1f}s)"
                    )
                    return True
        except Exception:
            pass
        time.sleep(min(0.5 * attempts, 2.0))  # 指数退避：0.5s, 1s, 1.5s, 2s, 2s...
    logger.warning(f"[ClashInstance] API :{api_port} 等待超时 ({attempts}次尝试)")
    return False


# ── 实例代理 API ──
def _api_req(instance_id: int, method: str, path: str, body=None) -> dict | None:
    api_port = PORT_BASE_API + instance_id
    url = f"http://127.0.0.1:{api_port}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"[ClashInstance:{instance_id}] API error: {e}")
        return None


def get_proxy_group(instance_id: int) -> tuple[str | None, list[str]]:
    """获取第 N 个实例的 Selector 组名和节点列表"""
    # 子实例的业务组名是 buy-N
    expected = f"buy-{instance_id}"
    enc = urllib.parse.quote(expected, safe="")
    detail = _api_req(instance_id, "GET", f"/proxies/{enc}")
    if detail and detail.get("all"):
        return expected, detail["all"]

    # fallback: 找第一个非 GLOBAL 的 Selector
    data = _api_req(instance_id, "GET", "/proxies")
    if not data:
        return None, []

    for name, proxy in data.get("proxies", {}).items():
        if proxy.get("type") == "Selector" and name != "GLOBAL":
            enc = urllib.parse.quote(name, safe="")
            detail = _api_req(instance_id, "GET", f"/proxies/{enc}")
            if detail and detail.get("all"):
                return name, detail["all"]
    return None, []


def switch_node(instance_id: int, group: str, node: str) -> bool:
    enc = urllib.parse.quote(group, safe="")
    result = _api_req(instance_id, "PUT", f"/proxies/{enc}", {"name": node})
    return result is None  # PUT 成功时返回 None


# ── 一次性启动/停止全部 ──

_running_instances: dict[int, subprocess.Popen] = {}
_lock = threading.Lock()


def start_all(count: int = INSTANCE_COUNT, wait_ready: bool = True) -> list[int]:
    """分配节点 + 启动 N 个 Clash 子实例（自动排序，每个实例不同节点）"""
    return auto_start(count)


def stop_all():
    """停止所有 Clash 子实例"""
    with _lock:
        for instance_id, proc in list(_running_instances.items()):
            try:
                proc.terminate()
                proc.wait(timeout=5)
                logger.info(f"[ClashInstance] 实例 {instance_id} 已停止")
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _running_instances.clear()


def get_instance_statuses() -> list[dict]:
    """获取所有子实例状态（含当前节点名），跳过实例 0（主实例 VergeRev 9097）
    遇到连续 3 个 API 无响应的端口自动停止扫描，避免大量 502/连接拒绝日志
    """
    statuses = []
    missing = 0
    for instance_id in range(1, INSTANCE_COUNT):
        api_port = PORT_BASE_API + instance_id
        proxy_port = PORT_BASE_MIXED + instance_id
        # 跳过未实际启动的实例（连续 3 个无响应即停止扫描）
        try:
            data = _api_req(instance_id, "GET", "/version")
            alive = data is not None and "version" in data
        except Exception:
            alive = False

        if not alive:
            missing += 1
            if missing >= 3:
                break
        else:
            missing = 0

        current_node = _assigned_nodes.get(instance_id, "")
        if alive:
            try:
                grp, nodes = get_proxy_group(instance_id)
                if grp:
                    data2 = _api_req(
                        instance_id,
                        "GET",
                        f"/proxies/{urllib.parse.quote(grp, safe='')}",
                    )
                    if data2:
                        current_node = data2.get("now", current_node or "")
            except Exception:
                pass

        statuses.append(
            {
                "id": instance_id,
                "alive": alive,
                "api_port": api_port,
                "proxy_port": proxy_port,
                "group": f"buy-{instance_id}",
                "assigned_node": _assigned_nodes.get(instance_id, ""),
                "current_node": current_node,
            }
        )
    return statuses
