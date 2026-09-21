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
| 5. Close chain (Qualification → Sales → Proposal → Customer) | **complete** — 314 tests, ruff + mypy strict green |
| 6. CEO dashboard (web) | **complete** — 342 tests, ruff + mypy strict green |
| 7. Simulated end-to-end test | **complete** — 343 tests, ruff + mypy strict green |
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
replies" now run for real (on mock providers) and drive a lead all the way from discovery
through outreach, a reply, qualification, a priced proposal, a second reply, and — with
the CEO's approval — a signed customer. "check for replies" is run twice in that path: once
to pick up the reply to the outreach email, and again, later, to pick up the reply to the
proposal — the same command each time, since checking the inbox isn't tied to which stage
a conversation is in. Commands needing Fulfillment, Customer Success, Finance, Marketing,
or Analytics still come back `BLOCKED` naming the agent that would handle it, not a fake
success.

**Everything that touches the outside world is currently mocked.** No email is sent, no
lead source is queried, no payment is taken. Mock providers are labeled `MOCK` in their
class names, their logs, and their output, and every row they produce is stored with
`is_mock = true`.

## Web dashboard

```bash
# Terminal 1 — the CEO-facing API and dashboard
uvicorn api.main:app --reload

# Terminal 2 — the worker. Nothing the dashboard does actually happens without this
# running: the API only enqueues commands and resumes approved tasks, it never executes
# an agent itself (ARCHITECTURE.md §2 — "the worker is the only component that executes
# agents").
python -m worker.runner
```

Open `http://127.0.0.1:8000/` for the dashboard: business metrics, the pending approval
queue with one-click approve/reject, a box to submit a CEO command, and the emergency
stop. The same operations are also a plain JSON API (`POST /commands`, `GET /approvals`,
`POST /approvals/{id}/approve`, `POST /system/stop`, ...) for anything other than a human
in a browser — `GET /health` reports database connectivity and the current safety
posture. There is no authentication yet: this is a trusted-network tool for a one-person
company, not a public-facing service, and every request accepts a plain "who did this"
actor field the same way the CLI's `--as` flag does.

The CLI (`cli/ceo.py`) still works and talks to the same database — use whichever is
convenient; neither is more authoritative than the other.

## Safety posture

Two independent switches must both be thrown before anything real happens:

1. `DRY_RUN=false` in the environment, and
2. the specific action type set to `autonomous` in `config/policies.yaml`.

For outreach and proposals specifically there's a third, structural gate that policy
config can't override: every message and every proposal is LLM-written, never a
CEO-approved template, so sending either always requires a human's approval regardless of
the autonomy setting — that setting exists for a future templated-send capability this
system doesn't have yet. Creating a customer record is approval-required the ordinary way
(policy default, not a forced override), since it's the one step in the close chain that
isn't itself an outbound message.

On top of that: suppression/do-not-contact checks, per-campaign and global daily send
limits, per-prospect cooldowns (cold outreach only — a proposal sent in direct response to
the prospect's own reply doesn't compete for the same anti-spam budget), duplicate
prevention via idempotency keys, a campaign kill switch, and a global emergency stop — all
re-checked at the moment of send, not just when a draft was first approved, so a
suppression or kill switch that arrives in between still blocks it.

## AI UGC Viral Cloner

A separate, standalone capability: turns a viral short-form video into a production-ready
ad (script, shot list, generation prompts) for the CEO's own locked AI avatar clone — never
a substitute presenter. File-based, no database, not yet wired into the agent runtime
above. See [ugc_cloner/README.md](ugc_cloner/README.md) for the pipeline, the avatar lock,
and what's real vs. stubbed (video/voice rendering).

```bash
python -m ugc_cloner.cli init-avatar
python -m ugc_cloner.cli build --product "..." --objective "..." --description "..."
```

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
| `api/` | FastAPI: JSON routes (`routes/`) plus the server-rendered dashboard (`routes/dashboard.py`, `templates/`) |
| `worker/` | The process that leases tasks and runs agents |
