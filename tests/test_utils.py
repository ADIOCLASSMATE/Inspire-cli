"""Tests for utility modules: job_cache, config."""

from pathlib import Path

import pytest

from inspire.cli.utils.job_cache import JobCache
from inspire.config import (
    Config,
    ConfigError,
    build_env_exports,
)

# ===========================================================================
# JobCache tests
# ===========================================================================


class TestJobCache:
    """Tests for JobCache class."""

    def test_add_and_get_job(self, tmp_path: Path) -> None:
        """Test adding and retrieving a job."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-12345678-1234-1234-1234-123456789abc",
            name="test-job",
            resource="4xH200",
            command="python train.py",
            status="RUNNING",
            log_path="/train/logs/test.log",
        )

        job = cache.get_job("job-12345678-1234-1234-1234-123456789abc")
        assert job is not None
        assert job["job_id"] == "job-12345678-1234-1234-1234-123456789abc"
        assert job["name"] == "test-job"
        assert job["resource"] == "4xH200"
        assert job["command"] == "python train.py"
        assert job["status"] == "RUNNING"
        assert job["log_path"] == "/train/logs/test.log"

    def test_get_nonexistent_job(self, tmp_path: Path) -> None:
        """Test getting a job that doesn't exist."""
        cache = JobCache(str(tmp_path / "jobs.json"))
        job = cache.get_job("job-nonexistent-0000-0000-000000000000")
        assert job is None

    def test_update_status(self, tmp_path: Path) -> None:
        """Test updating job status."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-12345678-1234-1234-1234-123456789abc",
            name="test-job",
            resource="H200",
            command="echo test",
            status="PENDING",
        )

        cache.update_status("job-12345678-1234-1234-1234-123456789abc", "RUNNING")

        job = cache.get_job("job-12345678-1234-1234-1234-123456789abc")
        assert job is not None
        assert job["status"] == "RUNNING"

    def test_list_jobs_sorted_by_creation(self, tmp_path: Path) -> None:
        """Test that jobs are sorted by creation time (newest first)."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-aaaaaaa1-0000-0000-0000-000000000001",
            name="job-1",
            resource="H200",
            command="echo 1",
            status="RUNNING",
        )
        cache.add_job(
            job_id="job-aaaaaaa2-0000-0000-0000-000000000002",
            name="job-2",
            resource="H200",
            command="echo 2",
            status="PENDING",
        )

        jobs = cache.list_jobs(limit=10)
        assert len(jobs) == 2
        # Most recent should be first
        assert jobs[0]["name"] == "job-2"
        assert jobs[1]["name"] == "job-1"

    def test_list_jobs_with_status_filter(self, tmp_path: Path) -> None:
        """Test filtering jobs by status."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-aaaaaaa1-0000-0000-0000-000000000001",
            name="running-job",
            resource="H200",
            command="echo 1",
            status="RUNNING",
        )
        cache.add_job(
            job_id="job-aaaaaaa2-0000-0000-0000-000000000002",
            name="pending-job",
            resource="H200",
            command="echo 2",
            status="PENDING",
        )

        running_jobs = cache.list_jobs(status="RUNNING")
        assert len(running_jobs) == 1
        assert running_jobs[0]["name"] == "running-job"

    def test_list_jobs_with_exclude_statuses(self, tmp_path: Path) -> None:
        """Test excluding jobs by status."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-aaaaaaa1-0000-0000-0000-000000000001",
            name="running-job",
            resource="H200",
            command="echo 1",
            status="RUNNING",
        )
        cache.add_job(
            job_id="job-aaaaaaa2-0000-0000-0000-000000000002",
            name="failed-job",
            resource="H200",
            command="echo 2",
            status="FAILED",
        )

        active_jobs = cache.list_jobs(exclude_statuses={"FAILED", "CANCELLED"})
        assert len(active_jobs) == 1
        assert active_jobs[0]["name"] == "running-job"

    def test_list_jobs_with_limit(self, tmp_path: Path) -> None:
        """Test limiting number of returned jobs."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        for i in range(5):
            cache.add_job(
                job_id=f"job-aaaaaaa{i}-0000-0000-0000-00000000000{i}",
                name=f"job-{i}",
                resource="H200",
                command=f"echo {i}",
                status="RUNNING",
            )

        jobs = cache.list_jobs(limit=3)
        assert len(jobs) == 3

    def test_remove_job(self, tmp_path: Path) -> None:
        """Test removing a job from cache."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-12345678-1234-1234-1234-123456789abc",
            name="test-job",
            resource="H200",
            command="echo test",
            status="RUNNING",
        )

        assert cache.remove_job("job-12345678-1234-1234-1234-123456789abc") is True
        assert cache.get_job("job-12345678-1234-1234-1234-123456789abc") is None

        # Removing nonexistent job returns False
        assert cache.remove_job("job-nonexistent-0000-0000-000000000000") is False

    def test_clear_cache(self, tmp_path: Path) -> None:
        """Test clearing all jobs from cache."""
        cache = JobCache(str(tmp_path / "jobs.json"))

        cache.add_job(
            job_id="job-12345678-1234-1234-1234-123456789abc",
            name="test-job",
            resource="H200",
            command="echo test",
            status="RUNNING",
        )

        cache.clear()

        jobs = cache.list_jobs()
        assert len(jobs) == 0

    def test_log_offset_operations(self, tmp_path: Path) -> None:
        """Test log offset get/set/reset operations."""
        cache = JobCache(str(tmp_path / "jobs.json"))
        job_id = "job-12345678-1234-1234-1234-123456789abc"

        cache.add_job(
            job_id=job_id,
            name="test-job",
            resource="H200",
            command="echo test",
            status="RUNNING",
        )

        # Initial offset should be 0
        assert cache.get_log_offset(job_id) == 0

        # Set offset
        cache.set_log_offset(job_id, 1000)
        assert cache.get_log_offset(job_id) == 1000

        # Reset offset
        cache.reset_log_offset(job_id)
        assert cache.get_log_offset(job_id) == 0

    def test_default_cache_path(self) -> None:
        """Test that default cache path is in home directory."""
        cache = JobCache()
        expected_path = Path.home() / ".inspire" / "jobs.json"
        assert cache.cache_path == expected_path


# ===========================================================================
# Config tests
# ===========================================================================


class TestConfig:
    """Tests for Config class and helper functions."""

    def test_get_expanded_cache_path(self) -> None:
        """Test that cache path ~ is expanded."""
        config = Config(
            username="test",
            password="test",
            job_cache_path="~/.inspire/jobs.json",
        )

        expanded = config.get_expanded_cache_path()
        assert "~" not in expanded
        assert ".inspire/jobs.json" in expanded


class TestConfigHelpers:
    """Tests for config helper functions."""

    def test_build_env_exports_empty(self) -> None:
        """Test building env exports with empty dict."""
        assert build_env_exports({}) == ""

    def test_build_env_exports_single(self) -> None:
        """Test building env exports with single var."""
        result = build_env_exports({"FOO": "bar"})
        assert result == "export FOO=bar && "

    def test_build_env_exports_multiple(self) -> None:
        """Test building env exports with multiple vars."""
        result = build_env_exports({"FOO": "bar", "BAZ": "qux"})
        # Order may vary due to dict iteration, so check both parts
        assert "export FOO=bar" in result
        assert "export BAZ=qux" in result
        assert result.endswith(" && ")
        assert " && " in result  # Separates the two exports

    def test_build_env_exports_env_ref_bare(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """remote_env supports $VARNAME to pull from local environment."""
        monkeypatch.setenv("TOKEN", "sekret")
        result = build_env_exports({"WANDB_API_KEY": "$TOKEN"})
        assert result == "export WANDB_API_KEY=sekret && "

    def test_build_env_exports_env_ref_braced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """remote_env supports ${VARNAME} to pull from local environment."""
        monkeypatch.setenv("TOKEN", "sekret")
        result = build_env_exports({"WANDB_API_KEY": "${TOKEN}"})
        assert result == "export WANDB_API_KEY=sekret && "

    def test_build_env_exports_empty_uses_same_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty remote_env value uses the local environment value for that key."""
        monkeypatch.setenv("WANDB_API_KEY", "sekret")
        result = build_env_exports({"WANDB_API_KEY": ""})
        assert result == "export WANDB_API_KEY=sekret && "

    def test_build_env_exports_quotes_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Values are safely shell-quoted."""
        monkeypatch.setenv("TOKEN", "has spaces")
        result = build_env_exports({"WANDB_API_KEY": "$TOKEN"})
        assert result == "export WANDB_API_KEY='has spaces' && "

    def test_build_env_exports_missing_env_var_raises(self) -> None:
        """Missing env var references should fail early."""
        with pytest.raises(ConfigError, match="not set in the local environment"):
            build_env_exports({"WANDB_API_KEY": "$MISSING"})

    def test_build_env_exports_invalid_key_raises(self) -> None:
        """Invalid shell variable names should fail early."""
        with pytest.raises(ConfigError, match="Invalid remote_env key"):
            build_env_exports({"NOT-VALID": "x"})

