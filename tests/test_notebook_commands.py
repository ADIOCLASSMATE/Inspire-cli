from pathlib import Path
from typing import Any, Optional

import pytest
from click.testing import CliRunner

from inspire import config as config_module
from inspire.cli.commands.notebook import notebook_commands as notebook_cmd_module
from inspire.cli.context import Context, EXIT_SUCCESS
from inspire.cli.main import main as cli_main
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web import session as web_session_module


def make_test_config(tmp_path: Path, include_compute_groups: bool = False) -> config_module.Config:
    config = config_module.Config(
        username="user",
        password="pass",
        base_url="https://example.invalid",
        target_dir=str(tmp_path / "logs"),
        job_cache_path=str(tmp_path / "jobs.json"),
        log_cache_dir=str(tmp_path / "log_cache"),
        job_workspace_id="ws-11111111-1111-1111-1111-111111111111",
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
    )
    if include_compute_groups:
        test_group_id = "lcg-test000-0000-0000-0000-000000000000"
        config.compute_groups = [
            {
                "name": "H200 TestRoom",
                "id": test_group_id,
                "gpu_type": "H200",
                "location": "Test",
            }
        ]
    return config


def test_notebook_create_accepts_priority_10(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run_notebook_create(ctx: Context, **kwargs: Any) -> None:
        assert ctx is not None
        captured.update(kwargs)

    monkeypatch.setattr(notebook_cmd_module, "run_notebook_create", fake_run_notebook_create)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "create", "--priority", "10"])

    assert result.exit_code == EXIT_SUCCESS
    assert captured["priority"] == 10


def test_notebook_create_rejects_priority_11(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_run_notebook_create(ctx: Context, **kwargs: Any) -> None:
        nonlocal called
        assert ctx is not None
        assert kwargs is not None
        called = True

    monkeypatch.setattr(notebook_cmd_module, "run_notebook_create", fake_run_notebook_create)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "create", "--priority", "11"])

    assert result.exit_code != EXIT_SUCCESS
    assert "1<=x<=10" in result.output
    assert called is False


def test_notebook_create_accepts_post_start_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run_notebook_create(ctx: Context, **kwargs: Any) -> None:
        assert ctx is not None
        captured.update(kwargs)

    monkeypatch.setattr(notebook_cmd_module, "run_notebook_create", fake_run_notebook_create)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "create", "--post-start", "echo hi"])

    assert result.exit_code == EXIT_SUCCESS
    assert captured["post_start"] == "echo hi"
    assert captured["post_start_script"] is None


def test_notebook_create_rejects_post_start_and_script_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def fake_run_notebook_create(ctx: Context, **kwargs: Any) -> None:
        nonlocal called
        assert ctx is not None
        assert kwargs is not None
        called = True

    monkeypatch.setattr(notebook_cmd_module, "run_notebook_create", fake_run_notebook_create)

    script_path = tmp_path / "bootstrap.sh"
    script_path.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "notebook",
            "create",
            "--post-start",
            "echo hi",
            "--post-start-script",
            str(script_path),
        ],
    )

    assert result.exit_code != EXIT_SUCCESS
    assert "Use either --post-start or --post-start-script" in result.output
    assert called is False


def test_notebook_start_accepts_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ws_cpu = "ws-6e6ba362-e98e-45b2-9c5a-311998e93d65"
    ws_gpu = "ws-9dcc0e1f-80a4-4af2-bc2f-0e352e7b17e6"

    config = config_module.Config(
        username="user",
        password="pass",
        base_url="https://example.invalid",
        target_dir=str(tmp_path / "logs"),
        job_cache_path=str(tmp_path / "jobs.json"),
        log_cache_dir=str(tmp_path / "log_cache"),
        job_workspace_id=None,
        workspace_cpu_id=ws_cpu,
        workspace_gpu_id=ws_gpu,
        workspace_internet_id=None,
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
    )

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {}

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    class FakeSession:
        workspace_id = "ws-00000000-0000-0000-0000-000000000000"
        storage_state = {}

    monkeypatch.setattr(web_session_module, "get_web_session", lambda: FakeSession())

    item = {
        "id": "78822a57-3830-44e7-8d45-e8b0d674fc44",
        "name": "ring-8h100-test",
        "status": "STOPPED",
        "created_at": "2026-02-01T10:00:00Z",
        "quota": {"cpu_count": 8, "gpu_count": 8},
    }

    def fake_request_json(
        session,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        body: Optional[dict] = None,
        timeout: int = 30,
        _retry_count: int = 0,
    ) -> dict:
        assert timeout
        assert _retry_count >= 0

        if method.upper() == "GET" and url.endswith("/api/v1/user/detail"):
            return {"data": {"id": "user-1"}}

        assert method.upper() == "POST"
        assert url.endswith("/api/v1/notebook/list")
        assert body and "workspace_id" in body
        assert (body.get("filter_by") or {}).get("keyword") == "ring-8h100-test"

        ws_id = str(body["workspace_id"])
        if ws_id == ws_cpu:
            return {"code": 0, "data": {"list": [item]}}
        if ws_id == ws_gpu:
            return {"code": 0, "data": {"list": []}}
        return {"code": 0, "data": {"list": []}}

    monkeypatch.setattr(web_session_module, "request_json", fake_request_json)

    started: dict[str, str] = {}

    def fake_start_notebook(notebook_id: str, session=None) -> dict:  # type: ignore[no-untyped-def]
        started["notebook_id"] = notebook_id
        return {"ok": True}

    monkeypatch.setattr(browser_api_module, "start_notebook", fake_start_notebook)

    def fake_wait_for_notebook_running(
        notebook_id: str, session=None, timeout: int = 600, poll_interval: int = 5
    ) -> dict:
        return {"status": "RUNNING", "notebook_id": notebook_id, "quota": {"gpu_count": 8}}

    monkeypatch.setattr(
        browser_api_module, "wait_for_notebook_running", fake_wait_for_notebook_running
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "start", "ring-8h100-test"])

    assert result.exit_code == EXIT_SUCCESS
    assert started["notebook_id"] == item["id"]


def test_notebook_start_name_conflict_prompts_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws_cpu = "ws-6e6ba362-e98e-45b2-9c5a-311998e93d65"
    ws_gpu = "ws-9dcc0e1f-80a4-4af2-bc2f-0e352e7b17e6"

    config = config_module.Config(
        username="user",
        password="pass",
        base_url="https://example.invalid",
        target_dir=str(tmp_path / "logs"),
        job_cache_path=str(tmp_path / "jobs.json"),
        log_cache_dir=str(tmp_path / "log_cache"),
        job_workspace_id=None,
        workspace_cpu_id=ws_cpu,
        workspace_gpu_id=ws_gpu,
        workspace_internet_id=None,
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
    )

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {}

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    class FakeSession:
        workspace_id = "ws-00000000-0000-0000-0000-000000000000"
        storage_state = {}

    monkeypatch.setattr(web_session_module, "get_web_session", lambda: FakeSession())

    cpu_item = {
        "id": "nb-cpu",
        "name": "ring-8h100-test",
        "status": "STOPPED",
        "created_at": "2026-02-02T10:00:00Z",
        "quota": {"cpu_count": 8, "gpu_count": 8},
    }
    gpu_item = {
        "id": "nb-gpu",
        "name": "ring-8h100-test",
        "status": "STOPPED",
        "created_at": "2026-02-01T10:00:00Z",
        "quota": {"cpu_count": 8, "gpu_count": 8},
    }

    def fake_request_json(
        session,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        body: Optional[dict] = None,
        timeout: int = 30,
        _retry_count: int = 0,
    ) -> dict:
        assert timeout
        assert _retry_count >= 0

        if method.upper() == "GET" and url.endswith("/api/v1/user/detail"):
            return {"data": {"id": "user-1"}}

        assert method.upper() == "POST"
        assert url.endswith("/api/v1/notebook/list")
        assert body and "workspace_id" in body
        assert (body.get("filter_by") or {}).get("keyword") == "ring-8h100-test"

        ws_id = str(body["workspace_id"])
        if ws_id == ws_cpu:
            return {"code": 0, "data": {"list": [cpu_item]}}
        if ws_id == ws_gpu:
            return {"code": 0, "data": {"list": [gpu_item]}}
        return {"code": 0, "data": {"list": []}}

    monkeypatch.setattr(web_session_module, "request_json", fake_request_json)

    started: dict[str, str] = {}

    def fake_start_notebook(notebook_id: str, session=None) -> dict:  # type: ignore[no-untyped-def]
        started["notebook_id"] = notebook_id
        return {"ok": True}

    monkeypatch.setattr(browser_api_module, "start_notebook", fake_start_notebook)

    def fake_wait_for_notebook_running(
        notebook_id: str, session=None, timeout: int = 600, poll_interval: int = 5
    ) -> dict:
        return {"status": "RUNNING", "notebook_id": notebook_id, "quota": {"gpu_count": 8}}

    monkeypatch.setattr(
        browser_api_module, "wait_for_notebook_running", fake_wait_for_notebook_running
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["notebook", "start", "ring-8h100-test"],
        input="2\n",
    )

    assert result.exit_code == EXIT_SUCCESS
    assert started["notebook_id"] == "nb-gpu"


def test_notebook_start_warns_when_no_wait_conflicts_with_configured_post_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws_cpu = "ws-6e6ba362-e98e-45b2-9c5a-311998e93d65"
    ws_gpu = "ws-9dcc0e1f-80a4-4af2-bc2f-0e352e7b17e6"

    config = config_module.Config(
        username="user",
        password="pass",
        base_url="https://example.invalid",
        target_dir=str(tmp_path / "logs"),
        job_cache_path=str(tmp_path / "jobs.json"),
        log_cache_dir=str(tmp_path / "log_cache"),
        job_workspace_id=None,
        workspace_cpu_id=ws_cpu,
        workspace_gpu_id=ws_gpu,
        workspace_internet_id=None,
        notebook_post_start="echo from config",
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
    )

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {}

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    class FakeSession:
        workspace_id = "ws-00000000-0000-0000-0000-000000000000"
        storage_state = {}

    monkeypatch.setattr(web_session_module, "get_web_session", lambda: FakeSession())

    item = {
        "id": "78822a57-3830-44e7-8d45-e8b0d674fc44",
        "name": "ring-8h100-test",
        "status": "STOPPED",
        "created_at": "2026-02-01T10:00:00Z",
        "quota": {"cpu_count": 8, "gpu_count": 8},
    }

    def fake_request_json(
        session,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        body: Optional[dict] = None,
        timeout: int = 30,
        _retry_count: int = 0,
    ) -> dict:
        assert timeout
        assert _retry_count >= 0

        if method.upper() == "GET" and url.endswith("/api/v1/user/detail"):
            return {"data": {"id": "user-1"}}

        assert method.upper() == "POST"
        assert url.endswith("/api/v1/notebook/list")
        assert body and "workspace_id" in body
        assert (body.get("filter_by") or {}).get("keyword") == "ring-8h100-test"

        ws_id = str(body["workspace_id"])
        if ws_id == ws_cpu:
            return {"code": 0, "data": {"list": [item]}}
        if ws_id == ws_gpu:
            return {"code": 0, "data": {"list": []}}
        return {"code": 0, "data": {"list": []}}

    monkeypatch.setattr(web_session_module, "request_json", fake_request_json)

    started: dict[str, str] = {}

    def fake_start_notebook(notebook_id: str, session=None) -> dict:  # type: ignore[no-untyped-def]
        started["notebook_id"] = notebook_id
        return {"ok": True}

    monkeypatch.setattr(browser_api_module, "start_notebook", fake_start_notebook)

    def fake_wait_for_notebook_running(
        notebook_id: str, session=None, timeout: int = 600, poll_interval: int = 5
    ) -> dict:
        return {"status": "RUNNING", "notebook_id": notebook_id, "quota": {"gpu_count": 8}}

    monkeypatch.setattr(
        browser_api_module, "wait_for_notebook_running", fake_wait_for_notebook_running
    )
    monkeypatch.setattr(browser_api_module, "run_command_in_notebook", lambda **kwargs: True)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "start", "ring-8h100-test", "--no-wait"])

    assert result.exit_code == EXIT_SUCCESS
    assert started["notebook_id"] == item["id"]
    assert "--no-wait requested" in result.output
    assert "set notebook_post_start=none" in result.output
    assert "Waiting for notebook to reach RUNNING status..." in result.output

