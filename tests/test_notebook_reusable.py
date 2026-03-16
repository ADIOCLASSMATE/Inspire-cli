import importlib

import pytest
from click.testing import CliRunner

from inspire.cli.context import EXIT_SUCCESS
from inspire.cli.main import main as cli_main


def test_notebook_reusable_outputs_json_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("inspire.cli.commands.notebook.notebook_commands")

    # Avoid Playwright: treat only nb-1 as idle.
    monkeypatch.setattr(
        mod,
        "check_notebook_idle_via_nvidia_smi",
        lambda **kwargs: kwargs.get("notebook_id") == "nb-1",
    )

    # Avoid auth/network
    class FakeSession:
        storage_state = {}
        all_workspace_ids = [
            "ws-1",
            "ws-2",
            "ws-1",  # duplicate
            "ws-00000000-0000-0000-0000-000000000000",  # filtered
        ]

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())
    monkeypatch.setattr(mod, "_try_get_current_user_ids", lambda session, base_url: ["u-1"])

    def fake_collect_all_ws(session, config):  # type: ignore[no-untyped-def]
        return ["ws-1", "ws-2"]

    monkeypatch.setattr(mod, "_collect_all_workspace_ids", fake_collect_all_ws)

    def fake_list(session, *, base_url, workspace_id, user_ids, status, **kwargs):  # type: ignore[no-untyped-def]
        assert status == ["RUNNING"]
        if workspace_id == "ws-1":
            return [
                {
                    "id": "nb-1",
                    "name": "a",
                    "status": "RUNNING",
                    "quota": {"gpu_count": 8},
                    "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                    "resource_spec_price": None,
                },
                {
                    "id": "nb-busy",
                    "name": "b",
                    "status": "RUNNING",
                    "quota": {"gpu_count": 8},
                    "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                    "resource_spec_price": None,
                },
                {
                    "id": "nb-wrong-type",
                    "name": "c",
                    "status": "RUNNING",
                    "quota": {"gpu_count": 8},
                    "node": {"gpu_info": {"gpu_product_simple": "A100"}},
                    "resource_spec_price": None,
                },
            ]
        if workspace_id == "ws-2":
            return [
                {
                    "id": "nb-wrong-count",
                    "name": "d",
                    "status": "RUNNING",
                    "quota": {"gpu_count": 4},
                    "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                    "resource_spec_price": None,
                }
            ]
        return []

    monkeypatch.setattr(mod, "_list_notebooks_for_workspace_paginated", fake_list)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "reusable", "-r", "8xH100", "--all"])
    assert result.exit_code == EXIT_SUCCESS

    import json

    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["id"] == "nb-1"
    assert payload["data"]["items"][0]["workspace_id"] == "ws-1"


def test_notebook_reusable_defaults_to_first_match(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("inspire.cli.commands.notebook.notebook_commands")

    calls: list[str] = []

    def fake_idle_check(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(str(kwargs.get("notebook_id")))
        # Treat both as idle.
        return kwargs.get("notebook_id") in {"nb-1", "nb-2"}

    monkeypatch.setattr(mod, "check_notebook_idle_via_nvidia_smi", fake_idle_check)

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())
    monkeypatch.setattr(mod, "_collect_all_workspace_ids", lambda session, config: ["ws-1"])
    monkeypatch.setattr(mod, "_try_get_current_user_ids", lambda session, base_url: ["u-1"])

    def fake_list(session, *, base_url, workspace_id, user_ids, status, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "id": "nb-1",
                "name": "a",
                "status": "RUNNING",
                "quota": {"gpu_count": 8},
                "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                "resource_spec_price": None,
            },
            {
                "id": "nb-2",
                "name": "b",
                "status": "RUNNING",
                "quota": {"gpu_count": 8},
                "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                "resource_spec_price": None,
            },
        ]

    monkeypatch.setattr(mod, "_list_notebooks_for_workspace_paginated", fake_list)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "reusable", "-r", "8xH100"])
    assert result.exit_code == EXIT_SUCCESS

    import json

    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["id"] == "nb-1"

    # Ensure we stopped after finding the first idle match.
    assert calls == ["nb-1"]


def test_notebook_reusable_all_does_not_stop_early(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("inspire.cli.commands.notebook.notebook_commands")

    calls: list[str] = []

    def fake_idle_check(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(str(kwargs.get("notebook_id")))
        return kwargs.get("notebook_id") in {"nb-1", "nb-2"}

    monkeypatch.setattr(mod, "check_notebook_idle_via_nvidia_smi", fake_idle_check)

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())
    monkeypatch.setattr(mod, "_collect_all_workspace_ids", lambda session, config: ["ws-1"])
    monkeypatch.setattr(mod, "_try_get_current_user_ids", lambda session, base_url: ["u-1"])

    def fake_list(session, *, base_url, workspace_id, user_ids, status, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "id": "nb-1",
                "name": "a",
                "status": "RUNNING",
                "quota": {"gpu_count": 8},
                "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                "resource_spec_price": None,
            },
            {
                "id": "nb-2",
                "name": "b",
                "status": "RUNNING",
                "quota": {"gpu_count": 8},
                "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                "resource_spec_price": None,
            },
        ]

    monkeypatch.setattr(mod, "_list_notebooks_for_workspace_paginated", fake_list)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "reusable", "-r", "8xH100", "--all"])
    assert result.exit_code == EXIT_SUCCESS

    import json

    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["total"] == 2
    assert [it["id"] for it in payload["data"]["items"]] == ["nb-1", "nb-2"]

    assert calls == ["nb-1", "nb-2"]


def test_notebook_reusable_propagates_probe_args(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("inspire.cli.commands.notebook.notebook_commands")

    seen: dict = {}

    def fake_idle_check(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return True

    monkeypatch.setattr(mod, "check_notebook_idle_via_nvidia_smi", fake_idle_check)

    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())
    monkeypatch.setattr(mod, "_collect_all_workspace_ids", lambda session, config: ["ws-1"])
    monkeypatch.setattr(mod, "_try_get_current_user_ids", lambda session, base_url: ["u-1"])

    def fake_list(session, *, base_url, workspace_id, user_ids, status, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "id": "nb-1",
                "name": "a",
                "status": "RUNNING",
                "quota": {"gpu_count": 8},
                "node": {"gpu_info": {"gpu_product_simple": "H100"}},
                "resource_spec_price": None,
            }
        ]

    monkeypatch.setattr(mod, "_list_notebooks_for_workspace_paginated", fake_list)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "notebook",
            "reusable",
            "-r",
            "8xH100",
            "--samples",
            "1",
            "--lab-timeout",
            "20000",
        ],
    )
    assert result.exit_code == EXIT_SUCCESS

    assert seen.get("samples") == 1
    assert seen.get("lab_timeout_ms") == 20000
    assert seen.get("probe") is not None


def test_notebook_reusable_forces_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("inspire.cli.commands.notebook.notebook_commands")

    # Avoid auth/network
    class FakeSession:
        storage_state = {}

    monkeypatch.setattr(mod, "require_web_session", lambda ctx, hint: FakeSession())
    monkeypatch.setattr(mod, "get_base_url", lambda: "https://example.invalid")
    monkeypatch.setattr(mod, "load_config", lambda ctx: object())
    monkeypatch.setattr(mod, "_collect_all_workspace_ids", lambda session, config: ["ws-1"])
    monkeypatch.setattr(mod, "_try_get_current_user_ids", lambda session, base_url: ["u-1"])
    monkeypatch.setattr(mod, "_list_notebooks_for_workspace_paginated", lambda *a, **k: [])

    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "reusable", "-r", "8xH100"])
    assert result.exit_code == EXIT_SUCCESS

    import json

    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["data"]["total"] == 0
