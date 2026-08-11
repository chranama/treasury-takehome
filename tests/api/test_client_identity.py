from pathlib import Path

from starlette.requests import Request

from app.api.client_identity import source_identity
from app.config import Settings


def settings(tmp_path: Path, *, trust_cloudflare: bool) -> Settings:
    return Settings(
        _env_file=None,
        database_path=tmp_path / "db.sqlite3",
        temp_dir=tmp_path / "tmp",
        frontend_dist_path=tmp_path / "dist",
        trust_cloudflare_client_ip=trust_cloudflare,
    )


def request(*, peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"cf-connecting-ip", forwarded.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/reviews",
            "headers": headers,
            "client": (peer, 12345),
        }
    )


def test_untrusted_forwarded_address_is_ignored(tmp_path: Path) -> None:
    identity = source_identity(
        request(peer="127.0.0.1", forwarded="203.0.113.8"),
        settings(tmp_path, trust_cloudflare=False),
    )

    assert identity == "127.0.0.1"


def test_trusted_cloudflare_address_is_canonicalized(tmp_path: Path) -> None:
    identity = source_identity(
        request(peer="127.0.0.1", forwarded="2001:0db8::1"),
        settings(tmp_path, trust_cloudflare=True),
    )

    assert identity == "2001:db8::1"


def test_invalid_cloudflare_address_falls_back_to_peer(tmp_path: Path) -> None:
    identity = source_identity(
        request(peer="127.0.0.1", forwarded="not-an-address"),
        settings(tmp_path, trust_cloudflare=True),
    )

    assert identity == "127.0.0.1"
