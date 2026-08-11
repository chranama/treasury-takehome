import re
from pathlib import Path

FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"
REMOTE_RUNTIME_REFERENCE = re.compile(
    r"https?://|(?:src|href)\s*=\s*[\"']//",
    re.IGNORECASE,
)


def test_frontend_source_has_no_remote_runtime_assets() -> None:
    runtime_files = [FRONTEND_ROOT / "index.html", *sorted((FRONTEND_ROOT / "src").rglob("*"))]
    text_files = [path for path in runtime_files if path.is_file()]

    assert text_files
    for path in text_files:
        assert not REMOTE_RUNTIME_REFERENCE.search(path.read_text(encoding="utf-8")), path
