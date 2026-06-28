<div align="center">
  <img width="160" src="assets/icon.ico" alt="logo">
  <h2>biliTickerBuy-mod</h2>

<p>
  <a href="https://github.com/Cbabagit/biliTickerBuy-mod/releases">
    <img src="https://img.shields.io/github/downloads/Cbabagit/biliTickerBuy-mod/total" alt="Downloads">
  </a>
  <a href="https://github.com/Cbabagit/biliTickerBuy-mod/releases">
    <img src="https://img.shields.io/github/v/release/Cbabagit/biliTickerBuy-mod" alt="Release">
  </a>
  <a href="https://github.com/Cbabagit/biliTickerBuy-mod/issues">
    <img src="https://img.shields.io/github/issues/Cbabagit/biliTickerBuy-mod" alt="Issues">
  </a>
</p>

**此仓库为 [mikumifa/biliTickerBuy](https://github.com/mikumifa/biliTickerBuy) 的定制改版**  
基于上游 v2.15.13/2.15.15，主打**防风控、高并发、低延迟** — 含 NTP 时间校准、H2 连接池、全屏终端 TUI、代理故障自动恢复

</div>

---

## ✨ Mod 特性一览

| 特性 | 上游 | 本 Mod |
|------|------|--------|
| **429 限速处理** | 固定重试 | **指数退避** `min(2^(attempt-1), 60s)` |
| **H2 连接管理** | 单连接，切代理必重建 | **连接池**，代理切换零重建延迟 |
| **NTP 时间校准** | ❌ | 双服务器回退，每次抢票前校准 |
| **防风控策略** | 固定配置 | **独立 Tab**，7 组开关（请求抖动/退避/代理切换/预热等） |
| **终端界面** | 基础日志输出 | **Textual 全屏 TUI**（header 状态栏 + 滚动日志 + 去重） |
| **代理 API** | JSON 格式 | JSON + **纯文本格式** |
| **代理 host:port 去重** | ❌ | 忽略认证信息后去重 |
| **默认重试次数** | 10 次 | **30 次** |
| **请求并发** | 1 个 | **批量 3 并发** |
| **代理测试** | requests/PySocks | **httpx + socksio**（与抢票同库） |
| **Tinydb 损坏** | 可能崩溃 | 自动修复 |

---

## 🚀 快速开始

### 下载可执行文件

从 [Releases](https://github.com/Cbabagit/biliTickerBuy-mod/releases) 下载 `biliTickerBuy.exe`，解压后直接运行即可。

> **Windows 用户须知：** 运行时可能触发安全软件拦截。这是 PyInstaller 打包的正常现象，请放心放行。如不信任可改为从源码运行。

### 从源码运行

```bash
git clone https://github.com/Cbabagit/biliTickerBuy-mod.git
cd biliTickerBuy-mod
pip install -r requirements.txt
python main.py --ticker
```

然后打开浏览器访问 `http://127.0.0.1:7860/`。

---

## 📖 使用指南

### 第一步：登录

1. 打开 Gradio 界面，切到「账号登录」Tab
2. 点击「生成二维码」，用 B 站手机客户端扫码
3. 扫码后点击「确认登录」
4. 登录成功后自动保存，后续无需重复登录

### 第二步：生成配置

1. 切到「生成配置」Tab
2. 输入活动的 B 站会员购链接（如 `https://show.bilibili.com/platform/detail.html?id=xxxx`）
3. 点击「获取票务信息」
4. 选择票档、填写联系信息、选择地址、选择购票人
5. 点击「生成配置」

### 第三步：开始抢票

1. 切到「操作抢票」Tab
2. 上传生成的 JSON 配置文件（可多选）
3. 设置抢票时间
4. 点击「开始抢票」
5. 查看实时日志

### 第四步：配置高级选项

在「高级设置」中可自定义：
- **代理**：填写 HTTP/SOCKS5 代理地址，或配置代理 API 自动获取
- **防风控**：开关各项策略（请求抖动、429 退避、代理切换、预热等）
- **杂项**：日志级别、并发策略、推送通知等

---

## 🔧 Mod 改动详情

### 防风控系统

#### NTP 时间校准 (`util/TimeUtil.py`)
- 双服务器回退（`ntp1.aliyun.com` → `ntp.ntsc.ac.cn`）
- 每次抢票前自动校准，精度毫秒级
- 支持 `GET /api/ticket/project/infoByDate` 返回值交叉校验

#### H2 连接池 (`util/request/BiliRequest.py`)
- **根因修复**：上游每次切代理都销毁唯一的 H2 client → 下次请求重建 TCP+TLS+H2 前言（300~1500ms）
- **方案**：维护 H2 连接池 `dict[str, httpx.Client]`，key = 代理 URL
  - 不同代理各自独立连接
  - 切回已有代理：零额外延迟（实测 <5ms）
  - 仅异常（超时/协议错误）才销毁当前连接
- **测试结果**：
  ```
  直连切Clash→切回直连：直连池复用 3.2ms ✅
  重复切回同一代理：   3.8ms ✅
  代理池整体替换：     全清池（旧代理地址失效）
  ```

#### 防风控策略 Tab
- **请求间隔抖动**：±30% 随机化，使行为更接近人类
- **HTTP 429 退避**：指数退避 `min(2^(attempt-1), 60s)`，避免被封
- **代理自动切换**：失败后自动换下一个，支持故障冷却
- **创建订单重试**：失败自动重试，可配置次数和批次大小
- **H2 连接预热**：启动时预先建立连接，减少首次请求延迟
- **准备订单退避**：指数退避重试，避免瞬时拥塞
- **Token 策略**：控制日志中的 Token 显隐

#### 代理增强 (`util/proxy/`)
- **纯文本格式支持**：Nexip 等来源每行一个完整 URL（`socks5://user:pass@host:port`）
- **host:port 去重**：忽略认证信息后去重，避免重复代理地址
- **httpx 代理测试**：与抢票流程使用相同 SOCKS5 库（`socksio`）
- **HTTPS 协议支持**：代理 API 可选择获取 HTTPS 代理

### 终端界面

#### Textual 全屏 TUI (`util/log/TerminalRenderer.py`)
- 全屏渲染，header 状态栏显示配名称/日志文件/运行平台
- 滚动日志区域，自动去重
- 自动回退：Textual 不可用时降级到 `PlainTerminalRenderer`

### 其他改进

| 模块 | 改动 |
|------|------|
| `util/proxy/ProxyApiProvider.py` | 纯文本格式 + host:port 去重 |
| `util/proxy/ProxyManager.py` | host:port 去重 + 回连重试策略 |
| `util/proxy/ProxyTester.py` | 改用 httpx（socksio），与抢票同库 |
| `util/request/BiliRequest.py` | 429 指数退避、ProxyError/100001 优化 |
| `util/Storage/KVDatabase.py` | tinydb 自动修复（上游 v2.15.13 已自带） |
| `util/log/TerminalRenderer.py` | 全屏 TUI + 降级回退 |
| `interface/project.py` | 移除 `is_hot_project` 依赖 |
| `tab/settings.py` | 索引有效性验证 |
| `app_cmd/config/ConfigBasic.py` | 默认重试 30 次、批量 3 并发 |
| `task/buy.py` | `_jittered_delay_ms` 抖动函数、H2 连接池适配 |
| `task/buy_helpers.py` | 时间校准集成 |
| `tab/go.py` | 自动填充时间符号修正 |
| `tab/config.py` | 防风控子 tab、代理格式下拉 |

---

## 🧪 测试数据

### H2 连接池 — 代理切换延迟

| 场景 | 耗时 | 说明 |
|------|------|------|
| 直连冷启动 | ~7700ms | H2 建连（首次不可避免） |
| Clash 代理冷启动 | ~2500ms | SOCKS5 + TCP + TLS |
| 直连→Clash | ~2800ms | 全清池（`replace_proxy_pool`） |
| Clash→直连（首次） | ~6600ms | 直连云首次 H2 建连 |
| 切回已有连接 | **<5ms** | 池复用，零额外延迟 ✅ |

测试 URL：本地 cloudflared `/ready` 端点（延迟 4~12ms，排除外网波动）

### NTP 校准

| 指标 | 值 |
|------|-----|
| 主服务器 | `ntp1.aliyun.com` |
| 回退服务器 | `ntp.ntsc.ac.cn` |
| 本地偏差 | 约 1.7~1.8 秒 |
| 校准精度 | 毫秒级 |

---

## 📦 目录结构

```
biliTickerBuy-mod/
├── app_cmd/          # 命令行入口（ticker / buy）
├── tab/              # Gradio Tab 页面
│   ├── config.py     # 高级设置（代理、防风控、杂项）
│   ├── go.py         # 操作抢票
│   ├── settings.py   # 生成配置 + 账号登录
│   └── ...
├── task/             # 核心抢票逻辑
│   ├── buy.py        # 抢票主循环
│   └── buy_helpers.py
├── util/             # 工具模块
│   ├── request/      # HTTP 客户端（BiliRequest + H2 连接池）
│   ├── proxy/        # 代理管理、测试、API 获取
│   ├── log/          # 日志系统、Terminal UI
│   ├── notifer/      # 推送通知（Server酱/Bark/ntfy/MeoW）
│   ├── TimeUtil.py   # NTP 时间校准
│   └── Constant.py   # 默认常量
├── assets/           # 图标、CSS
├── main.py           # 入口
├── requirements.txt  # Python 依赖
└── README.md         # 本文件
```

---

## 🛠️ 编译 EXE

```bash
pip install pyinstaller
pyinstaller main.spec
# 产物：dist/biliTickerBuy.exe
```

编译前确保激活了 `venv`（否则打包体积会大很多）。

---

## 🔗 相关链接

| 链接 | 说明 |
|------|------|
| [GitHub Releases](https://github.com/Cbabagit/biliTickerBuy-mod/releases) | 下载已编译 EXE |
| [上游仓库](https://github.com/mikumifa/biliTickerBuy) | 原版 biliTickerBuy |
| [上游飞书文档](https://n1x87b5cqay.feishu.cn/wiki/Eg4xwt3Dbiah02k1WqOcVk2YnMd) | 完整使用说明 |

---

## ⚠️ 免责声明

本项目遵循 MIT License 许可协议，仅供个人学习与研究使用。请勿用于任何商业牟利行为或违反相关平台规则的用途。产生的后果由使用者自行承担。

## 🙏 致谢

- [mikumifa/biliTickerBuy](https://github.com/mikumifa/biliTickerBuy) — 感谢原作者的出色工作与开源贡献

## ⭐️ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Cbabagit/biliTickerBuy-mod&type=Date)](https://www.star-history.com/#Cbabagit/biliTickerBuy-mod&Date)
