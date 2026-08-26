"""
Optional HTTP Basic Auth gate for the whole app.

Disabled by default (empty AUTH_USERNAME). Set AUTH_USERNAME and
AUTH_PASSWORD (env vars, or backend/.env) to require a login before the
API or the dashboard will respond to anything except the container
health check.
"""
import base64
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Never gated: Render's own container HEALTHCHECK hits this without
# credentials, and it carries no data worth protecting.
EXEMPT_PATHS = {"/healthz"}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str):
        super().__init__(app)
        self.username = username
        self.password = password

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                supplied_user, _, supplied_pass = decoded.partition(":")
            except Exception:
                supplied_user, supplied_pass = "", ""

            # compare_digest guards against timing attacks; both comparisons
            # always run so a correct username doesn't leak via response time.
            user_ok = secrets.compare_digest(supplied_user, self.username)
            pass_ok = secrets.compare_digest(supplied_pass, self.password)
            if user_ok and pass_ok:
                return await call_next(request)

        return Response(
            status_code=401,
            content="Authentication required.",
            headers={"WWW-Authenticate": 'Basic realm="Deep Microcap Screener"'},
        )
