#!/usr/bin/env python3
"""Test if openapi/v1 endpoints can be replaced with api/v1."""

import os
import sys
from inspire.config import Config
from inspire.platform.openapi.client import InspireAPI
from inspire.platform.openapi.models import InspireConfig

def test_openapi_vs_api():
    """Test if openapi/v1 and api/v1 are compatible."""

    # Load config
    try:
        config, _ = Config.from_files_and_env()
        print(f"✓ Loaded config for user: {config.username}")
        print(f"  Base URL: {config.base_url}")
    except Exception as e:
        print(f"✗ Failed to load config: {e}")
        return False

    # Test 1: Original openapi/v1
    print("\n=== Test 1: Using openapi/v1 (original) ===")
    try:
        inspire_config = InspireConfig(
            base_url=config.base_url,
            openapi_prefix="/openapi/v1",
        )
        api_openapi = InspireAPI(inspire_config)

        # Authenticate
        if api_openapi.authenticate(config.username, config.password):
            print("✓ Authentication successful with openapi/v1")

            # Try to list cluster nodes
            try:
                result = api_openapi.list_cluster_nodes(page_num=1, page_size=5)
                print(f"✓ list_cluster_nodes works: {result.get('code', 'N/A')}")
                if result.get('code') == 0:
                    print(f"  Found {len(result.get('data', {}).get('list', []))} nodes")
            except Exception as e:
                print(f"✗ list_cluster_nodes failed: {e}")
        else:
            print("✗ Authentication failed with openapi/v1")
            return False
    except Exception as e:
        print(f"✗ Test with openapi/v1 failed: {e}")
        return False

    # Test 2: Replace with api/v1
    print("\n=== Test 2: Using api/v1 (replacement) ===")
    try:
        inspire_config_api = InspireConfig(
            base_url=config.base_url,
            openapi_prefix="/api/v1",  # Changed from /openapi/v1
        )
        api_v1 = InspireAPI(inspire_config_api)

        # Authenticate
        if api_v1.authenticate(config.username, config.password):
            print("✓ Authentication successful with api/v1")

            # Try to list cluster nodes
            try:
                result = api_v1.list_cluster_nodes(page_num=1, page_size=5)
                print(f"✓ list_cluster_nodes works: {result.get('code', 'N/A')}")
                if result.get('code') == 0:
                    print(f"  Found {len(result.get('data', {}).get('list', []))} nodes")
                    print("\n✅ SUCCESS: api/v1 can replace openapi/v1!")
                    return True
            except Exception as e:
                print(f"✗ list_cluster_nodes failed: {e}")
                print("\n❌ FAILED: api/v1 cannot replace openapi/v1")
                return False
        else:
            print("✗ Authentication failed with api/v1")
            print("\n❌ FAILED: api/v1 cannot replace openapi/v1")
            return False
    except Exception as e:
        print(f"✗ Test with api/v1 failed: {e}")
        print("\n❌ FAILED: api/v1 cannot replace openapi/v1")
        return False

if __name__ == "__main__":
    success = test_openapi_vs_api()
    sys.exit(0 if success else 1)
