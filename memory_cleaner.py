#!/usr/bin/env python3
"""
biliTickerBuy 内存清理器 🧹
========================================
为抢票服务而生:保护 biliTickerBuy 主/子进程 + BHYG 进程,
杀掉高内存非关键进程(Edge、QQ、微信、网易云等),
WebView2 进程受保护,绝不杀。

用法:
  python memory_cleaner.py              # 一键清理
  python memory_cleaner.py --monitor    # 持续监控模式(每60秒)
  python memory_cleaner.py --status     # 只查看状态
  python memory_cleaner.py --dry-run    # 预览模式(不真杀)
  python memory_cleaner.py --elevate    # 提权运行

配置文件:memory_cleaner.json(可选,自动读取)
"""

import psutil
import os
import sys
import time
import json
import argparse
import logging
from datetime import datetime
from typing import List, Set, Tuple, Optional

# ── 目录 ──────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "btb_logs")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "memory_cleaner.json")

# ── 默认配置 ──────────────────────────────────────────
DEFAULT_CONFIG = {
    # 内存阈值(MB):只有超过此值的进程才杀
    "threshold_mb": 100,

    # 扫描间隔(秒)
    "interval_sec": 60,

    # 受保护进程名(子串匹配,大小写不敏感)
    "protected_names": [
        "bilitickerbuy",       # 抢票主/子进程
        "main-bhyg-windows",  # BHYG 进程
        "msedgewebview2",     # WebView2 - 绝对不杀
        # 系统关键进程
        "system", "idle", "smss", "csrss", "wininit",
        "services", "lsass", "winlogon", "svchost", "spoolsv",
        "conhost", "taskhostw", "sihost", "runtimebroker",
        "dwm", "fontdrvhost", "securityhealthservice",
        "securityhealthsystray", "startmenuexperiencehost",
        "searchapp", "searchindexer", "ctfmon", "explorer",
    ],

    # 可杀目标进程名(子串匹配)
    "target_names": [
        "msedge",              # Edge 浏览器(非 WebView2)
        "qq",                  # QQ
        "weixin",              # 微信
        "wechatappex",         # 微信 AppEx
        "cloudmusic",          # 网易云音乐
        "telegram",            # Telegram
        "windowsterminal",     # Windows 终端
        "textinputhost",       # 文本输入服务
        "wetype_server",       # 微信输入法
        "spotify",             # Spotify
        "discord",             # Discord
        "slack",               # Slack
    ],

    # 日志保留天数
    "log_keep_days": 7,
}

# ── 日志设置 ──────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"memory_cleaner_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("memclean")


# ══════════════════════════════════════════════════════
#  核心函数
# ══════════════════════════════════════════════════════

def load_config() -> dict:
    """加载配置(JSON),合并默认值"""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            cfg.update(user_cfg)
            logger.info(f"已加载配置文件: {CONFIG_PATH}")
        except Exception as e:
            logger.warning(f"配置文件读取失败,使用默认: {e}")
    return cfg


def save_config(cfg: dict):
    """保存配置到 JSON"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info(f"配置已保存: {CONFIG_PATH}")
    except Exception as e:
        logger.error(f"保存配置失败: {e}")


def is_admin() -> bool:
    """检查是否以管理员权限运行"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False


def elevate():
    """
    以管理员权限重新启动当前脚本。
    返回 True 表示新进程已启动,当前应退出。
    """
    if is_admin():
        return False
    logger.warning("[WARN] 权限不足,请求提权...")
    try:
        import ctypes
        args = " ".join(sys.argv[1:] if len(sys.argv) > 1 else [])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            f'"{__file__}" {args}',
            None, 1
        )
        logger.info("已启动提权进程,当前进程退出")
        return True
    except Exception as e:
        logger.error(f"提权失败: {e}")
        return False


def get_protected_pids(cfg: dict) -> Set[int]:
    """
    获取受保护进程的 PID 集合。
    三层防护:
      1) 进程名子串匹配 (protected_names)
      2) exe 路径匹配（运行在 biliTickerBuy 目录下）
      3) 命令行关键字匹配 (bilitickerbuy / bhyg)
    """
    script_dir = os.path.dirname(os.path.abspath(__file__)).lower()
    prot_names = [n.lower() for n in cfg.get("protected_names", [])]
    protected = set()

    for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
        try:
            pid = proc.info['pid']
            name = (proc.info['name'] or '').lower()
            exe_path = (proc.info['exe'] or '').lower()
            cmdline = ' '.join(proc.info['cmdline'] or []).lower()

            # 1) 进程名子串匹配
            if any(p in name for p in prot_names):
                protected.add(pid)
                continue

            # 2) exe 路径：运行在 SCRIPT_DIR 下
            if script_dir in exe_path:
                protected.add(pid)
                continue

            # 3) 命令行含 bilitickerbuy / bhyg
            if 'bilitickerbuy' in cmdline or 'bhyg' in cmdline:
                protected.add(pid)
                continue

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return protected


def scan_and_kill(cfg: dict, dry_run: bool = False) -> Tuple[int, float]:
    """
    扫描并杀进程。
    返回: (杀掉的进程数, 释放的内存 MB)
    """
    threshold_mb = cfg.get("threshold_mb", 100)
    target_names = [t.lower() for t in cfg.get("target_names", [])]

    # 先获取受保护 PID
    protected_pids = get_protected_pids(cfg)

    killed_count = 0
    freed_mb = 0.0
    skipped_noaccess = 0
    skipped_below = 0

    # 保护集已包含 exe 路径 / cmdline 匹配，不再漏 biliTickerBuy
    for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            pinfo = proc.info
            pid = pinfo['pid']
            name = (pinfo['name'] or '').lower()
            mem_bytes = (pinfo['memory_info'].rss if pinfo['memory_info'] else 0)
            mem_mb = mem_bytes / (1024 * 1024)

            # ── 受保护？（PID 快照，不靠进程名反复匹配）──
            if pid in protected_pids:
                continue

            # ── 是目标进程？ ──
            should_kill = False
            for target in target_names:
                if target in name:
                    should_kill = True
                    break

            if not should_kill:
                continue

            # ── 内存阈值 ──
            if mem_mb < threshold_mb:
                skipped_below += 1
                continue

            # ── 执行 ──
            if dry_run:
                logger.info(f"  [预览] 将杀: PID={pid:<5} {name:<22} {mem_mb:>7.1f}MB")
                killed_count += 1
                freed_mb += mem_mb
                continue

            try:
                proc.kill()
                killed_count += 1
                freed_mb += mem_mb
                logger.info(f"  [KILL] PID={pid:<5} {name:<22} {mem_mb:>7.1f}MB")
            except psutil.NoSuchProcess:
                logger.debug(f"  进程已消失: PID={pid} {name}")
            except psutil.AccessDenied:
                skipped_noaccess += 1
                logger.debug(f"  权限不足跳过: PID={pid} {name}")
            except Exception as e:
                logger.error(f"  杀进程失败: PID={pid} {name} 错误={e}")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.debug(f"扫描异常: {e}")
            continue

    return killed_count, freed_mb, skipped_noaccess, skipped_below


def get_memory_summary() -> dict:
    """获取内存摘要"""
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / 1024**3, 2),
        "used_gb": round(mem.used / 1024**3, 2),
        "available_gb": round(mem.available / 1024**3, 2),
        "percent": mem.percent,
        "used_mb": round(mem.used / 1024**2, 1),
        "available_mb": round(mem.available / 1024**2, 1),
    }


def top_memory_processes(count: int = 10) -> List[dict]:
    """返回内存最高的进程列表"""
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            mem_mb = (proc.info['memory_info'].rss if proc.info['memory_info'] else 0) / (1024 * 1024)
            procs.append({
                "pid": proc.info['pid'],
                "name": proc.info['name'] or '',
                "mem_mb": round(mem_mb, 1),
            })
        except:
            continue
    procs.sort(key=lambda x: x["mem_mb"], reverse=True)
    return procs[:count]


def print_status(cfg: dict):
    """打印当前系统状态"""
    mem = get_memory_summary()
    admin = is_admin()

    logger.info("")
    logger.info("=" * 62)
    logger.info(f"  [MEMCLEAN] biliTickerBuy 内存清理器 - 状态报告")
    logger.info("=" * 62)
    logger.info(f"  [MEM] {mem['used_gb']}GB / {mem['total_gb']}GB ({mem['percent']}%)")
    logger.info(f"  [ADMIN] {'YES' if admin else 'NO'}"
                f"{'(建议提权以杀更多进程)' if not admin else ''}")

    # 受保护进程
    protected_pids = get_protected_pids(cfg)
    logger.info(f"")
    logger.info(f"  [PROTECTED] 受保护进程 ({len(protected_pids)} 个):")
    for pid in sorted(protected_pids):
        try:
            p = psutil.Process(pid)
            mem_mb = p.memory_info().rss / (1024 * 1024)
            logger.info(f"       PID={pid:<5}  {p.name():<24}  {mem_mb:>7.1f}MB")
        except:
            pass

    # 高内存 Top
    logger.info(f"")
    logger.info(f"  [TOP] 高内存进程 Top 15:")
    for i, p in enumerate(top_memory_processes(15), 1):
        logger.info(f"       {i:>2}. PID={p['pid']:<5}  {p['name']:<24}  {p['mem_mb']:>7.1f}MB")

    logger.info("=" * 62)
    logger.info(f"")


def cleanup_old_logs(cfg: dict):
    """清理旧日志"""
    keep_days = cfg.get("log_keep_days", 7)
    now = time.time()
    for fname in os.listdir(LOG_DIR):
        fpath = os.path.join(LOG_DIR, fname)
        if not fname.startswith("memory_cleaner_") or not fname.endswith(".log"):
            continue
        try:
            mtime = os.path.getmtime(fpath)
            if now - mtime > keep_days * 86400:
                os.remove(fpath)
                logger.info(f"已清理旧日志: {fname}")
        except:
            pass


# ══════════════════════════════════════════════════════
#  子命令
# ══════════════════════════════════════════════════════

def cmd_status(cfg: dict):
    """查看状态"""
    print_status(cfg)


def cmd_clean(cfg: dict, args):
    """执行一次清理"""
    if not is_admin():
        logger.warning("[WARN] 当前非管理员权限,部分进程可能杀不掉")
        if args.elevate:
            logger.info("尝试提权...")
            if elevate():
                return  # 新进程已接手

    print_status(cfg)
    logger.info(f"  [SCAN] 开始扫描清理 (阈值: {cfg['threshold_mb']}MB)...")
    logger.info(f"  {' 预览模式' if args.dry_run else '  执行模式'} - 不杀受保护进程")
    logger.info("─" * 62)

    killed, freed, noaccess, below = scan_and_kill(cfg, dry_run=args.dry_run)

    logger.info("─" * 62)
    mem_after = get_memory_summary()
    if args.dry_run:
        logger.info(f"  [DONE] 扫描完成 (预览模式)")
        logger.info(f"     可杀: {killed} 个进程, 可释放约 {freed:.0f}MB")
    else:
        logger.info(f"  [DONE] 清理完成")
        logger.info(f"     已杀: {killed} 个进程, 释放 {freed:.0f}MB")
    if noaccess > 0:
        logger.info(f"     因权限不足跳过: {noaccess} 个")
    if below > 0:
        logger.info(f"     低于阈值跳过: {below} 个")
    logger.info(f"     清理后内存: {mem_after['used_gb']}GB / {mem_after['total_gb']}GB "
                f"({mem_after['percent']}%)")

    cleanup_old_logs(cfg)


def cmd_monitor(cfg: dict, args):
    """持续监控模式"""
    if not is_admin():
        logger.warning("[WARN] 当前非管理员权限")
        if args.elevate:
            logger.info("尝试提权...")
            if elevate():
                return

    interval = args.interval or cfg["interval_sec"]
    logger.info(f"  [MONITOR] 持续监控模式启动 (间隔: {interval}s)")
    logger.info(f"  [SCAN] 每 {interval} 秒扫描一次,杀掉 >{cfg['threshold_mb']}MB 的目标进程")
    logger.info(f"  [PROTECT] biliTickerBuy / BHYG / WebView2 受保护")
    logger.info(f"  {' 预览模式' if args.dry_run else '  执行模式'}")
    logger.info(f"  Ctrl+C 停止")
    logger.info("=" * 62)

    cycle = 0
    total_killed = 0
    total_freed = 0.0

    try:
        while True:
            cycle += 1
            logger.info(f"")
            logger.info(f"── 第 {cycle} 轮扫描 ──")
            mem_before = get_memory_summary()
            logger.info(f"  清理前内存: {mem_before['used_gb']}GB / {mem_before['total_gb']}GB "
                        f"({mem_before['percent']}%)")

            killed, freed, noaccess, below = scan_and_kill(cfg, dry_run=args.dry_run)

            if not args.dry_run:
                total_killed += killed
                total_freed += freed

            mem_after = get_memory_summary()
            logger.info(f"  清理后内存: {mem_after['used_gb']}GB / {mem_after['total_gb']}GB "
                        f"({mem_after['percent']}%)")
            logger.info(f"  本轮: 杀 {killed} 进程 / 释放 {freed:.0f}MB "
                        f"(累计: 杀 {total_killed} / 释放 {total_freed:.0f}MB)")

            cleanup_old_logs(cfg)
            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info(f"")
        logger.info(f"  [STOP] 监控停止")
        logger.info(f"  本轮运行共清理: {total_killed} 进程 / 释放 {total_freed:.0f}MB")


def cmd_generate_config():
    """生成默认配置文件"""
    save_config(DEFAULT_CONFIG)
    logger.info(f"默认配置已写入: {CONFIG_PATH}")
    logger.info("可编辑该文件修改阈值、保护/目标进程等")


# ══════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="[MEMCLEAN] biliTickerBuy 内存清理器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python memory_cleaner.py              # 一键清理
  python memory_cleaner.py -m           # 持续监控
  python memory_cleaner.py -s           # 只看状态
  python memory_cleaner.py -d           # 预览(不真杀)
  python memory_cleaner.py -e           # 提权
  python memory_cleaner.py -t 200       # 只杀 >200MB 的进程
  python memory_cleaner.py --gen-config # 生成默认配置文件
  python memory_cleaner.py -m -i 120    # 每2分钟扫一次
""",
    )

    # 模式
    parser.add_argument("-m", "--monitor", action="store_true", help="持续监控模式")
    parser.add_argument("-s", "--status", action="store_true", help="只查看状态")

    # 选项
    parser.add_argument("-d", "--dry-run", action="store_true", help="预览模式(不真杀)")
    parser.add_argument("-e", "--elevate", action="store_true", help="尝试提权运行")
    parser.add_argument("-t", "--threshold", type=int, help=f"内存阈值 MB(默认 {DEFAULT_CONFIG['threshold_mb']})")
    parser.add_argument("-i", "--interval", type=int, help=f"监控间隔秒(默认 {DEFAULT_CONFIG['interval_sec']})")
    parser.add_argument("--gen-config", action="store_true", help="生成默认配置文件")
    parser.add_argument("--no-config", action="store_true", help="不使用配置文件")

    args = parser.parse_args()

    # 生成配置
    if args.gen_config:
        cmd_generate_config()
        return

    # 加载配置
    cfg = DEFAULT_CONFIG.copy()
    if not args.no_config and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            cfg.update(user_cfg)
        except Exception as e:
            logger.warning(f"配置加载失败: {e}")

    # CLI 覆写
    if args.threshold is not None:
        cfg["threshold_mb"] = args.threshold

    # 运行
    try:
        import psutil
    except ImportError:
        logger.error("需要 psutil 库。安装: pip install psutil")
        sys.exit(1)

    if args.status:
        cmd_status(cfg)
    elif args.monitor:
        cmd_monitor(cfg, args)
    else:
        cmd_clean(cfg, args)


if __name__ == "__main__":
    main()
