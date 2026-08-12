from __future__ import annotations

import hashlib
import logging
import os
import plistlib
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from deploy.macos import run_server, smoke_p0

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "macos"
SHELL_ASSETS = [
    "build-release.sh",
    "check-service.sh",
    "install-release.sh",
    "preflight-host.sh",
    "rollback-release.sh",
    "start-label-review.sh",
]


def test_shell_assets_are_executable_and_parse() -> None:
    for name in SHELL_ASSETS:
        path = DEPLOY_ROOT / name
        assert os.access(path, os.X_OK), f"{name} must be executable in release archives"
        completed = subprocess.run(
            ["/bin/bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_launchd_template_fixes_single_process_and_private_service_identity() -> None:
    with (DEPLOY_ROOT / "dev.mealcheck.label-review.plist.template").open("rb") as input_file:
        payload = plistlib.load(input_file)

    assert payload["Label"] == "dev.mealcheck.label-review"
    assert payload["UserName"] == "chranama-server"
    assert payload["GroupName"] == "staff"
    assert payload["ProgramArguments"] == [
        "/Users/chranama-server/treasury-takehome/current/deploy/macos/start-label-review.sh"
    ]
    assert payload["WorkingDirectory"] == "/Users/chranama-server/treasury-takehome/current"
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["ThrottleInterval"] == 30
    assert payload["Umask"] == 0o77
    assert payload["StandardOutPath"].endswith("/logs/bootstrap.log")
    assert payload["StandardErrorPath"] == payload["StandardOutPath"]


def test_runtime_logging_is_bounded_and_access_log_is_disabled(tmp_path: Path) -> None:
    config = run_server.build_log_config(tmp_path)
    handler = config["handlers"]["service"]
    assert handler["class"] == "logging.handlers.RotatingFileHandler"
    assert handler["maxBytes"] == 1_048_576
    assert handler["backupCount"] == 5
    assert config["loggers"]["uvicorn.access"]["handlers"] == []
    assert config["loggers"]["uvicorn.access"]["propagate"] is False


def test_runtime_server_is_local_single_process_without_proxy_or_access_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(target: str, **options: object) -> None:
        captured["target"] = target
        captured.update(options)

    monkeypatch.setenv("TREASURY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(run_server.uvicorn, "run", fake_run)
    run_server.main()
    logging.shutdown()

    assert captured == {
        "target": "app.main:app",
        "host": "127.0.0.1",
        "port": 8081,
        "workers": 1,
        "access_log": False,
        "proxy_headers": False,
        "timeout_graceful_shutdown": 20,
        "log_config": None,
    }
    assert (tmp_path / "label-review.log").stat().st_mode & 0o777 == 0o600


def test_smoke_harness_refuses_unacknowledged_provider_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["smoke_p0.py", "local"])
    with pytest.raises(SystemExit, match="Refusing a potentially paid request"):
        smoke_p0.main()


def test_public_deployment_assets_contain_no_private_limit_values() -> None:
    content = "\n".join(
        path.read_text(encoding="utf-8") for path in DEPLOY_ROOT.iterdir() if path.is_file()
    )
    forbidden = [
        "$15",
        "100 provider attempts",
        "10 submissions per 60 seconds",
        "TREASURY_OPENAI_API_KEY=sk-",
    ]
    for value in forbidden:
        assert value not in content


def test_start_wrapper_resolves_current_to_one_immutable_release() -> None:
    content = (DEPLOY_ROOT / "start-label-review.sh").read_text(encoding="utf-8")
    assert 'RELEASE_DIR=$(cd "$CURRENT_RELEASE" && pwd -P)' in content
    assert 'TREASURY_FRONTEND_DIST_PATH="$RELEASE_DIR/frontend/dist"' in content
    assert 'exec "$RELEASE_DIR/.venv/bin/python"' in content


def _fake_uv(path: Path) -> None:
    path.write_text(
        """#!/bin/bash
set -euo pipefail
mkdir -p .venv/bin
printf '#!/bin/bash\\nexit 0\\n' > .venv/bin/python
printf '#!/bin/bash\\nexit 0\\n' > .venv/bin/uvicorn
chmod 700 .venv/bin/python .venv/bin/uvicorn
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _release_archive(directory: Path, commit: str) -> tuple[Path, Path]:
    source = directory / f"source-{commit[0]}"
    (source / "frontend" / "dist").mkdir(parents=True)
    (source / "deploy" / "macos").mkdir(parents=True)
    (source / "RELEASE_COMMIT").write_text(f"{commit}\n", encoding="utf-8")
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (source / "frontend" / "dist" / "index.html").write_text("ready", encoding="utf-8")
    start = source / "deploy" / "macos" / "start-label-review.sh"
    start.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    start.chmod(0o700)

    archive = directory / f"release-{commit[0]}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname=".")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = Path(f"{archive}.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


def test_install_and_explicit_rollback_switch_immutable_releases(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    data_root = tmp_path / "data"
    fake_uv = tmp_path / "uv"
    _fake_uv(fake_uv)
    environment = {
        **os.environ,
        "TREASURY_DEPLOY_APP_ROOT": str(app_root),
        "TREASURY_DEPLOY_DATA_ROOT": str(data_root),
        "TREASURY_DEPLOY_UV_BIN": str(fake_uv),
    }

    first = "a" * 40
    second = "b" * 40
    for commit in (first, second):
        archive, checksum = _release_archive(tmp_path, commit)
        completed = subprocess.run(
            [str(DEPLOY_ROOT / "install-release.sh"), str(archive), str(checksum)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr
        assert (app_root / "current").resolve() == app_root / "releases" / commit

    rollback = subprocess.run(
        [
            str(DEPLOY_ROOT / "rollback-release.sh"),
            "--confirm-schema-compatible",
            first,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rollback.returncode == 0, rollback.stderr
    assert (app_root / "current").resolve() == app_root / "releases" / first
    assert (app_root / "releases" / second).is_dir()
    assert (data_root / "config").stat().st_mode & 0o777 == 0o700
