"""Shared data models for v2 API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class V2JobInfo:
    """Training job information from v2 ListJobs / GetJob."""

    job_id: str
    name: str
    status: str
    command: str
    created_at: str
    finished_at: str | None
    project_id: str
    project_name: str
    workspace_id: str
    workspace_name: str
    logic_compute_group_id: str
    logic_compute_group_name: str
    priority: int
    framework_config: list[dict] = field(default_factory=list)
    timeline: dict | None = None

    @classmethod
    def from_api_response(cls, data: dict) -> "V2JobInfo":
        fc = data.get("framework_config", [{}])
        first_fc = fc[0] if fc else {}
        gpu_count = first_fc.get("gpu_count", 0)
        instance_count = first_fc.get("instance_count", 1)
        image = first_fc.get("image", "")

        return cls(
            job_id=data.get("job_id", ""),
            name=data.get("name", ""),
            status=str(data.get("status", "")),
            command=data.get("command", ""),
            created_at=data.get("created_at", ""),
            finished_at=data.get("finished_at"),
            project_id=data.get("project_id", ""),
            project_name=data.get("project_name", ""),
            workspace_id=data.get("workspace_id", ""),
            workspace_name=data.get("workspace_name", ""),
            logic_compute_group_id=data.get("logic_compute_group_id", ""),
            logic_compute_group_name=data.get("logic_compute_group_name", ""),
            priority=data.get("priority", data.get("task_priority", 0)),
            framework_config=fc,
            timeline=data.get("timeline"),
        )


@dataclass(frozen=True)
class V2InferenceInfo:
    """Inference serving information from v2 ListServings / GetServing."""

    inference_serving_id: str
    name: str
    status: str
    command: str
    image: str
    model_id: str
    model_version: int
    port: int
    replicas: int
    node_num_per_replica: int
    workspace_id: str
    project_id: str
    created_at: str

    @classmethod
    def from_api_response(cls, data: dict) -> "V2InferenceInfo":
        return cls(
            inference_serving_id=data.get("inference_serving_id", ""),
            name=data.get("name", ""),
            status=str(data.get("status", "")),
            command=data.get("command", ""),
            image=data.get("image", ""),
            model_id=data.get("model_id", ""),
            model_version=data.get("model_version", 1),
            port=data.get("port", 2400),
            replicas=data.get("replicas", 1),
            node_num_per_replica=data.get("node_num_per_replica", 1),
            workspace_id=data.get("workspace_id", ""),
            project_id=data.get("project_id", ""),
            created_at=data.get("created_at", ""),
        )


@dataclass(frozen=True)
class MetricTimeSeries:
    """A single metric time series from GetTaskMetric."""

    group_name: str
    metric_type: str
    resource_name: str
    time_series: list[dict] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict) -> "MetricTimeSeries":
        return cls(
            group_name=data.get("group_name", ""),
            metric_type=data.get("metric_type", ""),
            resource_name=data.get("resource_name", ""),
            time_series=data.get("time_series", []),
        )


@dataclass(frozen=True)
class MetricSummary:
    """Statistical summary of a metric time series."""

    avg: float
    p50: float
    p90: float
    min: float
    max: float
    latest: float
    stddev: float
    samples: int
    trend: str  # "up" | "down" | "stable"
    trend_magnitude: float
    idle_windows: list[IdleWindow] = field(default_factory=list)
    idle_total_samples: int = 0


@dataclass(frozen=True)
class IdleWindow:
    """A contiguous window where the metric value is below the idle threshold."""

    start_idx: int
    end_idx: int
    duration_samples: int


@dataclass(frozen=True)
class StatusFamily:
    """Normalized job status."""

    family: str  # "running" | "waiting" | "succeeded" | "failed" | "stopped" | "unknown"
    is_terminal: bool
    raw: str
