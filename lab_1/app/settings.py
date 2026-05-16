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


def _env_optional(name: str) -> str:
    v = os.getenv(name)
    return (v or "").strip()


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

        self.like_ttl = _env_int("APP_LIKE_TTL")
        self.event_reviews_ttl = _env_int("APP_EVENT_REVIEWS_TTL")

        self.redis_host = _require_env("REDIS_HOST")
        self.redis_port = _env_int("REDIS_PORT")
        # May be empty or unset for passwordless Redis.
        self.redis_password = _env_optional("REDIS_PASSWORD")
        self.redis_db = _env_int("REDIS_DB")

        self.cassandra_hosts = [h.strip() for h in _require_env("CASSANDRA_HOSTS").split(",") if h.strip()]
        if not self.cassandra_hosts:
            raise SettingsError("Missing required environment variable: CASSANDRA_HOSTS")
        self.cassandra_port = _env_int("CASSANDRA_PORT")
        self.cassandra_username = _env_optional("CASSANDRA_USERNAME")
        self.cassandra_password = _env_optional("CASSANDRA_PASSWORD")
        self.cassandra_keyspace = _require_env("CASSANDRA_KEYSPACE", allow_empty=False).strip('"').strip("'")
        self.cassandra_consistency = _require_env("CASSANDRA_CONSISTENCY")

        self.mongodb_database = _require_env("MONGODB_DATABASE")
        self.mongodb_user = _require_env("MONGODB_USER")
        self.mongodb_password = _require_env("MONGODB_PASSWORD")
        self.mongodb_host = _require_env("MONGODB_HOST")
        self.mongodb_port = _require_env("MONGODB_PORT")
        self.mongodb_auth_mechanism = _env_optional("MONGODB_AUTH_MECHANISM")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
