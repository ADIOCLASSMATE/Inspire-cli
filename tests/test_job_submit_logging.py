import importlib
from datetime import datetime, timezone

import pytest

from inspire.config import Config

job_submit_module = importlib.import_module("inspire.cli.utils.job_submit")


def test_build_remote_logged_command_uses_override_log_path(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config(username="u", password="p", target_dir="/train/target")

    final_command, log_path = job_submit_module.build_remote_logged_command(
        cfg,
        command="bash -c 'echo hi'",
        log_path_override="/custom/logs/out.log",
    )

    assert log_path == "/custom/logs/out.log"
    assert 'mkdir -p "/custom/logs"' in final_command
    assert '( cd "/train/target" && bash -c' in final_command
    assert '> "/custom/logs/out.log" 2>&1' in final_command


def test_build_remote_logged_command_default_path_is_in_target_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return datetime(2020, 1, 2, 3, 4, 5, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(job_submit_module, "datetime", FixedDateTime)

    cfg = Config(username="u", password="p", target_dir="/train/target")

    final_command, log_path = job_submit_module.build_remote_logged_command(
        cfg,
        command="bash -c 'echo hi'",
    )

    assert log_path == "/train/target/.inspire/training_master_20200102_030405.log"
    assert 'mkdir -p "/train/target/.inspire"' in final_command
    assert '( cd "/train/target" && bash -c' in final_command
    assert '> "/train/target/.inspire/training_master_20200102_030405.log" 2>&1' in final_command
