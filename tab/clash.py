"""
Clash 节点管理 Tab — 重写 v3
新增：节点延迟表、自动切换开关、布局优化
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import gradio as gr
from loguru import logger

from util import ConfigDB

# ── 配置键 ──
CFG_API_URL = "clash.api_url"
CFG_API_SECRET = "clash.api_secret"
CFG_PROXY_GROUP = "clash.proxy_group"
CFG_SELECTED_NODE = "clash.selected_node"
CFG_INSTANCE_COUNT = "clash.instance_count"
CFG_AUTO_MAIN = "clash.auto_switch_main"
CFG_AUTO_CHILD = "clash.auto_switch_child"

DEFAULT_API_URL = "http://127.0.0.1:9097"
DEFAULT_SECRET = "set-your-secret"
DEFAULT_INSTANCE_COUNT = 5


# ═══════════════════════════════════════════════
#  通用 API
# ═══════════════════════════════════════════════

def _headers() -> dict[str, str]:
    secret = ConfigDB.get(CFG_API_SECRET) or DEFAULT_SECRET
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def _api_url(path: str) -> str:
    base = ConfigDB.get(CFG_API_URL) or DEFAULT_API_URL
    return f"{base.rstrip('/')}{path}"


def _req(method: str, path: str, body: Any = None) -> Any:
    url = _api_url(path)
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        logger.warning(f"Clash API HTTP {e.code}: {body[:200]}")
        return None
    except Exception as e:
        logger.warning(f"Clash API error: {e}")
        return None


# ═══════════════════════════════════════════════
#  主实例 API
# ═══════════════════════════════════════════════

def test_connection() -> tuple[bool, str]:
    data = _req("GET", "/version")
    if data and "version" in data:
        return True, f"{data['version']}"
    return False, "连接失败"


def get_proxy_groups() -> list[dict]:
    data = _req("GET", "/proxies")
    if not data:
        return []
    groups = []
    for name, proxy in data.get("proxies", {}).items():
        if proxy.get("type") in ("Selector", "URLTest", "Fallback"):
            groups.append({
                "name": name, "type": proxy["type"],
                "now": proxy.get("now", ""),
                "all": proxy.get("all", []),
            })
    return groups


def get_proxies() -> dict[str, dict]:
    data = _req("GET", "/proxies")
    if not data:
        return {}
    skip_types = {"Selector", "URLTest", "Fallback", "Direct", "Reject",
                   "RejectDrop", "Compatible", "Pass"}
    return {n: p for n, p in data.get("proxies", {}).items() if p.get("type") not in skip_types}


def switch_node(group: str, node: str) -> tuple[bool, str]:
    enc = urllib.parse.quote(group, safe="")
    result = _req("PUT", f"/proxies/{enc}", {"name": node})
    if result is None:
        ConfigDB.insert(CFG_SELECTED_NODE, node)
        return True, f"已切换到: {node}"
    return False, f"切换失败: {result}"


def get_best_node(group: str) -> str | None:
    enc = urllib.parse.quote(group, safe="")
    data = _req("GET", f"/proxies/{enc}")
    if not data or not data.get("all"):
        return None
    all_proxies = get_proxies()
    sub_groups = set(n for n in data["all"] if n not in all_proxies)
    best, best_delay = None, 9999999
    for node in data["all"]:
        if node in sub_groups:
            continue
        p = all_proxies.get(node)
        if not p or not p.get("alive"):
            continue
        try:
            hist = p.get("extra", {}).get("history", [])
            delay = int(hist[-1]["delay"]) if hist else 9999999
            if 0 < delay < best_delay:
                best_delay, best = delay, node
        except (IndexError, KeyError, ValueError, TypeError):
            continue
    return best


def _shorten(s: str, maxlen: int = 22) -> str:
    return s if len(s) <= maxlen else s[:maxlen - 2] + ".."


# ── 节点延迟查询 ──

def _extract_region(node: str) -> str:
    regions = [
        ("🇯🇵", ["日本", "东京", "大阪", "樱花", "IX", "IIJ", "NTT"]),
        ("🇭🇰", ["香港", "HK", "HKT", "CMI", "HGC"]),
        ("🇰🇷", ["韩国", "首尔", "SK"]),
        ("🇸🇬", ["新加坡", "SG", "GTT"]),
        ("🇺🇸", ["美国", "洛杉矶", "硅谷", "圣何塞"]),
        ("🇹🇼", ["台湾", "台北"]),
        ("🇬🇧", ["英国", "伦敦"]),
        ("🇩🇪", ["德国", "法兰克福"]),
        ("🇫🇷", ["法国", "巴黎"]),
        ("🇲🇴", ["澳门"]),
        ("🇹🇭", ["泰国", "曼谷"]),
        ("🇷🇺", ["俄罗斯", "莫斯科"]),
        ("🇻🇳", ["越南", "河内"]),
        ("🇨🇦", ["加拿大"]),
        ("🇦🇺", ["澳大利亚", "悉尼"]),
    ]
    for flag, kws in regions:
        if any(k in node for k in kws):
            return flag
    return "🌐"


def _is_real_proxy_node(name: str) -> bool:
    if "：" in name:
        return False
    for c in name:
        cp = ord(c)
        if 0x1F1E6 <= cp <= 0x1F1FF:
            return True
    if _extract_region(name) != "🌐":
        return True
    if any(kw in name for kw in ("-", "|", "·", "IPLC", "IEPL", "BGP", "CN2", "GIA")):
        return True
    return False


def get_nodes_with_delay(group: str, live_test: bool = False) -> list[dict]:
    """获取某代理组下所有节点及其延迟信息
    如果 live_test=True，先触发实时延迟探测
    """
    if not group:
        return []

    if live_test:
        # 实时探测延迟
        try:
            from util.proxy.ClashSwitcher import test_all_delays
            delays = test_all_delays(0, group, timeout=5000)
        except ImportError:
            delays = None
    else:
        delays = None

    enc = urllib.parse.quote(group, safe="")
    data = _req("GET", f"/proxies/{enc}")
    if not data or not data.get("all"):
        return []

    proxies = get_proxies()
    sub_groups = set(n for n in data["all"] if n not in proxies)
    real_nodes = [n for n in data["all"] if n not in sub_groups]
    real_nodes = [n for n in real_nodes if _is_real_proxy_node(n)]

    results = []
    for node in real_nodes:
        info = proxies.get(node, {})
        alive = info.get("alive", False)
        delay = 9999
        # 优先使用实时探测结果
        if delays and node in delays:
            delay = delays[node]
            if delay < 9999:
                alive = True
        else:
            try:
                hist = info.get("extra", {}).get("history", [])
                if hist:
                    delay = int(hist[-1]["delay"])
            except (IndexError, KeyError, ValueError, TypeError):
                pass
        results.append({
            "name": node,
            "region": _extract_region(node),
            "delay": delay,
            "alive": alive,
        })

    # 按延迟排序 (alive 优先)
    results.sort(key=lambda r: (0 if r["alive"] else 1, r["delay"]))
    return results


def build_latency_table(group: str, live_test: bool = False) -> str:
    """生成节点延迟 HTML 表格"""
    nodes = get_nodes_with_delay(group, live_test=live_test)
    if not nodes:
        return '<div class="btb-card-note">请先连接并选择一个代理组</div>'

    rows = []
    for i, n in enumerate(nodes):
        alive_icon = "🟢" if n["alive"] else "🔴"
        delay_str = f"{n['delay']}ms" if n["delay"] < 9999 else "—"
        delay_class = "latency-ok" if n["alive"] and n["delay"] < 300 else ("latency-slow" if n["alive"] else "latency-dead")

        rows.append(
            f"<tr>"
            f"<td style='text-align:center;width:40px;'>{i+1}</td>"
            f"<td style='text-align:center;width:40px;'>{n['region']}</td>"
            f"<td style='max-width:250px;overflow:hidden;text-overflow:ellipsis;'>{n['name']}</td>"
            f"<td style='text-align:center;width:80px;'>{alive_icon}</td>"
            f"<td style='text-align:center;width:90px;font-weight:600;' class='{delay_class}'>{delay_str}</td>"
            f"</tr>"
        )

    return f"""
    <style>
        .latency-ok {{ color: #22c55e; }}
        .latency-slow {{ color: #f59e0b; }}
        .latency-dead {{ color: #ef4444; }}
    </style>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead style="background:var(--table-even-row-background-fill,#f0f4ff);">
            <tr>
                <th style="padding:6px 8px;text-align:center;">#</th>
                <th style="padding:6px 8px;text-align:center;">地区</th>
                <th style="padding:6px 8px;text-align:left;">节点名</th>
                <th style="padding:6px 8px;text-align:center;">状态</th>
                <th style="padding:6px 8px;text-align:center;">延迟</th>
            </tr>
        </thead>
        <tbody>{"".join(rows)}</tbody>
    </table>
    <div style="margin-top:6px;font-size:12px;color:#888;">
        🟢 在线 · 🔴 离线 · 绿=≤300ms · 黄=>300ms · 红=超时/离线
    </div>"""


# ═══════════════════════════════════════════════
#  子实例管理
# ═══════════════════════════════════════════════

def _importer():
    from util.proxy.ClashInstanceManager import (
        INSTANCE_COUNT,
        ensure_all,
        stop_all,
        get_instance_statuses,
        get_assignments,
        _force_select_node,
        add_instance as _add_instance,
        remove_instance as _remove_instance,
    )
    return INSTANCE_COUNT, ensure_all, stop_all, get_instance_statuses, get_assignments, _force_select_node, _add_instance, _remove_instance


def _switcher():
    from util.proxy.ClashSwitcher import is_api_available, get_status_text, _get_other_instances_current_node
    return is_api_available, get_status_text, _get_other_instances_current_node


def _ensure_imported():
    try:
        _importer()
        _switcher()
        return True
    except ImportError as e:
        logger.warning(f"ClashInstanceManager not available: {e}")
        return False


def auto_start_instances(count: int) -> tuple[str, str]:
    if not _ensure_imported():
        return "❌ ClashInstanceManager 未找到", ""
    try:
        _, ensure_all, _, _, get_assignments, _, _, _ = _importer()
        started = ensure_all(count)
        assignments = get_assignments()
        lines = [f"✅ 已确保 {len(started)}/{count} 个实例在线"]
        for iid in range(1, count + 1):
            ico = "🟢" if iid in started else "🔴"
            node = assignments.get(iid, "")
            lines.append(f"  {ico} 实例 {iid} → {node}")
        return "\n".join(lines), _build_instance_table()
    except Exception as e:
        logger.error(f"Auto-start failed: {e}")
        return f"❌ 启动失败: {e}", ""


def stop_all_instances() -> tuple[str, str]:
    if not _ensure_imported():
        return "❌ 模块未加载", ""
    try:
        _, _, stop_all, _, _, _, _, _ = _importer()
        stop_all()
        return "✅ 已停止全部子实例", _build_instance_table()
    except Exception as e:
        return f"❌ 停止失败: {e}", ""


def add_single_instance() -> tuple[str, str]:
    if not _ensure_imported():
        return "❌ 模块未加载", ""
    try:
        _, _, _, _, _, _, _add_instance, _ = _importer()
        iid = _add_instance()
        return f"✅ 已新增实例 {iid}", _build_instance_table()
    except Exception as e:
        return f"❌ {e}", ""


def switch_instance_node(instance_id: int, node_name: str) -> str:
    if not _ensure_imported():
        return "❌ 模块未加载"
    try:
        from util.proxy.ClashInstanceManager import get_proxy_group
        grp, _ = get_proxy_group(instance_id)
        if not grp:
            return f"❌ 实例 {instance_id} 无可用组"
        from util.proxy.ClashInstanceManager import _force_select_node
        ok = _force_select_node(instance_id, node_name)
        return f"✅ 实例 {instance_id} → {node_name}" if ok else "❌ 切换失败"
    except Exception as e:
        return f"❌ {e}"


def _build_instance_table() -> str:
    try:
        _, _, _, get_instance_statuses, get_assignments, _, _, _ = _importer()
        statuses = get_instance_statuses()
        assignments = get_assignments()
    except Exception:
        return '<div class="btb-card-note">⚠️ ClashInstanceManager 未加载</div>'

    if not statuses:
        return '<div class="btb-card-note">暂无子实例，请先启动</div>'

    auto_child = _get_auto_toggle(CFG_AUTO_CHILD)

    rows = []
    for s in statuses:
        iid = s["id"]
        if iid == 0:
            continue
        alive = s["alive"]
        assigned = assignments.get(iid, "")
        cur_node = s.get("current_node", "")
        cur_disp = cur_node if cur_node else "—"
        status_icon = "🟢 在线" if alive else "🔴 离线"
        auto_ico = "🟢" if auto_child else "⚪"

        # 获取延迟
        delay_str = "—"
        if alive and cur_node:
            try:
                enc = urllib.parse.quote(f"buy-{iid}", safe="")
                data = _req_raw(iid, "GET", f"/proxies/{enc}")
                if data:
                    now = data.get("now", "")
                    if now:
                        p2 = _req_raw(iid, "GET", "/proxies")
                        if p2:
                            info = p2.get("proxies", {}).get(now, {})
                            if info and info.get("alive"):
                                hist = info.get("extra", {}).get("history", [])
                                if hist:
                                    d = int(hist[-1]["delay"])
                                    delay_str = f"{d}ms"
            except Exception:
                pass

        rows.append(
            f"<tr>"
            f"<td style='text-align:center;font-weight:600;width:50px;'>{iid}</td>"
            f"<td style='text-align:center;width:70px;'>{status_icon}</td>"
            f"<td style='text-align:center;width:40px;'>{auto_ico}</td>"
            f"<td style='max-width:160px;overflow:hidden;text-overflow:ellipsis;'>{assigned}</td>"
            f"<td style='max-width:160px;overflow:hidden;text-overflow:ellipsis;'>{cur_disp}</td>"
            f"<td style='text-align:center;width:70px;'>{delay_str}</td>"
            f"<td style='width:70px;text-align:center;'>:{17890+iid}</td>"
            f"</tr>"
        )

    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead style="background:var(--table-even-row-background-fill,#f0f4ff);">
            <tr>
                <th style="padding:6px 8px;text-align:center;">#</th>
                <th style="padding:6px 8px;text-align:center;">状态</th>
                <th style="padding:6px 8px;text-align:center;">⚡</th>
                <th style="padding:6px 8px;text-align:left;">分配节点</th>
                <th style="padding:6px 8px;text-align:left;">当前节点</th>
                <th style="padding:6px 8px;text-align:center;">延迟</th>
                <th style="padding:6px 8px;text-align:center;">端口</th>
            </tr>
        </thead>
        <tbody>{"".join(rows)}</tbody>
    </table>
    <div style="margin-top:4px;font-size:12px;color:#888;">
        ⚡ 列 = 自动切换状态 · 🟢=启用 · ⚪=禁用
    </div>"""


def _req_raw(instance_id: int, method: str, path: str) -> Any:
    """子实例 API 请求（直接拼端口）"""
    port = 18090 + instance_id
    secret = ConfigDB.get(CFG_API_SECRET) or DEFAULT_SECRET
    url = f"http://127.0.0.1:{port}{path}"
    try:
        req = urllib.request.Request(url, method=method, headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_now_available_instances() -> list:
    try:
        _, _, _, get_instance_statuses, _, _, _, _ = _importer()
        items = []
        for s in get_instance_statuses():
            iid = s["id"]
            if iid == 0:
                continue
            ico = "🟢" if s["alive"] else "🔴"
            port = 17890 + iid
            items.append((f"{ico} 实例 {iid}（:{port}）", iid))
        return items if items else [("(无实例)", -1)]
    except Exception:
        return [("(未加载)", -1)]


def get_nodes_for_instance(instance_id: int) -> list:
    if instance_id < 1:
        return []
    try:
        from util.proxy.ClashInstanceManager import get_proxy_group
        grp, nodes = get_proxy_group(instance_id)
        if not grp or not nodes:
            return [("(无节点)", "")]
        clean = [n for n in nodes
                 if not any(k in n for k in ["：", "剩余", "到期", "重置", "更新订阅", "重启网络"])]
        return [(n, n) for n in clean]
    except Exception:
        return [("(错误)", "")]


# ═══════════════════════════════════════════════
#  自动开关 & 状态
# ═══════════════════════════════════════════════

def _get_auto_toggle(key: str) -> bool:
    val = ConfigDB.get(key)
    return val == "1" if val is not None else True


def _set_auto_toggle(key: str, enabled: bool):
    ConfigDB.insert(key, "1" if enabled else "0")


def toggle_auto_main(enabled: bool) -> str:
    _set_auto_toggle(CFG_AUTO_MAIN, enabled)
    return "✅ 主实例自动切换" + (" 已启用" if enabled else " 已禁用")


def toggle_auto_child(enabled: bool) -> str:
    _set_auto_toggle(CFG_AUTO_CHILD, enabled)
    return "✅ 子实例自动切换" + (" 已启用" if enabled else " 已禁用")


def get_auto_switch_status() -> str:
    lines = []
    try:
        is_api_available, get_status_text, _get_other_instances_current_node = _switcher()
        auto_main = _get_auto_toggle(CFG_AUTO_MAIN)
        auto_child = _get_auto_toggle(CFG_AUTO_CHILD)

        lines.append(f"**自动切换**: 主实例 {'🟢 启用' if auto_main else '⚪ 禁用'} · 子实例 {'🟢 启用' if auto_child else '⚪ 禁用'}")

        if not is_api_available(0):
            lines.append("主实例: 🔴 未连接")
        else:
            lines.append(f"**主实例** {get_status_text(0)}")

        try:
            _, _, _, get_instance_statuses, _, _, _, _ = _importer()
            for s in get_instance_statuses():
                iid = s["id"]
                if iid == 0 or not s["alive"]:
                    continue
                if is_api_available(iid):
                    lines.append(f"**子实例 {iid}** {get_status_text(iid)}")
        except Exception:
            pass

        lines.append("")
        lines.append("**跨实例反亲和性** — 切换时自动跳过其他实例正在使用的节点")
        if is_api_available(0):
            used = _get_other_instances_current_node(0)
            lines.append(f"其他实例当前节点: {', '.join(used) if used else '不在线或无占用'}")
        lines.append("")
        lines.append("**切换规则** 412/403/IP 报错 → 自动切到评分最高且未被占用的节点")
    except ImportError:
        lines.append("⚠️ ClashSwitcher 未加载")
    except Exception as e:
        lines.append(f"⚠️ 错误: {e}")
    return "\n\n".join(lines)


# ═══════════════════════════════════════════════
#  ConfigDB 存取
# ═══════════════════════════════════════════════

def save_config(api_url: str, secret: str) -> str:
    api_url = api_url.rstrip("/")
    ConfigDB.insert(CFG_API_URL, api_url)
    ConfigDB.insert(CFG_API_SECRET, secret)
    return "配置已保存"


def load_config() -> tuple[str, str]:
    return (
        ConfigDB.get(CFG_API_URL) or DEFAULT_API_URL,
        ConfigDB.get(CFG_API_SECRET) or DEFAULT_SECRET,
    )


# ═══════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════

def _build_group_tags(groups: list[dict]) -> str:
    if not groups:
        return '<span class="btb-card-note">无法获取代理组</span>'
    icons = {"Selector": "🎯", "URLTest": "📊", "Fallback": "🔄"}
    tags = []
    for g in groups:
        icon = icons.get(g["type"], "📦")
        tags.append(
            f'<span style="display:inline-block;padding:4px 10px;margin:3px;'
            f'background:var(--button-secondary-background-fill,#e5e7eb);'
            f'border-radius:999px;font-size:13px;">'
            f'{icon} {g["name"]} <b>{g["now"]}</b>'
            f'</span>'
        )
    return "".join(tags)


def _discover_quick_nodes(group: str) -> list:
    """返回快捷下拉选项 (label, value)"""
    if not group:
        return []
    enc = urllib.parse.quote(group, safe="")
    data = _req("GET", f"/proxies/{enc}")
    if not data or not data.get("all"):
        return []
    proxies = get_proxies()
    sub_groups = set(n for n in data["all"] if n not in proxies)
    real_nodes = [n for n in data["all"] if n not in sub_groups]
    quick = [("⚡ 切换至…", "__sep__")]
    best = get_best_node(group)
    if best:
        quick.append((f"⚡ 最佳延迟 → {_shorten(best, 20)}", best))

    regions = [
        ("🇯🇵 日本", ["🇯🇵", "日本", "东京", "大阪"]),
        ("🇭🇰 香港", ["🇭🇰", "香港", "HK", "HKT", "CMI"]),
        ("🇰🇷 韩国", ["🇰🇷", "韩国", "首尔", "SK"]),
        ("🇸🇬 新加坡", ["🇸🇬", "新加坡", "SG"]),
        ("🇺🇸 美国", ["🇺🇸", "美国", "洛杉矶", "硅谷"]),
        ("🇬🇧 英国", ["🇬🇧", "英国", "伦敦"]),
        ("🇩🇪 德国", ["🇩🇪", "德国", "法兰克福"]),
        ("🇫🇷 法国", ["🇫🇷", "法国", "巴黎"]),
    ]
    for label, keywords in regions:
        matched = [n for n in real_nodes if any(k in n for k in keywords)]
        picked = next((m for m in matched if proxies.get(m, {}).get("alive")), matched[0] if matched else None)
        if picked:
            quick.append((f"{label} → {_shorten(picked, 16)}", picked))
    return quick


# ═══════════════════════════════════════════════
#  Tab 构建
# ═══════════════════════════════════════════════

def clash_tab():
    defaults = load_config()
    saved_count = ConfigDB.get_as_int(CFG_INSTANCE_COUNT, DEFAULT_INSTANCE_COUNT)
    auto_main_default = _get_auto_toggle(CFG_AUTO_MAIN)
    auto_child_default = _get_auto_toggle(CFG_AUTO_CHILD)

    with gr.Column(elem_classes="btb-page-section"):

        # ════════════ 1. 配置面板 ════════════
        with gr.Column(elem_classes="btb-card btb-layout-card"):
            gr.HTML(
                """<div class="btb-card-head"><div><h3>🌐 Clash 配置</h3></div></div>"""
            )
            with gr.Row():
                api_url = gr.Textbox(
                    label="API 地址", value=defaults[0],
                    placeholder="http://127.0.0.1:9097", scale=3,
                )
                api_secret = gr.Textbox(
                    label="密钥", value=defaults[1],
                    placeholder="set-your-secret", type="password", scale=2,
                )
            with gr.Row(elem_classes="btb-inline-actions !justify-start"):
                connect_btn = gr.Button("🔌 连接测试", variant="primary", scale=0, min_width=140)
                save_btn = gr.Button("💾 保存", variant="secondary", scale=0, min_width=100)
                connection_status = gr.Textbox(label="", value="", interactive=False, scale=3)

        # ════════════ 2. 节点延迟仪表盘 ════════════
        with gr.Column(elem_classes="btb-card btb-layout-card"):
            gr.HTML(
                """<div class="btb-card-head"><div><h3>📊 节点延迟</h3></div>"""
                """<span class="btb-card-note">选择代理组查看所有节点实时延迟</span></div>"""
            )
            with gr.Row():
                latency_group = gr.Dropdown(
                    label="代理组", choices=[], interactive=True, scale=3,
                )
                refresh_latency_btn = gr.Button("🔄 刷新延迟", variant="secondary", scale=0, min_width=120)
                gr.Checkbox(label="自动刷新", value=False, scale=0, min_width=100)
            latency_table = gr.HTML(
                '<span class="btb-card-note">点击「连接测试」后选择组查看延迟</span>',
                elem_id="clash-latency-table",
            )

        # ════════════ 3. 主实例切换 ════════════
        with gr.Column(elem_classes="btb-card btb-layout-card"):
            gr.HTML(
                """<div class="btb-card-head"><div><h3>🔀 主实例代理</h3></div>"""
                """<span class="btb-card-note">切换主实例 (VergeRev) 的代理组节点</span></div>"""
            )
            # 自动切换开关
            with gr.Row():
                auto_main_toggle = gr.Checkbox(
                    label="⚡ 自动切换节点", value=auto_main_default,
                    info="失败时自动切换到评分最高的节点",
                    scale=1,
                )
                auto_main_status = gr.Textbox(label="", value="", interactive=False, scale=2)

            with gr.Row():
                group_dropdown = gr.Dropdown(
                    label="代理组", choices=[], interactive=True, scale=3,
                )
                refresh_groups_btn = gr.Button("🔄 刷新组", variant="secondary", scale=0, min_width=100)
            groups_tags = gr.HTML("点击「连接测试」查看代理组")

            with gr.Row():
                quick_dropdown = gr.Dropdown(
                    label="快捷切换", choices=[], interactive=True,
                    allow_custom_value=True, scale=3,
                )
                best_btn = gr.Button("⚡ 最佳延迟", variant="primary", scale=0, min_width=120)
            switch_result = gr.Textbox(label="", value="", interactive=False)

        # ════════════ 4. 多实例管理 ════════════
        with gr.Column(elem_classes="btb-card btb-layout-card"):
            gr.HTML(
                """<div class="btb-card-head"><div><h3>🚀 多实例管理</h3></div>"""
                """<span class="btb-card-note">为每个 Worker 分配独立 Clash 实例实现 IP 隔离</span></div>"""
            )

            # 自动切换开关（子实例全局）
            with gr.Row():
                auto_child_toggle = gr.Checkbox(
                    label="⚡ 子实例自动切换", value=auto_child_default,
                    info="子实例失败时自动切换到未被占用的最优节点",
                    scale=1,
                )
                auto_child_status = gr.Textbox(label="", value="", interactive=False, scale=2)

            with gr.Row():
                instance_count = gr.Number(
                    label="实例数量", value=saved_count,
                    minimum=1, maximum=10, step=1,
                    precision=0, scale=1,
                )
                with gr.Row(scale=3, elem_classes="btb-inline-actions !justify-start"):
                    auto_start_btn = gr.Button("🚀 一键启动", variant="primary", scale=0, min_width=120)
                    stop_all_btn = gr.Button("🛑 全部停止", variant="secondary", scale=0, min_width=100)
                    refresh_inst_btn = gr.Button("🔄 刷新", variant="secondary", scale=0, min_width=80)
                    add_one_btn = gr.Button("➕ 新增", variant="secondary", scale=0, min_width=80)

            inst_result = gr.Textbox(label="", value="", interactive=False, visible=True)
            inst_table = gr.HTML(
                '<span class="btb-card-note">点击「一键启动」查看子实例状态</span>',
                elem_id="btb-instance-table",
            )

            # 单实例切换折叠
            with gr.Column(elem_classes="btb-card btb-soft-accordion"):
                gr.HTML(
                    """<div class="btb-card-head"><div><h4>🔧 切换指定子实例节点</h4></div>"""
                    """<span class="btb-card-note">展开后选择实例和目标节点</span></div>"""
                )
                with gr.Row():
                    instance_sel = gr.Dropdown(
                        label="选择实例", choices=[], interactive=True,
                        allow_custom_value=True, scale=2,
                    )
                    node_sel = gr.Dropdown(
                        label="目标节点", choices=[], interactive=True, scale=3,
                    )
                    switch_inst_btn = gr.Button("🔄 切换", variant="secondary", scale=0, min_width=80)
                inst_switch_result = gr.Textbox(label="", value="", interactive=False)

        # ════════════ 5. 自动切换监控 ════════════
        with gr.Column(elem_classes="btb-card btb-layout-card"):
            gr.HTML(
                """<div class="btb-card-head"><div><h3>⚡ 自动切换状态</h3></div></div>"""
            )
            with gr.Row(elem_classes="btb-inline-actions !justify-start"):
                refresh_auto_btn = gr.Button("🔄 刷新状态", variant="secondary", scale=0, min_width=120)
            auto_status = gr.Markdown("点击「刷新状态」查看")

    # ════════════════════════════════════════════
    #  事件绑定
    # ════════════════════════════════════════════

    # ── 连接测试 ──
    # 输出: connection_status, groups_tags, group_dropdown, switch_result, quick_dropdown, inst_switch_result, latency_group, latency_table
    def _do_connect():
        ok, info = test_connection()
        if not ok:
            return (f"❌ {info}", "",
                    gr.Dropdown(choices=[], value=None), "",
                    gr.Dropdown(choices=[], value=None), "",
                    gr.Dropdown(choices=[], value=None),
                    '<span class="btb-card-note">连接失败</span>')
        groups = get_proxy_groups()
        html = _build_group_tags(groups)
        names = [g["name"] for g in groups]
        return (f"✅ {info}", html,
                gr.Dropdown(choices=names, value=None), "",
                gr.Dropdown(choices=[], value=None), "",
                gr.Dropdown(choices=names, value=None),
                '<span class="btb-card-note">选择组查看延迟</span>')

    connect_btn.click(
        fn=_do_connect,
        inputs=[],
        outputs=[
            connection_status, groups_tags, group_dropdown,
            switch_result, quick_dropdown, inst_switch_result,
            latency_group, latency_table,
        ],
    )

    # ── 保存 ──
    save_btn.click(
        fn=save_config,
        inputs=[api_url, api_secret],
        outputs=[connection_status],
    )

    # ── 刷新组 ──
    # 输出: groups_tags, group_dropdown, switch_result, quick_dropdown, inst_switch_result, latency_group, latency_table
    def _refresh_groups():
        groups = get_proxy_groups()
        if not groups:
            return ("", gr.Dropdown(choices=[], value=None), "",
                    gr.Dropdown(choices=[], value=None), "",
                    gr.Dropdown(choices=[], value=None),
                    '<span class="btb-card-note">无代理组</span>')
        html = _build_group_tags(groups)
        names = [g["name"] for g in groups]
        return (html, gr.Dropdown(choices=names, value=None), "",
                gr.Dropdown(choices=[], value=None), "",
                gr.Dropdown(choices=names, value=None),
                '<span class="btb-card-note">选择组查看延迟</span>')

    refresh_groups_btn.click(
        fn=_refresh_groups,
        inputs=[],
        outputs=[
            groups_tags, group_dropdown, switch_result,
            quick_dropdown, inst_switch_result,
            latency_group, latency_table,
        ],
    )

    # ── 选组 → 更新快捷下拉 + 延迟表 ──
    def _select_group(group_name: str) -> tuple[gr.Dropdown, str, str, str]:
        if not group_name:
            return (gr.Dropdown(choices=[], value=None), "",
                    "请选择组",
                    '<span class="btb-card-note">请选择组</span>')
        quick = _discover_quick_nodes(group_name)
        enc = urllib.parse.quote(group_name, safe="")
        data = _req("GET", f"/proxies/{enc}")
        current = data.get("now", "") if data else ""
        lat_table = build_latency_table(group_name, live_test=True)
        return (gr.Dropdown(choices=quick, value=None),
                f"当前: {current}", "",
                lat_table)

    group_dropdown.change(
        fn=_select_group,
        inputs=[group_dropdown],
        outputs=[quick_dropdown, switch_result, inst_switch_result, latency_table],
    )

    # ── 延迟组选择 → 刷新延迟表 ──
    latency_group.change(
        fn=lambda g: build_latency_table(g, live_test=True) if g else '<span class="btb-card-note">选择组</span>',
        inputs=[latency_group],
        outputs=[latency_table],
    )

    # ── 刷新延迟（实时探测所有节点）──
    refresh_latency_btn.click(
        fn=lambda g: build_latency_table(g, live_test=True) if g else '<span class="btb-card-note">请选择代理组</span>',
        inputs=[latency_group],
        outputs=[latency_table],
    )

    # ── 快捷切换 ──
    quick_dropdown.change(
        fn=lambda g, n: switch_node(g, n)[1] if g and n and n != "__sep__" else "",
        inputs=[group_dropdown, quick_dropdown],
        outputs=[switch_result],
    )

    # ── 最佳按钮 ──
    best_btn.click(
        fn=lambda g: switch_node(g, get_best_node(g))[1] if g and get_best_node(g) else "请先连接测试",
        inputs=[group_dropdown],
        outputs=[switch_result],
    )

    # ── 主实例自动切换开关 ──
    auto_main_toggle.change(
        fn=toggle_auto_main,
        inputs=[auto_main_toggle],
        outputs=[auto_main_status],
    )

    # ── 子实例自动切换开关 ──
    auto_child_toggle.change(
        fn=toggle_auto_child,
        inputs=[auto_child_toggle],
        outputs=[auto_child_status],
    )

    # ── 一键启动 ──
    def _auto_start(cnt: int):
        ConfigDB.insert(CFG_INSTANCE_COUNT, cnt)
        result = auto_start_instances(cnt)
        instances = get_now_available_instances()
        dd = gr.Dropdown(choices=instances, value=None) if instances else gr.Dropdown(choices=[], value=None)
        return result[0], result[1], dd

    auto_start_btn.click(
        fn=_auto_start,
        inputs=[instance_count],
        outputs=[inst_result, inst_table, instance_sel],
    )

    # ── 全部停止 ──
    stop_all_btn.click(
        fn=stop_all_instances,
        inputs=[],
        outputs=[inst_result, inst_table],
    )

    # ── 刷新实例 ──
    def _refresh_instances():
        instances = get_now_available_instances()
        tbl = _build_instance_table()
        dd = gr.Dropdown(choices=instances, value=None) if instances else gr.Dropdown(choices=[], value=None)
        return tbl, dd

    refresh_inst_btn.click(
        fn=_refresh_instances,
        inputs=[],
        outputs=[inst_table, instance_sel],
    )

    # ── 新增实例 ──
    add_one_btn.click(
        fn=add_single_instance,
        inputs=[],
        outputs=[inst_result, inst_table],
    )

    # ── 选择实例 → 加载其节点 ──
    instance_sel.change(
        fn=lambda iid: gr.Dropdown(choices=get_nodes_for_instance(iid or 0), value=None),
        inputs=[instance_sel],
        outputs=[node_sel],
    )

    # ── 切换实例节点 ──
    switch_inst_btn.click(
        fn=lambda iid, node: switch_instance_node(iid, node) if (iid or 0) > 0 and node else "请选择",
        inputs=[instance_sel, node_sel],
        outputs=[inst_switch_result],
    )

    # ── 自动切换状态 ──
    refresh_auto_btn.click(
        fn=get_auto_switch_status,
        inputs=[],
        outputs=[auto_status],
    )

    # 连接测试也刷新自动状态
    connect_btn.click(
        fn=get_auto_switch_status,
        inputs=[],
        outputs=[auto_status],
    )

    return connection_status
