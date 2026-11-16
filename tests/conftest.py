"""Suite-wide guard: tests never touch the network (CI must be deterministic and
polite to gpw.pl). Any attempt to open a socket fails loudly."""

import socket

import pytest


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise NetworkBlocked("network access is not allowed in tests")
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
