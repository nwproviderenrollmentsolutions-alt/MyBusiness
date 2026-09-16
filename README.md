# MyBusiness — AI-native one-person company OS

An operating system for running a company where AI agents do the work and the CEO
intervenes only for approvals, exceptions, and judgment calls.

**Objective: first revenue.** See [ARCHITECTURE.md](ARCHITECTURE.md) for the design,
the state machines, the security posture, and the milestone plan.

## Status

| Milestone | State |
|---|---|
| 1. Core infrastructure | **complete** — 143 tests, ruff + mypy strict green |
| 2. Agent runtime + Chief of Staff | not started |
| 3–5. Revenue vertical slice | not started |
| 6. CEO dashboard + command interface | not started |
| 7. Simulated end-to-end test | not started |
| 8. Real providers | not started |

**Everything that touches the outside world is currently mocked.** No email is sent, no
lead source is queried, no payment is taken. Mock providers are labeled `MOCK` in their
class names, their logs, and their output, and every row they produce is stored with
`is_mock = true`.

## Safety posture

Two independent switches must both be thrown before anything real happens:

1. `DRY_RUN=false` in the environment, and
2. the specific action type set to `autonomous` in `config/policies.yaml`.

On top of that: suppression/do-not-contact checks, per-campaign and global daily send
limits, per-prospect cooldowns, duplicate prevention via idempotency keys, a campaign
kill switch, and a global emergency stop.

## Setup

```bash
# Dependencies
uv pip install --system -e ".[dev]"

# Database (docker)
docker compose up -d postgres

# Database (no docker — e.g. a dev container with Postgres installed locally)
./scripts/devdb.sh start

# Schema
alembic upgrade head

# Tests
pytest                       # unit tests
pytest -m integration        # requires a live database
ruff check . && python3 -m mypy .
```

Copy `.env.example` to `.env` first. `.env` is git-ignored and is the only place secrets
live; agents never receive them and they never enter an LLM prompt.

## Layout

| Path | What lives there |
|---|---|
| `config/` | Settings loader, `policies.yaml` (CEO-editable), `agents.yaml` (wiring) |
| `db/` | SQLAlchemy models, enums, Alembic migrations |
| `core/` | Task queue, event outbox, policy engine, approval gate, audit log, registries |
| `providers/` | Swappable external-world adapters; `providers/mock/` is the default |
| `agents/` | One package per agent, each with its own manifest and tests |
| `api/` | FastAPI: CEO dashboard, command interface, approval queue |
| `worker/` | The process that leases tasks and runs agents |
