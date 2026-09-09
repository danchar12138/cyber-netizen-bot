"""FastAPI dependency providers and process-local singletons."""

from functools import lru_cache

from cnb_application import ConfigurationRegistry, build_default_registry


@lru_cache(maxsize=1)
def get_configuration_registry() -> ConfigurationRegistry:
    """Return the immutable built-in configuration registry."""
    return build_default_registry()
