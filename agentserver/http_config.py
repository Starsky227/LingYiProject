"""Agent HTTP 请求配置 — 硬编码外部 API 地址与超时默认值。"""

from __future__ import annotations


def _normalize_base_url(value: str, fallback: str) -> str:
    base_url = value.strip().rstrip("/")
    return base_url or fallback.rstrip("/")


def build_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url.rstrip('/')}{normalized_path}"


def get_request_timeout(default_timeout: float = 480.0) -> float:
    return default_timeout


def get_request_retries(default_retries: int = 0) -> int:
    return default_retries


def get_xxapi_url(path: str) -> str:
    return build_url("https://v2.xxapi.cn", path)


def get_xingzhige_url(path: str) -> str:
    return build_url("https://api.xingzhige.com", path)


def get_jkyai_url(path: str) -> str:
    return build_url("https://api.jkyai.top", path)
