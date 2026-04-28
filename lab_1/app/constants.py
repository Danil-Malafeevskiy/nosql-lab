import re


SESSION_COOKIE_NAME = "X-Session-Id"
MAX_BODY = 1_000_000
EVENT_CATEGORIES = frozenset({"meetup", "concert", "exhibition", "party", "other"})
OBJECT_ID_HEX = re.compile(r"^[a-f0-9]{24}$")

