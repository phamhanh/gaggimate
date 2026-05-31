#!/usr/bin/env python3
"""HTTP helpers for Gaggimate device access (stdlib only)."""

from __future__ import annotations

import socket
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def resolve_host(host: str, port: int = 80) -> str:
    """Resolve hostname once; return IP string (avoids slow mDNS per request)."""
    try:
        socket.inet_pton(socket.AF_INET, host)
        return host
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return host
    except OSError:
        pass

    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        if infos:
            return infos[0][4][0]

    raise OSError(f"Could not resolve {host!r}")


def http_base_url(host: str, port: int = 80) -> str:
    ip = resolve_host(host, port)
    if port == 80:
        return f"http://{ip}"
    return f"http://{ip}:{port}"


def http_get_bytes(url: str, timeout: float) -> bytes | None:
    try:
        with urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read()
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    except URLError:
        raise
