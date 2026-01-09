from __future__ import annotations

import socket
from typing import Any

from packages.shared_db.url_guard import is_url_safe


def _fake_getaddrinfo(
    host: str, *_: Any, **__: Any
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def test_public_ip_allowed() -> None:
    assert is_url_safe("https://93.184.216.34")


def test_private_ip_blocked() -> None:
    assert not is_url_safe("http://127.0.0.1")


def test_allowlist_blocks_nonlisted_host(monkeypatch: Any) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert not is_url_safe(
        "https://other.example.com", allowed_hosts={"example.com"}
    )


def test_allowlist_allows_subdomain(monkeypatch: Any) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert is_url_safe("https://api.example.com", allowed_hosts={"*.example.com"})


def test_allowlist_allows_ip(monkeypatch: Any) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert is_url_safe("https://93.184.216.34", allowed_hosts={"93.184.216.34"})
