# util/proxy - Clash 代理管理
# 注意：不要在此处顶层导入 ClashInstanceManager 或 ClashSwitcher，
# 因为 util.__init__ → BiliRequest → ProxyManager → util.proxy 有循环依赖。
# ClashInstanceManager 等由具体的 consumer（clash_launcher.py, tab/clash.py）直接导入。

__all__: list[str] = []
