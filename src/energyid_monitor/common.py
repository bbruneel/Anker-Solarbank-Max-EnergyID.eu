import os


def _require_env(name: str, default: str | None = None) -> str:
    """Get environment variable, raise if missing (unless default provided)."""
    value = os.getenv(name)
    if not value:
        if default is not None:
            return default
        raise ValueError(f"Missing required environment variable: {name}")
    return value
