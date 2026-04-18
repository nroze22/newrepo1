"""Postiz integration: publish clips to X, LinkedIn, YouTube, TikTok, Instagram,
Threads, Bluesky, Mastodon, Pinterest, Facebook, Reddit, Discord via the
user's self-hosted or cloud Postiz instance.

Postiz is a mature open-source social media scheduler; by handing off to it we
inherit OAuth flows, rate-limit handling, retries, per-post analytics, and a
calendar UI — and we get new platforms free as Postiz adds them.

API shape (https://docs.postiz.com/public-api):
    Authorization header: the raw API key (no "Bearer " prefix).
    Base URL: <instance>/public/v1
    Endpoints used here:
        GET  /integrations                        — list connected channels
        POST /upload            (multipart)       — upload media, returns {id, path}
        POST /upload-from-url                     — upload by URL
        POST /posts                               — create posts (schedule | draft | now)
        DELETE /posts/:id                         — cancel a scheduled post

Rate limit: 30 requests/hour per API key. We batch all integrations into a
single /posts call (allowed by the API) so one clip → one request regardless
of how many platforms it targets.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request


class PostizError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class PostizConfig:
    base_url: str = ""         # e.g. https://api.postiz.com  (no trailing /public/v1)
    api_key: str = ""

    @property
    def api_root(self) -> str:
        base = self.base_url.rstrip("/")
        if not base:
            return ""
        if base.endswith("/public/v1"):
            return base
        return base + "/public/v1"

    def valid(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass
class PostizIntegration:
    """One connected channel — Postiz's 'integration' is the UI's 'channel'."""

    id: str
    name: str = ""
    provider: str = ""        # e.g. "x", "linkedin", "tiktok" — used as settings.__type
    picture: str = ""
    disabled: bool = False

    @classmethod
    def from_api(cls, raw: dict) -> "PostizIntegration":
        return cls(
            id=str(raw.get("id") or raw.get("identifier") or ""),
            name=str(raw.get("name") or raw.get("identifier") or ""),
            provider=str(raw.get("providerIdentifier") or raw.get("provider") or "").lower(),
            picture=str(raw.get("picture") or ""),
            disabled=bool(raw.get("disabled", False)),
        )


@dataclass
class ScheduleSpec:
    integration_id: str
    provider: str            # drives settings.__type
    content: str             # caption for this platform


@dataclass
class SchedulePost:
    integration_id: str
    provider: str
    postiz_post_id: str | None
    status: str              # scheduled | draft | published | error
    scheduled_at: str | None
    error: str = ""


# ----------------------------------------------------------------------
# HTTP helpers — stdlib only, no hard-dep on httpx/requests.
# ----------------------------------------------------------------------


def _headers(cfg: PostizConfig, *, content_type: str | None = None) -> dict[str, str]:
    h = {"Authorization": cfg.api_key}
    if content_type:
        h["Content-Type"] = content_type
    return h


def _request_json(
    cfg: PostizConfig, method: str, path: str, *,
    body: dict | None = None, params: dict | None = None,
    timeout: float = 30.0,
) -> dict | list:
    if not cfg.valid():
        raise PostizError("Postiz is not configured — set URL and API key in Integrations.")
    url = cfg.api_root + path
    if params:
        url += "?" + parse.urlencode({k: v for k, v in params.items() if v is not None})

    data: bytes | None = None
    headers = _headers(cfg)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise PostizError(
            f"Postiz {method} {path} failed: HTTP {e.code}",
            status=e.code, body=body_text,
        ) from e
    except error.URLError as e:
        raise PostizError(f"Postiz {method} {path} network error: {e.reason}") from e

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _multipart(cfg: PostizConfig, path: str, file_path: Path, *, field_name: str = "file",
               timeout: float = 300.0) -> dict:
    """Upload a file via multipart/form-data using stdlib."""
    if not cfg.valid():
        raise PostizError("Postiz is not configured.")
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    boundary = "----postizClipperBoundary7f3a9"
    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "application/octet-stream"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("ascii"))
    body.extend(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode("ascii")
    )
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))

    url = cfg.api_root + path
    req = request.Request(
        url, data=bytes(body), method="POST",
        headers={
            "Authorization": cfg.api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise PostizError(
            f"Postiz upload failed: HTTP {e.code}", status=e.code, body=body_text,
        ) from e
    except error.URLError as e:
        raise PostizError(f"Postiz upload network error: {e.reason}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------


class PostizClient:
    def __init__(self, config: PostizConfig):
        self.config = config

    def ping(self) -> bool:
        """Cheap credentials check: list integrations (counts against rate limit)."""
        try:
            self.list_integrations()
            return True
        except PostizError:
            return False

    def list_integrations(self) -> list[PostizIntegration]:
        data = _request_json(self.config, "GET", "/integrations")
        if isinstance(data, dict):
            # Some Postiz versions wrap the list.
            data = data.get("data") or data.get("integrations") or []
        if not isinstance(data, list):
            return []
        return [PostizIntegration.from_api(x) for x in data if isinstance(x, dict)]

    def upload_media(self, path: Path) -> dict:
        """Upload a single file. Returns the upload object (at minimum {id, path})."""
        result = _multipart(self.config, "/upload", path)
        if isinstance(result, list):
            result = result[0] if result else {}
        return result

    def schedule_posts(
        self,
        *,
        specs: Iterable[ScheduleSpec],
        media_upload_id: str | None = None,
        media_path: str | None = None,
        when: datetime | None = None,
        kind: str = "schedule",    # schedule | draft | now
    ) -> dict:
        """Create one Postiz /posts request covering every target platform."""
        specs_list = list(specs)
        if not specs_list:
            raise ValueError("No platforms selected to schedule.")
        if kind not in ("schedule", "draft", "now"):
            raise ValueError(f"kind must be schedule|draft|now, got {kind!r}")
        iso = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        posts: list[dict] = []
        for s in specs_list:
            value: list[dict] = [{"content": s.content}]
            if media_upload_id:
                value[0]["image"] = [{"id": media_upload_id, "path": media_path or ""}]
            posts.append({
                "integration": {"id": s.integration_id},
                "value": value,
                "settings": {"__type": s.provider} if s.provider else {},
            })
        body = {"type": kind, "date": iso, "posts": posts}
        return _request_json(self.config, "POST", "/posts", body=body)  # type: ignore[return-value]

    def delete_post(self, post_id: str) -> dict:
        return _request_json(self.config, "DELETE", f"/posts/{parse.quote(post_id)}")  # type: ignore[return-value]

    def list_posts(
        self, *, start: datetime | None = None, end: datetime | None = None,
        customer_id: str | None = None,
    ) -> list[dict]:
        params = {
            "startDate": (start or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
            "endDate": (end or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
            "customerId": customer_id,
        }
        data = _request_json(self.config, "GET", "/posts", params=params)
        if isinstance(data, dict):
            data = data.get("data") or data.get("posts") or []
        if not isinstance(data, list):
            return []
        return data


def summarize_schedule_response(resp: dict | list) -> list[SchedulePost]:
    """Best-effort parse of Postiz's /posts response into our SchedulePost rows.

    Postiz has evolved its response shape a few times; we accept a list or
    a wrapped object and extract post ids where present.
    """
    posts: list[SchedulePost] = []
    items: list[dict] = []
    if isinstance(resp, list):
        items = [x for x in resp if isinstance(x, dict)]
    elif isinstance(resp, dict):
        inner = resp.get("posts") or resp.get("data") or resp.get("result") or []
        if isinstance(inner, list):
            items = [x for x in inner if isinstance(x, dict)]
    for item in items:
        integration = item.get("integration") or {}
        integration_id = str(
            integration.get("id") if isinstance(integration, dict) else item.get("integrationId") or ""
        )
        provider = str((integration or {}).get("providerIdentifier") or item.get("provider") or "").lower()
        posts.append(SchedulePost(
            integration_id=integration_id,
            provider=provider,
            postiz_post_id=str(item.get("id") or "") or None,
            status=str(item.get("state") or item.get("status") or "scheduled").lower(),
            scheduled_at=str(item.get("publishDate") or item.get("date") or "") or None,
        ))
    return posts
