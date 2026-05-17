from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
)

RATE_LIMIT_PER_MINUTE = "10/minute"
RATE_LIMIT_PER_HOUR = "100/hour"
