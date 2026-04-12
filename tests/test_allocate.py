"""Tests for the GPU availability overview engine."""

from __future__ import annotations

import pytest

from inspire.platform.web.browser_api.availability.allocate import (
    AllocateResult,
    GroupStatus,
    ProjectBudget,
    _has_budget,
    _priority_value,
    compute_allocate_overview,
)
from inspire.platform.web.browser_api.availability.models import GPUAvailability
from inspire.platform.web.browser_api.projects import ProjectInfo


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _gpu_group(
    group_id: str = "g1",
    group_name: str = "H200-A",
    gpu_type: str = "H200",
    total_gpus: int = 64,
    used_gpus: int = 32,
    available_gpus: int = 32,
    low_priority_gpus: int = 0,
) -> GPUAvailability:
    return GPUAvailability(
        group_id=group_id,
        group_name=group_name,
        gpu_type=gpu_type,
        total_gpus=total_gpus,
        used_gpus=used_gpus,
        available_gpus=available_gpus,
        low_priority_gpus=low_priority_gpus,
    )


def _project(
    project_id: str = "p1",
    name: str = "TestProject",
    *,
    budget: float | None = None,
    remain_budget: float | None = None,
    member_remain_budget: float | None = None,
    member_remain_gpu_hours: float | None = None,
    gpu_limit: bool = False,
    member_gpu_limit: bool = False,
    priority_name: str = "0",
) -> ProjectInfo:
    return ProjectInfo(
        project_id=project_id,
        name=name,
        workspace_id="ws-test",
        budget=budget,
        remain_budget=remain_budget,
        member_remain_budget=member_remain_budget,
        member_remain_gpu_hours=member_remain_gpu_hours,
        gpu_limit=gpu_limit,
        member_gpu_limit=member_gpu_limit,
        priority_name=priority_name,
    )


# ---------------------------------------------------------------------------
# _priority_value
# ---------------------------------------------------------------------------


def test_priority_value_parses_int() -> None:
    assert _priority_value(_project(priority_name="6")) == 6


def test_priority_value_defaults_to_zero() -> None:
    assert _priority_value(_project(priority_name="")) == 0
    assert _priority_value(_project(priority_name="invalid")) == 0


# ---------------------------------------------------------------------------
# _has_budget
# ---------------------------------------------------------------------------


def test_has_budget_true_with_remaining() -> None:
    p = _project(budget=100.0, remain_budget=50.0, member_remain_budget=10.0)
    assert _has_budget(p) is True


def test_has_budget_false_when_project_budget_exhausted() -> None:
    p = _project(budget=100.0, remain_budget=0.0, member_remain_budget=10.0)
    assert _has_budget(p) is False


def test_has_budget_false_when_member_budget_exhausted() -> None:
    p = _project(budget=100.0, remain_budget=50.0, member_remain_budget=0.0)
    assert _has_budget(p) is False


def test_has_budget_true_when_no_budget_field() -> None:
    p = _project(member_remain_budget=None, remain_budget=None)
    assert _has_budget(p) is True


# ---------------------------------------------------------------------------
# GroupStatus properties
# ---------------------------------------------------------------------------


def test_group_has_free() -> None:
    g = GroupStatus(group_id="g1", group_name="A", gpu_type="H200",
                    available_gpus=10, low_priority_gpus=0, total_gpus=64,
                    gpus_needed=8)
    assert g.has_free is True


def test_group_not_free() -> None:
    g = GroupStatus(group_id="g1", group_name="A", gpu_type="H200",
                    available_gpus=4, low_priority_gpus=0, total_gpus=64,
                    gpus_needed=8)
    assert g.has_free is False


def test_group_has_preemptible() -> None:
    g = GroupStatus(group_id="g1", group_name="A", gpu_type="H200",
                    available_gpus=2, low_priority_gpus=10, total_gpus=64,
                    gpus_needed=8)
    assert g.has_preemptible is True


def test_group_not_preemptible() -> None:
    g = GroupStatus(group_id="g1", group_name="A", gpu_type="H200",
                    available_gpus=0, low_priority_gpus=4, total_gpus=64,
                    gpus_needed=8)
    assert g.has_preemptible is False


def test_group_negative_available_still_preemptible() -> None:
    g = GroupStatus(group_id="g1", group_name="A", gpu_type="H200",
                    available_gpus=-20, low_priority_gpus=30, total_gpus=920,
                    gpus_needed=8)
    assert g.has_free is False
    assert g.has_preemptible is True


# ---------------------------------------------------------------------------
# compute_allocate_overview
# ---------------------------------------------------------------------------


def _setup_mocks(monkeypatch, groups, projects):
    monkeypatch.setattr(
        "inspire.platform.web.browser_api.availability.allocate.get_accurate_gpu_availability",
        lambda: groups,
    )
    monkeypatch.setattr(
        "inspire.platform.web.browser_api.availability.allocate.list_projects",
        lambda: projects,
    )


def test_overview_returns_all_groups(monkeypatch) -> None:
    groups = [
        _gpu_group(group_id="g1", group_name="H200-A", available_gpus=32),
        _gpu_group(group_id="g2", group_name="H200-B", available_gpus=0, low_priority_gpus=10),
        _gpu_group(group_id="g3", group_name="H200-C", available_gpus=0, low_priority_gpus=0),
    ]
    projects = [
        _project(project_id="p1", name="High", budget=100.0,
                 remain_budget=50.0, member_remain_budget=10.0, priority_name="8"),
    ]
    _setup_mocks(monkeypatch, groups, projects)

    result = compute_allocate_overview(gpus=8, gpu_type="H200")
    assert len(result.groups) == 3
    assert result.groups[0].has_free is True
    assert result.groups[1].has_free is False
    assert result.groups[1].has_preemptible is True
    assert result.groups[2].has_free is False
    assert result.groups[2].has_preemptible is False


def test_overview_returns_all_projects(monkeypatch) -> None:
    groups = [_gpu_group(available_gpus=32)]
    projects = [
        _project(project_id="p1", name="WithBudget", budget=100.0,
                 remain_budget=50.0, member_remain_budget=10.0, priority_name="6"),
        _project(project_id="p2", name="NoBudget", budget=100.0,
                 remain_budget=-10.0, member_remain_budget=-10.0, priority_name="8"),
    ]
    _setup_mocks(monkeypatch, groups, projects)

    result = compute_allocate_overview(gpus=8, gpu_type="H200")
    assert len(result.projects) == 2
    assert result.projects[0].has_budget is True  # WithBudget sorted first
    assert result.projects[1].has_budget is False


def test_overview_no_matching_gpu_type_raises(monkeypatch) -> None:
    groups = [_gpu_group(gpu_type="H100")]
    projects = [_project(budget=100.0, remain_budget=50.0, member_remain_budget=10.0)]
    _setup_mocks(monkeypatch, groups, projects)

    with pytest.raises(ValueError, match="No compute groups found"):
        compute_allocate_overview(gpus=8, gpu_type="H200")


def test_overview_groups_sorted_free_first(monkeypatch) -> None:
    groups = [
        _gpu_group(group_id="g1", group_name="NoFree", available_gpus=0, low_priority_gpus=10),
        _gpu_group(group_id="g2", group_name="HasFree", available_gpus=32),
    ]
    projects = [_project(budget=100.0, remain_budget=50.0, member_remain_budget=10.0)]
    _setup_mocks(monkeypatch, groups, projects)

    result = compute_allocate_overview(gpus=8, gpu_type="H200")
    assert result.groups[0].group_name == "HasFree"


def test_overview_projects_sorted_budget_then_priority(monkeypatch) -> None:
    groups = [_gpu_group(available_gpus=32)]
    projects = [
        _project(project_id="p1", name="NoBudgetHighPri", budget=100.0,
                 remain_budget=-10.0, member_remain_budget=-10.0, priority_name="10"),
        _project(project_id="p2", name="BudgetLowPri", budget=100.0,
                 remain_budget=50.0, member_remain_budget=10.0, priority_name="3"),
        _project(project_id="p3", name="BudgetHighPri", budget=100.0,
                 remain_budget=50.0, member_remain_budget=10.0, priority_name="8"),
    ]
    _setup_mocks(monkeypatch, groups, projects)

    result = compute_allocate_overview(gpus=8, gpu_type="H200")
    # Budget first, then by priority desc
    assert result.projects[0].project_name == "BudgetHighPri"
    assert result.projects[1].project_name == "BudgetLowPri"
    assert result.projects[2].has_budget is False
