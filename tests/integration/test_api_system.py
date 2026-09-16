from __future__ import annotations

import pytest

from core import flags
from db.session import session_scope

pytestmark = pytest.mark.integration


def test_stop_engages_the_emergency_stop(client):
    response = client.post("/system/stop", json={"actor": "ceo", "reason": "testing"})
    assert response.status_code == 200
    assert response.json() == {"emergency_stop": True}

    with session_scope() as session:
        assert flags.is_emergency_stopped(session) is True


def test_go_releases_the_emergency_stop(client):
    client.post("/system/stop", json={"actor": "ceo"})

    response = client.post("/system/go", json={"actor": "ceo"})
    assert response.status_code == 200
    assert response.json() == {"emergency_stop": False}

    with session_scope() as session:
        assert flags.is_emergency_stopped(session) is False


def test_stop_defaults_actor_and_reason(client):
    response = client.post("/system/stop", json={})
    assert response.status_code == 200
    assert response.json()["emergency_stop"] is True
