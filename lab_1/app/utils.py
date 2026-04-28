from __future__ import annotations

import re
from datetime import datetime, timezone


def utc_now_rfc3339() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_ttl(raw: str) -> int:
    return int(str(raw).strip().split()[0])


def parse_yyyymmdd(s: str) -> datetime | None:
    if len(s) != 8 or not re.fullmatch(r"[0-9]{8}", s):
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_objectid_hex(s: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{24}", s))


def parse_rfc3339_datetime(s: str) -> tuple[datetime | None, str | None]:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid"
    if dt.tzinfo is None:
        return None, "invalid"
    return dt, None

