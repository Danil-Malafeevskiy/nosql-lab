import os


class SettingsError(RuntimeError):
    pass


def _require_env(name: str, *, allow_empty: bool = False) -> str:
    v = os.getenv(name)
    if v is None:
        raise SettingsError(f"Missing required environment variable: {name}")
    v = v.strip()
    if not allow_empty and v == "":
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
        self.session_ttl = _env_int("APP_USER_SESSION_TTL")

        self.redis_host = _require_env("REDIS_HOST")
        self.redis_port = _env_int("REDIS_PORT")
        self.redis_password = _require_env("REDIS_PASSWORD", allow_empty=True)
        self.redis_db = _env_int("REDIS_DB")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
