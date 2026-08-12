"""Single-user password gate for the deployed app — this is personal
financial data (balances, transactions, RFC), so every route except the
login/status endpoints must require a valid session before this app is
reachable from the public internet.

Deliberately NOT a full user-account system: there's exactly one user.
A signed session cookie (Starlette's SessionMiddleware, itsdangerous under
the hood) beats a hand-rolled token store — no session table, no expiry
sweep job, and the cookie is tamper-evident (signed, not just opaque) so a
client can't forge `authenticated: True` without knowing SESSION_SECRET.

Two secrets, both required in production and validated at startup so a
misconfigured deploy fails loud instead of silently serving unauthenticated:
  APP_PASSWORD    — the login password (compared with a constant-time check).
  SESSION_SECRET  — signs the session cookie; rotating it logs everyone out.
Neither has a default here — see .env.example for local dev values.
"""

from __future__ import annotations

import os
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

# Paths reachable without a session. Everything else 401s — fail-closed by
# default via a global middleware (see install_auth) rather than an
# opt-in `Depends(...)` on each route, which is one missed decorator away
# from silently exposing an endpoint. /docs and /openapi.json are
# deliberately NOT here: the one real user still gets them once logged in
# (a session cookie is enough, this only blocks anonymous access), and
# leaving the API schema world-readable on a deployment that exists
# specifically to gate this data has no upside.
_PUBLIC_PATHS = {"/auth/login", "/auth/status"}


def install_auth(app: FastAPI) -> None:
    session_secret = os.environ.get("SESSION_SECRET")
    if not session_secret:
        raise RuntimeError(
            "SESSION_SECRET is not set — required so session cookies survive a "
            "restart/redeploy and can't be forged. Generate one with "
            "`python3 -c 'import secrets; print(secrets.token_hex(32))'` and set it "
            "via .env locally or `flyctl secrets set` in production."
        )
    # Order is load-bearing: Starlette's add_middleware() INSERTS AT THE
    # FRONT of the middleware list, and the front of that list ends up
    # OUTERMOST (runs first on the way in) — so whichever of these two is
    # registered SECOND runs FIRST. require_session must run AFTER
    # SessionMiddleware has populated request.session, so it has to be
    # registered first here, letting SessionMiddleware's later add_middleware
    # call take the outer/first-to-run slot. Registered in the wrong order
    # once already — every request 500'd with "SessionMiddleware must be
    # installed to access request.session" even though it plainly was.
    @app.middleware("http")
    async def require_session(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        if not request.session.get("authenticated"):
            # A plain `return`, not `raise HTTPException` — this middleware
            # sits outside Starlette's ExceptionMiddleware in the stack, so
            # an HTTPException raised here would be caught by the outer
            # ServerErrorMiddleware instead and surfaced as a generic 500,
            # not the intended 401 (a real, documented FastAPI gotcha).
            return JSONResponse(status_code=401, content={"detail": "No autenticado"})
        return await call_next(request)

    https_only = os.environ.get("APP_ENV") == "production"
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=https_only,
    )

    @app.post("/auth/login")
    def login(body: dict, request: Request) -> dict:
        app_password = os.environ.get("APP_PASSWORD")
        if not app_password:
            raise RuntimeError(
                "APP_PASSWORD is not set — required to gate this app before it's "
                "reachable from the public internet. Set it via .env locally or "
                "`flyctl secrets set` in production."
            )
        submitted = body.get("password", "")
        if not secrets.compare_digest(submitted, app_password):
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")
        request.session["authenticated"] = True
        return {"ok": True}

    @app.post("/auth/logout")
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"ok": True}

    @app.get("/auth/status")
    def auth_status(request: Request) -> dict:
        return {"authenticated": bool(request.session.get("authenticated"))}
