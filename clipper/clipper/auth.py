"""Single-password auth for the web UI.

Activation: set CLIPPER_PASSWORD. Without it, the middleware is a no-op so
the local-dev and CLI-only flows keep working.

The middleware:
  * lets /login, /logout, and /healthz pass through
  * checks a signed session cookie for other routes
  * issues the cookie after a valid password on POST /login
  * for API endpoints returns 401 JSON so the frontend can handle it

Cookie value = base64( hmac_sha256(secret, "v1|<ts>") ). Rotates when the
password changes. No PII, no user accounts — just a shared password gate
appropriate for a single-creator install.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware


COOKIE_NAME = "clipper_auth"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
OPEN_PATHS = {"/login", "/logout", "/healthz", "/static"}


def _secret_from(password: str) -> bytes:
    return hashlib.sha256(("clipper-v1|" + password).encode("utf-8")).digest()


def _sign(password: str) -> str:
    ts = str(int(time.time()))
    secret = _secret_from(password)
    mac = hmac.new(secret, ("v1|" + ts).encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(ts.encode() + b"." + mac).decode("ascii")


def _verify(token: str, password: str, *, ttl: int = SESSION_TTL) -> bool:
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        ts_b, mac = decoded.split(b".", 1)
        ts = int(ts_b.decode())
        if time.time() - ts > ttl:
            return False
        secret = _secret_from(password)
        expected = hmac.new(secret, ("v1|" + ts_b.decode()).encode("utf-8"), hashlib.sha256).digest()
        return hmac.compare_digest(mac, expected)
    except Exception:
        return False


LOGIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Podcast Clipper — sign in</title>
<style>
  body { margin: 0; min-height: 100vh; background: #0b0d12; color: #e8eaf0;
         font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         display: flex; align-items: center; justify-content: center; }
  form { background: #151822; border: 1px solid #2a2f3d; border-radius: 14px; padding: 28px;
         width: min(380px, 90vw); box-shadow: 0 12px 32px rgba(0,0,0,.35); }
  h1 { margin: 0 0 6px 0; font-size: 18px; }
  p { color: #8a93a6; font-size: 13px; margin: 0 0 20px 0; }
  input { background: #1d2230; border: 1px solid #2a2f3d; color: #e8eaf0; padding: 10px 12px;
          border-radius: 8px; width: 100%; font: inherit; box-sizing: border-box; }
  button { margin-top: 14px; background: #ff5b7a; border: 0; color: #1a0008; font-weight: 700;
           padding: 10px 16px; border-radius: 8px; cursor: pointer; width: 100%; font: inherit; }
  .err { color: #f87171; font-size: 13px; margin-top: 10px; min-height: 18px; }
</style></head><body>
<form method="post" action="/login">
  <h1>🎙️ Podcast Clipper</h1>
  <p>Enter the workspace password to continue.</p>
  <input type="password" name="password" placeholder="Password" autofocus>
  <button type="submit">Sign in</button>
  <div class="err">__ERR__</div>
</form>
</body></html>"""


class PasswordAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, password: str):
        super().__init__(app)
        self.password = password

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in OPEN_PATHS):
            return await call_next(request)

        # GET/POST /login handled below by app routes (we register them).
        if _verify(request.cookies.get(COOKIE_NAME, ""), self.password):
            return await call_next(request)

        # API → JSON 401; HTML → redirect to login.
        if path.startswith("/api/") or path.startswith("/media/") or path.startswith("/clips/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=303)


def install_auth(app) -> None:
    """Mount the middleware + /login + /logout if CLIPPER_PASSWORD is set."""
    password = os.environ.get("CLIPPER_PASSWORD", "").strip()
    if not password:
        return

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        return LOGIN_HTML.replace("__ERR__", "")

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        entered = (form.get("password") or "").strip()
        if entered != password:
            html = LOGIN_HTML.replace("__ERR__", "Wrong password.")
            return HTMLResponse(html, status_code=401)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            COOKIE_NAME, _sign(password),
            httponly=True, samesite="lax", max_age=SESSION_TTL,
            secure=(request.url.scheme == "https"),
        )
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # Install after the routes above are registered.
    app.add_middleware(PasswordAuthMiddleware, password=password)
