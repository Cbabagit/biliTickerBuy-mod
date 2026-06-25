import datetime


BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")
GO_UPLOADED_FILES_STATE_KEY = "go.uploaded_config_files"
DEFAULT_REQUEST_INTERVAL = 1000
DEFAULT_RATE_LIMIT_DELAY_MS = 100

# === 自适应限速引擎 ===
# 429 指数退避基数 (ms)，retry 1: min, retry 2: min*2 … capped at max
RATE_LIMIT_BACKOFF_BASE_MS = 1000       # 首次等待 1s
RATE_LIMIT_BACKOFF_MAX_MS = 30000       # 上限 30s

# 请求间隔随机抖动比例 (±jitter_ratio 范围均匀随机)
REQUEST_JITTER_RATIO = 0.3              # ±30%

# 1 秒盾对齐：同一账号同场次 create 最小间隔 (ms)
MIN_CREATE_INTERVAL_MS = 1000

# 多进程共享状态目录 (相对于 workspace)
SHARED_STATE_DIR = "shared_buyer_state"
SHARED_STATE_FILE = "buyer_state_%s.json"  # % account_key
SHARED_STATE_MAX_AGE_MS = 5000

# kfcTime cookie 清理（预热残留）
KFC_COOKIE_NAME = "kfcTime"
KFC_COOKIE_DOMAIN = ".bilibili.com"

# 代理池自动补给（参考上游 PR #992）
PROXY_API_REFRESH_INTERVAL_SECONDS = 300  # 5 分钟刷新一次
DEFAULT_CREATE_REQUEST_BATCH_SIZE = 3
DEFAULT_PROXY_MAX_CONSECUTIVE_FAILURES = 2
DEFAULT_PROXY_COOLDOWN_SECONDS = 180
DEFAULT_PROXY_BACKOFF_MAX_SECONDS = 600
DEFAULT_LOG_RETENTION_DAYS = 7
DEFAULT_MAX_LOG_FILES = 200
DEFAULT_MAX_RUN_DIRS = 100
BASE_URL = "https://show.bilibili.com"
WARMUP_AT_SECONDS = 5.0
COUNTDOWN_REPORT_INTERVAL_SECONDS = 15
DEFAULT_CREATE_RETRY_LIMIT = 20
DEFAULT_OUTER_LOOP_INTERVAL = 0
UPDATE_CHANNEL_KEY = "update_channel"
PACKAGE_NAME = "bilitickerbuy"
_LOG_VIEW_ROUTE = "/__btb/logs/view"
_LOG_STREAM_ROUTE = "/__btb/logs/stream"
MEOW_API_BASE = "https://api.chuckfang.com"
DEFAULT_TIMEOUT = (3.05, 8)
H2_TIMEOUT = {
    "connect": 3.05,
    "read": 5.0,
    "write": 5.0,
    "pool": 5.0,
}
H2_LIMITS = {
    "max_keepalive_connections": 10,
    "max_connections": 20,
    "keepalive_expiry": 60.0,
}
