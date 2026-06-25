class BiliBlockedError(RuntimeError):
    """412/403 风控/IP 被封异常，携带响应对象供调用方诊断。"""

    def __init__(self, message: str, *, response=None):
        super().__init__(message)
        self.response = response
