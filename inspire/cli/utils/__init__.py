"""CLI utility modules."""

from inspire.config import Config, ConfigError
# AuthManager and AuthenticationError were removed.
# Use ``get_web_session()`` from ``inspire.platform.web.session`` instead.

__all__ = ["Config", "ConfigError"]
