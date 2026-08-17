from __future__ import annotations

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <MCP_AUTH_TOKEN>. Mapping stays in this process."""

    def __init__(self, app, expected: str) -> None:
        super().__init__(app)
        self._expected = expected.encode("utf-8")

    async def dispatch(self, request: Request, call_next) -> Response:
        auth = request.headers.get("authorization", "")
        scheme, _, value = auth.partition(" ")
        provided = value.encode("utf-8")
        if scheme.lower() != "bearer":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            match = hmac.compare_digest(provided, self._expected)
        except (TypeError, ValueError):
            match = False
        if not match:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def require_http_token() -> str:
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise SystemExit("HTTP mode requires MCP_AUTH_TOKEN (distinct from CASPIAN_API_KEY)")
    return token


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def run_http(mcp, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("HTTP mode only binds loopback (127.0.0.1)")
    token = require_http_token()
    mcp.settings.host = host
    mcp.settings.port = port
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, expected=token)
    import uvicorn

    uvicorn.run(app, host=host, port=port)
