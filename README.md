<div align="center">
  <img width="160" src="assets/icon.ico" alt="logo">
  <h2>biliTickerBuy-mod</h1>

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

**基于 [mikumifa/biliTickerBuy](https://github.com/mikumifa/biliTickerBuy) v2.15.13 的定制优化版本**  
开源免费，B 站会员购辅助工具 — 激进抢票策略、429 退避、多实例协调

</div>

## 📦 与上游的差异

| 特性 | 上游 | 本 Mod |
|------|------|--------|
| 429 限速处理 | 固定重试 | 指数退避，`min(2^(attempt-1), 60s)` |
| 请求并发 | 1 个 | 批量 3 并发 |
| 默认重试次数 | 10 次 | 30 次 |
| 1 秒盾 | 可能误判自身进程 | 已修复，排除自身 |
| 代理管理 | 基础切换 | 多实例反亲和、故障冷却、指数退避 |
| 监控仪表盘 | ✅ | 内置 Gradio 仪表盘 |
| 双实例协调 | ❌ | primary/secondary 协同 |
| UA 轮换 | 固定 | 随机化、延迟策略 |
| tinydb 损坏 | 可能崩溃 | 自动修复 |

## 🚀 快速开始

### 下载可执行文件

从 [Releases](https://github.com/Cbabagit/biliTickerBuy-mod/releases) 下载 `biliTickerBuy.exe`，直接运行即可。

### 从源码运行

```bash
git clone https://github.com/Cbabagit/biliTickerBuy-mod.git
cd biliTickerBuy-mod
pip install -r requirements.txt
python main.py
```

### 配置文件

1. 将你的 B 站 cookies 填入 `cookies.json`（参考模板格式）
2. 编辑 `config.json` 设置代理、通知等参数
3. 运行后通过 Gradio UI 上传票务配置文件

> **安全提示：** `cookies.json` 和 `config.json` 包含敏感信息，已默认被 `.gitignore` 排除，不会被提交到仓库。

## 🔧 Mod 改动详情

### 核心优化
- **`task/buy.py`** — 429 指数退避算法，失败时等待时间呈指数增长，避免封号
- **`task/shared_rate_state.py`** — 修复 1 秒盾逻辑，排除自身进程避免自我阻塞
- **`app_cmd/config/ConfigBasic.py`** — 默认重试 30 次、批量 3 并发

### 监控 & 协调
- **`tab/dashboard.py`** — Gradio 内置实时监控仪表盘
- **`app_cmd/ticker.py`** — dashboard tab 支持
- **`task/coordinator.py`** — 双实例协调（primary/secondary 通过共享 JSON 互通状态）

### 反检测
- **`task/antidetect.py`** — UA 轮换、请求延迟策略、行为随机化

### 网络层
- **`util/request/BiliRequest.py`** — 429 指数退避、ProxyError/100001 异常优化

### 稳定性
- **`interface/project.py`** — 移除 `is_hot_project` 依赖
- **`tab/settings.py`** — 索引有效性验证
- **`util/Storage/KVDatabase.py`** — tinydb 文件损坏自动修复

## 📖 完整文档

详细使用方法请参阅上游的 [安装指南](./docs/installation.md) 或 [飞书文档](https://n1x87b5cqay.feishu.cn/wiki/Eg4xwt3Dbiah02k1WqOcVk2YnMd)。

## ⚠️ 免责声明

本项目遵循 MIT License 许可协议，仅供个人学习与研究使用。请勿用于任何商业牟利行为或违反相关平台规则的用途。产生的后果由使用者自行承担。

## 🙏 致谢

- 上游项目：[mikumifa/biliTickerBuy](https://github.com/mikumifa/biliTickerBuy) — 感谢原作者的开源贡献
- 相关项目：[biliTickerSkill](https://github.com/mikumifa/biliTickerSkill)、[biliTickerStorm](https://github.com/mikumifa/biliTickerStorm)

## ⭐️ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Cbabagit/biliTickerBuy-mod&type=Date)](https://www.star-history.com/#Cbabagit/biliTickerBuy-mod&Date)
