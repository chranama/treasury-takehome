from ipaddress import ip_address

from fastapi import Request

from app.config import Settings


def source_identity(request: Request, settings: Settings) -> str:
    """Return an ephemeral throttle signal without persisting or logging the address."""

    if settings.trust_cloudflare_client_ip:
        forwarded = request.headers.get("CF-Connecting-IP", "").strip()
        try:
            return str(ip_address(forwarded))
        except ValueError:
            pass
    if request.client is None:
        return "unknown"
    return request.client.host
