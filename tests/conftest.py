import pytest

from liara_agent import domains


@pytest.fixture(autouse=True)
def no_dns_lookups(monkeypatch):
    """route_target() asks DNS for a canonical name; tests must never touch the network."""
    monkeypatch.setattr(domains, "route_target", lambda app: "")
