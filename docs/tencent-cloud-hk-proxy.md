# 腾讯云香港轻量 SOCKS5 代理搭建指南

## 适用场景

- 你已经有腾讯云账号且有免费额度
- 想让 LV2 账号走另一个出口 IP 抢票（避免和 LV5 共用电信 IP 导致同时 412）
- 需要微信支付且境外提供商的方案

## 推荐配置

| 项目 | 选择 |
|------|------|
| 地域 | **香港**（离大陆最近，延迟最低） |
| 镜像 | Ubuntu 22.04 LTS |
| 带宽 | 30Mbps（峰值） |
| 月付 | ¥24/月（免费额度可抵扣） |

## 部署步骤

### 1. 购买服务器

**腾讯云控制台 → 轻量应用服务器 → 新建**

- 地域选择「**香港**」
- 镜像选择「**Ubuntu 22.04 LTS**」
- 套餐选择最低配即可（1核1G 够用）
- 勾选「**免费分配公网 IP**」
- 设置 root 密码
- 立即购买（用免费额度）

### 2. SSH 登录

```bash
ssh root@<你的服务器IP>
```

### 3. 一键安装 Dante SOCKS5 代理

```bash
apt update && apt install -y dante-server
```

### 4. 配置 Dante

编辑配置：

```bash
nano /etc/danted.conf
```

替换为以下内容（**无密码认证，建议搭配防火墙 IP 白名单使用**）：

```
logoutput: syslog
user.privileged: root
user.unprivileged: nobody

# 监听所有网卡的 1080 端口
internal: 0.0.0.0 port = 1080

# 出口走主网卡
external: eth0

# SOCKS 版本
socksmethod: none
clientmethod: none

# 允许所有来源连接
client pass {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    log: error
}

# 允许所有流量通过
pass {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    protocol: tcp
    socksmethod: none
    log: error
}
```

### 5. 启动 Dante

```bash
# 重启服务
systemctl restart danted

# 设为开机自启
systemctl enable danted

# 查看状态
systemctl status danted

# 检查端口是否在监听
netstat -tlnp | grep 1080
```

### 6. 配置腾讯云防火墙

**腾讯云控制台 → 轻量服务器 → 防火墙 → 添加规则：**

| 协议 | 端口 | 来源 |
|------|------|------|
| TCP | 1080 | 0.0.0.0/0 |
| TCP | 22 | 你的电信公网 IP（仅限 SSH） |

> **安全建议**：SOCKS5 无密码情况下，强烈建议 TCP 1080 的**来源仅限你电信宽带的公网 IP**，
> 不要开 0.0.0.0/0。不知道公网 IP 可以去 `ip.sb` 查看。

### 7. 验证代理可用

在 Windows 电脑上测试：

```bash
curl -x socks5://<服务器IP>:1080 https://show.bilibili.com
```

如果能正常返回 HTML，说明代理成功。

### 8. 在 biliTickerBuy 中使用

LV2 账号的配置中设置：

```
--https-proxys "socks5://<服务器IP>:1080"
```

或者在 Gradio 界面 → 代理设置 → 填写 `socks5://<服务器IP>:1080`

## 故障排查

### 连接被拒绝
```bash
# 检查进程是否启动
systemctl status danted

# 查看防火墙
iptables -L -n
```

### 代理太慢
- 检查腾讯云 HK 到 B 站的延迟：`ping show.bilibili.com`
- 检查本地电信到 HK 的延迟：`ping <服务器IP>`
- 如果延迟 > 80ms，考虑换个地区的轻量服务器（如新加坡）

### 无法连接 B 站
```bash
# 在服务器上测试
curl https://show.bilibili.com
```
如果服务器本身连不上 B 站，可能是 DNS 问题：
```bash
echo "nameserver 8.8.8.8" >> /etc/resolv.conf
```

## 费用

腾讯云香港轻量 ¥24/月，首次购买通常有 1-3 个月免费试用额度。
如果你已经有免费额度，首月无需付费。
