"""Job ID format validation utilities (moved from openapi/errors.py)."""

import re
from typing import Optional

JOB_ID_PATTERN = re.compile(
    r"^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
JOB_ID_EXPECTED_LENGTH = 40  # "job-" (4) + UUID with hyphens (36)


def _validate_job_id_format(job_id: str) -> Optional[str]:
    """Validate job ID format and return a helpful message if invalid.

    Returns None if valid, or an error message if invalid.
    """
    if not job_id:
        return "Job ID cannot be empty"

    if not job_id.startswith("job-"):
        return f"Job ID should start with 'job-', got: {job_id[:20]}..."

    if JOB_ID_PATTERN.match(job_id):
        return None  # Valid

    actual_len = len(job_id)
    if actual_len < JOB_ID_EXPECTED_LENGTH:
        missing = JOB_ID_EXPECTED_LENGTH - actual_len
        return (
            f"Job ID appears to be truncated (got {actual_len} chars, expected {JOB_ID_EXPECTED_LENGTH}). "
            f"Missing {missing} character(s). Did you copy the full ID?"
        )
    elif actual_len > JOB_ID_EXPECTED_LENGTH:
        return f"Job ID is too long (got {actual_len} chars, expected {JOB_ID_EXPECTED_LENGTH})"
    else:
        return "Job ID format is invalid. Expected format: job-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
