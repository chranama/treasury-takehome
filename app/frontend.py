from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

HTML_CACHE_CONTROL = "public, max-age=0, must-revalidate, no-transform"
HTML_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "img-src 'self' blob: data:",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
    )
)


class FrontendStaticFiles(StaticFiles):
    """Serve the compiled UI without permitting edge-injected browser dependencies."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = HTML_CACHE_CONTROL
            response.headers["Content-Security-Policy"] = HTML_CONTENT_SECURITY_POLICY
        return response
