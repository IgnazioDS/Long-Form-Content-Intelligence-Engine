from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolved_host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {info[4][0] for info in infos}
    if not ips:
        return False
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if not _is_public_ip(ip):
            return False
    return True


def is_url_safe(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in _BLOCKED_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return _resolved_host_is_public(normalized)
    return _is_public_ip(ip)
