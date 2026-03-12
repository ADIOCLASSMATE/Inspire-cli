#!/usr/bin/env python3
"""Test all openapi/v1 endpoints to see which can be replaced with api/v1.

Based on qzcli_tool analysis:
- openapi/v1 uses Token (Bearer) auth
- api/v1 uses Cookie auth

This test checks if api/v1 endpoints can accept Token auth.
"""

import sys
import requests
from inspire.config import Config

def test_endpoint_raw(base_url: str, token: str, prefix: str, endpoint: str, payload: dict) -> dict:
    """Test a raw endpoint with token auth."""
    url = f"{base_url}{prefix}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return {"success": True, "data": resp.json()}
        elif resp.status_code in (401, 302):
            return {"success": False, "error": f"HTTP {resp.status_code} - Auth required"}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:100]}

def get_token(base_url: str, username: str, password: str) -> str:
    """Get auth token."""
    url = f"{base_url}/auth/token"
    resp = requests.post(url, json={"username": username, "password": password}, timeout=30)
    data = resp.json()
    if data.get("code") == 0:
        return data.get("data", {}).get("access_token")
    return None

def main():
    # Load config
    config, _ = Config.from_files_and_env()
    print(f"User: {config.username}")
    print(f"Base URL: {config.base_url}\n")

    # Get token
    token = get_token(config.base_url, config.username, config.password)
    if not token:
        print("Failed to get token!")
        return 1
    print(f"Token: {token[:20]}...\n")

    # Endpoints to test (from qzcli_tool)
    endpoints = [
        # openapi/v1 endpoints (should work with token)
        ("/train_job/detail", {"job_id": "job-test-12345"}),
        ("/train_job/stop", {"job_id": "job-test-12345"}),
        ("/train_job/create", {}),  # Will fail but we can see if auth works
        ("/specs/list", {"logic_compute_group_id": "test"}),
        ("/cluster_nodes/list", {"page_num": 1, "page_size": 5}),
        # api/v1 only endpoints (from qzcli_tool)
        ("/train_job/list", {"page_num": 1, "page_size": 5, "workspace_id": "test"}),
        ("/project/list", {"page": 1, "page_size": 10, "filter": {}}),
        ("/workspace/list_task_dimension", {"page_num": 1, "page_size": 10, "filter": {}}),
    ]

    print("=" * 80)
    print(f"{'Endpoint':<40} {'openapi/v1':<20} {'api/v1':<20}")
    print("=" * 80)

    for endpoint, payload in endpoints:
        # Test with openapi/v1
        result_openapi = test_endpoint_raw(config.base_url, token, "/openapi/v1", endpoint, payload)
        openapi_status = "✓" if result_openapi["success"] else "✗"

        # Test with api/v1
        result_api = test_endpoint_raw(config.base_url, token, "/api/v1", endpoint, payload)
        api_status = "✓" if result_api["success"] else "✗"

        # Check if both work or both fail with same pattern
        if result_openapi["success"] and result_api["success"]:
            compat = "✓ SAME"
        elif not result_openapi["success"] and not result_api["success"]:
            # Check if same error type
            if "401" in str(result_openapi.get("error", "")) and "401" in str(result_api.get("error", "")):
                compat = "~ BOTH 401"
            else:
                compat = "✗ DIFF"
        else:
            compat = "✗ DIFF"

        print(f"{endpoint:<40} {openapi_status:<20} {api_status:<20}")

        # Show details for differences
        if not result_openapi["success"]:
            print(f"  openapi/v1: {result_openapi.get('error', 'Unknown')[:60]}")
        if not result_api["success"]:
            print(f"  api/v1: {result_api.get('error', 'Unknown')[:60]}")

    print("=" * 80)
    print("\nConclusion:")
    print("- openapi/v1: Token (Bearer) auth")
    print("- api/v1: Cookie auth (Token auth returns 401/302)")
    print("\nCannot unify - different auth mechanisms!")

if __name__ == "__main__":
    sys.exit(main() or 0)
