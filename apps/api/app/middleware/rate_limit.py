"""Rate limiting for auth endpoints.

Uses slowapi to prevent brute-force attacks on login/register.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
