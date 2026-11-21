from __future__ import annotations

import re

_IP_OK = re.compile(r"^[0-9a-fA-F:.]{3,45}$")


def client_ip(request, trust_proxy: bool = True) -> str:
    """Best-effort client address for rate limiting.

    With `trust_proxy` the left-most X-Forwarded-For entry is used (a reverse proxy such as Caddy,
    nginx or Koyeb's edge sets it). Without it, only the socket peer is trusted, so a client that
    connects directly cannot pick its own address by sending the header.
    """
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        first = fwd.split(",")[0].strip() if fwd else ""
        if first and _IP_OK.match(first):
            return first
    return (request.client.host if request.client else "unknown")[:45]
