"""
FastAPI ˷
ṩ REST API + SSE ʵʱ־
"""

import asyncio
import base64
import hmac
import json
import os
import queue
import random
import secrets
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, TOKENS_DIR, CONFIG_FILE, STATE_FILE, STATIC_DIR, DATA_DIR
from .register import EventEmitter, run, _fetch_proxy_from_pool
from .mail_providers import create_provider, MultiMailRouter
from .pool_maintainer import PoolMaintainer, Sub2ApiMaintainer

DEFAULT_PROXY_POOL_PROVIDER = "zenproxy_api"
PROXY_POOL_PROVIDER_CHOICES = {
    "zenproxy_api": "zenproxy_api",
    "zenproxy": "zenproxy_api",
    "dreamy_socks5_pool": "dreamy_socks5_pool",
    "dreamy_socks5": "dreamy_socks5_pool",
    "socks5_proxy": "dreamy_socks5_pool",
    "docker_warp_socks": "docker_warp_socks",
    "warp_socks": "docker_warp_socks",
    "warp": "docker_warp_socks",
}
PROXY_POOL_PROVIDER_DEFAULT_URLS = {
    "zenproxy_api": "https://zenproxy.top/api/fetch",
    "dreamy_socks5_pool": "socks5://127.0.0.1:1080",
    "docker_warp_socks": "socks5://127.0.0.1:9091",
}

# ==========================================
# ͬãڴ־û data/sync_config.json
# ==========================================

# CONFIG_FILE  TOKENS_DIR ѴӰ __init__.py 


def _load_sync_config() -> Dict[str, str]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "base_url": "", "bearer_token": "", "account_name": "AutoReg", "auto_sync": "false",
        "cpa_base_url": "", "cpa_token": "", "min_candidates": 800,
        "used_percent_threshold": 95, "auto_maintain": False, "maintain_interval_minutes": 30,
        "upload_mode": "snapshot",
        "mail_provider": "mailtm",
        "mail_config": {
            "api_base": "https://api.mail.tm",
            "api_key": "",
            "bearer_token": "",
            "site_password": "",
            "admin_password": "",
        },
        "sub2api_min_candidates": 200,
        "sub2api_auto_maintain": False,
        "sub2api_maintain_interval_minutes": 30,
        "proxy": "",
        "auto_register": False,
        "proxy_pool_enabled": True,
        "proxy_pool_provider": "zenproxy_api",
        "proxy_pool_api_url": "https://zenproxy.top/api/fetch",
        "proxy_pool_auth_mode": "query",
        "proxy_pool_api_key": "19c0ec43-8f76-4c97-81bc-bcda059eeba4",
        "proxy_pool_count": 1,
        "proxy_pool_country": "US",
        "desired_token_count": 0,
        "local_auto_maintain": False,
        "local_maintain_interval_minutes": 30,
        "local_probe_timeout_seconds": 12,
        "api_auth_enabled": False,
        "x_api_key": "",
    }


def _normalize_proxy_pool_provider(value: Any) -> str:
    key = str(value or DEFAULT_PROXY_POOL_PROVIDER).strip().lower()
    return PROXY_POOL_PROVIDER_CHOICES.get(key, DEFAULT_PROXY_POOL_PROVIDER)


def _default_proxy_pool_api_url(provider: str) -> str:
    return PROXY_POOL_PROVIDER_DEFAULT_URLS.get(provider, PROXY_POOL_PROVIDER_DEFAULT_URLS[DEFAULT_PROXY_POOL_PROVIDER])


def _normalize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """ɵĵṩǨƵṩ̸ʽУ"""
    legacy = str(cfg.get("mail_provider", "mailtm") or "mailtm").strip().lower()
    legacy_cfg = cfg.get("mail_config") or {}
    if not isinstance(legacy_cfg, dict):
        legacy_cfg = {}

    raw_providers = cfg.get("mail_providers")
    providers = raw_providers if isinstance(raw_providers, list) else []
    providers = [str(n).strip().lower() for n in providers if str(n).strip()]
    if not providers:
        providers = [legacy]

    raw_cfgs = cfg.get("mail_provider_configs")
    provider_cfgs = raw_cfgs if isinstance(raw_cfgs, dict) else {}
    for name in providers:
        if name not in provider_cfgs or not isinstance(provider_cfgs.get(name), dict):
            provider_cfgs[name] = {}
    if legacy in provider_cfgs:
        for k, v in legacy_cfg.items():
            provider_cfgs[legacy].setdefault(k, v)

    strategy = str(cfg.get("mail_strategy", "round_robin") or "round_robin").strip().lower()
    if strategy not in ("round_robin", "random", "failover"):
        strategy = "round_robin"

    cfg["mail_providers"] = providers
    cfg["mail_provider_configs"] = provider_cfgs
    cfg["mail_strategy"] = strategy
    cfg["mail_provider"] = providers[0]
    upload_mode = str(cfg.get("upload_mode", "snapshot") or "snapshot").strip().lower()
    if upload_mode not in ("snapshot", "decoupled"):
        upload_mode = "snapshot"
    cfg["upload_mode"] = upload_mode
    cfg.setdefault("multithread", False)
    try:
        cfg["thread_count"] = max(1, min(int(cfg.get("thread_count", 3)), 10))
    except (ValueError, TypeError):
        cfg["thread_count"] = 3
    cfg["proxy_pool_enabled"] = bool(cfg.get("proxy_pool_enabled", True))
    proxy_pool_provider = _normalize_proxy_pool_provider(cfg.get("proxy_pool_provider"))
    cfg["proxy_pool_provider"] = proxy_pool_provider
    proxy_pool_default_url = _default_proxy_pool_api_url(proxy_pool_provider)
    proxy_pool_api_url = str(cfg.get("proxy_pool_api_url", proxy_pool_default_url) or "").strip()
    cfg["proxy_pool_api_url"] = proxy_pool_api_url or proxy_pool_default_url
    proxy_pool_auth_mode = str(cfg.get("proxy_pool_auth_mode", "query") or "").strip().lower()
    if proxy_pool_auth_mode not in ("header", "query"):
        proxy_pool_auth_mode = "query"
    cfg["proxy_pool_auth_mode"] = proxy_pool_auth_mode
    cfg["proxy_pool_api_key"] = str(cfg.get("proxy_pool_api_key", "19c0ec43-8f76-4c97-81bc-bcda059eeba4") or "").strip()
    try:
        cfg["proxy_pool_count"] = max(1, min(int(cfg.get("proxy_pool_count", 1)), 20))
    except (TypeError, ValueError):
        cfg["proxy_pool_count"] = 1
    cfg["proxy_pool_country"] = str(cfg.get("proxy_pool_country", "US") or "US").strip().upper() or "US"
    try:
        cfg["desired_token_count"] = max(0, int(cfg.get("desired_token_count", 0)))
    except (TypeError, ValueError):
        cfg["desired_token_count"] = 0
    cfg["local_auto_maintain"] = bool(cfg.get("local_auto_maintain", False))
    try:
        cfg["local_maintain_interval_minutes"] = max(5, int(cfg.get("local_maintain_interval_minutes", 30)))
    except (TypeError, ValueError):
        cfg["local_maintain_interval_minutes"] = 30
    try:
        cfg["local_probe_timeout_seconds"] = max(5, min(int(cfg.get("local_probe_timeout_seconds", 12)), 60))
    except (TypeError, ValueError):
        cfg["local_probe_timeout_seconds"] = 12
    cfg["api_auth_enabled"] = bool(cfg.get("api_auth_enabled", False))
    cfg["x_api_key"] = str(cfg.get("x_api_key", "") or "").strip()
    return cfg


def _pool_relay_url_from_fetch_url(api_url: str) -> str:
    raw = str(api_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        from urllib.parse import urlparse
        parsed = urlparse(raw)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc
        if not netloc:
            return ""
        return f"{scheme}://{netloc}/api/relay"
    except Exception:
        return ""


def _save_sync_config(cfg: Dict[str, str]) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


_sync_config = _normalize_config(_load_sync_config())


def _mask_secret(value: str, keep: int = 6) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= keep:
        return raw
    return raw[:keep] + "..."


def _env_api_key() -> str:
    return str(os.getenv("X_API_KEY", "") or "").strip()


def _configured_api_key() -> str:
    return str(_sync_config.get("x_api_key", "") or "").strip()


def _effective_api_key() -> str:
    env = _env_api_key()
    if env:
        return env
    return _configured_api_key()


def _is_api_auth_enabled() -> bool:
    if _env_api_key():
        return True
    return bool(_sync_config.get("api_auth_enabled", False) and _configured_api_key())


def _extract_request_api_key(request: Request) -> str:
    x_api_key = request.headers.get("x-api-key", "").strip()
    if x_api_key:
        return x_api_key
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    query_key = request.query_params.get("api_key", "").strip()
    if query_key:
        return query_key
    return ""


def _generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def _push_refresh_token(base_url: str, bearer: str, refresh_token: str) -> Dict[str, Any]:
    """
     Sub2Api ƽ̨ API ύ refresh_token
     {ok: bool, status: int, body: str}
    """
    url = base_url.rstrip("/") + "/api/v1/admin/openai/refresh-token"
    payload = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return {"ok": False, "status": exc.code, "body": body}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


UPLOAD_PLATFORMS = ("cpa", "sub2api")


def _extract_uploaded_platforms(token_data: Dict[str, Any]) -> List[str]:
    platforms = set()
    raw_platforms = token_data.get("uploaded_platforms")
    if isinstance(raw_platforms, list):
        for p in raw_platforms:
            name = str(p).strip().lower()
            if name in UPLOAD_PLATFORMS:
                platforms.add(name)
    if token_data.get("cpa_uploaded") or token_data.get("cpa_synced"):
        platforms.add("cpa")
    if token_data.get("sub2api_uploaded") or token_data.get("sub2api_synced") or token_data.get("synced"):
        platforms.add("sub2api")
    return [p for p in UPLOAD_PLATFORMS if p in platforms]


def _is_sub2api_uploaded(token_data: Dict[str, Any]) -> bool:
    return "sub2api" in _extract_uploaded_platforms(token_data)


def _mark_token_uploaded_platform(file_path: str, platform: str) -> bool:
    platform_name = str(platform).strip().lower()
    if platform_name not in UPLOAD_PLATFORMS:
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            token_data = json.load(f)
        if not isinstance(token_data, dict):
            return False

        platforms = _extract_uploaded_platforms(token_data)
        if platform_name not in platforms:
            platforms.append(platform_name)
        token_data["uploaded_platforms"] = [p for p in UPLOAD_PLATFORMS if p in set(platforms)]
        token_data[f"{platform_name}_uploaded"] = True
        token_data[f"{platform_name}_synced"] = True

        if platform_name == "sub2api":
            token_data["synced"] = True  # ݾǰ߼

        uploaded_at = token_data.get("uploaded_at")
        if not isinstance(uploaded_at, dict):
            uploaded_at = {}
        uploaded_at[platform_name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        token_data["uploaded_at"] = uploaded_at

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


# ==========================================
# ͳݳ־û
# ==========================================

# STATE_FILE ѴӰ __init__.py 


def _load_state() -> Dict[str, int]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"success": 0, "fail": 0}


def _save_state(success: int, fail: int) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps({"success": success, "fail": fail}),
            encoding="utf-8",
        )
    except Exception:
        pass


# ==========================================
# Ӧóʼ
# ==========================================

app = FastAPI(title="OpenAI Pool Orchestrator", version=__version__)


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    if path == "/api/auth/status":
        return await call_next(request)
    if not _is_api_auth_enabled():
        return await call_next(request)

    expected = _effective_api_key()
    provided = _extract_request_api_key(request)
    if provided and hmac.compare_digest(provided, expected):
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={
            "detail": "Unauthorized: provide API key via Authorization Bearer or X-API-Key",
        },
    )

# STATIC_DIR  TOKENS_DIR ѴӰ __init__.py 
STATIC_DIR.mkdir(exist_ok=True)
os.makedirs(str(TOKENS_DIR), exist_ok=True)

# ==========================================
# ״̬
# ==========================================


class TaskState:
    """ȫ״ֶ̬֧Worker"""

    def __init__(self) -> None:
        self.status: str = "idle"  # idle | running | stopping
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._worker_threads: Dict[int, threading.Thread] = {}
        self._task_lock = threading.RLock()
        # ־㲥Уasyncio
        self._sse_queues: list[asyncio.Queue] = []
        self._sse_lock = threading.Lock()
        # ͳƣļʷ
        _s = _load_state()
        self.success_count: int = _s.get("success", 0)
        self.fail_count: int = _s.get("fail", 0)
        self.current_proxy: str = ""
        self.worker_count: int = 0
        self.multithread: bool = False
        self.upload_mode: str = "snapshot"
        self.target_count: int = 0
        # ǰڼʷۼ success/fail 룩
        self.run_success_count: int = 0
        self.run_fail_count: int = 0
        self.platform_success_count: Dict[str, int] = {name: 0 for name in UPLOAD_PLATFORMS}
        self.platform_fail_count: Dict[str, int] = {name: 0 for name in UPLOAD_PLATFORMS}
        self.platform_backlog_count: Dict[str, int] = {name: 0 for name in UPLOAD_PLATFORMS}
        self._upload_queues: Dict[str, queue.Queue] = {}

    def subscribe(self) -> asyncio.Queue:
        """ÿ SSE Ӷһ"""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._sse_lock:
            self._sse_queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._sse_lock:
            try:
                self._sse_queues.remove(q)
            except ValueError:
                pass

    def broadcast(self, event: Dict[str, Any]) -> None:
        """̹߳㲥־¼"""
        with self._sse_lock:
            for q in list(self._sse_queues):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def _make_emitter(self) -> EventEmitter:
        """һŽӵ㲥 EventEmitter"""
        thread_q: queue.Queue = queue.Queue(maxsize=500)

        def _bridge() -> None:
            """̶߳Ѳ㲥 asyncio """
            while True:
                try:
                    event = thread_q.get(timeout=0.2)
                    if event is None:
                        break
                    self.broadcast(event)
                except queue.Empty:
                    if self.stop_event.is_set() and thread_q.empty():
                        break

        bridge_thread = threading.Thread(target=_bridge, daemon=True)
        bridge_thread.start()
        self._bridge_thread = bridge_thread
        self._bridge_q = thread_q

        return EventEmitter(q=thread_q, cli_mode=True)

    def _stop_bridge(self) -> None:
        if hasattr(self, "_bridge_q"):
            try:
                self._bridge_q.put_nowait(None)
            except queue.Full:
                pass

    def start_task(
        self,
        proxy: str,
        multithread: bool = False,
        thread_count: int = 1,
        target_count: int = 0,
        cpa_target_count: Optional[int] = None,
        sub2api_target_count: Optional[int] = None,
    ) -> None:
        cpa_target = None if cpa_target_count is None else max(0, int(cpa_target_count))
        sub2api_target = None if sub2api_target_count is None else max(0, int(sub2api_target_count))
        upload_mode = str(_sync_config.get("upload_mode", "snapshot") or "snapshot").strip().lower()
        if upload_mode not in ("snapshot", "decoupled"):
            upload_mode = "snapshot"

        with self._task_lock:
            if self.status in ("running", "stopping"):
                raise RuntimeError("лֹͣ")
            self.status = "running"
            self.stop_event.clear()
            self.current_proxy = proxy
            n = max(1, min(thread_count, 10)) if multithread else 1
            self.worker_count = n
            self.multithread = multithread
            self.upload_mode = upload_mode
            self.target_count = max(0, target_count)
            self.run_success_count = 0
            self.run_fail_count = 0
            self.platform_success_count = {name: 0 for name in UPLOAD_PLATFORMS}
            self.platform_fail_count = {name: 0 for name in UPLOAD_PLATFORMS}
            self.platform_backlog_count = {name: 0 for name in UPLOAD_PLATFORMS}
            self._upload_queues = {}
            self._worker_threads = {}

        emitter = self._make_emitter()
        emitter.info(
            f"ϴ: {'вƽ̨CPASub2Api' if upload_mode == 'snapshot' else '˫ƽ̨ͬ˺˫ϴ'}",
            step="mode",
        )

        mail_router = MultiMailRouter(_sync_config)
        pool_maintainer = _get_pool_maintainer()
        auto_sync_enabled = _sync_config.get("auto_sync", "true") == "true"
        upload_remaining: Dict[str, Optional[int]] = {
            "cpa": cpa_target,
            "sub2api": sub2api_target,
        }
        snapshot_strict_serial = (
            upload_mode == "snapshot"
            and cpa_target is not None
            and sub2api_target is not None
        )
        token_states: Dict[str, Dict[str, Any]] = {}
        token_states_lock = threading.RLock()
        upload_queues: Dict[str, queue.Queue] = {}
        upload_workers: Dict[str, threading.Thread] = {}
        producers_done = threading.Event()

        def _reserve_upload_slot(platform: str) -> bool:
            with self._task_lock:
                remain = upload_remaining.get(platform)
                if remain is None:
                    return True
                if remain <= 0:
                    return False
                upload_remaining[platform] = remain - 1
                return True

        def _release_upload_slot(platform: str) -> None:
            with self._task_lock:
                remain = upload_remaining.get(platform)
                if remain is not None:
                    upload_remaining[platform] = remain + 1

        def _decoupled_slots_exhausted() -> bool:
            """˫ƽ̨ͬ + жǷ޿ϴλ"""
            if upload_mode != "decoupled":
                return False
            with self._task_lock:
                finite_remains = [
                    remain
                    for remain in upload_remaining.values()
                    if remain is not None
                ]
            return bool(finite_remains) and all(remain <= 0 for remain in finite_remains)

        def _reserve_snapshot_serial_platform() -> Optional[str]:
            with self._task_lock:
                cpa_remain = upload_remaining.get("cpa")
                if cpa_remain is not None and cpa_remain > 0:
                    upload_remaining["cpa"] = cpa_remain - 1
                    return "cpa"
                sub2api_remain = upload_remaining.get("sub2api")
                if sub2api_remain is not None and sub2api_remain > 0:
                    upload_remaining["sub2api"] = sub2api_remain - 1
                    return "sub2api"
            return None

        def _record_platform_result(platform: str, ok: bool) -> None:
            if platform not in UPLOAD_PLATFORMS:
                return
            with self._task_lock:
                if ok:
                    self.platform_success_count[platform] = self.platform_success_count.get(platform, 0) + 1
                else:
                    self.platform_fail_count[platform] = self.platform_fail_count.get(platform, 0) + 1

        def _refresh_backlog() -> None:
            with self._task_lock:
                if upload_mode != "decoupled":
                    self.platform_backlog_count = {name: 0 for name in UPLOAD_PLATFORMS}
                    return
                self.platform_backlog_count = {
                    platform: q.qsize()
                    for platform, q in upload_queues.items()
                }

        def _apply_final_result(email: str, prefix: str, ok: bool) -> None:
            if ok:
                with self._task_lock:
                    self.success_count += 1
                    self.run_success_count += 1
                    _save_state(self.success_count, self.fail_count)
                    should_stop = self.target_count > 0 and self.run_success_count >= self.target_count
                if should_stop:
                    emitter.success(
                        f"{prefix}ѴĿ {self.target_count} Զֹͣ",
                        step="auto_stop",
                    )
                    self.stop_event.set()
            else:
                with self._task_lock:
                    self.fail_count += 1
                    self.run_fail_count += 1
                    _save_state(self.success_count, self.fail_count)
                emitter.error(f"{prefix}ƽ̨ϴδɣβɹ: {email}", step="retry")

        def _auto_sync(file_name: str, email: str, em: "EventEmitter") -> bool:
            cfg = _sync_config
            if cfg.get("auto_sync", "true") != "true":
                return True
            base_url = cfg.get("base_url", "").strip()
            bearer = cfg.get("bearer_token", "").strip()
            if not base_url or not bearer:
                em.error("Զͬȱƽַ̨ Tokenȱ", step="sync")
                return False

            em.info(f"Զͬ {email}...", step="sync")
            fpath = os.path.join(TOKENS_DIR, file_name)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    token_data = json.load(f)
            except Exception as e:
                em.error(f"Զͬ쳣: ȡ Token ʧ: {e}", step="sync")
                return False

            last_status = 0
            last_body = ""
            for attempt in range(3):
                try:
                    result = _push_account_api(base_url, bearer, email, token_data)
                    last_status = int(result.get("status") or 0)
                    last_body = str(result.get("body") or "")
                    if result.get("ok"):
                        if not _mark_token_uploaded_platform(fpath, "sub2api"):
                            em.warn(f"Զͬɹرʧ: {email}", step="sync")
                        em.success(f"Զͬɹ: {email}", step="sync")
                        return True
                except Exception as e:
                    last_status = 0
                    last_body = str(e)
                if attempt < 2:
                    time.sleep(2 ** attempt)

            em.error(f"Զͬʧ({last_status}): {last_body[:120]}", step="sync")
            return False

        def _upload_to_cpa(file_name: str, file_path: str, token_json: str, email: str, prefix: str) -> bool:
            if not pool_maintainer:
                return True
            try:
                td = json.loads(token_json)
                cpa_ok = pool_maintainer.upload_token(file_name, td, proxy=proxy or "")
                if cpa_ok:
                    if not _mark_token_uploaded_platform(file_path, "cpa"):
                        emitter.warn(f"{prefix}CPA ϴɹرʧ: {email}", step="cpa_upload")
                    emitter.success(f"{prefix}CPA ϴɹ: {email}", step="cpa_upload")
                else:
                    emitter.error(f"{prefix}CPA ϴʧ: {email}", step="cpa_upload")
                return cpa_ok
            except Exception as ex:
                emitter.error(f"{prefix}CPA ϴ쳣: {ex}", step="cpa_upload")
                return False

        def _upload_to_sub2api(file_name: str, email: str, refresh_token: str, prefix: str) -> bool:
            if not auto_sync_enabled:
                return True
            if not refresh_token:
                emitter.error(f"{prefix}ȱ refresh_token޷Զͬ: {email}", step="sync")
                return False
            return _auto_sync(file_name, email, emitter)

        def _register_decoupled_token(
            token_key: str,
            email: str,
            prefix: str,
            required_platforms: set[str],
            failed_platforms: set[str],
        ) -> None:
            final_ok: Optional[bool] = None
            no_required_platforms = False
            with token_states_lock:
                token_states[token_key] = {
                    "email": email,
                    "prefix": prefix,
                    "required": set(required_platforms),
                    "done": set(),
                    "failed": set(failed_platforms),
                    "finalized": False,
                }
                state = token_states[token_key]
                if state["failed"]:
                    state["finalized"] = True
                    token_states.pop(token_key, None)
                    final_ok = False
                elif not state["required"]:
                    state["finalized"] = True
                    token_states.pop(token_key, None)
                    no_required_platforms = True
            if final_ok is not None:
                _apply_final_result(email, prefix, final_ok)
                return
            if no_required_platforms:
                # ľ󣬺ע᲻Ӧɹ
                if _decoupled_slots_exhausted():
                    emitter.info(
                        f"{prefix}ƽ̨Ŀ㣬ϴҲƳɹ: {email}",
                        step="auto_stop",
                    )
                    self.stop_event.set()
                    return
                # ֶϴƽ̨/עɹΪ
                _apply_final_result(email, prefix, True)

        def _complete_decoupled_platform(token_key: str, platform: str, ok: bool) -> None:
            final_ok: Optional[bool] = None
            email = "unknown"
            prefix = ""
            with token_states_lock:
                state = token_states.get(token_key)
                if not state or state.get("finalized"):
                    return
                if ok:
                    state["done"].add(platform)
                else:
                    state["failed"].add(platform)
                email = state.get("email", "unknown")
                prefix = state.get("prefix", "")
                if state["failed"]:
                    state["finalized"] = True
                    token_states.pop(token_key, None)
                    final_ok = False
                elif state["required"].issubset(state["done"]):
                    state["finalized"] = True
                    token_states.pop(token_key, None)
                    final_ok = True
            if final_ok is not None:
                _apply_final_result(email, prefix, final_ok)

        def _enqueue_upload_job(platform: str, job: Dict[str, Any], prefix: str) -> None:
            q = upload_queues.get(platform)
            if not q:
                _release_upload_slot(platform)
                _complete_decoupled_platform(job["token_key"], platform, False)
                return
            try:
                q.put_nowait(job)
                _refresh_backlog()
            except queue.Full:
                emitter.error(f"{prefix}{platform.upper()} ϴ: {job.get('email', 'unknown')}", step="sync")
                _release_upload_slot(platform)
                _complete_decoupled_platform(job["token_key"], platform, False)

        def _upload_worker_loop(platform: str) -> None:
            q = upload_queues[platform]
            while True:
                if producers_done.is_set() and q.empty():
                    break
                try:
                    job = q.get(timeout=0.3)
                except queue.Empty:
                    _refresh_backlog()
                    continue

                _refresh_backlog()
                ok = False
                if platform == "cpa":
                    ok = _upload_to_cpa(
                        file_name=job["file_name"],
                        file_path=job["file_path"],
                        token_json=job["token_json"],
                        email=job["email"],
                        prefix=job.get("prefix", ""),
                    )
                elif platform == "sub2api":
                    ok = _upload_to_sub2api(
                        file_name=job["file_name"],
                        email=job["email"],
                        refresh_token=job.get("refresh_token", ""),
                        prefix=job.get("prefix", ""),
                    )
                _record_platform_result(platform, ok)
                if not ok:
                    _release_upload_slot(platform)
                _complete_decoupled_platform(job["token_key"], platform, ok)
                q.task_done()
                _refresh_backlog()

        def _worker_loop(worker_id: int) -> None:
            prefix = f"[W{worker_id}] " if n > 1 else ""
            count = 0
            while not self.stop_event.is_set():
                if _decoupled_slots_exhausted():
                    emitter.info(f"{prefix}˫ƽ̨Ŀ㣬ֹͣע", step="auto_stop")
                    self.stop_event.set()
                    break
                count += 1
                provider_name, provider = mail_router.next_provider()
                emitter.info(
                    f"{prefix}>>>  {count} ע (: {provider_name}) <<<",
                    step="start",
                )
                try:
                    token_json = run(
                        proxy=proxy or None,
                        emitter=emitter,
                        stop_event=self.stop_event,
                        mail_provider=provider,
                        proxy_pool_config={
                            "enabled": bool(_sync_config.get("proxy_pool_enabled", False)),
                            "provider": _normalize_proxy_pool_provider(_sync_config.get("proxy_pool_provider")),
                            "api_url": str(_sync_config.get("proxy_pool_api_url", "")).strip(),
                            "auth_mode": str(_sync_config.get("proxy_pool_auth_mode", "query")).strip().lower(),
                            "api_key": str(_sync_config.get("proxy_pool_api_key", "")).strip(),
                            "count": _sync_config.get("proxy_pool_count", 1),
                            "country": str(_sync_config.get("proxy_pool_country", "US") or "US").strip().upper(),
                        },
                    )

                    # յֹͣźʱȻȴѳɹõ token⡰עɹδ/δͬ
                    if self.stop_event.is_set() and not token_json:
                        break

                    if token_json:
                        mail_router.report_success(provider_name)
                        try:
                            t_data = json.loads(token_json)
                            fname_email = t_data.get("email", "unknown").replace("@", "_")
                            refresh_token = t_data.get("refresh_token", "")
                            email = t_data.get("email", "unknown")
                        except Exception:
                            fname_email = "unknown"
                            refresh_token = ""
                            email = "unknown"

                        file_name = f"token_{fname_email}_{time.time_ns()}.json"
                        file_path = os.path.join(TOKENS_DIR, file_name)
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(token_json)

                        emitter.success(f"{prefix}Token ѱ: {file_name}", step="saved")
                        self.broadcast({
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "level": "token_saved",
                            "message": file_name,
                            "step": "saved",
                        })

                        if upload_mode == "snapshot":
                            if snapshot_strict_serial:
                                selected_platform = _reserve_snapshot_serial_platform()
                                if selected_platform == "cpa":
                                    emitter.info(f"{prefix}ģʽνϴ CPA -> {email}", step="cpa_upload")
                                    cpa_ok = _upload_to_cpa(file_name, file_path, token_json, email, prefix) if pool_maintainer else True
                                    _record_platform_result("cpa", cpa_ok)
                                    if not cpa_ok:
                                        _release_upload_slot("cpa")
                                    _apply_final_result(email, prefix, cpa_ok)
                                elif selected_platform == "sub2api":
                                    emitter.info(f"{prefix}ģʽνϴ Sub2Api -> {email}", step="sync")
                                    sub2api_ok = _upload_to_sub2api(file_name, email, refresh_token, prefix) if auto_sync_enabled else True
                                    _record_platform_result("sub2api", sub2api_ok)
                                    if not sub2api_ok:
                                        _release_upload_slot("sub2api")
                                    _apply_final_result(email, prefix, sub2api_ok)
                                else:
                                    emitter.info(f"{prefix}ģʽĿ㣬ֹͣϴ: {email}", step="auto_stop")
                                    self.stop_event.set()
                            else:
                                cpa_ok = True
                                cpa_required = False
                                if pool_maintainer:
                                    cpa_required = _reserve_upload_slot("cpa")
                                if pool_maintainer and not cpa_required:
                                    emitter.info(f"{prefix}CPA ѴĿֵϴ: {email}", step="cpa_upload")
                                if pool_maintainer and cpa_required:
                                    cpa_ok = _upload_to_cpa(file_name, file_path, token_json, email, prefix)
                                    _record_platform_result("cpa", cpa_ok)
                                    if not cpa_ok:
                                        _release_upload_slot("cpa")

                                sub2api_ok = True
                                sub2api_required = False
                                if auto_sync_enabled:
                                    sub2api_required = _reserve_upload_slot("sub2api")
                                if auto_sync_enabled and not sub2api_required:
                                    emitter.info(f"{prefix}Sub2Api ѴĿֵͬ: {email}", step="sync")
                                if auto_sync_enabled and sub2api_required:
                                    sub2api_ok = _upload_to_sub2api(file_name, email, refresh_token, prefix)
                                    _record_platform_result("sub2api", sub2api_ok)
                                    if not sub2api_ok:
                                        _release_upload_slot("sub2api")

                                _apply_final_result(email, prefix, cpa_ok and sub2api_ok)
                        else:
                            required_platforms: set[str] = set()
                            failed_platforms: set[str] = set()

                            if pool_maintainer:
                                if _reserve_upload_slot("cpa"):
                                    required_platforms.add("cpa")
                                else:
                                    emitter.info(f"{prefix}CPA ѴĿֵϴ: {email}", step="cpa_upload")

                            if auto_sync_enabled:
                                if _reserve_upload_slot("sub2api"):
                                    if refresh_token:
                                        required_platforms.add("sub2api")
                                    else:
                                        failed_platforms.add("sub2api")
                                        _release_upload_slot("sub2api")
                                        emitter.error(f"{prefix}ȱ refresh_token޷Զͬ: {email}", step="sync")
                                else:
                                    emitter.info(f"{prefix}Sub2Api ѴĿֵͬ: {email}", step="sync")

                            token_key = file_name
                            _register_decoupled_token(token_key, email, prefix, required_platforms, failed_platforms)

                            base_job = {
                                "token_key": token_key,
                                "file_name": file_name,
                                "file_path": file_path,
                                "token_json": token_json,
                                "email": email,
                                "refresh_token": refresh_token,
                                "prefix": prefix,
                            }
                            if "cpa" in required_platforms:
                                _enqueue_upload_job("cpa", base_job, prefix)
                            if "sub2api" in required_platforms:
                                _enqueue_upload_job("sub2api", base_job, prefix)
                    else:
                        mail_router.report_failure(provider_name)
                        with self._task_lock:
                            self.fail_count += 1
                            self.run_fail_count += 1
                            _save_state(self.success_count, self.fail_count)
                        emitter.error(f"{prefix}עʧܣԺ...", step="retry")

                except Exception as e:
                    mail_router.report_failure(provider_name)
                    with self._task_lock:
                        self.fail_count += 1
                        self.run_fail_count += 1
                        _save_state(self.success_count, self.fail_count)
                    emitter.error(f"{prefix}δ쳣: {e}", step="runtime")

                if self.stop_event.is_set():
                    break

                wait = random.randint(5, 30)
                emitter.info(f"{prefix}Ϣ {wait} ...", step="wait")
                self.stop_event.wait(wait)

        if upload_mode == "decoupled":
            upload_queues = {
                platform: queue.Queue(maxsize=2000)
                for platform in UPLOAD_PLATFORMS
            }
            with self._task_lock:
                self._upload_queues = upload_queues
            _refresh_backlog()
            for platform in UPLOAD_PLATFORMS:
                t = threading.Thread(target=_upload_worker_loop, args=(platform,), daemon=True)
                upload_workers[platform] = t
                t.start()

        def _monitor() -> None:
            with self._task_lock:
                workers = list(self._worker_threads.values())
            for t in workers:
                t.join()
            if upload_mode == "decoupled":
                producers_done.set()
                for ut in upload_workers.values():
                    ut.join()
                stale_results: List[Dict[str, Any]] = []
                with token_states_lock:
                    for token_key in list(token_states.keys()):
                        state = token_states.pop(token_key, None)
                        if state and not state.get("finalized"):
                            stale_results.append(state)
                for state in stale_results:
                    _apply_final_result(state.get("email", "unknown"), state.get("prefix", ""), False)
                with self._task_lock:
                    self._upload_queues = {}
                    self.platform_backlog_count = {name: 0 for name in UPLOAD_PLATFORMS}
            emitter.info("Workerֹͣ", step="stopped")
            self._stop_bridge()
            with self._task_lock:
                self.status = "idle"
                self._worker_threads.clear()
                self.worker_count = 0

        for wid in range(1, n + 1):
            t = threading.Thread(target=_worker_loop, args=(wid,), daemon=True)
            with self._task_lock:
                self._worker_threads[wid] = t
            t.start()

        self.thread = threading.Thread(target=_monitor, daemon=True)
        self.thread.start()

    def stop_task(self) -> None:
        with self._task_lock:
            if self.status == "running":
                self.status = "stopping"
                self.stop_event.set()


_state = TaskState()

# Զά̨
_auto_maintain_thread: Optional[threading.Thread] = None
_auto_maintain_stop = threading.Event()
_pool_maintain_lock = threading.Lock()

_local_auto_maintain_thread: Optional[threading.Thread] = None
_local_auto_maintain_stop = threading.Event()
_local_maintain_lock = threading.Lock()


def _get_pool_maintainer() -> Optional[PoolMaintainer]:
    cfg = _sync_config
    base_url = str(cfg.get("cpa_base_url", "")).strip()
    token = str(cfg.get("cpa_token", "")).strip()
    if not base_url or not token:
        return None
    return PoolMaintainer(
        cpa_base_url=base_url,
        cpa_token=token,
        min_candidates=int(cfg.get("min_candidates", 800)),
        used_percent_threshold=int(cfg.get("used_percent_threshold", 95)),
    )


def _get_sub2api_maintainer() -> Optional[Sub2ApiMaintainer]:
    cfg = _sync_config
    base_url = str(cfg.get("base_url", "")).strip()
    bearer = str(cfg.get("bearer_token", "")).strip()
    email = str(cfg.get("email", "")).strip()
    password = str(cfg.get("password", "")).strip()
    if not base_url:
        return None
    if not bearer and not (email and password):
        return None
    return Sub2ApiMaintainer(
        base_url=base_url,
        bearer_token=bearer,
        min_candidates=int(cfg.get("sub2api_min_candidates", 200)),
        email=email,
        password=password,
    )


# ==========================================
# API ·
# ==========================================


class StartRequest(BaseModel):
    proxy: str = "http://127.0.0.1:7897"
    multithread: bool = False
    thread_count: int = 3


class ProxyCheckRequest(BaseModel):
    proxy: str


class ProxyPoolTestRequest(BaseModel):
    enabled: bool = True
    provider: str = "zenproxy_api"
    api_url: str = "https://zenproxy.top/api/fetch"
    auth_mode: str = "query"  # "header" | "query"
    api_key: str = ""
    count: int = 1
    country: str = "US"


class ProxyPoolConfigRequest(BaseModel):
    proxy_pool_enabled: bool = True
    proxy_pool_provider: str = "zenproxy_api"
    proxy_pool_api_url: str = "https://zenproxy.top/api/fetch"
    proxy_pool_auth_mode: str = "query"  # "header" | "query"
    proxy_pool_api_key: str = ""
    proxy_pool_count: int = 1
    proxy_pool_country: str = "US"


class ProxySaveRequest(BaseModel):
    proxy: str
    auto_register: bool = False
    desired_token_count: int = 0
    local_auto_maintain: bool = False
    local_maintain_interval_minutes: int = 30


class SyncConfigRequest(BaseModel):
    base_url: str          # Sub2Api ƽַ̨
    bearer_token: str = ""  # Ա JWTѡ
    email: str = ""        # Ա
    password: str = ""     # Ա
    account_name: str = "AutoReg"
    auto_sync: str = "true"  # "true" | "false"
    upload_mode: str = "snapshot"  # "snapshot" | "decoupled"
    sub2api_min_candidates: int = 200
    sub2api_auto_maintain: bool = False
    sub2api_maintain_interval_minutes: int = 30
    multithread: bool = False
    thread_count: int = 3
    auto_register: bool = False
    desired_token_count: int = 0
    local_auto_maintain: bool = False
    local_maintain_interval_minutes: int = 30


class SyncNowRequest(BaseModel):
    filenames: List[str] = []  # б = ͬȫ


class UploadModeRequest(BaseModel):
    upload_mode: str = "snapshot"  # "snapshot" | "decoupled"


class ApiAuthConfigRequest(BaseModel):
    enabled: bool = False
    api_key: str = ""
    regenerate: bool = False


@app.get("/api/auth/status")
async def api_auth_status() -> Dict[str, Any]:
    env_key = _env_api_key()
    configured_key = _configured_api_key()
    effective_key = _effective_api_key()
    return {
        "enabled": _is_api_auth_enabled(),
        "source": "env" if env_key else "config",
        "env_override": bool(env_key),
        "api_auth_enabled": bool(_sync_config.get("api_auth_enabled", False)),
        "configured": bool(effective_key),
        "api_key_preview": _mask_secret(effective_key, 8),
        "configured_key_preview": _mask_secret(configured_key, 8),
        "accept": [
            "Authorization: Bearer <api_key>",
            "X-API-Key: <api_key>",
            "query ?api_key=<api_key> (SSE)",
        ],
    }


@app.post("/api/auth/config")
async def api_set_auth_config(req: ApiAuthConfigRequest) -> Dict[str, Any]:
    if _env_api_key():
        raise HTTPException(status_code=409, detail="X_API_KEY 环境变量已生效，不能通过页面修改")

    enabled = bool(req.enabled)
    new_key = str(req.api_key or "").strip()
    current = _configured_api_key()

    if req.regenerate:
        new_key = _generate_api_key()
    elif enabled and not new_key and not current:
        new_key = _generate_api_key()

    if enabled and not new_key and not current:
        raise HTTPException(status_code=400, detail="启用鉴权前请填写 API Key")

    if new_key:
        _sync_config["x_api_key"] = new_key
    elif not enabled:
        _sync_config["x_api_key"] = current

    if enabled and not _configured_api_key():
        raise HTTPException(status_code=400, detail="启用鉴权前请填写 API Key")

    _sync_config["api_auth_enabled"] = enabled
    _save_sync_config(_sync_config)

    return {
        "status": "saved",
        "enabled": _is_api_auth_enabled(),
        "api_key": new_key,
        "api_key_preview": _mask_secret(_effective_api_key(), 8),
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ǰļδҵ</h1>", status_code=404)


@app.post("/api/start")
async def api_start(req: StartRequest) -> Dict[str, Any]:
    try:
        _state.start_task(req.proxy, req.multithread, req.thread_count)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "started", "proxy": req.proxy, "workers": _state.worker_count}


@app.post("/api/stop")
async def api_stop() -> Dict[str, str]:
    if _state.status == "idle":
        raise HTTPException(status_code=409, detail="ûе")
    _state.stop_task()
    return {"status": "stopping"}


@app.post("/api/proxy/save")
async def api_save_proxy(req: ProxySaveRequest) -> Dict[str, str]:
    _sync_config["proxy"] = req.proxy.strip()
    _sync_config["auto_register"] = req.auto_register
    _sync_config["desired_token_count"] = max(0, int(req.desired_token_count))
    _sync_config["local_auto_maintain"] = bool(req.local_auto_maintain)
    _sync_config["local_maintain_interval_minutes"] = max(5, int(req.local_maintain_interval_minutes))
    _save_sync_config(_sync_config)
    _stop_local_auto_maintain()
    if _sync_config.get("local_auto_maintain"):
        _start_local_auto_maintain()
    return {"status": "saved"}


@app.get("/api/proxy")
async def api_get_proxy() -> Dict[str, Any]:
    return {
        "proxy": _sync_config.get("proxy", ""),
        "auto_register": _sync_config.get("auto_register", False),
        "desired_token_count": _sync_config.get("desired_token_count", 0),
        "local_auto_maintain": _sync_config.get("local_auto_maintain", False),
        "local_maintain_interval_minutes": _sync_config.get("local_maintain_interval_minutes", 30),
    }


@app.get("/api/status")
async def api_status() -> Dict[str, Any]:
    return {
        "status": _state.status,
        "success": _state.success_count,
        "fail": _state.fail_count,
        "proxy": _state.current_proxy,
        "worker_count": _state.worker_count,
        "multithread": _state.multithread,
        "upload_mode": _state.upload_mode,
        "platform_success": dict(_state.platform_success_count),
        "platform_fail": dict(_state.platform_fail_count),
        "platform_backlog": dict(_state.platform_backlog_count),
    }


@app.get("/api/tokens")
async def api_tokens() -> Dict[str, Any]:
    tokens = []
    recent_total_tokens = 0
    if os.path.isdir(TOKENS_DIR):
        # ļеʱУעǰ棩
        # ļʽ: token_email_1234567890.jsonȡһΪʱ
        import re
        def _sort_key(f):
            m = re.search(r'_(\d{10,})\.json$', f)
            return int(m.group(1)) if m else 0
        all_files = [f for f in os.listdir(TOKENS_DIR) if f.endswith(".json")]
        all_files.sort(key=_sort_key, reverse=True)
        for fname in all_files:
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(TOKENS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content_raw = json.load(f)
                content = content_raw if isinstance(content_raw, dict) else {}
                uploaded_platforms = _extract_uploaded_platforms(content)
                plan_type = _extract_plan_type(content) or str(content.get("plan_type") or "").strip() or "unknown"
                quota_remaining_percent = _to_float(content.get("quota_remaining_percent"))
                if quota_remaining_percent is None:
                    quota_used_percent = _to_float(content.get("quota_used_percent"))
                    if quota_used_percent is not None:
                        quota_remaining_percent = max(0.0, min(100.0, 100.0 - quota_used_percent))
                recent_token_usage = _to_int(content.get("recent_token_usage"))
                if recent_token_usage is not None and recent_token_usage > 0:
                    recent_total_tokens += recent_token_usage
                tokens.append(
                    {
                        "filename": fname,
                        "email": content.get("email", ""),
                        "expired": content.get("expired", ""),
                        "uploaded_platforms": uploaded_platforms,
                        "plan_type": plan_type,
                        "quota_remaining_percent": quota_remaining_percent,
                        "recent_token_usage": recent_token_usage,
                        "last_alive_check": content.get("last_alive_check", ""),
                        "alive": content.get("alive", None),
                        "content": content,
                    }
                )
            except Exception:
                pass
    return {
        "tokens": tokens,
        "summary": {
            "total_accounts": len(tokens),
            "recent_total_tokens": recent_total_tokens,
        },
    }


@app.delete("/api/tokens/{filename}")
async def api_delete_token(filename: str) -> Dict[str, str]:
    # ȫˣֹ·Խ
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Ƿļ")
    fpath = os.path.join(TOKENS_DIR, filename)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="ļ")
    os.remove(fpath)
    return {"status": "deleted"}


@app.get("/api/sync-config")
async def api_get_sync_config() -> Dict[str, Any]:
    """ȡǰͬã"""
    cfg = dict(_sync_config)
    cfg["password"] = ""  # ش
    token = cfg.get("bearer_token", "")
    cfg["bearer_token_preview"] = token[:12] + "..." if len(token) > 12 else (token or "")
    cfg["bearer_token"] = ""  # ش token
    #  cpa_token
    cpa_token = str(cfg.get("cpa_token", ""))
    cfg["cpa_token_preview"] = (cpa_token[:12] + "...") if len(cpa_token) > 12 else (cpa_token or "")
    cfg["cpa_token"] = ""
    proxy_pool_api_key = str(cfg.get("proxy_pool_api_key", ""))
    cfg["proxy_pool_api_key_preview"] = (
        (proxy_pool_api_key[:8] + "...") if len(proxy_pool_api_key) > 8 else (proxy_pool_api_key or "")
    )
    cfg["proxy_pool_api_key"] = ""
    x_api_key = str(cfg.get("x_api_key", ""))
    cfg["x_api_key_preview"] = _mask_secret(x_api_key, 8)
    cfg["x_api_key"] = ""
    #  mail_provider_configs
    raw_configs = cfg.get("mail_provider_configs") or {}
    safe_configs: Dict[str, Dict] = {}
    for pname, pcfg in raw_configs.items():
        if not isinstance(pcfg, dict):
            continue
        sc = dict(pcfg)
        for secret_key in ("bearer_token", "api_key", "site_password", "admin_password", "x_custom_auth", "x_admin_auth"):
            val = str(sc.get(secret_key, ""))
            if val:
                sc[f"{secret_key}_preview"] = (val[:8] + "...") if len(val) > 8 else val
                sc.pop(secret_key, None)
        safe_configs[pname] = sc
    cfg["mail_provider_configs"] = safe_configs
    cfg.setdefault("sub2api_min_candidates", 200)
    cfg.setdefault("sub2api_auto_maintain", False)
    cfg.setdefault("sub2api_maintain_interval_minutes", 30)
    cfg.setdefault("upload_mode", "snapshot")
    cfg.setdefault("multithread", False)
    cfg.setdefault("thread_count", 3)
    cfg.setdefault("proxy_pool_enabled", True)
    cfg.setdefault("proxy_pool_provider", DEFAULT_PROXY_POOL_PROVIDER)
    cfg.setdefault("proxy_pool_api_url", _default_proxy_pool_api_url(_normalize_proxy_pool_provider(cfg.get("proxy_pool_provider"))))
    cfg.setdefault("proxy_pool_auth_mode", "query")
    cfg.setdefault("proxy_pool_count", 1)
    cfg.setdefault("proxy_pool_country", "US")
    cfg.setdefault("desired_token_count", 0)
    cfg.setdefault("local_auto_maintain", False)
    cfg.setdefault("local_maintain_interval_minutes", 30)
    cfg.setdefault("api_auth_enabled", False)
    return cfg


@app.get("/api/proxy-pool/config")
async def api_get_proxy_pool_config() -> Dict[str, Any]:
    provider = _normalize_proxy_pool_provider(_sync_config.get("proxy_pool_provider"))
    default_url = _default_proxy_pool_api_url(provider)
    api_url = str(_sync_config.get("proxy_pool_api_url", default_url) or "").strip()
    if not api_url:
        api_url = default_url
    auth_mode = str(_sync_config.get("proxy_pool_auth_mode", "query") or "").strip().lower()
    if auth_mode not in ("header", "query"):
        auth_mode = "query"
    try:
        count = max(1, min(int(_sync_config.get("proxy_pool_count", 1) or 1), 20))
    except (TypeError, ValueError):
        count = 1
    country = str(_sync_config.get("proxy_pool_country", "US") or "US").strip().upper() or "US"
    api_key = str(_sync_config.get("proxy_pool_api_key", "") or "").strip()
    return {
        "proxy_pool_enabled": bool(_sync_config.get("proxy_pool_enabled", True)),
        "proxy_pool_provider": provider,
        "proxy_pool_api_url": api_url,
        "proxy_pool_auth_mode": auth_mode,
        "proxy_pool_api_key": "",
        "proxy_pool_api_key_preview": (api_key[:8] + "...") if len(api_key) > 8 else (api_key or ""),
        "proxy_pool_count": count,
        "proxy_pool_country": country,
    }


@app.post("/api/proxy-pool/config")
async def api_set_proxy_pool_config(req: ProxyPoolConfigRequest) -> Dict[str, Any]:
    provider = _normalize_proxy_pool_provider(req.proxy_pool_provider)
    proxy_pool_auth_mode = str(req.proxy_pool_auth_mode or "query").strip().lower()
    if proxy_pool_auth_mode not in ("header", "query"):
        proxy_pool_auth_mode = "query"

    default_url = _default_proxy_pool_api_url(provider)
    proxy_pool_api_url = str(req.proxy_pool_api_url or default_url).strip()
    if not proxy_pool_api_url:
        proxy_pool_api_url = default_url

    proxy_pool_api_key = req.proxy_pool_api_key.strip() if req.proxy_pool_api_key else ""
    if not proxy_pool_api_key:
        proxy_pool_api_key = str(_sync_config.get("proxy_pool_api_key", "") or "").strip()

    try:
        proxy_pool_count = max(1, min(int(req.proxy_pool_count), 20))
    except (TypeError, ValueError):
        proxy_pool_count = 1
    proxy_pool_country = str(req.proxy_pool_country or "US").strip().upper() or "US"

    _sync_config.update({
        "proxy_pool_enabled": bool(req.proxy_pool_enabled),
        "proxy_pool_provider": provider,
        "proxy_pool_api_url": proxy_pool_api_url,
        "proxy_pool_auth_mode": proxy_pool_auth_mode,
        "proxy_pool_api_key": proxy_pool_api_key,
        "proxy_pool_count": proxy_pool_count,
        "proxy_pool_country": proxy_pool_country,
    })
    _save_sync_config(_sync_config)
    return {"status": "saved"}


@app.post("/api/upload-mode")
async def api_set_upload_mode(req: UploadModeRequest) -> Dict[str, Any]:
    upload_mode = str(req.upload_mode or "snapshot").strip().lower()
    if upload_mode not in ("snapshot", "decoupled"):
        raise HTTPException(status_code=400, detail="upload_mode ֧ snapshot / decoupled")
    _sync_config["upload_mode"] = upload_mode
    _save_sync_config(_sync_config)
    # ״̬ͬڴ״̬ǰǰ
    with _state._task_lock:
        if _state.status == "idle":
            _state.upload_mode = upload_mode
    return {"status": "saved", "upload_mode": upload_mode}


def _verify_sub2api_login(base_url: str, email: str, password: str) -> Dict[str, Any]:
    """ͨ HTTP API ֤ Sub2Api ƽ̨¼ƾǷȷ"""
    from curl_cffi import requests as cffi_req

    # ԶȫЭ飨 https://
    url = base_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    login_url = url.rstrip("/") + "/api/v1/auth/login"
    try:
        resp = cffi_req.post(
            login_url,
            json={"email": email, "password": password},
            impersonate="chrome",
            timeout=15,
        )
        raw_body = resp.text
        if resp.status_code != 200:
            try:
                err_body = json.loads(raw_body)
                err_msg = err_body.get("message") or err_body.get("error") or raw_body[:200]
            except json.JSONDecodeError:
                err_msg = raw_body[:200]
            return {"ok": False, "error": f"¼ʧ(HTTP {resp.status_code}): {err_msg}"}
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"ط JSON ʽ: {raw_body[:200]}"}

        token = (
            body.get("token")
            or body.get("access_token")
            or (body.get("data") or {}).get("token")
            or (body.get("data") or {}).get("access_token")
            or ""
        )
        return {"ok": True, "token": token}
    except Exception as e:
        return {"ok": False, "error": f"쳣: {e}"}


@app.post("/api/sync-config")
async def api_set_sync_config(req: SyncConfigRequest) -> Dict[str, Any]:
    """ͬã֤¼ƾݣ"""
    global _sync_config
    new_base_url = req.base_url.strip()
    if new_base_url and not new_base_url.startswith(("http://", "https://")):
        new_base_url = "https://" + new_base_url
    new_email = req.email.strip()
    new_password = req.password.strip() if req.password else _sync_config.get("password", "")

    if not new_base_url:
        raise HTTPException(status_code=400, detail="дƽַ̨")
    if not new_email or not new_password:
        raise HTTPException(status_code=400, detail="д")

    # ֤¼ƾ
    verify = _verify_sub2api_login(new_base_url, new_email, new_password)
    if not verify["ok"]:
        raise HTTPException(status_code=400, detail=verify["error"])

    upload_mode = str(req.upload_mode or "snapshot").strip().lower()
    if upload_mode not in ("snapshot", "decoupled"):
        upload_mode = "snapshot"

    _sync_config.update({
        "base_url": new_base_url,
        "bearer_token": verify.get("token", "") or _sync_config.get("bearer_token", ""),
        "email": new_email,
        "password": new_password,
        "account_name": req.account_name.strip(),
        "auto_sync": req.auto_sync,
        "upload_mode": upload_mode,
        "sub2api_min_candidates": max(1, req.sub2api_min_candidates),
        "sub2api_auto_maintain": req.sub2api_auto_maintain,
        "sub2api_maintain_interval_minutes": max(5, req.sub2api_maintain_interval_minutes),
        "multithread": req.multithread,
        "thread_count": max(1, min(req.thread_count, 10)),
        "auto_register": req.auto_register,
        "desired_token_count": max(0, req.desired_token_count),
        "local_auto_maintain": bool(req.local_auto_maintain),
        "local_maintain_interval_minutes": max(5, req.local_maintain_interval_minutes),
    })
    # ʷֶ
    _sync_config.pop("headful", None)
    _save_sync_config(_sync_config)

    # ͣȷ߳˳
    _stop_sub2api_auto_maintain()
    if req.sub2api_auto_maintain:
        _start_sub2api_auto_maintain()
    _stop_local_auto_maintain()
    if req.local_auto_maintain:
        _start_local_auto_maintain()

    return {"status": "saved", "verified": True}


@app.post("/api/sync-now")
async def api_sync_now(req: SyncNowRequest) -> Dict[str, Any]:
    """ֶͬ Token ļ͵ Sub2Api ƽ̨"""
    cfg = _sync_config
    base_url = cfg.get("base_url", "").strip()
    bearer   = cfg.get("bearer_token", "").strip()
    if not base_url or not bearer:
        raise HTTPException(status_code=400, detail=" Sub2Api ƽַ̨ Bearer Token")

    results = []
    fnames = req.filenames
    if not fnames:
        if os.path.isdir(TOKENS_DIR):
            fnames = [f for f in os.listdir(TOKENS_DIR) if f.endswith(".json")]

    for fname in fnames:
        if "/" in fname or "\\" in fname or ".." in fname:
            continue
        fpath = os.path.join(TOKENS_DIR, fname)
        if not os.path.isfile(fpath):
            results.append({"file": fname, "ok": False, "error": "ļ"})
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            rt = data.get("refresh_token", "").strip()
            email = data.get("email", fname)
            if not rt:
                results.append({"file": fname, "ok": False, "error": " refresh_token ֶ"})
                continue
            result = _push_refresh_token(base_url, bearer, rt)
            if result["ok"]:
                _mark_token_uploaded_platform(fpath, "sub2api")
            results.append({
                "file": fname,
                "email": email,
                "ok": result["ok"],
                "status": result["status"],
                "body": result["body"][:200],
            })
        except Exception as e:
            results.append({"file": fname, "ok": False, "error": str(e)})

    ok_count  = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    return {"total": len(results), "ok": ok_count, "fail": fail_count, "results": results}


class Sub2ApiLoginRequest(BaseModel):
    base_url: str
    email: str
    password: str


@app.post("/api/sub2api-login")
async def api_sub2api_login(req: Sub2ApiLoginRequest) -> Dict[str, Any]:
    """˺¼ Sub2Api ƽ̨Զȡ Bearer Token"""
    global _sync_config
    base_url = req.base_url.strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="дƽַ̨")

    # ԶȫЭ飨 https://
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    login_url = base_url.rstrip("/") + "/api/v1/auth/login"
    payload = json.dumps({"email": req.email, "password": req.password}).encode("utf-8")
    request = urllib.request.Request(
        login_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            raw_body = resp.read().decode("utf-8")
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                raise HTTPException(status_code=502, detail=f"ط JSON ʽ: {raw_body[:200]}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            err_body = json.loads(raw)
            err_msg = err_body.get("message") or err_body.get("error") or raw[:200]
        except json.JSONDecodeError:
            err_msg = raw[:200]
        raise HTTPException(status_code=exc.code, detail=f"¼ʧ: {err_msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"쳣: {e}")

    # ݲͬӦṹtoken / data.token / access_token
    token = (
        body.get("token")
        or body.get("access_token")
        or (body.get("data") or {}).get("token")
        or (body.get("data") or {}).get("access_token")
        or ""
    )
    if not token:
        raise HTTPException(status_code=502, detail=f"Ӧδҵ token ֶ: {str(body)[:300]}")

    # Զ浽 sync_config
    _sync_config["base_url"] = base_url
    _sync_config["bearer_token"] = token
    _save_sync_config(_sync_config)

    return {"ok": True, "token_preview": token[:16] + "..."}


@app.post("/api/check-proxy")
async def api_check_proxy(req: ProxyCheckRequest) -> Dict[str, Any]:
    """Ƿãͨ Cloudflare Trace"""
    proxy = req.proxy.strip()
    if not proxy:
        return {"ok": False, "loc": None, "error": "ַΪ"}
    try:
        from curl_cffi import requests as cffi_req

        proxies = {"http": proxy, "https": proxy}
        try:
            resp = cffi_req.get(
                "https://cloudflare.com/cdn-cgi/trace",
                proxies=proxies,
                http_version="v2",
                impersonate="chrome",
                timeout=8,
            )
        except Exception as exc:
            if "HTTP/3 is not supported over an HTTP proxy" not in str(exc):
                raise
            resp = cffi_req.get(
                "https://cloudflare.com/cdn-cgi/trace",
                proxies=proxies,
                http_version="v1",
                impersonate="chrome",
                timeout=8,
            )
        text = resp.text
        import re

        loc_m = re.search(r"^loc=(.+)$", text, re.MULTILINE)
        loc = loc_m.group(1) if loc_m else "?"
        supported = loc not in ("CN", "HK")
        return {"ok": supported, "loc": loc, "error": None if supported else "ڵز֧"}
    except Exception as e:
        return {"ok": False, "loc": None, "error": str(e)}


@app.post("/api/proxy-pool/test")
async def api_proxy_pool_test(req: ProxyPoolTestRequest) -> Dict[str, Any]:
    """ԴȡţȡĴѡ loc ̽"""
    provider = _normalize_proxy_pool_provider(req.provider)
    auth_mode = str(req.auth_mode or "query").strip().lower()
    if auth_mode not in ("header", "query"):
        auth_mode = "query"
    default_url = _default_proxy_pool_api_url(provider)
    api_url = str(req.api_url or default_url).strip() or default_url
    api_key = req.api_key.strip() if req.api_key else str(_sync_config.get("proxy_pool_api_key", "")).strip()
    try:
        count = max(1, min(int(req.count or _sync_config.get("proxy_pool_count", 1)), 20))
    except (TypeError, ValueError):
        count = 1
    country = str(req.country or _sync_config.get("proxy_pool_country", "US") or "US").strip().upper() or "US"

    cfg = {
        "enabled": bool(req.enabled),
        "provider": provider,
        "api_url": api_url,
        "auth_mode": auth_mode,
        "api_key": api_key,
        "count": count,
        "country": country,
        "timeout_seconds": 10,
    }
    if not cfg["enabled"]:
        return {"ok": False, "error": "δ"}
    if provider == "zenproxy_api" and not cfg["api_key"]:
        return {"ok": False, "error": "API Key Ϊ"}

    try:
        from curl_cffi import requests as cffi_req
        import re

        relay_url = _pool_relay_url_from_fetch_url(api_url) if provider == "zenproxy_api" else ""
        if relay_url and provider == "zenproxy_api":
            relay_params = {
                "api_key": api_key,
                "url": "https://cloudflare.com/cdn-cgi/trace",
                "country": country,
            }
            try:
                relay_resp = cffi_req.get(
                    relay_url,
                    params=relay_params,
                    http_version="v2",
                    impersonate="chrome",
                    timeout=8,
                )
            except Exception as exc:
                if "HTTP/3 is not supported over an HTTP proxy" not in str(exc):
                    raise
                relay_resp = cffi_req.get(
                    relay_url,
                    params=relay_params,
                    http_version="v1",
                    impersonate="chrome",
                    timeout=8,
                )
            if relay_resp.status_code == 200:
                relay_text = relay_resp.text
                relay_loc_m = re.search(r"^loc=(.+)$", relay_text, re.MULTILINE)
                relay_loc = relay_loc_m.group(1) if relay_loc_m else "?"
                relay_supported = relay_loc not in ("CN", "HK")
                return {
                    "ok": True,
                    "proxy": "(relay)",
                    "relay_used": True,
                    "relay_url": relay_url,
                    "count": count,
                    "country": country,
                    "loc": relay_loc,
                    "supported": relay_supported,
                    "trace_error": None,
                }

        proxy = _fetch_proxy_from_pool(cfg)
        proxies = {"http": proxy, "https": proxy}
        trace_error = ""
        loc = None
        supported = None
        try:
            try:
                resp = cffi_req.get(
                    "https://cloudflare.com/cdn-cgi/trace",
                    proxies=proxies,
                    http_version="v2",
                    impersonate="chrome",
                    timeout=8,
                )
            except Exception as exc:
                if "HTTP/3 is not supported over an HTTP proxy" not in str(exc):
                    raise
                resp = cffi_req.get(
                    "https://cloudflare.com/cdn-cgi/trace",
                    proxies=proxies,
                    http_version="v1",
                    impersonate="chrome",
                    timeout=8,
                )
            text = resp.text
            loc_m = re.search(r"^loc=(.+)$", text, re.MULTILINE)
            loc = loc_m.group(1) if loc_m else "?"
            supported = loc not in ("CN", "HK")
        except Exception as e:
            trace_error = str(e)

        return {
            "ok": True,
            "proxy": proxy,
            "relay_used": False,
            "count": count,
            "country": country,
            "loc": loc,
            "supported": supported,
            "trace_error": trace_error or None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/logs")
async def api_logs() -> StreamingResponse:
    """SSE ʵʱ־"""

    async def event_generator() -> AsyncGenerator[str, None]:
        q = _state.subscribe()
        try:
            # ȷ
            yield f"data: {json.dumps({'ts': '', 'level': 'connected', 'message': '־ӳɹ', 'step': ''}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 
                    yield ": heartbeat\n\n"
                except Exception:
                    break
        finally:
            _state.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )



class BatchSyncRequest(BaseModel):
    filenames: List[str] = []  # б = ͬȫ


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    """ JWT payloadǩ"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        pad = 4 - len(payload) % 4
        if pad != 4:
            payload += "=" * pad
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _extract_plan_type(token_data: Dict[str, Any]) -> str:
    access_token = str(token_data.get("access_token") or "")
    id_token = str(token_data.get("id_token") or "")

    for token in (access_token, id_token):
        if not token:
            continue
        payload = _decode_jwt_payload(token)
        auth_data = payload.get("https://api.openai.com/auth") or {}
        if isinstance(auth_data, dict):
            plan = str(auth_data.get("chatgpt_plan_type") or "").strip()
            if plan:
                return plan
    raw_plan = str(token_data.get("plan_type") or "").strip()
    return raw_plan


def _extract_account_id(token_data: Dict[str, Any]) -> str:
    raw_account_id = str(token_data.get("account_id") or "").strip()
    if raw_account_id:
        return raw_account_id

    access_token = str(token_data.get("access_token") or "")
    if not access_token:
        return ""
    payload = _decode_jwt_payload(access_token)
    auth_data = payload.get("https://api.openai.com/auth") or {}
    if isinstance(auth_data, dict):
        return str(auth_data.get("chatgpt_account_id") or "").strip()
    return ""


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_recent_token_usage(usage_data: Dict[str, Any]) -> Optional[int]:
    """ usage Ӧȡ token ģݲֶͬνṹ"""
    preferred_paths = (
        ("recent_usage", "total_tokens"),
        ("usage", "total_tokens"),
        ("token_usage", "total_tokens"),
        ("total_tokens",),
        ("tokens_used",),
        ("used_tokens",),
        ("rate_limit", "primary_window", "used"),
        ("rate_limit", "secondary_window", "used"),
    )
    for path in preferred_paths:
        cursor: Any = usage_data
        matched = True
        for key in path:
            if isinstance(cursor, dict) and key in cursor:
                cursor = cursor[key]
            else:
                matched = False
                break
        if matched:
            val = _to_int(cursor)
            if val is not None and val >= 0:
                return val

    best_score = -1
    best_value: Optional[int] = None

    def _walk(node: Any, path: List[str]) -> None:
        nonlocal best_score, best_value
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, path + [str(k).lower()])
            return
        if isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, path + [str(idx)])
            return
        val = _to_int(node)
        if val is None or val < 0:
            return
        joined = ".".join(path)
        if "token" not in joined:
            return
        score = 1
        if "total" in joined:
            score += 2
        if "recent" in joined or "usage" in joined:
            score += 1
        if "used" in joined:
            score += 1
        if "limit" in joined or "max" in joined:
            score -= 1
        if score > best_score:
            best_score = score
            best_value = val

    _walk(usage_data, [])
    return best_value


def _probe_local_token_file(file_name: str, proxy: str, timeout: int) -> Dict[str, Any]:
    file_path = os.path.join(TOKENS_DIR, file_name)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result: Dict[str, Any] = {
        "filename": file_name,
        "file_path": file_path,
        "email": "",
        "alive": None,
        "delete": False,
        "delete_reason": "",
        "recent_token_usage": None,
        "content": None,
        "updated": False,
    }
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        token_data = raw if isinstance(raw, dict) else {}
    except Exception as e:
        result["delete"] = True
        result["delete_reason"] = f"read_failed:{e}"
        return result

    email = str(token_data.get("email") or "").strip()
    result["email"] = email or file_name

    plan_type = _extract_plan_type(token_data)
    if plan_type and str(token_data.get("plan_type") or "").strip() != plan_type:
        token_data["plan_type"] = plan_type
        result["updated"] = True

    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        result["delete"] = True
        result["delete_reason"] = "missing_access_token"
        return result

    account_id = _extract_account_id(token_data)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    if account_id:
        headers["Chatgpt-Account-Id"] = account_id

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(
            "https://chatgpt.com/backend-api/wham/usage",
            headers=headers,
            timeout=max(5, int(timeout)),
            proxies=proxies,
        )
    except Exception as e:
        token_data["alive"] = None
        token_data["last_alive_check"] = now_str
        token_data["last_alive_error"] = str(e)[:200]
        token_data["last_alive_status"] = 0
        result["content"] = token_data
        result["updated"] = True
        result["delete_reason"] = f"probe_failed:{e}"
        return result

    token_data["last_alive_check"] = now_str
    token_data["last_alive_status"] = resp.status_code

    if resp.status_code in (401, 403):
        result["alive"] = False
        result["delete"] = True
        result["delete_reason"] = f"http_{resp.status_code}"
        return result

    if resp.status_code != 200:
        token_data["alive"] = None
        token_data["last_alive_error"] = f"http_{resp.status_code}"
        result["content"] = token_data
        result["updated"] = True
        result["delete_reason"] = f"http_{resp.status_code}"
        return result

    try:
        usage_data = resp.json()
    except Exception:
        usage_data = {}

    used_percent = _to_float(
        ((usage_data.get("rate_limit") or {}).get("primary_window") or {}).get("used_percent")
    )
    remaining_percent: Optional[float] = None
    if used_percent is not None:
        remaining_percent = max(0.0, min(100.0, 100.0 - used_percent))

    recent_token_usage = _extract_recent_token_usage(usage_data)

    token_data["alive"] = True
    token_data["last_alive_error"] = ""
    if used_percent is not None:
        token_data["quota_used_percent"] = round(used_percent, 2)
    if remaining_percent is not None:
        token_data["quota_remaining_percent"] = round(remaining_percent, 2)
    if recent_token_usage is not None:
        token_data["recent_token_usage"] = recent_token_usage

    result["alive"] = True
    result["recent_token_usage"] = recent_token_usage
    result["content"] = token_data
    result["updated"] = True
    return result


def _maintain_local_tokens_sync() -> Dict[str, Any]:
    files = [f for f in os.listdir(TOKENS_DIR) if f.endswith(".json")] if os.path.isdir(TOKENS_DIR) else []
    if not files:
        return {
            "checked": 0,
            "invalid_count": 0,
            "deleted_ok": 0,
            "deleted_fail": 0,
            "remaining": 0,
            "recent_total_tokens": 0,
        }

    proxy = str(_sync_config.get("proxy", "") or "").strip()
    timeout = max(5, min(int(_sync_config.get("local_probe_timeout_seconds", 12) or 12), 60))
    workers = max(1, min(10, len(files)))

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_probe_local_token_file, file_name, proxy, timeout): file_name
            for file_name in files
        }
        for fut in as_completed(future_map):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append(
                    {
                        "filename": future_map[fut],
                        "file_path": os.path.join(TOKENS_DIR, future_map[fut]),
                        "alive": None,
                        "delete": False,
                        "delete_reason": f"future_error:{e}",
                        "content": None,
                        "updated": False,
                    }
                )

    deleted_ok = 0
    deleted_fail = 0
    invalid_count = 0
    remaining = 0
    recent_total_tokens = 0

    for item in results:
        file_path = str(item.get("file_path") or "")
        should_delete = bool(item.get("delete"))
        if should_delete:
            invalid_count += 1
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                deleted_ok += 1
            except Exception:
                deleted_fail += 1
            continue

        content = item.get("content")
        if isinstance(content, dict) and item.get("updated") and file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(content, f, ensure_ascii=False)
            except Exception:
                pass

        remaining += 1
        usage_val = _to_int((content or {}).get("recent_token_usage") if isinstance(content, dict) else item.get("recent_token_usage"))
        if usage_val is not None and usage_val > 0:
            recent_total_tokens += usage_val

    return {
        "checked": len(results),
        "invalid_count": invalid_count,
        "deleted_ok": deleted_ok,
        "deleted_fail": deleted_fail,
        "remaining": remaining,
        "recent_total_tokens": recent_total_tokens,
    }


def _sync_remove_local_tokens_by_cpa_names(names: List[str]) -> Dict[str, Any]:
    """ CPA ɾ auth-file ƣͬɾ token ļ"""
    if not names or not os.path.isdir(TOKENS_DIR):
        return {"local_deleted_ok": 0, "local_deleted_fail": 0, "local_deleted_files": []}

    local_deleted_ok = 0
    local_deleted_fail = 0
    local_deleted_files: List[str] = []

    normalized_names: List[str] = []
    seen_names: set[str] = set()
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        safe_name = os.path.basename(name).strip()
        if not safe_name or safe_name in (".", "..") or ".." in safe_name:
            continue
        if safe_name in seen_names:
            continue
        seen_names.add(safe_name)
        normalized_names.append(safe_name)

    if not normalized_names:
        return {"local_deleted_ok": 0, "local_deleted_fail": 0, "local_deleted_files": []}

    local_files = [f for f in os.listdir(TOKENS_DIR) if f.endswith(".json")]
    email_to_file: Dict[str, str] = {}
    for local_name in local_files:
        path = os.path.join(TOKENS_DIR, local_name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                email = str(data.get("email") or "").strip().lower()
                if email:
                    email_to_file[email] = local_name
        except Exception:
            continue

    for name in normalized_names:
        candidate = name
        if not candidate.endswith(".json"):
            candidate_json = f"{candidate}.json"
            candidate = candidate_json if candidate_json in local_files else candidate
        target_file = candidate if candidate in local_files else ""
        if not target_file:
            #  CPA ƿַ
            target_file = email_to_file.get(candidate.lower(), "")
        if not target_file:
            continue
        target_path = os.path.join(TOKENS_DIR, target_file)
        try:
            if os.path.isfile(target_path):
                os.remove(target_path)
                local_deleted_ok += 1
                local_deleted_files.append(target_file)
                if target_file in local_files:
                    local_files.remove(target_file)
            else:
                local_deleted_fail += 1
        except Exception:
            local_deleted_fail += 1

    return {
        "local_deleted_ok": local_deleted_ok,
        "local_deleted_fail": local_deleted_fail,
        "local_deleted_files": local_deleted_files,
    }


def _build_account_payload(email: str, token_data: Dict[str, Any]) -> Dict[str, Any]:
    """ο chatgpt_register.py  /api/v1/admin/accounts  payload"""
    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    id_token      = token_data.get("id_token", "")

    at_payload = _decode_jwt_payload(access_token) if access_token else {}
    at_auth    = at_payload.get("https://api.openai.com/auth") or {}
    chatgpt_account_id = at_auth.get("chatgpt_account_id", "") or token_data.get("account_id", "")
    chatgpt_user_id    = at_auth.get("chatgpt_user_id", "")
    exp_timestamp      = at_payload.get("exp", 0)
    expires_at = exp_timestamp if isinstance(exp_timestamp, int) and exp_timestamp > 0 else int(time.time()) + 863999

    it_payload = _decode_jwt_payload(id_token) if id_token else {}
    it_auth    = it_payload.get("https://api.openai.com/auth") or {}
    organization_id = it_auth.get("organization_id", "")
    if not organization_id:
        orgs = it_auth.get("organizations") or []
        if orgs:
            organization_id = (orgs[0] or {}).get("id", "")

    return {
        "name": email,
        "notes": "",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 863999,
            "expires_at": expires_at,
            "chatgpt_account_id": chatgpt_account_id,
            "chatgpt_user_id": chatgpt_user_id,
            "organization_id": organization_id,
        },
        "extra": {"email": email},
        "proxy_id": None,
        "concurrency": 10,
        "priority": 1,
        "rate_multiplier": 1,
        "group_ids": [2, 4],
        "expires_at": None,
        "auto_pause_on_expired": True,
    }


def _push_account_api(base_url: str, bearer: str, email: str, token_data: Dict[str, Any]) -> Dict[str, Any]:
    """ /api/v1/admin/accounts ύ˺Ϣ"""
    from curl_cffi import requests as cffi_req
    url = base_url.rstrip("/") + "/api/v1/admin/accounts"
    payload = _build_account_payload(email, token_data)
    try:
        resp = cffi_req.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Referer": base_url.rstrip("/") + "/admin/accounts",
            },
            impersonate="chrome",
            timeout=20,
        )
        return {"ok": resp.status_code in (200, 201), "status": resp.status_code, "body": resp.text[:300]}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


@app.post("/api/sync-batch")
async def api_sync_batch(req: BatchSyncRequest) -> Dict[str, Any]:
    """ͨ HTTP API  Token  Sub2Api ƽ̨"""
    cfg = _sync_config
    base_url = cfg.get("base_url", "").strip()
    bearer   = cfg.get("bearer_token", "").strip()

    if not base_url:
        raise HTTPException(status_code=400, detail=" Sub2Api ƽַ̨")
    if not bearer:
        raise HTTPException(status_code=400, detail="Bearer Token Ϊգ±Զ¼ȡ")

    fnames = req.filenames or []
    if not fnames:
        fnames = [f for f in os.listdir(TOKENS_DIR) if f.endswith(".json")]

    results = []
    for fname in fnames:
        if "/" in fname or "\\" in fname or ".." in fname:
            continue
        fpath = os.path.join(TOKENS_DIR, fname)
        if not os.path.isfile(fpath):
            results.append({"file": fname, "ok": False, "error": "ļ"})
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                token_data = json.load(f)
            email = token_data.get("email", fname)
            if _is_sub2api_uploaded(token_data):
                results.append({"file": fname, "email": email, "ok": True, "skipped": True})
                continue
            result = _push_account_api(base_url, bearer, email, token_data)
            results.append({"file": fname, "email": email, **result})
            if result["ok"]:
                _mark_token_uploaded_platform(fpath, "sub2api")
                _state.broadcast({
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "level": "success",
                    "message": f"[API] {email}: ɹ",
                    "step": "sync",
                })
            else:
                _state.broadcast({
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "level": "error",
                    "message": f"[API] {email}: ʧ({result['status']}) {result['body'][:100]}",
                    "step": "sync",
                })
        except Exception as e:
            results.append({"file": fname, "ok": False, "error": str(e)})

    ok_count   = sum(1 for r in results if r.get("ok") and not r.get("skipped"))
    skip_count = sum(1 for r in results if r.get("skipped"))
    fail_count = sum(1 for r in results if not r.get("ok"))
    return {"total": len(results), "ok": ok_count, "skipped": skip_count, "fail": fail_count, "results": results}


# ==========================================
# Pool / Mail  & ά API
# ==========================================


class PoolConfigRequest(BaseModel):
    cpa_base_url: str = ""
    cpa_token: str = ""
    min_candidates: int = 800
    used_percent_threshold: int = 95
    auto_maintain: bool = False
    maintain_interval_minutes: int = 30


class MailConfigRequest(BaseModel):
    mail_provider: str = "mailtm"
    mail_config: Dict[str, str] = {}
    mail_providers: List[str] = []
    mail_provider_configs: Dict[str, Dict[str, str]] = {}
    mail_strategy: str = "round_robin"


@app.get("/api/pool/config")
async def api_get_pool_config() -> Dict[str, Any]:
    cfg = _sync_config
    token = str(cfg.get("cpa_token", ""))
    return {
        "cpa_base_url": cfg.get("cpa_base_url", ""),
        "cpa_token_preview": (token[:12] + "...") if len(token) > 12 else token,
        "min_candidates": cfg.get("min_candidates", 800),
        "used_percent_threshold": cfg.get("used_percent_threshold", 95),
        "auto_maintain": cfg.get("auto_maintain", False),
        "maintain_interval_minutes": cfg.get("maintain_interval_minutes", 30),
    }


@app.post("/api/pool/config")
async def api_set_pool_config(req: PoolConfigRequest) -> Dict[str, Any]:
    global _sync_config
    _sync_config["cpa_base_url"] = req.cpa_base_url.strip()
    if req.cpa_token.strip():
        _sync_config["cpa_token"] = req.cpa_token.strip()
    _sync_config["min_candidates"] = req.min_candidates
    _sync_config["used_percent_threshold"] = req.used_percent_threshold
    _sync_config["auto_maintain"] = req.auto_maintain
    _sync_config["maintain_interval_minutes"] = max(5, req.maintain_interval_minutes)
    _save_sync_config(_sync_config)

    # ͣԶά
    if req.auto_maintain:
        _start_auto_maintain()
    else:
        _stop_auto_maintain()

    return {"status": "saved"}


@app.get("/api/pool/status")
async def api_pool_status() -> Dict[str, Any]:
    pm = _get_pool_maintainer()
    if not pm:
        return {"configured": False, "error": "CPA δ"}
    status = await run_in_threadpool(pm.get_pool_status)
    status["configured"] = True
    return status


@app.post("/api/pool/check")
async def api_pool_check() -> Dict[str, Any]:
    pm = _get_pool_maintainer()
    if not pm:
        raise HTTPException(status_code=400, detail="CPA δ")
    result = await run_in_threadpool(pm.test_connection)
    return result


@app.post("/api/pool/maintain")
async def api_pool_maintain() -> Dict[str, Any]:
    pm = _get_pool_maintainer()
    if not pm:
        raise HTTPException(status_code=400, detail="CPA δ")
    if not _pool_maintain_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="άִ")
    try:
        result = await run_in_threadpool(pm.probe_and_clean_sync)
        sync_result = _sync_remove_local_tokens_by_cpa_names(result.get("deleted_names") or [])
        result.update(sync_result)
        _state.broadcast({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": "info",
            "message": (
                f"[POOL] ά: Ч {result.get('invalid_count', 0)}, "
                f"ɾ {result.get('deleted_ok', 0)}, "
                f"ͬɾ {result.get('local_deleted_ok', 0)}"
            ),
            "step": "pool_maintain",
        })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _pool_maintain_lock.release()


@app.post("/api/pool/auto")
async def api_pool_auto(enable: bool = True) -> Dict[str, Any]:
    global _sync_config
    _sync_config["auto_maintain"] = enable
    _save_sync_config(_sync_config)
    if enable:
        _start_auto_maintain()
    else:
        _stop_auto_maintain()
    return {"auto_maintain": enable}


@app.get("/api/mail/config")
async def api_get_mail_config() -> Dict[str, Any]:
    cfg = _sync_config
    # ݾɸʽ
    mail_cfg = dict(cfg.get("mail_config") or {})
    for secret_key in ("bearer_token", "api_key", "site_password", "admin_password", "x_custom_auth", "x_admin_auth"):
        val = str(mail_cfg.get(secret_key, ""))
        if val:
            preview_size = 12 if secret_key == "bearer_token" else 8
            mail_cfg[f"{secret_key}_preview"] = (val[:preview_size] + "...") if len(val) > preview_size else val
            mail_cfg.pop(secret_key, None)

    #  provider_configs еֶ
    raw_configs = cfg.get("mail_provider_configs") or {}
    safe_configs: Dict[str, Dict] = {}
    for pname, pcfg in raw_configs.items():
        sc = dict(pcfg)
        for secret_key in ("bearer_token", "api_key", "site_password", "admin_password", "x_custom_auth", "x_admin_auth"):
            val = str(sc.get(secret_key, ""))
            if val:
                sc[f"{secret_key}_preview"] = (val[:8] + "...") if len(val) > 8 else val
                sc.pop(secret_key, None)
        safe_configs[pname] = sc

    return {
        "mail_provider": cfg.get("mail_provider", "mailtm"),
        "mail_config": mail_cfg,
        "mail_providers": cfg.get("mail_providers", []),
        "mail_provider_configs": safe_configs,
        "mail_strategy": cfg.get("mail_strategy", "round_robin"),
    }


@app.post("/api/mail/config")
async def api_set_mail_config(req: MailConfigRequest) -> Dict[str, Any]:
    global _sync_config
    # ݾɸʽ
    _sync_config["mail_provider"] = req.mail_provider.strip() or "mailtm"
    existing = _sync_config.get("mail_config") or {}
    for k, v in req.mail_config.items():
        if v.strip():
            existing[k] = v.strip()
    _sync_config["mail_config"] = existing

    # ¶ṩ̸ʽ
    if req.mail_providers:
        _sync_config["mail_providers"] = req.mail_providers
    _sync_config["mail_strategy"] = req.mail_strategy or "round_robin"

    # ϲ provider_configs·ǿֵԿ
    existing_configs = _sync_config.get("mail_provider_configs") or {}
    for pname, pcfg in req.mail_provider_configs.items():
        if pname not in existing_configs:
            existing_configs[pname] = {}
        for k, v in pcfg.items():
            if v.strip():
                existing_configs[pname][k] = v.strip()
    _sync_config["mail_provider_configs"] = existing_configs

    _save_sync_config(_sync_config)
    return {"status": "saved"}


@app.post("/api/mail/test")
async def api_mail_test() -> Dict[str, Any]:
    try:
        router = MultiMailRouter(_sync_config)
        results = []
        for pname, provider in router.providers():
            ok, msg = await run_in_threadpool(provider.test_connection, _state.current_proxy or "")
            results.append({"provider": pname, "ok": ok, "message": msg})
        all_ok = all(r["ok"] for r in results)
        return {"ok": all_ok, "results": results, "message": "ȫͨ" if all_ok else "ʧ"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _try_auto_register() -> None:
    """ά״̬ԶעᲹ"""
    ts = datetime.now().strftime("%H:%M:%S")
    if not _sync_config.get("auto_register"):
        _state.broadcast({
            "ts": ts, "level": "info",
            "message": "[AUTO] Զעδ빴ѡزԶע᡹",
            "step": "auto_register",
        })
        return
    proxy = _sync_config.get("proxy", "").strip()
    proxy_pool_enabled = bool(_sync_config.get("proxy_pool_enabled", False))
    if not proxy and not proxy_pool_enabled:
        _state.broadcast({
            "ts": ts, "level": "warn",
            "message": "[AUTO] Զע᣺δù̶Ҵδã",
            "step": "auto_register",
        })
        return
    if _state.status != "idle":
        _state.broadcast({
            "ts": ts, "level": "info",
            "message": f"[AUTO] Զע᣺ǰ״̬ {_state.status}",
            "step": "auto_register",
        })
        return
    upload_mode = str(_sync_config.get("upload_mode", "snapshot") or "snapshot").strip().lower()
    if upload_mode not in ("snapshot", "decoupled"):
        upload_mode = "snapshot"
    gap = 0
    cpa_gap = 0
    sub2api_gap = 0
    api_error = False
    pm = _get_pool_maintainer()
    if pm:
        try:
            cpa_gap = pm.calculate_gap()
        except Exception as e:
            api_error = True
            _state.broadcast({
                "ts": ts, "level": "warn",
                "message": f"[AUTO] CPA ״̬ѯʧܣԺ: {e}",
                "step": "auto_register",
            })
    sm = _get_sub2api_maintainer()
    if sm and _sync_config.get("auto_sync", "true") == "true":
        try:
            sub2api_gap = sm.calculate_gap()
        except Exception as e:
            api_error = True
            _state.broadcast({
                "ts": ts, "level": "warn",
                "message": f"[AUTO] Sub2Api ״̬ѯʧܣԺ: {e}",
                "step": "auto_register",
            })
    elif sm:
        _state.broadcast({
            "ts": ts, "level": "info",
            "message": "[AUTO] Sub2Api ԶͬδԶŽ CPA ȱִ",
            "step": "auto_register",
        })
    gap = (cpa_gap + sub2api_gap) if upload_mode == "snapshot" else max(cpa_gap, sub2api_gap)
    if api_error and gap <= 0:
        return
    if gap <= 0:
        _state.broadcast({
            "ts": ts, "level": "info",
            "message": "[AUTO] ѳ㣬貹ע",
            "step": "auto_register",
        })
        return
    multithread = _sync_config.get("multithread", False)
    thread_count = int(_sync_config.get("thread_count", 3))
    try:
        _state.start_task(
            proxy,
            multithread,
            thread_count,
            target_count=gap,
            cpa_target_count=cpa_gap if pm else 0,
            sub2api_target_count=sub2api_gap if sm and _sync_config.get("auto_sync", "true") == "true" else 0,
        )
        _state.broadcast({
            "ts": ts, "level": "success",
            "message": (
                f"[AUTO] Զעܲ {gap}CPA ȱ {cpa_gap} / Sub2Api ȱ {sub2api_gap} / "
                f" {upload_mode}"
            ),
            "step": "auto_register",
        })
    except RuntimeError as e:
        _state.broadcast({
            "ts": ts, "level": "warn",
            "message": f"[AUTO] Զעʧܣ{e}",
            "step": "auto_register",
        })


def _count_local_tokens() -> int:
    if not os.path.isdir(TOKENS_DIR):
        return 0
    return len([f for f in os.listdir(TOKENS_DIR) if f.endswith(".json")])


def _try_local_auto_register(current_count: Optional[int] = None) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    expected = max(0, int(_sync_config.get("desired_token_count", 0) or 0))
    if expected <= 0:
        return
    if not _sync_config.get("auto_register"):
        return

    if current_count is None:
        current_count = _count_local_tokens()
    gap = expected - int(current_count)
    if gap <= 0:
        return

    proxy = str(_sync_config.get("proxy", "") or "").strip()
    proxy_pool_enabled = bool(_sync_config.get("proxy_pool_enabled", False))
    if not proxy and not proxy_pool_enabled:
        _state.broadcast({
            "ts": ts,
            "level": "warn",
            "message": "[LOCAL] Զţδù̶Ҵδ",
            "step": "local_auto",
        })
        return
    if _state.status != "idle":
        _state.broadcast({
            "ts": ts,
            "level": "info",
            "message": f"[LOCAL] Զţǰ״̬ {_state.status}",
            "step": "local_auto",
        })
        return

    multithread = bool(_sync_config.get("multithread", False))
    thread_count = int(_sync_config.get("thread_count", 3) or 3)
    try:
        _state.start_task(proxy, multithread, thread_count, target_count=gap)
        _state.broadcast({
            "ts": ts,
            "level": "success",
            "message": f"[LOCAL] Ч˺ {current_count}/{expected}Զ {gap} ",
            "step": "local_auto",
        })
    except RuntimeError as e:
        _state.broadcast({
            "ts": ts,
            "level": "warn",
            "message": f"[LOCAL] Զʧܣ{e}",
            "step": "local_auto",
        })


def _start_local_auto_maintain() -> None:
    global _local_auto_maintain_thread
    if _local_auto_maintain_thread and _local_auto_maintain_thread.is_alive():
        return
    _local_auto_maintain_stop.clear()
    interval = max(5, int(_sync_config.get("local_maintain_interval_minutes", 30))) * 60

    def _loop() -> None:
        while not _local_auto_maintain_stop.is_set():
            if not _local_maintain_lock.acquire(blocking=False):
                _state.broadcast({
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "level": "warn",
                    "message": "[LOCAL] Զִ",
                    "step": "local_auto",
                })
            else:
                try:
                    result = _maintain_local_tokens_sync()
                    _state.broadcast({
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "level": "info",
                        "message": (
                            f"[LOCAL] Զ:  {result.get('checked', 0)}, "
                            f"Ч {result.get('invalid_count', 0)}, "
                            f"ɾ {result.get('deleted_ok', 0)}, "
                            f" {result.get('remaining', 0)}"
                        ),
                        "step": "local_auto",
                    })
                    _try_local_auto_register(result.get("remaining", 0))
                except Exception as e:
                    _state.broadcast({
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "level": "error",
                        "message": f"[LOCAL] Զ쳣: {e}",
                        "step": "local_auto",
                    })
                finally:
                    _local_maintain_lock.release()
            _local_auto_maintain_stop.wait(interval)

    _local_auto_maintain_thread = threading.Thread(target=_loop, daemon=True)
    _local_auto_maintain_thread.start()


def _stop_local_auto_maintain() -> None:
    global _local_auto_maintain_thread
    _local_auto_maintain_stop.set()
    t = _local_auto_maintain_thread
    if t and t.is_alive():
        t.join(timeout=1.5)
    _local_auto_maintain_thread = None


def _start_auto_maintain() -> None:
    global _auto_maintain_thread
    if _auto_maintain_thread and _auto_maintain_thread.is_alive():
        return
    _auto_maintain_stop.clear()
    interval = max(5, int(_sync_config.get("maintain_interval_minutes", 30))) * 60

    def _loop():
        while not _auto_maintain_stop.is_set():
            pm = _get_pool_maintainer()
            if pm:
                if not _pool_maintain_lock.acquire(blocking=False):
                    _state.broadcast({
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "level": "warn",
                        "message": "[POOL] Զάάִ",
                        "step": "pool_auto",
                    })
                else:
                    try:
                        result = pm.probe_and_clean_sync()
                        sync_result = _sync_remove_local_tokens_by_cpa_names(result.get("deleted_names") or [])
                        result.update(sync_result)
                        _state.broadcast({
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "level": "info",
                            "message": (
                                f"[POOL] Զά: Ч {result.get('invalid_count', 0)}, "
                                f"ɾ {result.get('deleted_ok', 0)}, "
                                f"ͬɾ {result.get('local_deleted_ok', 0)}"
                            ),
                            "step": "pool_auto",
                        })
                    except Exception as e:
                        _state.broadcast({
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "level": "error",
                            "message": f"[POOL] Զά쳣: {e}",
                            "step": "pool_auto",
                        })
                    finally:
                        _pool_maintain_lock.release()
                    _try_auto_register()
            _auto_maintain_stop.wait(interval)

    _auto_maintain_thread = threading.Thread(target=_loop, daemon=True)
    _auto_maintain_thread.start()


def _stop_auto_maintain() -> None:
    _auto_maintain_stop.set()


# ==========================================
# Sub2Api ά API & Զά
# ==========================================

_sub2api_auto_maintain_thread: Optional[threading.Thread] = None
_sub2api_auto_maintain_stop = threading.Event()
_sub2api_maintain_lock = threading.Lock()


@app.get("/api/sub2api/pool/status")
async def api_sub2api_pool_status() -> Dict[str, Any]:
    sm = _get_sub2api_maintainer()
    if not sm:
        return {"configured": False, "error": "Sub2Api δ"}
    status = await run_in_threadpool(sm.get_pool_status)
    status["configured"] = True
    return status


@app.post("/api/sub2api/pool/check")
async def api_sub2api_pool_check() -> Dict[str, Any]:
    sm = _get_sub2api_maintainer()
    if not sm:
        raise HTTPException(status_code=400, detail="Sub2Api δ")
    result = await run_in_threadpool(sm.test_connection)
    return result


@app.post("/api/sub2api/pool/maintain")
async def api_sub2api_pool_maintain() -> Dict[str, Any]:
    sm = _get_sub2api_maintainer()
    if not sm:
        raise HTTPException(status_code=400, detail="Sub2Api δ")
    if not _sub2api_maintain_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Sub2Api άִ")
    try:
        result = await run_in_threadpool(sm.probe_and_clean_sync)
        _state.broadcast({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": "info",
            "message": (
                f"[Sub2Api] ά: 쳣 {result.get('error_count', 0)}, "
                f"ˢ {result.get('refreshed', 0)}, "
                f"ɾ {result.get('deleted_ok', 0)}"
            ),
            "step": "sub2api_maintain",
        })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _sub2api_maintain_lock.release()


def _start_sub2api_auto_maintain() -> None:
    global _sub2api_auto_maintain_thread
    if _sub2api_auto_maintain_thread and _sub2api_auto_maintain_thread.is_alive():
        return
    _sub2api_auto_maintain_stop.clear()
    interval = max(5, int(_sync_config.get("sub2api_maintain_interval_minutes", 30))) * 60

    def _loop():
        while not _sub2api_auto_maintain_stop.is_set():
            sm = _get_sub2api_maintainer()
            if sm:
                if not _sub2api_maintain_lock.acquire(blocking=False):
                    _state.broadcast({
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "level": "warn",
                        "message": "[Sub2Api] Զάάִ",
                        "step": "sub2api_auto",
                    })
                else:
                    try:
                        result = sm.probe_and_clean_sync()
                        _state.broadcast({
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "level": "info",
                            "message": (
                                f"[Sub2Api] Զά: 쳣 {result.get('error_count', 0)}, "
                                f"ˢ {result.get('refreshed', 0)}, "
                                f"ɾ {result.get('deleted_ok', 0)}"
                            ),
                            "step": "sub2api_auto",
                        })
                    except Exception as e:
                        _state.broadcast({
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "level": "error",
                            "message": f"[Sub2Api] Զά쳣: {e}",
                            "step": "sub2api_auto",
                        })
                    finally:
                        _sub2api_maintain_lock.release()
                    _try_auto_register()
            _sub2api_auto_maintain_stop.wait(interval)

    _sub2api_auto_maintain_thread = threading.Thread(target=_loop, daemon=True)
    _sub2api_auto_maintain_thread.start()


def _stop_sub2api_auto_maintain() -> None:
    global _sub2api_auto_maintain_thread
    _sub2api_auto_maintain_stop.set()
    t = _sub2api_auto_maintain_thread
    if t and t.is_alive():
        t.join(timeout=1.5)
    _sub2api_auto_maintain_thread = None


# ʱָԶά
if _sync_config.get("auto_maintain"):
    _start_auto_maintain()
if _sync_config.get("sub2api_auto_maintain"):
    _start_sub2api_auto_maintain()
if _sync_config.get("local_auto_maintain"):
    _start_local_auto_maintain()


# ؾ̬ļ
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ==========================================
# ڣֱУ
# ==========================================

if __name__ == "__main__":
    from .__main__ import main
    main()
