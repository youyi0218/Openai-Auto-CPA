import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import socket
import hashlib
import base64
import threading
import argparse
import queue
from http.cookies import SimpleCookie
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable
import urllib.parse
import urllib.request
import urllib.error

from curl_cffi import requests

# ==========================================
# 日志事件发射器
# ==========================================


class EventEmitter:
    """
    将注册流程中的日志事件发射到队列，供 SSE 消费。
    同时支持 CLI 模式（直接 print）。
    """

    def __init__(self, q: Optional[queue.Queue] = None, cli_mode: bool = False):
        self._q = q
        self._cli_mode = cli_mode

    def emit(self, level: str, message: str, step: str = "") -> None:
        """
        level: "info" | "success" | "error" | "warn"
        step:  可选的流程阶段标识，如 "check_proxy" / "create_email" 等
        """
        ts = datetime.now().strftime("%H:%M:%S")
        event = {
            "ts": ts,
            "level": level,
            "message": message,
            "step": step,
        }
        if self._cli_mode:
            prefix_map = {
                "info": "[*]",
                "success": "[+]",
                "error": "[Error]",
                "warn": "[!]",
            }
            prefix = prefix_map.get(level, "[*]")
            print(f"{prefix} {message}")
        if self._q is not None:
            try:
                self._q.put_nowait(event)
            except queue.Full:
                pass

    def info(self, msg: str, step: str = "") -> None:
        self.emit("info", msg, step)

    def success(self, msg: str, step: str = "") -> None:
        self.emit("success", msg, step)

    def error(self, msg: str, step: str = "") -> None:
        self.emit("error", msg, step)

    def warn(self, msg: str, step: str = "") -> None:
        self.emit("warn", msg, step)


# 默认 CLI 发射器（兼容直接运行）
_cli_emitter = EventEmitter(cli_mode=True)


# ==========================================
# Mail.tm 临时邮箱 API
# ==========================================

MAILTM_BASE = "https://api.mail.tm"
DEFAULT_PROXY_POOL_URL = "https://zenproxy.top/api/fetch"
DEFAULT_PROXY_POOL_AUTH_MODE = "query"
DEFAULT_PROXY_POOL_API_KEY = "19c0ec43-8f76-4c97-81bc-bcda059eeba4"
DEFAULT_PROXY_POOL_COUNT = 1
DEFAULT_PROXY_POOL_COUNTRY = "US"
DEFAULT_PROXY_POOL_PROVIDER = "zenproxy_api"
DEFAULT_DREAMY_SOCKS5_PROXY = "socks5://127.0.0.1:1080"
DEFAULT_DOCKER_WARP_PROXY = "socks5://127.0.0.1:9091"
DEFAULT_HTTP_VERSION = "v2"
H3_PROXY_ERROR_HINT = "HTTP/3 is not supported over an HTTP proxy"
POOL_RELAY_RETRIES = 2
POOL_PROXY_FETCH_RETRIES = 3
POOL_RELAY_REQUEST_RETRIES = 2


def _call_with_http_fallback(request_func, url: str, **kwargs: Any):
    """
    curl_cffi 在某些站点可能优先尝试 H3，遇到 HTTP 代理不支持时自动降级到 HTTP/1.1 重试。
    """
    try:
        return request_func(url, **kwargs)
    except Exception as exc:
        if H3_PROXY_ERROR_HINT not in str(exc):
            raise
        retry_kwargs = dict(kwargs)
        retry_kwargs["http_version"] = "v1"
        return request_func(url, **retry_kwargs)


def _normalize_proxy_value(proxy_value: Any) -> str:
    value = str(proxy_value or "").strip().strip('"').strip("'")
    if not value:
        return ""
    if value.startswith("{") or value.startswith("[") or value.startswith("<"):
        return ""
    if "://" in value:
        return value
    if ":" not in value:
        return ""
    return f"http://{value}"


def _to_proxies_dict(proxy_value: str) -> Optional[Dict[str, str]]:
    normalized = _normalize_proxy_value(proxy_value)
    if not normalized:
        return None
    return {"http": normalized, "https": normalized}


def _build_proxy_from_host_port(host: Any, port: Any, proxy_type: Any = "") -> str:
    host_value = str(host or "").strip()
    port_value = str(port or "").strip()
    if not host_value or not port_value:
        return ""
    proxy_type_value = str(proxy_type or "").strip().lower()
    if proxy_type_value in ("socks5", "socks", "shadowsocks"):
        return _normalize_proxy_value(f"socks5://{host_value}:{port_value}")
    return _normalize_proxy_value(f"http://{host_value}:{port_value}")


def _normalize_pool_provider(provider: Any) -> str:
    value = str(provider or DEFAULT_PROXY_POOL_PROVIDER).strip().lower()
    if value in ("zenproxy", "zenproxy_api", "zenproxy_pool"):
        return "zenproxy_api"
    if value in ("dreamy_socks5_pool", "dreamy_socks5", "socks5_proxy"):
        return "dreamy_socks5_pool"
    if value in ("docker_warp_socks", "warp_socks", "warp"):
        return "docker_warp_socks"
    return DEFAULT_PROXY_POOL_PROVIDER


def _default_pool_endpoint(provider: str) -> str:
    if provider == "dreamy_socks5_pool":
        return DEFAULT_DREAMY_SOCKS5_PROXY
    if provider == "docker_warp_socks":
        return DEFAULT_DOCKER_WARP_PROXY
    return DEFAULT_PROXY_POOL_URL


def _pool_host_from_api_url(api_url: str) -> str:
    raw = str(api_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        return str(parsed.hostname or "").strip()
    except Exception:
        return ""


def _pool_relay_url_from_fetch_url(api_url: str) -> str:
    raw = str(api_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc
        if not netloc:
            return ""
        return f"{scheme}://{netloc}/api/relay"
    except Exception:
        return ""


def _trace_via_pool_relay(pool_cfg: Dict[str, Any]) -> str:
    relay_url = _pool_relay_url_from_fetch_url(str(pool_cfg.get("api_url") or ""))
    if not relay_url:
        raise RuntimeError("代理池 relay 地址解析失败")

    api_key = str(pool_cfg.get("api_key") or DEFAULT_PROXY_POOL_API_KEY).strip() or DEFAULT_PROXY_POOL_API_KEY
    country = str(pool_cfg.get("country") or DEFAULT_PROXY_POOL_COUNTRY).strip().upper() or DEFAULT_PROXY_POOL_COUNTRY
    timeout = int(pool_cfg.get("timeout_seconds") or 10)
    timeout = max(8, min(timeout, 30))

    params = {
        "api_key": api_key,
        "url": "https://cloudflare.com/cdn-cgi/trace",
        "country": country,
    }
    retry_count = max(1, int(pool_cfg.get("relay_retries") or POOL_RELAY_RETRIES))
    last_error = ""
    for i in range(retry_count):
        try:
            resp = _call_with_http_fallback(
                requests.get,
                relay_url,
                params=params,
                impersonate="chrome",
                timeout=timeout,
            )
            if resp.status_code == 200:
                return str(resp.text or "")
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_error = str(exc)
        if i < retry_count - 1:
            time.sleep(min(0.3 * (i + 1), 1.0))
    raise RuntimeError(f"代理池 relay 请求失败: {last_error or 'unknown error'}")
def _extract_proxy_from_obj(obj: Any, relay_host: str = "") -> str:
    if isinstance(obj, str):
        return _normalize_proxy_value(obj)
    if isinstance(obj, (list, tuple)):
        for item in obj:
            proxy = _extract_proxy_from_obj(item, relay_host)
            if proxy:
                return proxy
        return ""
    if isinstance(obj, dict):
        local_port = obj.get("local_port")
        if local_port in (None, ""):
            local_port = obj.get("localPort")
        if local_port not in (None, ""):
            # ZenProxy 文档中的 local_port 是代理绑定端口，优先使用 api_url 主机名。
            if relay_host:
                proxy = _normalize_proxy_value(f"http://{relay_host}:{local_port}")
                if proxy:
                    return proxy
            proxy = _normalize_proxy_value(f"http://127.0.0.1:{local_port}")
            if proxy:
                return proxy

        host = str(obj.get("ip") or obj.get("host") or obj.get("server") or "").strip()
        port = str(obj.get("port") or "").strip()
        proxy_type = obj.get("type") or obj.get("protocol") or obj.get("scheme") or ""
        if host and port:
            proxy = _build_proxy_from_host_port(host, port, proxy_type)
            if proxy:
                return proxy

        for key in ("proxy", "proxy_url", "url", "value", "result", "data", "proxy_list", "list", "proxies"):
            if key in obj:
                proxy = _extract_proxy_from_obj(obj.get(key), relay_host)
                if proxy:
                    return proxy

        for value in obj.values():
            proxy = _extract_proxy_from_obj(value, relay_host)
            if proxy:
                return proxy
    return ""


def _proxy_tcp_reachable(proxy_url: str, timeout_seconds: float = 1.2) -> bool:
    value = str(proxy_url or "").strip()
    if not value:
        return False
    if "://" not in value:
        value = "http://" + value
    try:
        parsed = urlparse(value)
        host = str(parsed.hostname or "").strip()
        port = int(parsed.port or 0)
    except Exception:
        return False
    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except Exception:
        return False


def _fetch_proxy_from_pool(pool_cfg: Dict[str, Any]) -> str:
    enabled = bool(pool_cfg.get("enabled"))
    if not enabled:
        return ""

    provider = _normalize_pool_provider(pool_cfg.get("provider"))
    if provider in ("dreamy_socks5_pool", "docker_warp_socks"):
        endpoint = str(
            pool_cfg.get("fixed_proxy")
            or pool_cfg.get("api_url")
            or _default_pool_endpoint(provider)
        ).strip()
        proxy = _normalize_proxy_value(endpoint)
        if not proxy:
            raise RuntimeError("固定代理地址无效，请检查代理池配置")
        return proxy

    api_url = str(pool_cfg.get("api_url") or DEFAULT_PROXY_POOL_URL).strip() or DEFAULT_PROXY_POOL_URL
    auth_mode = str(pool_cfg.get("auth_mode") or DEFAULT_PROXY_POOL_AUTH_MODE).strip().lower()
    if auth_mode not in ("header", "query"):
        auth_mode = DEFAULT_PROXY_POOL_AUTH_MODE
    api_key = str(pool_cfg.get("api_key") or "").strip()
    if not api_key and provider == "zenproxy_api":
        api_key = DEFAULT_PROXY_POOL_API_KEY
    relay_host = str(pool_cfg.get("relay_host") or "").strip()
    if not relay_host:
        relay_host = _pool_host_from_api_url(api_url)
    try:
        count = int(pool_cfg.get("count") or DEFAULT_PROXY_POOL_COUNT)
    except (TypeError, ValueError):
        count = DEFAULT_PROXY_POOL_COUNT
    count = max(1, min(count, 20))
    country = str(pool_cfg.get("country") or DEFAULT_PROXY_POOL_COUNTRY).strip().upper() or DEFAULT_PROXY_POOL_COUNTRY
    timeout = int(pool_cfg.get("timeout_seconds") or 10)
    timeout = max(3, min(timeout, 30))

    headers: Dict[str, str] = {}
    params: Dict[str, str] = {"count": str(count), "country": country}
    if api_key:
        if auth_mode == "query":
            params["api_key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    resp = _call_with_http_fallback(
        requests.get,
        api_url,
        headers=headers or None,
        params=params or None,
        http_version=DEFAULT_HTTP_VERSION,
        impersonate="chrome",
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"代理池请求失败: HTTP {resp.status_code}")

    proxy = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            proxies = payload.get("proxies")
            if isinstance(proxies, list):
                for item in proxies:
                    proxy = _extract_proxy_from_obj(item, relay_host)
                    if proxy:
                        break
        if not proxy:
            proxy = _extract_proxy_from_obj(payload, relay_host)
    except Exception:
        proxy = ""

    if not proxy:
        proxy = _normalize_proxy_value(resp.text)
    if not proxy:
        raise RuntimeError("代理池响应中未找到可用代理")
    return proxy


def _resolve_request_proxies(
    default_proxies: Any = None,
    proxy_selector: Optional[Callable[[], Any]] = None,
) -> Any:
    if not proxy_selector:
        return default_proxies
    try:
        selected = proxy_selector()
        if selected is not None:
            return selected
    except Exception:
        pass
    return default_proxies


def _mailtm_headers(*, token: str = "", use_json: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if use_json:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _mailtm_domains(proxies: Any = None) -> list[str]:
    resp = _call_with_http_fallback(
        requests.get,
        f"{MAILTM_BASE}/domains",
        headers=_mailtm_headers(),
        proxies=proxies,
        http_version=DEFAULT_HTTP_VERSION,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"获取 Mail.tm 域名失败，状态码: {resp.status_code}")

    data = resp.json()
    domains = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("hydra:member") or data.get("items") or []
    else:
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        is_active = item.get("isActive", True)
        is_private = item.get("isPrivate", False)
        if domain and is_active and not is_private:
            domains.append(domain)

    return domains


def get_email_and_token(
    proxies: Any = None,
    emitter: EventEmitter = _cli_emitter,
    proxy_selector: Optional[Callable[[], Any]] = None,
) -> tuple[str, str]:
    """创建 Mail.tm 邮箱并获取 Bearer Token"""
    try:
        domains = _mailtm_domains(_resolve_request_proxies(proxies, proxy_selector))
        if not domains:
            emitter.error("Mail.tm 没有可用域名", step="create_email")
            return "", ""
        domain = random.choice(domains)

        for _ in range(5):
            local = f"oc{secrets.token_hex(5)}"
            email = f"{local}@{domain}"
            password = secrets.token_urlsafe(18)

            create_resp = _call_with_http_fallback(
                requests.post,
                f"{MAILTM_BASE}/accounts",
                headers=_mailtm_headers(use_json=True),
                json={"address": email, "password": password},
                proxies=_resolve_request_proxies(proxies, proxy_selector),
                http_version=DEFAULT_HTTP_VERSION,
                impersonate="chrome",
                timeout=15,
            )

            if create_resp.status_code not in (200, 201):
                continue

            token_resp = _call_with_http_fallback(
                requests.post,
                f"{MAILTM_BASE}/token",
                headers=_mailtm_headers(use_json=True),
                json={"address": email, "password": password},
                proxies=_resolve_request_proxies(proxies, proxy_selector),
                http_version=DEFAULT_HTTP_VERSION,
                impersonate="chrome",
                timeout=15,
            )

            if token_resp.status_code == 200:
                token = str(token_resp.json().get("token") or "").strip()
                if token:
                    return email, token

        emitter.error("Mail.tm 邮箱创建成功但获取 Token 失败", step="create_email")
        return "", ""
    except Exception as e:
        emitter.error(f"请求 Mail.tm API 出错: {e}", step="create_email")
        return "", ""


def generate_account_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    if length < 10:
        length = 10
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(ch.islower() for ch in password)
            and any(ch.isupper() for ch in password)
            and any(ch.isdigit() for ch in password)
        ):
            return password


def get_oai_code(
    token: str, email: str, proxies: Any = None, emitter: EventEmitter = _cli_emitter,
    stop_event: Optional[threading.Event] = None,
    proxy_selector: Optional[Callable[[], Any]] = None,
) -> str:
    """使用 Mail.tm Token 轮询获取 OpenAI 验证码"""
    url_list = f"{MAILTM_BASE}/messages"
    regex = r"(?<!\d)(\d{6})(?!\d)"
    seen_ids: set[str] = set()

    emitter.info(f"正在等待邮箱 {email} 的验证码...", step="wait_otp")

    for i in range(40):
        if stop_event and stop_event.is_set():
            return ""
        try:
            resp = _call_with_http_fallback(
                requests.get,
                url_list,
                headers=_mailtm_headers(token=token),
                proxies=_resolve_request_proxies(proxies, proxy_selector),
                http_version=DEFAULT_HTTP_VERSION,
                impersonate="chrome",
                timeout=15,
            )
            if resp.status_code != 200:
                time.sleep(3)
                continue

            data = resp.json()
            if isinstance(data, list):
                messages = data
            elif isinstance(data, dict):
                messages = data.get("hydra:member") or data.get("messages") or []
            else:
                messages = []

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = str(msg.get("id") or "").strip()
                if not msg_id or msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                read_resp = _call_with_http_fallback(
                    requests.get,
                    f"{MAILTM_BASE}/messages/{msg_id}",
                    headers=_mailtm_headers(token=token),
                    proxies=_resolve_request_proxies(proxies, proxy_selector),
                    http_version=DEFAULT_HTTP_VERSION,
                    impersonate="chrome",
                    timeout=15,
                )
                if read_resp.status_code != 200:
                    continue

                mail_data = read_resp.json()
                sender = str(
                    ((mail_data.get("from") or {}).get("address") or "")
                ).lower()
                subject = str(mail_data.get("subject") or "")
                intro = str(mail_data.get("intro") or "")
                text = str(mail_data.get("text") or "")
                html = mail_data.get("html") or ""
                if isinstance(html, list):
                    html = "\n".join(str(x) for x in html)
                content = "\n".join([subject, intro, text, str(html)])

                if "openai" not in sender and "openai" not in content.lower():
                    continue

                m = re.search(regex, content)
                if m:
                    emitter.success(f"验证码已到达: {m.group(1)}", step="wait_otp")
                    return m.group(1)
        except Exception:
            pass

        # 每轮等待时输出进度
        if (i + 1) % 5 == 0:
            emitter.info(f"已等待 {(i+1)*3} 秒，继续轮询...", step="wait_otp")
        time.sleep(3)

    emitter.error("超时，未收到验证码", step="wait_otp")
    return ""


# ==========================================
# OAuth 授权与辅助函数
# ==========================================

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = f"http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, str]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _post_form(
    url: str,
    data: Dict[str, str],
    timeout: int = 30,
    proxy: str = "",
) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    handlers = []
    normalized_proxy = _normalize_proxy_value(proxy)
    if normalized_proxy:
        handlers.append(urllib.request.ProxyHandler({"http": normalized_proxy, "https": normalized_proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    proxy: str = "",
    account_password: str = "",
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        proxy=proxy,
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }
    if account_password:
        config["password"] = account_password

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# 核心注册逻辑
# ==========================================

from . import TOKENS_DIR as _PKG_TOKENS_DIR

TOKENS_DIR = str(_PKG_TOKENS_DIR)


def run(
    proxy: Optional[str],
    emitter: EventEmitter = _cli_emitter,
    stop_event: Optional[threading.Event] = None,
    mail_provider=None,
    proxy_pool_config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    static_proxy = _normalize_proxy_value(proxy)
    static_proxies: Any = _to_proxies_dict(static_proxy)

    pool_cfg_raw = proxy_pool_config or {}
    pool_provider = _normalize_pool_provider(pool_cfg_raw.get("provider"))
    pool_default_endpoint = _default_pool_endpoint(pool_provider)
    pool_api_key = str(pool_cfg_raw.get("api_key") or "").strip()
    if not pool_api_key and pool_provider == "zenproxy_api":
        pool_api_key = DEFAULT_PROXY_POOL_API_KEY
    pool_cfg = {
        "enabled": bool(pool_cfg_raw.get("enabled", False)),
        "provider": pool_provider,
        "api_url": str(pool_cfg_raw.get("api_url") or pool_default_endpoint).strip() or pool_default_endpoint,
        "fixed_proxy": str(pool_cfg_raw.get("fixed_proxy") or "").strip(),
        "auth_mode": str(pool_cfg_raw.get("auth_mode") or DEFAULT_PROXY_POOL_AUTH_MODE).strip().lower() or DEFAULT_PROXY_POOL_AUTH_MODE,
        "api_key": pool_api_key,
        "count": pool_cfg_raw.get("count", DEFAULT_PROXY_POOL_COUNT),
        "country": str(pool_cfg_raw.get("country") or DEFAULT_PROXY_POOL_COUNTRY).strip().upper() or DEFAULT_PROXY_POOL_COUNTRY,
        "timeout_seconds": int(pool_cfg_raw.get("timeout_seconds") or 10),
    }
    if pool_cfg["auth_mode"] not in ("header", "query"):
        pool_cfg["auth_mode"] = DEFAULT_PROXY_POOL_AUTH_MODE
    try:
        pool_cfg["count"] = max(1, min(int(pool_cfg.get("count") or DEFAULT_PROXY_POOL_COUNT), 20))
    except (TypeError, ValueError):
        pool_cfg["count"] = DEFAULT_PROXY_POOL_COUNT

    last_pool_proxy = ""
    pool_fail_streak = 0
    warned_fallback = False

    def _next_proxy_value() -> str:
        nonlocal last_pool_proxy, pool_fail_streak, warned_fallback
        if pool_cfg["enabled"]:
            max_fetch_retries = max(1, int(pool_cfg.get("fetch_retries") or POOL_PROXY_FETCH_RETRIES))
            last_error = ""
            for _ in range(max_fetch_retries):
                try:
                    fetched = _fetch_proxy_from_pool(pool_cfg)
                    if fetched and not _proxy_tcp_reachable(fetched):
                        last_error = f"代理池代理不可达: {fetched}"
                        continue
                    last_pool_proxy = fetched
                    pool_fail_streak = 0
                    warned_fallback = False
                    return fetched
                except Exception as e:
                    last_error = str(e)

            pool_fail_streak += 1
            if static_proxy:
                if not warned_fallback:
                    emitter.warn(f"代理池不可用，回退固定代理: {last_error or 'unknown error'}", step="check_proxy")
                    warned_fallback = True
                return static_proxy
            if pool_fail_streak <= 3:
                emitter.warn(f"代理池不可用: {last_error or 'unknown error'}", step="check_proxy")
            return ""
        return static_proxy
    def _next_proxies() -> Any:
        proxy_value = _next_proxy_value()
        return _to_proxies_dict(proxy_value)

    s = requests.Session(impersonate="chrome")
    pool_relay_url = _pool_relay_url_from_fetch_url(str(pool_cfg.get("api_url") or ""))
    pool_relay_enabled = bool(
        pool_cfg["enabled"]
        and pool_cfg.get("provider") == "zenproxy_api"
        and pool_relay_url
    )
    relay_cookie_jar: Dict[str, str] = {}
    pool_relay_api_key = str(pool_cfg.get("api_key") or DEFAULT_PROXY_POOL_API_KEY).strip() or DEFAULT_PROXY_POOL_API_KEY
    pool_relay_country = str(pool_cfg.get("country") or DEFAULT_PROXY_POOL_COUNTRY).strip().upper() or DEFAULT_PROXY_POOL_COUNTRY
    relay_fallback_warned = False
    relay_bypass_openai_hosts = False
    openai_relay_probe_done = False
    mail_proxy_selector = None if pool_relay_enabled else _next_proxy_value
    mail_proxies_selector = None if pool_relay_enabled else _next_proxies

    def _fallback_proxies_for_relay_failure() -> Any:
        if static_proxy:
            return _to_proxies_dict(static_proxy)
        return None

    def _target_host(target_url: str) -> str:
        return str(urlparse(str(target_url or "")).hostname or "").strip().lower()

    def _is_openai_like_host(host: str) -> bool:
        return bool(host) and (host.endswith("openai.com") or host.endswith("chatgpt.com"))

    def _should_bypass_relay_for_target(target_url: str) -> bool:
        host = _target_host(target_url)
        return relay_bypass_openai_hosts and _is_openai_like_host(host)

    def _warn_relay_fallback(reason: str, target_url: str) -> None:
        nonlocal relay_fallback_warned, relay_bypass_openai_hosts
        host = _target_host(target_url) or str(target_url or "?")
        if _is_openai_like_host(host):
            relay_bypass_openai_hosts = True
        if relay_fallback_warned:
            return
        if static_proxy:
            emitter.warn(f"代理池 relay 对 {host} 不可用，回退固定代理: {reason}", step="check_proxy")
        else:
            emitter.warn(f"代理池 relay 对 {host} 不可用，回退直连: {reason}", step="check_proxy")
        relay_fallback_warned = True

    def _update_relay_cookie_jar(resp: Any) -> None:
        try:
            for k, v in (resp.cookies or {}).items():
                key = str(k or "").strip()
                if key:
                    relay_cookie_jar[key] = str(v or "")
        except Exception:
            pass
        set_cookie_values: list[str] = []
        try:
            values = resp.headers.get_list("set-cookie")  # type: ignore[attr-defined]
            if values:
                set_cookie_values.extend(str(v or "") for v in values if str(v or "").strip())
        except Exception:
            pass
        if not set_cookie_values:
            try:
                set_cookie_raw = str(resp.headers.get("set-cookie") or "")
                if set_cookie_raw.strip():
                    set_cookie_values.append(set_cookie_raw)
            except Exception:
                pass
        for set_cookie_raw in set_cookie_values:
            try:
                parsed_cookie = SimpleCookie()
                parsed_cookie.load(set_cookie_raw)
                for k, morsel in parsed_cookie.items():
                    key = str(k or "").strip()
                    if key:
                        relay_cookie_jar[key] = str(morsel.value or "")
            except Exception:
                pass
        try:
            for k, v in relay_cookie_jar.items():
                s.cookies.set(k, v)
        except Exception:
            pass

    def _request_via_pool_relay(method: str, target_url: str, **kwargs: Any):
        if not pool_relay_enabled:
            raise RuntimeError("代理池 relay 未启用")
        relay_retries_override = kwargs.pop("_relay_retries", None)
        relay_params = {
            "api_key": pool_relay_api_key,
            "url": str(target_url),
            "method": str(method or "GET").upper(),
            "country": pool_relay_country,
        }
        target_params = kwargs.pop("params", None)
        if target_params:
            query_text = urlencode(target_params, doseq=True)
            if query_text:
                separator = "&" if "?" in relay_params["url"] else "?"
                relay_params["url"] = f"{relay_params['url']}{separator}{query_text}"

        headers = dict(kwargs.pop("headers", {}) or {})
        if relay_cookie_jar and not any(str(k).lower() == "cookie" for k in headers.keys()):
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in relay_cookie_jar.items())
        kwargs.pop("proxies", None)
        kwargs.setdefault("impersonate", "chrome")
        kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
        kwargs.setdefault("timeout", 20)

        method_upper = relay_params["method"]
        retry_count = max(
            1,
            int(
                relay_retries_override
                if relay_retries_override is not None
                else (pool_cfg.get("relay_request_retries") or POOL_RELAY_REQUEST_RETRIES)
            ),
        )
        last_error = ""
        for i in range(retry_count):
            try:
                resp = _call_with_http_fallback(
                    lambda relay_endpoint, **call_kwargs: requests.request(method_upper, relay_endpoint, **call_kwargs),
                    pool_relay_url,
                    params=relay_params,
                    headers=headers or None,
                    **kwargs,
                )
                _update_relay_cookie_jar(resp)
                if resp.status_code >= 500 or resp.status_code == 429:
                    last_error = f"HTTP {resp.status_code}"
                    if i < retry_count - 1:
                        time.sleep(min(0.4 * (i + 1), 1.2))
                        continue
                return resp
            except Exception as exc:
                last_error = str(exc)
                if i < retry_count - 1:
                    time.sleep(min(0.4 * (i + 1), 1.2))
        raise RuntimeError(f"代理池 relay 请求失败: {last_error or 'unknown error'}")

    def _ensure_openai_relay_ready() -> None:
        nonlocal openai_relay_probe_done
        if not pool_relay_enabled or relay_bypass_openai_hosts or openai_relay_probe_done:
            return
        openai_relay_probe_done = True
        probe_url = "https://auth.openai.com/"
        try:
            probe_resp = _request_via_pool_relay(
                "GET",
                probe_url,
                timeout=5,
                allow_redirects=False,
                _relay_retries=1,
            )
            status = int(probe_resp.status_code or 0)
            if status < 200 or status >= 400:
                raise RuntimeError(f"HTTP {status}")
            emitter.info("代理池 relay OpenAI 预检通过", step="check_proxy")
        except Exception as exc:
            _warn_relay_fallback(f"{exc} (OpenAI 预检)", probe_url)

    def _session_get(url: str, **kwargs: Any):
        if pool_relay_enabled and not _should_bypass_relay_for_target(url):
            try:
                relay_resp = _request_via_pool_relay("GET", url, **kwargs)
                if relay_resp.status_code < 500 and relay_resp.status_code != 429:
                    return relay_resp
                raise RuntimeError(f"HTTP {relay_resp.status_code}")
            except Exception as exc:
                _warn_relay_fallback(str(exc), url)
                kwargs["proxies"] = _fallback_proxies_for_relay_failure()
                kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
                kwargs.setdefault("timeout", 20)
                return _call_with_http_fallback(s.get, url, **kwargs)
        if pool_relay_enabled and _should_bypass_relay_for_target(url):
            kwargs["proxies"] = _fallback_proxies_for_relay_failure()
            kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
            kwargs.setdefault("timeout", 20)
            return _call_with_http_fallback(s.get, url, **kwargs)
        kwargs["proxies"] = _next_proxies()
        kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
        kwargs.setdefault("timeout", 15)
        return _call_with_http_fallback(s.get, url, **kwargs)

    def _session_post(url: str, **kwargs: Any):
        if pool_relay_enabled and not _should_bypass_relay_for_target(url):
            try:
                relay_resp = _request_via_pool_relay("POST", url, **kwargs)
                if relay_resp.status_code < 500 and relay_resp.status_code != 429:
                    return relay_resp
                raise RuntimeError(f"HTTP {relay_resp.status_code}")
            except Exception as exc:
                _warn_relay_fallback(str(exc), url)
                kwargs["proxies"] = _fallback_proxies_for_relay_failure()
                kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
                kwargs.setdefault("timeout", 20)
                return _call_with_http_fallback(s.post, url, **kwargs)
        if pool_relay_enabled and _should_bypass_relay_for_target(url):
            kwargs["proxies"] = _fallback_proxies_for_relay_failure()
            kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
            kwargs.setdefault("timeout", 20)
            return _call_with_http_fallback(s.post, url, **kwargs)
        kwargs["proxies"] = _next_proxies()
        kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
        kwargs.setdefault("timeout", 15)
        return _call_with_http_fallback(s.post, url, **kwargs)

    def _raw_get(url: str, **kwargs: Any):
        if pool_relay_enabled and not _should_bypass_relay_for_target(url):
            try:
                relay_resp = _request_via_pool_relay("GET", url, **kwargs)
                if relay_resp.status_code < 500 and relay_resp.status_code != 429:
                    return relay_resp
                raise RuntimeError(f"HTTP {relay_resp.status_code}")
            except Exception as exc:
                _warn_relay_fallback(str(exc), url)
                kwargs["proxies"] = _fallback_proxies_for_relay_failure()
                kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
                kwargs.setdefault("impersonate", "chrome")
                kwargs.setdefault("timeout", 20)
                return _call_with_http_fallback(requests.get, url, **kwargs)
        if pool_relay_enabled and _should_bypass_relay_for_target(url):
            kwargs["proxies"] = _fallback_proxies_for_relay_failure()
            kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
            kwargs.setdefault("impersonate", "chrome")
            kwargs.setdefault("timeout", 20)
            return _call_with_http_fallback(requests.get, url, **kwargs)
        kwargs["proxies"] = _next_proxies()
        kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
        kwargs.setdefault("impersonate", "chrome")
        kwargs.setdefault("timeout", 15)
        return _call_with_http_fallback(requests.get, url, **kwargs)

    def _raw_post(url: str, **kwargs: Any):
        if pool_relay_enabled and not _should_bypass_relay_for_target(url):
            try:
                relay_resp = _request_via_pool_relay("POST", url, **kwargs)
                if relay_resp.status_code < 500 and relay_resp.status_code != 429:
                    return relay_resp
                raise RuntimeError(f"HTTP {relay_resp.status_code}")
            except Exception as exc:
                _warn_relay_fallback(str(exc), url)
                kwargs["proxies"] = _fallback_proxies_for_relay_failure()
                kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
                kwargs.setdefault("impersonate", "chrome")
                kwargs.setdefault("timeout", 20)
                return _call_with_http_fallback(requests.post, url, **kwargs)
        if pool_relay_enabled and _should_bypass_relay_for_target(url):
            kwargs["proxies"] = _fallback_proxies_for_relay_failure()
            kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
            kwargs.setdefault("impersonate", "chrome")
            kwargs.setdefault("timeout", 20)
            return _call_with_http_fallback(requests.post, url, **kwargs)
        kwargs["proxies"] = _next_proxies()
        kwargs.setdefault("http_version", DEFAULT_HTTP_VERSION)
        kwargs.setdefault("impersonate", "chrome")
        kwargs.setdefault("timeout", 15)
        return _call_with_http_fallback(requests.post, url, **kwargs)

    def _submit_callback_url_via_pool_relay(
        *,
        callback_url: str,
        expected_state: str,
        code_verifier: str,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        account_password: str = "",
    ) -> str:
        cb = _parse_callback_url(callback_url)
        if cb["error"]:
            desc = cb["error_description"]
            raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())
        if not cb["code"]:
            raise ValueError("callback url missing ?code=")
        if not cb["state"]:
            raise ValueError("callback url missing ?state=")
        if cb["state"] != expected_state:
            raise ValueError("state mismatch")

        token_resp = _request_via_pool_relay(
            "POST",
            TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data=urllib.parse.urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": cb["code"],
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                }
            ),
            timeout=30,
        )
        if token_resp.status_code != 200:
            raise RuntimeError(
                f"token exchange failed: {token_resp.status_code}: {str(token_resp.text or '')[:240]}"
            )
        try:
            token_json = token_resp.json()
        except Exception:
            token_json = json.loads(str(token_resp.text or "{}"))

        access_token = str(token_json.get("access_token") or "").strip()
        refresh_token = str(token_json.get("refresh_token") or "").strip()
        id_token = str(token_json.get("id_token") or "").strip()
        expires_in = _to_int(token_json.get("expires_in"))

        claims = _jwt_claims_no_verify(id_token)
        email = str(claims.get("email") or "").strip()
        auth_claims = claims.get("https://api.openai.com/auth") or {}
        account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

        now = int(time.time())
        expired_rfc3339 = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
        )
        now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

        config = {
            "id_token": id_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
            "last_refresh": now_rfc3339,
            "expires_at": expired_rfc3339,
            "email": email,
            "type": "codex",
            "expired": expired_rfc3339,
        }
        if account_password:
            config["password"] = account_password
        return json.dumps(config, ensure_ascii=False, separators=(",", ":"))

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _extract_or_create_device_id(init_resp: Any, step: str) -> str:
        did_value = s.cookies.get("oai-did") or relay_cookie_jar.get("oai-did") or ""
        if not did_value:
            did_m = re.search(r"oai-did=([0-9a-fA-F-]{20,})", str(init_resp.text or ""))
            if did_m:
                did_value = did_m.group(1)
        if not did_value:
            did_value = str(uuid.uuid4())
            emitter.warn(f"未从响应提取到 oai-did，已使用临时 Device ID: {did_value}", step=step)
        relay_cookie_jar["oai-did"] = did_value
        try:
            s.cookies.set("oai-did", did_value)
        except Exception:
            pass
        emitter.info(f"Device ID: {did_value}", step=step)
        return did_value

    def _fetch_sentinel(device_id: str, flow: str, step: str) -> str:
        emitter.info("正在获取 Sentinel Token...", step=step)
        req_body = json.dumps(
            {"p": "", "id": device_id, "flow": flow},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sen_resp = _raw_post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=req_body,
        )
        if sen_resp.status_code != 200:
            emitter.error(f"Sentinel 异常拦截，状态码: {sen_resp.status_code}", step=step)
            return ""
        try:
            sen_token = str((sen_resp.json() or {}).get("token") or "").strip()
        except Exception:
            sen_token = ""
        if not sen_token:
            emitter.error("Sentinel Token 解析失败", step=step)
            return ""
        emitter.success("Sentinel Token 获取成功", step=step)
        return json.dumps(
            {"p": "", "t": "", "c": sen_token, "id": device_id, "flow": flow},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _wait_for_openai_otp(timeout: int = 120) -> str:
        if mail_provider is not None:
            try:
                return mail_provider.wait_for_otp(
                    dev_token,
                    email,
                    proxy=static_proxy,
                    proxy_selector=mail_proxy_selector,
                    timeout=timeout,
                    stop_event=stop_event,
                )
            except TypeError:
                return mail_provider.wait_for_otp(
                    dev_token,
                    email,
                    proxy=static_proxy,
                    timeout=timeout,
                    stop_event=stop_event,
                )
        return get_oai_code(
            dev_token,
            email,
            static_proxies,
            emitter,
            stop_event,
            proxy_selector=mail_proxies_selector,
        )

    def _exchange_callback(callback_url: str, oauth_ctx: OAuthStart) -> str:
        if pool_relay_enabled and not _should_bypass_relay_for_target(TOKEN_URL):
            try:
                return _submit_callback_url_via_pool_relay(
                    callback_url=callback_url,
                    code_verifier=oauth_ctx.code_verifier,
                    redirect_uri=oauth_ctx.redirect_uri,
                    expected_state=oauth_ctx.state,
                    account_password=account_password,
                )
            except Exception as exc:
                _warn_relay_fallback(str(exc), TOKEN_URL)
        return submit_callback_url(
            callback_url=callback_url,
            code_verifier=oauth_ctx.code_verifier,
            redirect_uri=oauth_ctx.redirect_uri,
            expected_state=oauth_ctx.state,
            proxy=(static_proxy if pool_relay_enabled else _next_proxy_value()),
            account_password=account_password,
        )

    def _complete_workspace_and_exchange(oauth_ctx: OAuthStart, continue_url: str) -> Optional[str]:
        current_url = str(continue_url or "").strip()
        if current_url and "code=" in current_url and "state=" in current_url:
            emitter.success("Token 获取成功！", step="get_token")
            return _exchange_callback(current_url, oauth_ctx)

        emitter.info("正在解析 Workspace 信息...", step="workspace")
        auth_cookie = s.cookies.get("oai-client-auth-session") or relay_cookie_jar.get("oai-client-auth-session") or ""
        if auth_cookie:
            auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])
            workspaces = auth_json.get("workspaces") or []
            workspace_id = str((workspaces[0] or {}).get("id") or "").strip() if workspaces else ""
            if workspace_id:
                select_resp = _session_post(
                    "https://auth.openai.com/api/accounts/workspace/select",
                    headers={
                        "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                        "content-type": "application/json",
                    },
                    json={"workspace_id": workspace_id},
                )
                if select_resp.status_code != 200:
                    emitter.error(f"选择 workspace 失败，状态码: {select_resp.status_code}", step="workspace")
                    emitter.error(select_resp.text, step="workspace")
                    return None
                emitter.success(f"Workspace 选择成功: {workspace_id}", step="workspace")
                try:
                    current_url = str((select_resp.json() or {}).get("continue_url") or current_url).strip()
                except Exception:
                    current_url = current_url.strip()
            else:
                emitter.warn("授权 Cookie 中未解析到 workspace_id，直接跟随 continue_url", step="workspace")
        else:
            emitter.warn("未获取到授权 Cookie，直接跟随 continue_url", step="workspace")

        if not current_url:
            emitter.error("未找到可继续的 OAuth 回调地址", step="get_token")
            return None

        emitter.info("正在获取最终 OAuth Token...", step="get_token")
        for _ in range(8):
            if _stopped():
                return None
            if "code=" in current_url and "state=" in current_url:
                result = _exchange_callback(current_url, oauth_ctx)
                emitter.success("Token 获取成功！", step="get_token")
                return result
            final_resp = _session_get(current_url, allow_redirects=False, timeout=15)
            location = final_resp.headers.get("Location") or ""
            if final_resp.status_code not in [301, 302, 303, 307, 308] or not location:
                break
            current_url = urllib.parse.urljoin(current_url, location)

        emitter.error("未能在重定向链中捕获到最终 Callback URL", step="get_token")
        return None

    def _reset_auth_session() -> None:
        nonlocal s
        s = requests.Session(impersonate="chrome")
        relay_cookie_jar.clear()

    try:
        # ------- 步骤1：网络环境检查 -------
        emitter.info("正在检查网络环境...", step="check_proxy")
        try:
            trace_text = ""
            relay_error = ""
            relay_used = False
            if pool_relay_enabled:
                try:
                    trace_text = _trace_via_pool_relay(pool_cfg)
                    relay_used = True
                except Exception as e:
                    relay_error = str(e)
                    if static_proxy:
                        emitter.warn(f"代理池 relay 检查失败，回退固定代理: {relay_error}", step="check_proxy")
                    else:
                        emitter.warn(f"代理池 relay 检查失败，尝试直连代理: {relay_error}", step="check_proxy")
            if not trace_text:
                trace_resp = _session_get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
                trace_text = trace_resp.text
            trace = trace_text
            loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
            loc = loc_re.group(1) if loc_re else None
            ip_re = re.search(r"^ip=(.+)$", trace, re.MULTILINE)
            current_ip = ip_re.group(1).strip() if ip_re else ""
            if relay_used:
                emitter.info("代理池 relay 连通检查成功", step="check_proxy")
            emitter.info(f"当前 IP 所在地: {loc}", step="check_proxy")
            if current_ip:
                emitter.info(f"当前出口 IP: {current_ip}", step="check_proxy")
            if loc == "CN" or loc == "HK":
                emitter.error("检查代理哦 — 所在地不支持 (CN/HK)", step="check_proxy")
                return None
            emitter.success("网络环境检查通过", step="check_proxy")
            _ensure_openai_relay_ready()
        except Exception as e:
            emitter.error(f"网络连接检查失败: {e}", step="check_proxy")
            return None

        if _stopped():
            return None

        # ------- 步骤2：创建临时邮箱 -------
        if mail_provider is not None:
            emitter.info("正在创建临时邮箱...", step="create_email")
            try:
                email, dev_token = mail_provider.create_mailbox(
                    proxy=static_proxy,
                    proxy_selector=mail_proxy_selector,
                )
            except TypeError:
                email, dev_token = mail_provider.create_mailbox(proxy=static_proxy)
            except Exception as e:
                provider_detail = str(getattr(mail_provider, "last_error", "") or "").strip()
                if provider_detail:
                    emitter.error(f"temporary mailbox create exception: {e} | detail: {provider_detail}", step="create_email")
                else:
                    emitter.error(f"temporary mailbox create exception: {e}", step="create_email")
                return None
        else:
            emitter.info("正在创建 Mail.tm 临时邮箱...", step="create_email")
            email, dev_token = get_email_and_token(
                static_proxies,
                emitter,
                proxy_selector=mail_proxies_selector,
            )
        if not email or not dev_token:
            provider_detail = ""
            if mail_provider is not None:
                provider_detail = str(getattr(mail_provider, "last_error", "") or "").strip()
            if provider_detail:
                emitter.error(f"temporary mailbox create failed: {provider_detail}", step="create_email")
            else:
                emitter.error("temporary mailbox create failed", step="create_email")
            return None
        emitter.success(f"临时邮箱创建成功: {email}", step="create_email")
        account_password = generate_account_password()

        if _stopped():
            return None

        # ------- 步骤3：生成 OAuth URL，获取 Device ID -------
        emitter.info("正在生成 OAuth 授权链接...", step="oauth_init")
        signup_oauth = generate_oauth_url()
        resp = _session_get(signup_oauth.auth_url, timeout=20)
        emitter.info(f"OAuth 初始化状态: {resp.status_code}", step="oauth_init")
        if resp.status_code >= 400:
            emitter.error(f"OAuth 初始化失败，状态码: {resp.status_code}", step="oauth_init")
            return None
        did = _extract_or_create_device_id(resp, "oauth_init")

        if _stopped():
            return None

        # ------- 步骤4：获取 Sentinel Token -------
        sentinel = _fetch_sentinel(did, "authorize_continue", "sentinel")
        if not sentinel:
            return None

        if _stopped():
            return None

        # ------- 步骤5：提交注册 -------
        emitter.info("正在提交注册邮箱...", step="signup")
        signup_resp = _session_post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            json={"username": {"value": email, "kind": "email"}, "screen_hint": "signup"},
        )
        emitter.info(f"注册邮箱提交状态: {signup_resp.status_code}", step="signup")
        if signup_resp.status_code != 200:
            emitter.error(str(signup_resp.text or "")[:240], step="signup")
            return None

        # ------- 步骤6：设置账号密码并发送 OTP -------
        emitter.info("正在设置账号密码...", step="signup")
        register_resp = _session_post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "origin": "https://auth.openai.com",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={"username": email, "password": account_password},
        )
        emitter.info(f"账号密码设置状态: {register_resp.status_code}", step="signup")
        if register_resp.status_code != 200:
            emitter.error(str(register_resp.text or "")[:240], step="signup")
            return None

        emitter.info("正在发送邮箱验证码...", step="send_otp")
        otp_resp = _session_get(
            "https://auth.openai.com/api/accounts/email-otp/send",
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "referer": "https://auth.openai.com/create-account/password",
                "upgrade-insecure-requests": "1",
            },
            allow_redirects=True,
        )
        emitter.info(f"验证码发送状态: {otp_resp.status_code}", step="send_otp")

        if otp_resp.status_code != 200:
            emitter.error(f"验证码发送失败（状态码 {otp_resp.status_code}）", step="send_otp")
            return None

        if _stopped():
            return None

        # ------- 步骤7：轮询邮箱拿验证码 -------
        code = _wait_for_openai_otp()
        if not code:
            return None

        if _stopped():
            return None

        # ------- 步骤8：提交验证码 -------
        emitter.info("正在验证 OTP...", step="verify_otp")
        code_resp = _session_post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={"code": code},
        )
        emitter.info(f"验证码校验状态: {code_resp.status_code}", step="verify_otp")
        if code_resp.status_code != 200:
            emitter.warn("首次 OTP 校验失败，尝试重新发送验证码", step="verify_otp")
            otp_retry_resp = _session_get(
                "https://auth.openai.com/api/accounts/email-otp/send",
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "referer": "https://auth.openai.com/create-account/password",
                    "upgrade-insecure-requests": "1",
                },
                allow_redirects=True,
            )
            if otp_retry_resp.status_code != 200:
                emitter.error(str(code_resp.text or "")[:240], step="verify_otp")
                return None
            code = _wait_for_openai_otp(timeout=60)
            if not code:
                return None
            code_resp = _session_post(
                "https://auth.openai.com/api/accounts/email-otp/validate",
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json={"code": code},
            )
            emitter.info(f"验证码重试校验状态: {code_resp.status_code}", step="verify_otp")
            if code_resp.status_code != 200:
                emitter.error(str(code_resp.text or "")[:240], step="verify_otp")
                return None

        if _stopped():
            return None

        # ------- 步骤9：创建账户 -------
        emitter.info("正在创建账户信息...", step="create_account")
        create_account_headers = {
            "referer": "https://auth.openai.com/about-you",
            "accept": "application/json",
            "content-type": "application/json",
        }
        create_account_sentinel = _fetch_sentinel(did, "authorize_continue", "create_account")
        if create_account_sentinel:
            create_account_headers["openai-sentinel-token"] = create_account_sentinel
        create_account_resp = _session_post(
            "https://auth.openai.com/api/accounts/create_account",
            headers=create_account_headers,
            json={"name": "Neo", "birthdate": "2000-02-20"},
        )
        create_account_status = create_account_resp.status_code
        emitter.info(f"账户创建状态: {create_account_status}", step="create_account")

        if create_account_status != 200:
            emitter.error(create_account_resp.text, step="create_account")
            return None

        emitter.success("账户创建成功！", step="create_account")
        try:
            create_account_data = create_account_resp.json() or {}
        except Exception:
            create_account_data = {}
        signup_continue_url = str(
            create_account_data.get("continue_url")
            or create_account_data.get("url")
            or create_account_data.get("redirect_url")
            or ""
        ).strip()
        if signup_continue_url:
            try:
                _session_get(signup_continue_url, allow_redirects=True, timeout=15)
            except Exception:
                pass

        if _stopped():
            return None

        emitter.info("注册完成，正在使用邮箱密码单独登录获取 Token...", step="oauth_login")
        _reset_auth_session()

        oauth = generate_oauth_url()
        oauth_init_resp = _session_get(oauth.auth_url, timeout=20)
        emitter.info(f"独立 OAuth 初始化状态: {oauth_init_resp.status_code}", step="oauth_login")
        if oauth_init_resp.status_code >= 400:
            emitter.error(f"独立 OAuth 初始化失败，状态码: {oauth_init_resp.status_code}", step="oauth_login")
            return None
        login_did = _extract_or_create_device_id(oauth_init_resp, "oauth_login")

        if _stopped():
            return None

        login_sentinel = _fetch_sentinel(login_did, "authorize_continue", "oauth_login")
        if not login_sentinel:
            return None

        emitter.info("正在提交登录邮箱...", step="oauth_login")
        login_continue_resp = _session_post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": str(oauth_init_resp.url or "https://auth.openai.com/log-in"),
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": login_sentinel,
            },
            json={"username": {"value": email, "kind": "email"}},
        )
        emitter.info(f"登录邮箱提交状态: {login_continue_resp.status_code}", step="oauth_login")
        if login_continue_resp.status_code != 200:
            emitter.error(str(login_continue_resp.text or "")[:240], step="oauth_login")
            return None
        try:
            continue_data = login_continue_resp.json() or {}
        except Exception:
            emitter.error("登录邮箱响应解析失败", step="oauth_login")
            return None
        continue_url = str(continue_data.get("continue_url") or "").strip()
        page_type = str(((continue_data.get("page") or {}).get("type") or "")).strip()

        if _stopped():
            return None

        password_sentinel = _fetch_sentinel(login_did, "password_verify", "oauth_login")
        if not password_sentinel:
            return None

        emitter.info("正在提交账号密码登录...", step="oauth_login")
        password_resp = _session_post(
            "https://auth.openai.com/api/accounts/password/verify",
            headers={
                "referer": "https://auth.openai.com/log-in/password",
                "origin": "https://auth.openai.com",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": password_sentinel,
            },
            json={"password": account_password},
        )
        emitter.info(f"账号密码校验状态: {password_resp.status_code}", step="oauth_login")
        if password_resp.status_code != 200:
            emitter.error(str(password_resp.text or "")[:240], step="oauth_login")
            return None
        try:
            password_data = password_resp.json() or {}
        except Exception:
            emitter.error("密码校验响应解析失败", step="oauth_login")
            return None
        continue_url = str(password_data.get("continue_url") or continue_url).strip()
        page_type = str(((password_data.get("page") or {}).get("type") or page_type)).strip()

        need_oauth_otp = (
            page_type == "email_otp_verification"
            or "email-verification" in continue_url
            or "email-otp" in continue_url
        )
        if need_oauth_otp:
            emitter.info("登录阶段需要邮箱 OTP，正在等待验证码...", step="oauth_login")
            otp_validated = False
            for attempt in range(2):
                if _stopped():
                    return None
                oauth_code = _wait_for_openai_otp(timeout=(120 if attempt == 0 else 60))
                if not oauth_code:
                    return None
                oauth_otp_resp = _session_post(
                    "https://auth.openai.com/api/accounts/email-otp/validate",
                    headers={
                        "referer": "https://auth.openai.com/email-verification",
                        "origin": "https://auth.openai.com",
                        "accept": "application/json",
                        "content-type": "application/json",
                    },
                    json={"code": oauth_code},
                )
                emitter.info(f"登录阶段 OTP 校验状态: {oauth_otp_resp.status_code}", step="oauth_login")
                if oauth_otp_resp.status_code == 200:
                    try:
                        oauth_otp_data = oauth_otp_resp.json() or {}
                    except Exception:
                        oauth_otp_data = {}
                    continue_url = str(oauth_otp_data.get("continue_url") or continue_url).strip()
                    page_type = str(((oauth_otp_data.get("page") or {}).get("type") or page_type)).strip()
                    otp_validated = True
                    break
                emitter.warn(f"登录阶段 OTP 校验失败，第 {attempt + 1} 次尝试未通过", step="oauth_login")
                time.sleep(2)
            if not otp_validated:
                emitter.error("登录阶段 OTP 校验失败", step="oauth_login")
                return None

        if _stopped():
            return None

        return _complete_workspace_and_exchange(oauth, continue_url)

        if _stopped():
            return None

        # ------- 步骤10：解析 Workspace -------
        emitter.info("正在解析 Workspace 信息...", step="workspace")
        auth_cookie = s.cookies.get("oai-client-auth-session") or relay_cookie_jar.get("oai-client-auth-session") or ""
        if not auth_cookie:
            emitter.error("未能获取到授权 Cookie", step="workspace")
            return None

        auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])
        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            emitter.error("授权 Cookie 里没有 workspace 信息", step="workspace")
            return None
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            emitter.error("无法解析 workspace_id", step="workspace")
            return None

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        select_resp = _session_post(
            "https://auth.openai.com/api/accounts/workspace/select",
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "content-type": "application/json",
            },
            data=select_body,
        )

        if select_resp.status_code != 200:
            emitter.error(f"选择 workspace 失败，状态码: {select_resp.status_code}", step="workspace")
            emitter.error(select_resp.text, step="workspace")
            return None

        emitter.success(f"Workspace 选择成功: {workspace_id}", step="workspace")

        # ------- 步骤11：跟踪重定向，获取 Token -------
        emitter.info("正在获取最终 OAuth Token...", step="get_token")
        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            emitter.error("workspace/select 响应里缺少 continue_url", step="get_token")
            return None

        current_url = continue_url
        for _ in range(6):
            if _stopped():
                return None
            final_resp = _session_get(current_url, allow_redirects=False, timeout=15)
            location = final_resp.headers.get("Location") or ""

            if final_resp.status_code not in [301, 302, 303, 307, 308]:
                break
            if not location:
                break

            next_url = urllib.parse.urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                if pool_relay_enabled and not _should_bypass_relay_for_target(TOKEN_URL):
                    try:
                        result = _submit_callback_url_via_pool_relay(
                            callback_url=next_url,
                            code_verifier=oauth.code_verifier,
                            redirect_uri=oauth.redirect_uri,
                            expected_state=oauth.state,
                        )
                    except Exception as exc:
                        _warn_relay_fallback(str(exc), TOKEN_URL)
                        result = submit_callback_url(
                            callback_url=next_url,
                            code_verifier=oauth.code_verifier,
                            redirect_uri=oauth.redirect_uri,
                            expected_state=oauth.state,
                            proxy=static_proxy,
                        )
                else:
                    result = submit_callback_url(
                        callback_url=next_url,
                        code_verifier=oauth.code_verifier,
                        redirect_uri=oauth.redirect_uri,
                        expected_state=oauth.state,
                        proxy=(static_proxy if pool_relay_enabled else _next_proxy_value()),
                    )
                emitter.success("Token 获取成功！", step="get_token")
                return result
            current_url = next_url

        emitter.error("未能在重定向链中捕获到最终 Callback URL", step="get_token")
        return None

    except Exception as e:
        emitter.error(f"运行时发生错误: {e}", step="runtime")
        return None


# ==========================================
# CLI 入口（兼容直接运行）
# ==========================================


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI 账号池编排器脚本")
    parser.add_argument(
        "--proxy", default=None, help="代理地址，如 http://127.0.0.1:7897"
    )
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument(
        "--sleep-max", type=int, default=30, help="循环模式最长等待秒数"
    )
    args = parser.parse_args()

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    os.makedirs(TOKENS_DIR, exist_ok=True)

    count = 0
    print("[Info] OpenAI 账号池编排器 - CLI 模式")

    while True:
        count += 1
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 开始第 {count} 次注册流程 <<<"
        )

        try:
            token_json = run(args.proxy)

            if token_json:
                try:
                    t_data = json.loads(token_json)
                    fname_email = t_data.get("email", "unknown").replace("@", "_")
                except Exception:
                    fname_email = "unknown"

                file_name = f"token_{fname_email}_{int(time.time())}.json"
                file_path = os.path.join(TOKENS_DIR, file_name)

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(token_json)

                print(f"[*] 成功! Token 已保存至: {file_path}")
            else:
                print("[-] 本次注册失败。")

        except Exception as e:
            print(f"[Error] 发生未捕获异常: {e}")

        if args.once:
            break

        wait_time = random.randint(sleep_min, sleep_max)
        print(f"[*] 休息 {wait_time} 秒...")
        time.sleep(wait_time)


if __name__ == "__main__":
    main()

