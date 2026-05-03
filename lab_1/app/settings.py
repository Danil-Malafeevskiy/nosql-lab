import os


class SettingsError(RuntimeError):
    pass


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if v is None:
        raise SettingsError(f"Missing required environment variable: {name}")
    v = v.strip()
    if v == "":
        raise SettingsError(f"Missing required environment variable: {name}")
    return v


def _env_int(name: str) -> int:
    raw = _require_env(name)
    try:
        return int(raw)
    except ValueError as e:
        raise SettingsError(f"Invalid int in environment variable {name}: {raw}") from e


class Settings:
    def __init__(self) -> None:
        self.app_host = _require_env("APP_HOST")
        self.app_port = _env_int("APP_PORT")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

