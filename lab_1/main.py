from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets

import redis


SESSION_COOKIE_NAME = "X-Session-Id"


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class HealthCheckHandler(BaseHTTPRequestHandler):
    redis_client = None
    session_ttl = 60

    def _path_only(self) -> str:
        return self.path.split("?", 1)[0]

    def _drain_request_body(self) -> None:
        length = self.headers.get("Content-Length")
        if length is None:
            return
        try:
            n = int(length)
        except ValueError:
            return
        if n > 0:
            self.rfile.read(n)

    def do_GET(self):
        self.close_connection = True
        if self._path_only() != "/health":
            self._send_json(404, {"status": "not_found"})
            return

        sid = self._extract_sid_cookie()
        extra_headers = []
        if sid:
            extra_headers.append(("Set-Cookie", self._build_session_cookie(sid)))

        self._send_json(200, {"status": "ok"}, extra_headers=extra_headers)

    def do_POST(self):
        self.close_connection = True
        self._drain_request_body()

        if self._path_only() != "/session":
            self._send_json(404, {"status": "not_found"})
            return

        sid = self._extract_sid_cookie()
        now = utc_now_rfc3339()

        try:
            if sid and self._session_exists(sid):
                self._refresh_session(sid, now)
                status_code = 200
            else:
                sid = self._create_new_session(now)
                status_code = 201
        except (redis.RedisError, OSError):
            self._send_json(503, {"status": "redis_unavailable"})
            return

        self.send_response(status_code)
        self.send_header("Set-Cookie", self._build_session_cookie(sid))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _session_key(self, sid: str) -> str:
        return f"sid:{sid}"

    def _session_exists(self, sid: str) -> bool:
        return bool(self.redis_client.exists(self._session_key(sid)))

    def _create_new_session(self, now: str) -> str:
        create_session_script = """
        local key = KEYS[1]
        local created_at = ARGV[1]
        local updated_at = ARGV[2]
        local ttl = tonumber(ARGV[3])

        if redis.call('EXISTS', key) == 1 then
            return 0
        end

        redis.call('HSET', key, 'created_at', created_at, 'updated_at', updated_at)
        redis.call('EXPIRE', key, ttl)
        return 1
        """

        for _ in range(64):
            sid = secrets.token_hex(16)
            key = self._session_key(sid)
            created = self.redis_client.eval(create_session_script, 1, key, now, now, self.session_ttl)
            if int(created) == 1:
                return sid
        raise RuntimeError("failed to allocate session id")

    def _refresh_session(self, sid: str, now: str) -> None:
        key = self._session_key(sid)
        pipe = self.redis_client.pipeline()
        pipe.hset(key, mapping={"updated_at": now})
        pipe.expire(key, self.session_ttl)
        pipe.execute()

    def _extract_sid_cookie(self):
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None

        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def _build_session_cookie(self, sid: str) -> str:
        return f"{SESSION_COOKIE_NAME}={sid}; HttpOnly; Path=/; Max-Age={self.session_ttl}"

    def _send_json(self, status_code: int, payload: dict, extra_headers=None) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(server_class=HTTPServer, handler_class=HealthCheckHandler):
    app_host = os.getenv("APP_HOST", "localhost")
    port = int(os.getenv("APP_PORT", "8080"))
    raw_ttl = os.getenv("APP_USER_SESSION_TTL", "60")
    session_ttl = int(str(raw_ttl).strip().split()[0])
    redis_host = os.getenv("REDIS_HOST", "redis")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD", "")
    redis_db = int(os.getenv("REDIS_DB", "0"))

    client = redis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password or None,
        db=redis_db,
        decode_responses=True,
    )

    handler_class.redis_client = client
    handler_class.session_ttl = session_ttl

    server_address = (app_host, port)
    httpd = server_class(server_address, handler_class)
    print(f"Listening on {app_host}:{port} (open http://{app_host}:{port})")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
