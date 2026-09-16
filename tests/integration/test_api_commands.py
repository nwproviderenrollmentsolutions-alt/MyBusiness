from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db.enums import CommandStatus, TaskStatus
from db.models.command import CeoCommand
from db.models.runtime import AgentTask
from db.session import session_scope

pytestmark = pytest.mark.integration


def test_submitting_a_command_only_enqueues_it(client):
    """The API never executes agents (that's the worker's job) — a freshly submitted
    command must come back pending, with a chief_of_staff task queued but not run."""
    response = client.post("/commands", json={"text": "show status", "created_by": "sam"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == CommandStatus.PENDING.value
    assert body["created_by"] == "sam"
    assert body["response"] is None
    command_id = body["id"]

    with session_scope() as session:
        command = session.get(CeoCommand, uuid.UUID(command_id))
        assert command is not None
        assert command.status is CommandStatus.PENDING

        task = session.scalar(
            select(AgentTask).where(AgentTask.dedupe_key == f"ceo_command:{command_id}")
        )
        assert task is not None
        assert task.agent == "chief_of_staff"
        assert task.status is TaskStatus.PENDING


def test_submitting_a_command_defaults_the_actor_to_ceo(client):
    response = client.post("/commands", json={"text": "show status"})
    assert response.json()["created_by"] == "ceo"


def test_blank_command_text_is_rejected(client):
    response = client.post("/commands", json={"text": ""})
    assert response.status_code == 422


def test_list_commands_returns_most_recent_first(client):
    client.post("/commands", json={"text": "first"})
    client.post("/commands", json={"text": "second"})

    response = client.get("/commands")
    assert response.status_code == 200
    texts = [row["raw_text"] for row in response.json()]
    assert texts == ["second", "first"]


def test_get_command_resolves_after_the_worker_processes_it(client):
    from core.registry import AGENTS
    from worker import runner

    created = client.post("/commands", json={"text": "show status"}).json()

    with session_scope() as session:
        task = session.scalar(
            select(AgentTask).where(AgentTask.dedupe_key == f"ceo_command:{created['id']}")
        )
        task_id = task.id
    assert "chief_of_staff" in AGENTS.names()
    runner.execute_task(task_id, worker_id="test-worker")

    response = client.get(f"/commands/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == CommandStatus.ANSWERED.value
    assert body["response"] is not None


def test_get_unknown_command_is_404(client):
    response = client.get(f"/commands/{uuid.uuid4()}")
    assert response.status_code == 404
