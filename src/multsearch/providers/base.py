class BaseSearchProvider:
    """所有 provider 的基类：仅持有 API 端点与凭证。"""

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.api_key = api_key
