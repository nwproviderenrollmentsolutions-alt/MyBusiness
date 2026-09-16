# MyBusiness — AI-native one-person company OS

An operating system for running a company where AI agents do the work and the CEO
intervenes only for approvals, exceptions, and judgment calls.

**Objective: first revenue.** See [ARCHITECTURE.md](ARCHITECTURE.md) for the design,
the state machines, the security posture, and the milestone plan.

## Status

| Milestone | State |
|---|---|
| 1. Core infrastructure | **complete** — 143 tests, ruff + mypy strict green |
| 2. Agent runtime + Chief of Staff | **complete** — 176 tests, ruff + mypy strict green |
| 3. Discovery chain (Opportunity → Lead Discovery → Enrichment → Scoring) | **complete** — 226 tests, ruff + mypy strict green |
| 4. Outreach chain (Outreach + Conversation Management) | **complete** — 265 tests, ruff + mypy strict green |
| 5. Close chain (Qualification → Sales → Proposal → Customer) | not started |
| 6. CEO dashboard (web) | not started — a CLI (`cli/ceo.py`) covers the CEO interface for now |
| 7. Simulated end-to-end test | not started |
| 8. Real providers | not started |

## CEO command line

```bash
python -m cli.ceo command "what's our status?"
python -m cli.ceo commands              # recent command history
python -m cli.ceo approvals             # pending approval queue
python -m cli.ceo approve <id> --notes "looks good"
python -m cli.ceo reject <id> --notes "not yet"
python -m cli.ceo stop                  # global emergency stop — bypasses everything else
python -m cli.ceo go                    # release the emergency stop
python -m cli.ceo work                  # drain the task queue once
```

"find our best market opportunity", "find N qualified <segment> leads", and "check for
replies" now run for real (on mock providers) — the latter two drive leads all the way
through drafting, human approval, sending, and reply handling. Commands needing
Qualification, Sales, or Proposal still come back `BLOCKED` naming the agent that would
handle it, not a fake success.

**Everything that touches the outside world is currently mocked.** No email is sent, no
lead source is queried, no payment is taken. Mock providers are labeled `MOCK` in their
class names, their logs, and their output, and every row they produce is stored with
`is_mock = true`.

## Safety posture

Two independent switches must both be thrown before anything real happens:

1. `DRY_RUN=false` in the environment, and
2. the specific action type set to `autonomous` in `config/policies.yaml`.

For outreach specifically there's a third, structural gate that policy config can't
override: every message Outreach drafts is LLM-written, never a CEO-approved template, so
it always requires a human's approval regardless of the autonomy setting — that setting
exists for a future templated-send capability this system doesn't have yet.

On top of that: suppression/do-not-contact checks, per-campaign and global daily send
limits, per-prospect cooldowns, duplicate prevention via idempotency keys, a campaign
kill switch, and a global emergency stop — all re-checked at the moment of send, not just
when a draft was first approved, so a suppression or kill switch that arrives in between
still blocks it.

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
