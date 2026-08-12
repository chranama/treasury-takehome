import re
from pathlib import Path

FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"
REMOTE_RUNTIME_REFERENCE = re.compile(
    r"https?://|(?:src|href)\s*=\s*[\"']//",
    re.IGNORECASE,
)
CLOUDFLARE_WEB_ANALYTICS_MARKERS = (
    "static.cloudflareinsights.com",
    "data-cf-beacon",
    "/cdn-cgi/rum",
)


def frontend_runtime_text_files() -> list[Path]:
    runtime_files = [FRONTEND_ROOT / "index.html", *sorted((FRONTEND_ROOT / "src").rglob("*"))]
    return [path for path in runtime_files if path.is_file()]


def test_frontend_source_has_no_remote_runtime_assets() -> None:
    text_files = frontend_runtime_text_files()

    assert text_files
    for path in text_files:
        assert not REMOTE_RUNTIME_REFERENCE.search(path.read_text(encoding="utf-8")), path


def test_frontend_source_has_no_cloudflare_web_analytics() -> None:
    text_files = frontend_runtime_text_files()

    assert text_files
    for path in text_files:
        content = path.read_text(encoding="utf-8").lower()
        for marker in CLOUDFLARE_WEB_ANALYTICS_MARKERS:
            assert marker not in content, path
