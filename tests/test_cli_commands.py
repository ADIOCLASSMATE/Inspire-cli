import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from click.testing import CliRunner

from inspire.cli.main import main as cli_main
from inspire.cli.context import (
    EXIT_SUCCESS,
    EXIT_CONFIG_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_TIMEOUT,
    EXIT_LOG_NOT_FOUND,
    EXIT_JOB_NOT_FOUND,
    EXIT_VALIDATION_ERROR,
)

from inspire import config as config_module
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web import session as web_session_module
from inspire.platform.web.session import SessionExpiredError
from inspire.config import ConfigError
from inspire.cli.utils.job_cache import JobCache

# Valid test job IDs (must match the format: job-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
TEST_JOB_ID = "job-12345678-1234-1234-1234-123456789abc"
TEST_JOB_ID_2 = "job-abcdef12-3456-7890-abcd-ef1234567890"
TEST_JOB_ID_3 = "job-11111111-2222-3333-4444-555555555555"
TEST_DOCKER_REGISTRY = "registry.local"


def _parse_json_stream(output: str) -> List[Dict[str, Any]]:
    """Parse one or more JSON documents echoed sequentially."""
    decoder = json.JSONDecoder()
    payloads: List[Dict[str, Any]] = []
    index = 0
    length = len(output)
    while index < length:
        while index < length and output[index].isspace():
            index += 1
        if index >= length:
            break
        parsed, index = decoder.raw_decode(output, index)
        payloads.append(parsed)
    return payloads


def make_test_config(tmp_path: Path, include_compute_groups: bool = False) -> config_module.Config:
    """Create a test Config object.

    Args:
        tmp_path: Temporary directory path
        include_compute_groups: If True, include test compute groups
    """
    config = config_module.Config(
        username="user",
        password="pass",
        base_url="https://example.invalid",
        target_dir=str(tmp_path / "logs"),
        job_cache_path=str(tmp_path / "jobs.json"),
        log_cache_dir=str(tmp_path / "log_cache"),
        job_workspace_id="ws-11111111-1111-1111-1111-111111111111",
        job_image="registry.local/test-image:v1",
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
    )
    # Add test compute groups if requested
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


def patch_config_and_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, include_compute_groups: bool = False
) -> None:
    """Patch Config.from_files_and_env and get_web_session to use local stubs.

    Args:
        monkeypatch: pytest monkeypatch fixture
        tmp_path: Temporary directory path
        include_compute_groups: If True, include test compute groups in config
    """
    config = make_test_config(tmp_path, include_compute_groups=include_compute_groups)
    config.target_dir and Path(config.target_dir).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INSPIRE_JOB_CACHE", config.job_cache_path)

    def fake_from_files_and_env(cls, require_target_dir: bool = False, require_credentials: bool = True) -> tuple:  # type: ignore[override]
        if require_target_dir and not config.target_dir:
            raise ConfigError("Missing INSPIRE_TARGET_DIR")
        return config, {}

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    api = object()

    # Patch browser API job functions for submit_training_job pipeline
    monkeypatch.setattr(
        browser_api_module,
        "get_train_resource_prices",
        lambda **kw: [{
            "quota_id": "test-quota-id",
            "cpu_count": 15,
            "gpu_count": 1,
            "gpu_type": "NVIDIA_H200_SXM_141G",
            "memory_size_gib": 200,
        }],
    )
    monkeypatch.setattr(
        browser_api_module,
        "resolve_train_resource_spec_price",
        lambda **kw: {
            "cpu_type": "",
            "cpu_count": 15,
            "gpu_type": "NVIDIA_H200_SXM_141G",
            "gpu_count": 1,
            "memory_size_gib": 200,
            "logic_compute_group_id": "",
            "quota_id": "test-quota-id",
        },
    )
    monkeypatch.setattr(
        browser_api_module,
        "create_job",
        lambda **kw: {"data": {"job_id": TEST_JOB_ID}},
    )
    monkeypatch.setattr(
        browser_api_module,
        "get_job_detail",
        lambda job_id, session=None: {
            "data": {"job_id": job_id, "name": "test-job", "status": "SUCCEEDED"}
        },
    )
    monkeypatch.setattr(
        browser_api_module,
        "stop_job",
        lambda job_id, session=None: {"data": {"status": "stopped"}},
    )
    # Patch get_web_session (replaces old AuthManager.get_api)
    monkeypatch.setattr(
        web_session_module, "get_web_session", lambda **kw: api
    )

    # Mock browser API calls for project selection

    test_project = browser_api_module.ProjectInfo(
        project_id="project-test-123",
        name="Test Project",
        workspace_id="ws-test-workspace",
        member_gpu_limit=True,
        member_remain_gpu_hours=100.0,
    )

    monkeypatch.setattr(
        browser_api_module,
        "list_projects",
        lambda workspace_id=None, session=None: [test_project],
    )

    monkeypatch.setattr(
        browser_api_module,
        "select_project",
        lambda projects, requested=None, **_: (test_project, None),
    )

    return api


# ---------------------------------------------------------------------------
# Global main entry with subcommands
# ---------------------------------------------------------------------------


def test_global_json_flag_with_resources_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Include test compute groups in config
    patch_config_and_auth(monkeypatch, tmp_path, include_compute_groups=True)
    from inspire.platform.web import browser_api as browser_api_module

    # Use a test placeholder UUID instead of real compute group ID
    test_group_id = "lcg-test000-0000-0000-0000-000000000000"
    monkeypatch.setattr(
        browser_api_module,
        "get_accurate_gpu_availability",
        lambda: [
            browser_api_module.GPUAvailability(
                group_id=test_group_id,
                group_name="H200 TestRoom",
                gpu_type="NVIDIA H200",
                total_gpus=128,
                used_gpus=32,
                available_gpus=96,
                low_priority_gpus=8,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(cli_main, ["--json", "resources", "list"])
    assert result.exit_code == 0

    payload = json.loads(result.output)
    assert payload["success"] is True
    assert "availability" in payload["data"]
    assert payload["data"]["availability"][0]["group_id"] == test_group_id


def test_global_debug_flag_runs_subcommand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)
    from inspire.platform.web import browser_api as browser_api_module

    monkeypatch.setattr(
        browser_api_module,
        "get_accurate_gpu_availability",
        lambda: [],
    )
    runner = CliRunner()

    result = runner.invoke(cli_main, ["--debug", "resources", "list"])
    assert result.exit_code == 0


def test_job_help_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Smoke test to ensure `inspire job --help` works (no import/syntax errors)."""
    patch_config_and_auth(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli_main, ["job", "--help"])
    assert result.exit_code == 0
    assert "Manage training jobs" in result.output


# ---------------------------------------------------------------------------
# Job command group
# ---------------------------------------------------------------------------


def test_job_create_human_output_updates_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli_main,
        [
            "job",
            "create",
            "--name",
            "test-job",
            "--resource",
            "H200",
            "--command",
            "echo hi",
            "--no-auto",
        ],
    )

    assert result.exit_code == 0
    assert "Job created: job-123" in result.output

    # Verify job cache file was created
    cache_path = Path(make_test_config(tmp_path).job_cache_path)
    assert cache_path.exists()


def test_job_create_json_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli_main,
        [
            "--json",
            "job",
            "create",
            "--name",
            "test-job",
            "--resource",
            "H200",
            "--command",
            "echo hi",
            "--no-auto",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["success"] is True
    assert data["data"]["job_id"] == TEST_JOB_ID


def test_job_create_requires_target_dir(monkeypatch: pytest.MonkeyPatch):
    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):
        assert require_target_dir is True
        raise ConfigError("Missing INSPIRE_TARGET_DIR")

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "job",
            "create",
            "--name",
            "test-job",
            "--resource",
            "H200",
            "--command",
            "echo hi",
        ],
    )

    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "Missing INSPIRE_TARGET_DIR" in result.output


def test_wrap_in_bash():
    """Test the bash wrapper helper function."""
    from inspire.cli.utils.job_submit import wrap_in_bash

    # Basic wrapping
    assert wrap_in_bash("python train.py") == "bash -c 'python train.py'"

    # Source command (the main use case)
    result = wrap_in_bash("source .env && python train.py")
    assert result == "bash -c 'source .env && python train.py'"

    # Escape single quotes
    result = wrap_in_bash("echo 'hello'")
    assert result == "bash -c 'echo '\\''hello'\\'''"

    # Skip if already wrapped
    assert wrap_in_bash("bash -c 'foo'") == "bash -c 'foo'"
    assert wrap_in_bash("sh -c 'foo'") == "sh -c 'foo'"
    assert wrap_in_bash("/bin/bash -c 'foo'") == "/bin/bash -c 'foo'"
    assert wrap_in_bash("/bin/sh -c 'foo'") == "/bin/sh -c 'foo'"

    # Whitespace handling
    assert wrap_in_bash("  bash -c 'foo'  ") == "  bash -c 'foo'  "


def test_job_status_updates_cache_and_formats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli_main, ["job", "status", TEST_JOB_ID])
    assert result.exit_code == 0
    assert "Job Status" in result.output
    assert TEST_JOB_ID in result.output


def test_job_command_prefers_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    api = patch_config_and_auth(monkeypatch, tmp_path)

    # Seed cache with a different command to ensure API is preferred
    config = make_test_config(tmp_path)
    cache = JobCache(config.get_expanded_cache_path())
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="cached command",
        status="RUNNING",
        log_path=None,
    )

    api_calls: list[str] = []

    def api_detail(job_id: str, session=None) -> Dict[str, Any]:
        api_calls.append(job_id)
        return {"data": {"job_id": job_id, "command": "api command"}}

    monkeypatch.setattr(browser_api_module, "get_job_detail", api_detail)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "command", TEST_JOB_ID])

    assert result.exit_code == 0
    assert "api command" in result.output
    assert "cached command" not in result.output
    assert api_calls == [TEST_JOB_ID]


def test_job_command_falls_back_to_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    api = patch_config_and_auth(monkeypatch, tmp_path)

    config = make_test_config(tmp_path)
    cache = JobCache(config.get_expanded_cache_path())
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="cached command",
        status="RUNNING",
        log_path=None,
    )

    def api_detail(job_id: str, session=None) -> Dict[str, Any]:  # noqa: ARG001
        raise SessionExpiredError("bad credentials")

    monkeypatch.setattr(browser_api_module, "get_job_detail", api_detail)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "command", TEST_JOB_ID])

    assert result.exit_code == 0
    assert "cached command" in result.output


def test_job_status_not_found_sets_specific_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    api = patch_config_and_auth(monkeypatch, tmp_path)

    def failing_get_job_detail(job_id: str, session=None) -> Dict[str, Any]:
        raise RuntimeError("Job not found")

    monkeypatch.setattr(browser_api_module, "get_job_detail", failing_get_job_detail)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "status", "missing-id"])
    assert result.exit_code == EXIT_JOB_NOT_FOUND


def test_job_stop_with_force_and_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli_main,
        ["--json", "job", "stop", TEST_JOB_ID],
    )
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert data["data"]["job_id"] == TEST_JOB_ID
    assert data["data"]["status"] == "stopped"


def test_job_wait_succeeds_and_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    api = patch_config_and_auth(monkeypatch, tmp_path)

    # Ensure the job is immediately in a terminal state
    def get_job_detail(job_id: str) -> Dict[str, Any]:
        return {
            "data": {
                "job_id": job_id,
                "name": "wait-job",
                "status": "SUCCEEDED",
                "running_time_ms": "1000",
            }
        }

    monkeypatch.setattr(browser_api_module, "get_job_detail", get_job_detail)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["job", "wait", TEST_JOB_ID, "--timeout", "60", "--interval", "1"],
    )
    assert result.exit_code == EXIT_SUCCESS
    assert "SUCCEEDED" in result.output


def test_job_wait_json_output_has_no_human_banner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = patch_config_and_auth(monkeypatch, tmp_path)

    def get_job_detail(job_id: str) -> Dict[str, Any]:
        return {
            "data": {
                "job_id": job_id,
                "name": "wait-job",
                "status": "SUCCEEDED",
                "running_time_ms": "1000",
            }
        }

    monkeypatch.setattr(browser_api_module, "get_job_detail", get_job_detail)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["--json", "job", "wait", TEST_JOB_ID, "--timeout", "60", "--interval", "1"],
    )

    assert result.exit_code == EXIT_SUCCESS
    assert "Waiting for job" not in result.output
    payloads = _parse_json_stream(result.output)
    assert payloads
    for payload in payloads:
        assert payload["success"] is True


def test_job_wait_times_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)

    # Force time to jump ahead so we immediately hit timeout
    from importlib import import_module

    job_deps = import_module("inspire.cli.commands.job.job_deps")

    calls: List[int] = []

    def fake_time() -> int:
        # First call (start_time) -> 0, second call -> large value
        calls.append(1)
        return 0 if len(calls) == 1 else 10

    monkeypatch.setattr(job_deps.time, "time", fake_time)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["job", "wait", TEST_JOB_ID, "--timeout", "1", "--interval", "1"],
    )
    assert result.exit_code == EXIT_TIMEOUT
    assert "Timeout after 1s" in result.output


def test_job_list_uses_local_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)

    # Provide a fake JobCache implementation
    from importlib import import_module

    job_deps = import_module("inspire.cli.commands.job.job_deps")

    class FakeCache:
        def __init__(self, path: str) -> None:  # noqa: ARG002
            pass

        def list_jobs(
            self,
            limit: int = 10,
            status: Optional[str] = None,
            exclude_statuses: Optional[set] = None,
        ) -> List[Dict[str, Any]]:
            return [
                {
                    "job_id": TEST_JOB_ID,
                    "name": "cached-job",
                    "status": status or "PENDING",
                    "created_at": "2025-01-01T00:00:00",
                }
            ]

    monkeypatch.setattr(job_deps, "JobCache", FakeCache)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "list", "--limit", "5"])

    assert result.exit_code == 0
    assert "cached-job" in result.output
    assert TEST_JOB_ID in result.output


def test_job_list_watch_json_does_not_clear_screen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_config_and_auth(monkeypatch, tmp_path)

    from importlib import import_module

    job_commands_module = import_module("inspire.cli.commands.job.job_commands")

    def fail_clear(cmd: str) -> int:  # noqa: ARG001
        raise AssertionError("clear should not be called in JSON mode")

    monkeypatch.setattr(job_commands_module.os, "system", fail_clear)
    monkeypatch.setattr(
        job_commands_module.job_deps.time,
        "sleep",
        lambda interval: (_ for _ in ()).throw(KeyboardInterrupt()),  # noqa: ARG005
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "job", "list", "--watch", "--interval", "1"])

    assert result.exit_code == EXIT_SUCCESS
    payloads = _parse_json_stream(result.output)
    assert payloads
    for payload in payloads:
        assert payload["success"] is True


def test_job_update_refreshes_job_creating_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)

    # Seed cache with a job in an early-stage API status that should still be refreshed
    config = make_test_config(tmp_path)
    cache = JobCache(config.get_expanded_cache_path())
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="creating-job",
        resource="H200",
        command="echo hi",
        status="job_creating",
        log_path=None,
    )

    called_job_ids: list[str] = []

    def tracking_get_job_detail(job_id: str, session=None) -> dict:
        called_job_ids.append(job_id)
        return {"data": {"job_id": job_id, "status": "SUCCEEDED"}}

    monkeypatch.setattr(browser_api_module, "get_job_detail", tracking_get_job_detail)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "job", "update", "--delay", "0"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True

    updated_ids = {entry["job_id"] for entry in payload["data"]["updated"]}
    assert updated_ids == {TEST_JOB_ID}

    # Ensure the job was actually polled and the cache was updated
    assert called_job_ids == [TEST_JOB_ID]
    refreshed = cache.get_job(TEST_JOB_ID)
    assert refreshed is not None
    assert refreshed["status"] == "SUCCEEDED"


def test_job_logs_path_and_tail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)

    # Add job to cache with a remote log path
    config = make_test_config(tmp_path)
    from inspire.cli.utils.job_cache import JobCache

    cache = JobCache(config.get_expanded_cache_path())
    remote_log_path = f"/train/logs/.inspire/training_master_{TEST_JOB_ID}.log"
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=remote_log_path,
    )

    # Create local cache directory and log file (simulating already-fetched log)
    local_cache_dir = Path(config.log_cache_dir)
    local_cache_dir.mkdir(parents=True, exist_ok=True)
    local_log_path = local_cache_dir / f"{TEST_JOB_ID}.log"
    local_log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")

    # Mock notebook-based log fetch to do nothing (log already cached)
    from importlib import import_module

    job_logs_module = import_module("inspire.cli.commands.job.job_logs")

    monkeypatch.setattr(job_logs_module, "_get_explicit_notebook_id", lambda *a, **kw: None)
    monkeypatch.setattr(job_logs_module, "_fetch_and_cache_log_via_notebook", lambda *a, **kw: None)

    runner = CliRunner()

    # --path just prints path
    result = runner.invoke(cli_main, ["job", "logs", TEST_JOB_ID, "--path"])
    assert result.exit_code == 0
    assert str(remote_log_path) in result.output

    # --tail reads last N lines
    result_tail = runner.invoke(cli_main, ["job", "logs", TEST_JOB_ID, "--tail", "2"])
    assert result_tail.exit_code == 0
    assert "line2" in result_tail.output
    assert "line3" in result_tail.output


def test_job_logs_json_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)

    # Add job to cache with a remote log path
    config = make_test_config(tmp_path)
    from inspire.cli.utils.job_cache import JobCache

    cache = JobCache(config.get_expanded_cache_path())
    remote_log_path = f"/train/logs/.inspire/training_master_{TEST_JOB_ID}.log"
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=remote_log_path,
    )

    # Create local cache directory and log file
    local_cache_dir = Path(config.log_cache_dir)
    local_cache_dir.mkdir(parents=True, exist_ok=True)
    local_log_path = local_cache_dir / f"{TEST_JOB_ID}.log"
    local_log_path.write_text("test log content\n", encoding="utf-8")

    # Mock notebook-based log fetch
    from importlib import import_module

    job_logs_module = import_module("inspire.cli.commands.job.job_logs")

    monkeypatch.setattr(job_logs_module, "_get_explicit_notebook_id", lambda *a, **kw: None)
    monkeypatch.setattr(job_logs_module, "_fetch_and_cache_log_via_notebook", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "job", "logs", TEST_JOB_ID])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["success"] is True
    assert "log_path" in data["data"]
    assert "content" in data["data"]
    assert "test log content" in data["data"]["content"]


def test_job_logs_legacy_filename_is_migrated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_config_and_auth(monkeypatch, tmp_path)

    config = make_test_config(tmp_path)
    from inspire.cli.utils.job_cache import JobCache

    cache = JobCache(config.get_expanded_cache_path())
    remote_log_path = f"/train/logs/.inspire/training_master_{TEST_JOB_ID}.log"
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=remote_log_path,
    )

    local_cache_dir = Path(config.log_cache_dir)
    local_cache_dir.mkdir(parents=True, exist_ok=True)
    legacy_log_path = local_cache_dir / f"job-{TEST_JOB_ID}.log"
    legacy_log_path.write_text("legacy line1\nlegacy line2\n", encoding="utf-8")

    from importlib import import_module

    job_logs_module = import_module("inspire.cli.commands.job.job_logs")

    def fail_fetch(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("fetch should not be called when legacy cache exists")

    monkeypatch.setattr(job_logs_module, "_get_explicit_notebook_id", lambda *a, **kw: None)
    monkeypatch.setattr(job_logs_module, "_fetch_and_cache_log_via_notebook", fail_fetch)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "logs", TEST_JOB_ID, "--tail", "1"])

    assert result.exit_code == 0
    assert "legacy line2" in result.output
    new_path = local_cache_dir / f"{TEST_JOB_ID}.log"
    assert new_path.exists()
    assert not legacy_log_path.exists()


def test_job_logs_missing_file_sets_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Config.from_files_and_env will succeed but cache has no log_path for this job.
    patch_config_and_auth(monkeypatch, tmp_path)

    # Add job to cache WITHOUT log_path to test the "log not found" path
    config = make_test_config(tmp_path)
    from inspire.cli.utils.job_cache import JobCache

    cache = JobCache(config.get_expanded_cache_path())
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=None,  # No log path means LogNotFound
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "logs", TEST_JOB_ID])

    assert result.exit_code == EXIT_LOG_NOT_FOUND
    assert f"No log file found for job {TEST_JOB_ID}" in result.output


def test_job_logs_follow_json_uses_notebook_follow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_config_and_auth(monkeypatch, tmp_path)

    config = make_test_config(tmp_path)
    cache = JobCache(config.get_expanded_cache_path())
    remote_log_path = f"/train/logs/.inspire/training_master_{TEST_JOB_ID}.log"
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=remote_log_path,
    )

    from importlib import import_module

    job_logs_module = import_module("inspire.cli.commands.job.job_logs")

    called = {"notebook_follow": False}
    monkeypatch.setattr(job_logs_module, "_get_explicit_notebook_id", lambda *a, **kw: "nb-1")
    monkeypatch.setattr(
        job_logs_module,
        "_follow_logs_via_api",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API unavailable")),
    )
    monkeypatch.setattr(
        job_logs_module,
        "_follow_logs_via_notebook",
        lambda *a, **kw: (called.__setitem__("notebook_follow", True) or EXIT_SUCCESS),
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "job", "logs", TEST_JOB_ID, "--follow"])

    assert result.exit_code == EXIT_SUCCESS
    assert called["notebook_follow"] is True


def test_job_logs_follow_returns_follow_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_config_and_auth(monkeypatch, tmp_path)

    config = make_test_config(tmp_path)
    cache = JobCache(config.get_expanded_cache_path())
    remote_log_path = f"/train/logs/.inspire/training_master_{TEST_JOB_ID}.log"
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=remote_log_path,
    )

    from importlib import import_module

    job_logs_module = import_module("inspire.cli.commands.job.job_logs")

    monkeypatch.setattr(job_logs_module, "_get_explicit_notebook_id", lambda *a, **kw: "nb-1")
    monkeypatch.setattr(
        job_logs_module,
        "_follow_logs_via_api",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API unavailable")),
    )
    monkeypatch.setattr(job_logs_module, "_follow_logs_via_notebook", lambda *args, **kwargs: EXIT_GENERAL_ERROR)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "logs", TEST_JOB_ID, "--follow"])

    assert result.exit_code == EXIT_GENERAL_ERROR


def test_job_logs_notebook_option_uses_specified_notebook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_config_and_auth(monkeypatch, tmp_path)

    config = make_test_config(tmp_path)
    cache = JobCache(config.get_expanded_cache_path())
    remote_log_path = f"/train/logs/.inspire/training_master_{TEST_JOB_ID}.log"
    cache.add_job(
        job_id=TEST_JOB_ID,
        name="test-job",
        resource="H200",
        command="echo test",
        status="RUNNING",
        log_path=remote_log_path,
    )

    from importlib import import_module

    job_logs_module = import_module("inspire.cli.commands.job.job_logs")
    observed: dict[str, str | None] = {"notebook_id": None}

    def fake_resolve(*args, **kwargs):  # noqa: ANN002, ANN003
        # --notebook option passes the notebook id directly
        return kwargs.get("notebook") or "nb-default"

    def fake_fetch(*args, **kwargs):  # noqa: ANN002, ANN003
        observed["notebook_id"] = kwargs.get("notebook_id")
        # Write log content to cache_path
        import pathlib

        cache_path = kwargs.get("cache_path")
        if cache_path:
            pathlib.Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(cache_path).write_text("notebook fetch content\n", encoding="utf-8")

    monkeypatch.setattr(job_logs_module, "_get_explicit_notebook_id", fake_resolve)
    monkeypatch.setattr(job_logs_module, "_fetch_and_cache_log_via_notebook", fake_fetch)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "logs", TEST_JOB_ID, "--notebook", "my-nb"])

    assert result.exit_code == EXIT_SUCCESS


def test_job_logs_notebook_option_requires_job_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    patch_config_and_auth(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "logs", "--notebook", "my-nb"])

    # --notebook without JOB_ID should fail
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Resources / nodes / config commands
# ---------------------------------------------------------------------------


def test_nodes_list_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Include test compute groups in config
    patch_config_and_auth(monkeypatch, tmp_path, include_compute_groups=True)
    from inspire.platform.web import browser_api as browser_api_module

    test_group_id = "lcg-test000-0000-0000-0000-000000000000"
    monkeypatch.setattr(
        browser_api_module,
        "get_full_free_node_counts",
        lambda group_ids, gpu_per_node=8, session=None, _retry=True: [  # noqa: ARG005
            browser_api_module.FullFreeNodeCount(
                group_id=test_group_id,
                group_name="H200 TestRoom",
                gpu_per_node=gpu_per_node,
                total_nodes=10,
                ready_nodes=8,
                full_free_nodes=3,
            )
        ],
    )
    # Also mock get_accurate_gpu_availability which is called by the nodes command
    monkeypatch.setattr(
        browser_api_module,
        "get_accurate_gpu_availability",
        lambda workspace_id=None, session=None, _retry=True: [  # noqa: ARG005
            browser_api_module.GPUAvailability(
                group_id=test_group_id,
                group_name="H200 TestRoom",
                gpu_type="H200",
                total_gpus=80,
                used_gpus=68,
                available_gpus=12,
                low_priority_gpus=0,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(cli_main, ["--json", "resources", "nodes"])
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert data["data"]["groups"]
    assert data["data"]["total_full_free_nodes"] == 3


def test_config_check_auth_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config = make_test_config(tmp_path)
    config.docker_registry = TEST_DOCKER_REGISTRY

    def fake_from_files_and_env(cls, require_target_dir: bool = False, require_credentials: bool = True) -> tuple:  # type: ignore[override]
        return config, {}

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    def fake_load_session():
        raise ValueError("No valid web session")

    monkeypatch.setattr(web_session_module.models.WebSession, "load", staticmethod(fake_load_session))

    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "check"])

    assert result.exit_code == EXIT_AUTH_ERROR
    assert "Authentication failed" in result.output


def test_config_check_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        raise ConfigError("missing env")

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "config", "check"])

    assert result.exit_code == EXIT_CONFIG_ERROR
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["error"]["type"] == "ConfigError"


def test_config_check_json_includes_base_url_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)
    config.prefer_source = "toml"
    config.base_url = "https://my-inspire.internal"
    config.docker_registry = TEST_DOCKER_REGISTRY

    project_dir = tmp_path / ".inspire"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_config = project_dir / "config.toml"
    project_config.write_text(
        """
[api]
base_url = "https://my-inspire.internal"
"""
    )
    global_config = tmp_path / "global-config.toml"

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {"base_url": config_module.SOURCE_PROJECT}

    def fake_get_config_paths(cls):  # type: ignore[override]
        return global_config, project_config

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )
    monkeypatch.setenv("INSPIRE_BASE_URL", "https://env.example")
    monkeypatch.setattr(web_session_module, "get_web_session", lambda **kw: object())

    # Mock WebSession.load() for the auth check (config check uses cached session check)
    class FakeSession:
        storage_state = {"cookies": [{"name": "test", "value": "test"}]}

    monkeypatch.setattr(web_session_module.models.WebSession, "load", staticmethod(lambda **kw: FakeSession()))

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "config", "check"])

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.output)
    resolution = payload["data"]["base_url_resolution"]
    assert resolution["value"] == "https://my-inspire.internal"
    assert resolution["source"] == config_module.SOURCE_PROJECT
    assert resolution["prefer_source"] == "toml"
    assert resolution["env_present"] is True
    assert resolution["project_config_path"] == str(project_config)
    assert resolution["global_config_path"] == str(global_config)


def test_config_check_accepts_local_json_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)
    config.base_url = "https://my-inspire.internal"
    config.docker_registry = TEST_DOCKER_REGISTRY

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {"base_url": config_module.SOURCE_ENV}

    def fake_get_config_paths(cls):  # type: ignore[override]
        return None, None

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )
    monkeypatch.setattr(web_session_module, "get_web_session", lambda **kw: object())
    monkeypatch.setattr(
        web_session_module.models.WebSession, "load",
        staticmethod(lambda **kw: type("Fake", (), {"storage_state": {"cookies": [{}]}})())
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "check", "--json"])

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["auth_ok"] is True
    assert "base_url_resolution" in payload["data"]


def test_config_check_rejects_placeholder_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)
    config.base_url = "https://api.example.com"

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {"base_url": config_module.SOURCE_DEFAULT}

    def fake_get_config_paths(cls):  # type: ignore[override]
        return None, None

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )
    monkeypatch.setattr(
        web_session_module, "get_web_session", lambda **kw: pytest.fail("should not auth")
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "config", "check"])

    assert result.exit_code == EXIT_CONFIG_ERROR
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["error"]["type"] == "ConfigError"
    assert "Placeholder host values detected" in payload["error"]["message"]
    assert "INSPIRE_BASE_URL" in payload["error"]["message"]


def test_config_check_requires_docker_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)
    config.base_url = "https://my-inspire.internal"
    config.docker_registry = None

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {
            "base_url": config_module.SOURCE_ENV,
            "docker_registry": config_module.SOURCE_DEFAULT,
        }

    def fake_get_config_paths(cls):  # type: ignore[override]
        return None, None

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )
    monkeypatch.setattr(
        web_session_module, "get_web_session", lambda **kw: pytest.fail("should not auth")
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "config", "check"])

    assert result.exit_code == EXIT_CONFIG_ERROR
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["error"]["type"] == "ConfigError"
    assert "Missing docker registry configuration" in payload["error"]["message"]
    assert "INSPIRE_DOCKER_REGISTRY" in payload["error"]["message"]


def test_config_check_rejects_top_level_project_base_url_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)
    config.base_url = "https://my-inspire.internal"

    project_dir = tmp_path / ".inspire"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_config = project_dir / "config.toml"
    project_config.write_text('base_url = "https://wrong.example.com"\n')

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {"base_url": config_module.SOURCE_PROJECT}

    def fake_get_config_paths(cls):  # type: ignore[override]
        return None, project_config

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )
    monkeypatch.setattr(
        web_session_module, "get_web_session", lambda **kw: pytest.fail("should not auth")
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "config", "check"])

    assert result.exit_code == EXIT_CONFIG_ERROR
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert "top-level `base_url`" in payload["error"]["message"]
    assert "[api]" in payload["error"]["message"]


def test_config_check_allows_path_defaults_for_endpoint_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)
    config.base_url = "https://my-inspire.internal"
    config.docker_registry = TEST_DOCKER_REGISTRY
    config.auth_endpoint = "/auth/token"
    config.browser_api_prefix = "/api/v1"

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {"base_url": config_module.SOURCE_ENV}

    def fake_get_config_paths(cls):  # type: ignore[override]
        return None, None

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )
    monkeypatch.setattr(web_session_module, "get_web_session", lambda **kw: object())
    monkeypatch.setattr(
        web_session_module.models.WebSession, "load",
        staticmethod(lambda **kw: type("Fake", (), {"storage_state": {"cookies": [{}]}})())
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "check"])

    assert result.exit_code == EXIT_SUCCESS
    assert "Configuration looks good" in result.output


def test_init_json_global_contract_via_top_level_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli_main, ["--json", "init", "--template", "--project", "--force"])

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["mode"] == "template"
    assert payload["data"]["files_written"] == [str(tmp_path / ".inspire" / "config.toml")]


def test_config_show_respects_global_json_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path)

    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]
        return config, {"username": config_module.SOURCE_ENV}

    def fake_get_config_paths(cls):  # type: ignore[override]
        return None, None

    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(
        config_module.Config, "get_config_paths", classmethod(fake_get_config_paths)
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "config", "show"])

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.output)
    assert "config_files" in payload
    assert "values" in payload
    assert "INSPIRE_USERNAME" in payload["values"]


def test_notebook_list_all_workspaces_combines_results(
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
        "name": "cpu-notebook",
        "status": "RUNNING",
        "created_at": "2026-02-01T10:00:00Z",
        "quota": {"cpu_count": 4, "gpu_count": 0},
    }
    gpu_item = {
        "id": "nb-gpu",
        "name": "gpu-notebook",
        "status": "RUNNING",
        "created_at": "2026-02-02T10:00:00Z",
        "quota": {"cpu_count": 8, "gpu_count": 1},
        "resource_spec_price": {"gpu_info": {"gpu_product_simple": "H200"}},
    }

    calls: list[str] = []

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
        assert headers is None or isinstance(headers, dict)
        assert timeout
        assert _retry_count >= 0

        assert method.upper() == "POST"
        assert url.endswith("/api/v1/notebook/list")
        assert body and "workspace_id" in body

        ws_id = str(body["workspace_id"])
        calls.append(ws_id)

        if ws_id == ws_cpu:
            return {"code": 0, "data": {"list": [cpu_item]}}
        if ws_id == ws_gpu:
            return {"code": 0, "data": {"list": [gpu_item]}}
        return {"code": 0, "data": {"list": []}}

    monkeypatch.setattr(web_session_module, "request_json", fake_request_json)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "list", "--all-workspaces", "--all", "--json"])

    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.output)
    items = payload["data"]["items"]
    assert [item["id"] for item in items] == ["nb-gpu", "nb-cpu"]
    assert calls == [ws_cpu, ws_gpu]
