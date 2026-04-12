"""Configuration models, schema, and loaders for Inspire CLI."""

from __future__ import annotations

from inspire.config.env import build_env_exports
from inspire.config.load import config_from_files_and_env, get_config_paths
from inspire.config.models import (
    CONFIG_FILENAME,
    PROJECT_CONFIG_DIR,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_GLOBAL,
    SOURCE_PROJECT,
    Config,
    ConfigError,
)
from inspire.config.schema import (  # noqa: F401
    CATEGORY_ORDER,
    CONFIG_OPTIONS,
    get_categories,
    get_option_by_env,
    get_option_by_toml,
    get_options_by_category,
    get_options_by_scope,
    get_required_options,
    get_secret_options,
)
from inspire.config.schema_models import (  # noqa: F401
    ConfigOption,
    _parse_bool,
    _parse_float,
    _parse_int,
    parse_value,
)

__all__ = [
    "CATEGORY_ORDER",
    "CONFIG_FILENAME",
    "CONFIG_OPTIONS",
    "PROJECT_CONFIG_DIR",
    "SOURCE_DEFAULT",
    "SOURCE_ENV",
    "SOURCE_GLOBAL",
    "SOURCE_PROJECT",
    "Config",
    "ConfigError",
    "ConfigOption",
    "_parse_bool",
    "_parse_float",
    "_parse_int",

    "build_env_exports",

    "config_from_files_and_env",
    "get_categories",
    "get_config_paths",
    "get_option_by_env",
    "get_option_by_toml",
    "get_options_by_category",
    "get_options_by_scope",
    "get_required_options",
    "get_secret_options",
    "parse_value",
]
