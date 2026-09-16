# MyBusiness — AI-Native One-Person Company OS

## 0. Primary objective

**First revenue, not a platform.** Every milestone is judged by whether it moves a real
prospect closer to a signed deal. Infrastructure exists only to make that path safe,
auditable, and repeatable. Breadth (fulfillment, finance, marketing, CS) comes after the
first revenue vertical works end to end.

The optimization target is **qualified conversations and revenue**, never message volume.
Any design choice that increases send volume at the cost of relevance is wrong by default.

## 1. Principles

1. **Agents are bounded functions**, not autonomous loops. `run(input) -> output`, with a
   declared input schema, output schema, tool allowlist, permissions, timeout, and retry policy.
2. **No agent calls another agent directly.** Coordination happens through the task queue,
   domain events, and shared database rows. This is what makes an agent replaceable.
3. **The LLM never holds tool access.** A model proposes a structured action; deterministic
   code validates it against the policy engine and executes it through the tool registry.
   There is no path from model output to an external side effect that skips policy.
4. **External actions are guilty until proven innocent.** Outbound communication defaults to
   human approval until the CEO explicitly enables autonomy for that action type.
5. **Everything is auditable.** Every state change, decision, approval, and tool call lands in
   an append-only audit log with the actor, before/after state, and the task that caused it.
6. **Mocks are labeled.** A mocked provider says `MOCK` in its name, its logs, its returned
   payloads, and its stored rows. No simulated result may be mistakable for real-world data.

## 2. System overview

```
CEO  ── commands / goals / approvals ──┐
                                       ▼
                            Chief of Staff (orchestrator)
                                       │ creates
                                       ▼
                        agent_tasks (Postgres queue)
                                       │ leased by
                                       ▼
                   Worker ── dispatch ──> Agent.run(input, ctx)
                                       │                │
                                       │                ├── tool_registry (allowlist enforced)
                                       │                │        └── providers (MOCK today)
                                       │                └── policy_engine (allow/approve/deny)
                                       │
            ┌──────────────────────────┼───────────────────────────┐
            ▼                          ▼                           ▼
      agent_runs                outbox events               approvals (CEO decides)
   (tokens, cost, timing)   (dispatched to subscribers)            │
            │                          │                           ▼
            └────────────> audit_logs (append-only) <──────────────┘
```

The worker is the only component that executes agents. The API serves the CEO dashboard,
the CEO command interface, and the approval queue. Both talk to the same Postgres.

## 3. Scope

### Revenue vertical slice (build now)

```
CEO → Chief of Staff → Opportunity Discovery → Lead Discovery → Lead Enrichment →
Lead Scoring → Outreach → Conversation Management → Qualification → Sales →
Proposal → Human Approval → Customer
```

### Deferred (must fit without rewrite)

Fulfillment, Customer Success, Finance, Marketing, Market Intelligence, Analytics beyond
basic KPIs. These are added as new agent modules plus their own tables. They require **no**
changes to the task queue, event bus, policy engine, approval gate, audit log, or agent
contract. That is the test of whether this architecture held up.

## 4. Directory structure

```
MyBusiness/
├── ARCHITECTURE.md
├── pyproject.toml
├── alembic.ini
├── docker-compose.yml           # postgres (local dev convenience)
├── .env.example
├── config/
│   ├── settings.py              # layered config: defaults -> yaml -> env
│   ├── policies.yaml            # approval thresholds, limits, autonomy switches
│   └── agents.yaml              # agent registry: module, version, subscriptions, limits
├── db/
│   ├── base.py                  # declarative base, shared column types
│   ├── session.py               # engine/session factory
│   ├── enums.py                 # explicit state enums (single source of truth)
│   ├── models/                  # one module per domain area
│   └── migrations/              # alembic
├── core/
│   ├── agent_base.py            # Agent protocol, AgentContext, AgentInput/Output
│   ├── registry.py              # agent registry (name/version -> implementation)
│   ├── task_queue.py            # enqueue, lease (SKIP LOCKED), complete, fail, retry
│   ├── event_bus.py             # transactional outbox + dispatcher
│   ├── policy.py                # policy engine: allow / require_approval / deny
│   ├── approval_gate.py         # approval request + resolution
│   ├── audit.py                 # append-only audit writer
│   ├── tool_registry.py         # per-agent tool permission enforcement
│   ├── suppression.py           # do-not-contact + duplicate/idempotency checks
│   ├── observability.py         # structured JSON logging, correlation ids
│   └── errors.py
├── providers/                   # swappable external-world adapters
│   ├── base.py                  # Protocols: LLM, WebResearch, LeadDiscovery, Email,
│   │                            #            CRM, Calendar, Payment
│   └── mock/                    # MOCK implementations (default in dev/test)
├── agents/                      # one package per agent (see §7)
├── api/                         # FastAPI: dashboard, CEO command, approvals, health
├── worker/
│   └── runner.py
├── tests/
│   ├── unit/
│   └── integration/
└── scripts/
```

## 5. Data model

### 5.1 Entities

| Table | Purpose | Key columns |
|---|---|---|
| `opportunities` | Business opportunities (market/offer level), **not** CRM deals | title, segment, thesis, estimated_value, status |
| `companies` | Target organizations | name, domain, industry, size_band, source, is_mock |
| `prospects` | People at companies (PII) | company_id, full_name, title, email, phone, source, is_mock |
| `leads` | An engagement attempt with a prospect | prospect_id, company_id, opportunity_id, status, score |
| `lead_scores` | Scoring history, never overwritten | lead_id, score, model_version, rationale, created_at |
| `campaigns` | Outreach program with its own kill switch | name, opportunity_id, channel, status, daily_send_limit |
| `conversations` | Threaded exchange with a prospect | lead_id, campaign_id, channel, status |
| `messages` | Individual inbound/outbound message | conversation_id, direction, state, body, idempotency_key |
| `deals` | Sales pipeline | lead_id, opportunity_id, stage, value, currency, probability |
| `proposals` | Versioned proposal documents | deal_id, version, state, content, sent_at |
| `customers` | Won and onboarded | company_id, deal_id, status, mrr |
| `agent_tasks` | The work queue | agent, input, status, priority, attempts, lease_expires_at, dedupe_key |
| `agent_runs` | Execution record + cost | task_id, agent, version, status, tokens_in/out, cost_usd, duration_ms, tool_calls |
| `approvals` | Human decision gate | action_type, subject_ref, payload, risk, status, decided_by, decided_at |
| `outbox_events` | Transactional domain events | event_type, subject_ref, payload, processed_at, attempts |
| `audit_logs` | Append-only history | actor_type, actor, action, subject_ref, before, after, task_id |
| `suppressions` | Do-not-contact list | value (email/domain/phone), scope, reason, source |
| `kpi_snapshots` | Computed metrics over time | metric, dimensions, value, period_start, computed_at |
| `system_flags` | Runtime switches (kill switch, autonomy) | key, value, updated_by, updated_at |

`is_mock` is a real column on every externally-sourced entity (`companies`, `prospects`,
`leads`, `messages`). Mock-provider data is written with `is_mock = true`, the dashboard
labels it, and KPI queries exclude it from revenue metrics. This is how requirement 22 is
enforced structurally rather than by convention.

### 5.2 Explicit state machines

The requested state list mixes two different lifecycles, so it is split (see §12, conflict 1):

**`ActionState`** — anything with an external-world effect (`messages`, `proposals`, and any
future outbound artifact):

```
draft ──> pending_approval ──> approved ──> executing ──> completed
  │              │                              │
  │              └──> rejected                  └──> failed
  └──> suppressed                                      (retry -> executing)
```

`do_not_contact` is a property of a **recipient**, not of a message. A message blocked by the
suppression list terminates in `suppressed`, and the lead moves to `do_not_contact`. Both
states exist; they just live on the entities they actually describe.

**`TaskStatus`** — queue-level lifecycle for `agent_tasks`:

```
pending -> leased -> running -> succeeded
                        │
                        ├─> awaiting_approval -> pending (approved) | cancelled (rejected)
                        └─> failed -> pending (retry, with backoff) | dead_letter
```

**`LeadStatus`**: `discovered → enriched → scored → contacted → engaged → qualified |
disqualified | do_not_contact`.
**`DealStage`**: `qualification → proposal → negotiation → won | lost`.
**`OpportunityStatus`**: `proposed → validating → approved → active → shelved`.

### 5.3 Ownership

Each table has exactly one owning agent for writes (declared in `config/agents.yaml`).
Others read. Enforced in application code now, and by Postgres role grants when the system
moves off a single-process deployment.

## 6. Task and event system

**Queue.** `agent_tasks` polled with `SELECT ... FOR UPDATE SKIP LOCKED`, leased with an
expiry so a crashed worker's task is recovered rather than lost. Retries use exponential
backoff with jitter up to `max_attempts`, then dead-letter for CEO visibility. A `dedupe_key`
unique index makes task creation idempotent — the same event cannot spawn duplicate work.

**Events.** Written to `outbox_events` in the *same transaction* as the state change that
caused them, then dispatched by the worker to subscribers declared in `config/agents.yaml`.
Dispatch creates new tasks. An emitting agent never knows who consumes its events, which is
precisely how a later Fulfillment agent subscribes to `deal.won` without touching Sales.

**Idempotency.** Three layers: `dedupe_key` on tasks, `idempotency_key` on outbound messages
(unique), and provider-level keys passed to external APIs so a retry after a timeout cannot
double-send.

## 7. Agent contract

Every agent package contains `agent.py`, `schemas.py`, `manifest.py`, `prompts.py` (if it
uses an LLM), and `tests/`. The manifest is data, not prose:

```python
class AgentManifest(BaseModel):
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    allowed_tools: list[str]           # enforced by tool_registry at call time
    allowed_actions: list[ActionType]  # permitted without approval (subject to policy)
    approval_required_actions: list[ActionType]
    owns_tables: list[str]
    subscribes_to: list[str]           # event types
    timeout_seconds: int
    max_attempts: int
    retry_backoff_seconds: float
```

```python
class AgentOutput(BaseModel):
    status: Literal["success", "failed", "needs_approval"]
    result: dict | None = None
    events: list[EventRequest] = []
    follow_up_tasks: list[TaskRequest] = []
    approval_request: ApprovalRequest | None = None
    error: AgentError | None = None
    usage: UsageRecord | None = None   # tokens, cost, tool calls
```

The worker — not the agent — persists runs, emits events, enqueues follow-ups, writes audit
entries, and opens approvals. Agents return intent; the runtime performs effects. An agent
that crashes mid-way cannot leave a half-committed side effect.

## 8. Policy engine and permissions

`policy.evaluate(action_type, context) -> Decision(allow | require_approval | deny | simulate, reason)`.

`simulate` is a separate outcome from `allow` on purpose: under dry run an action may be
fully authorized and still must not execute, and no caller should be able to read "would
have been allowed" as permission to act. The decision also carries the action type it
authorized, which the tool registry checks — so an authorization for `lead.score` cannot
be laundered into permission to send email.

Dry run simulates only **world-changing** actions (`WORLD_CHANGING_ACTIONS` in
`db/enums.py`). Research and enrichment still execute, because dry run means nobody is
contacted, not that the system stops thinking.

Evaluated in order, first match wins:

1. **Global kill switch** (`system_flags.emergency_stop`) → deny all external actions.
2. **Campaign kill switch** (`campaigns.status = killed`) → deny for that campaign.
3. **Dry run** (global or per-task) → deny real execution, allow simulation.
4. **Suppression / do-not-contact** → deny, terminate message as `suppressed`.
5. **Duplicate / idempotency conflict** → deny.
6. **Rate limits** (per campaign per day, per prospect cooldown, global daily cap) → deny or defer.
7. **Autonomy switch** for the action type — default `require_approval` for every external
   communication until the CEO explicitly flips it in `policies.yaml` or `system_flags`.
8. **Thresholds** (deal value, discount %, proposal value) → `require_approval` above limit.
9. Otherwise → `allow`.

Every evaluation is logged with its reason, including allows. The CEO can answer "why did this
message go out?" from the audit log alone.

## 9. Human approval

A gated action creates an `approvals` row with the full proposed payload, a plain-language
summary, risk level, and links to the subject entity. The originating task moves to
`awaiting_approval` and stops consuming worker capacity. Approve → task re-queued with the
approval attached, action executes, audit records the human decider. Reject → task cancelled,
artifact moves to `rejected`, an event fires so the agent can adapt. Optional per-action-type
expiry with a configurable default (default: expire to rejected, never auto-send).

Edit-and-approve is supported: the CEO may modify the payload, and the executed action is the
edited version, with both versions retained in the audit trail.

## 10. Dry run

`dry_run` is set globally in config and can be overridden per task. In dry run agents may
research, score, generate copy, draft proposals, and simulate sends. Provider adapters receive
the flag and return simulated results tagged `MOCK`/`SIMULATED`; the outbound execution path
refuses real sends outright rather than relying on provider politeness. Dry-run artifacts are
persisted normally (so the CEO can review what *would* have happened) with `is_mock = true`.

**Default posture: dry run ON, autonomy OFF.** Real sending requires two deliberate acts —
disabling dry run and enabling autonomy for that action type.

## 11. Providers

Protocols in `providers/base.py`, MOCK implementations first, selected by config:
`LLMProvider`, `WebResearchProvider`, `LeadDiscoveryProvider`, `EmailProvider`, `CRMProvider`,
`CalendarProvider`, `PaymentProvider`. Swapping to a real vendor is a config change plus one
new module; no agent code changes. Mock providers are deterministic (seeded) so tests are
reproducible, and every record they produce is flagged `is_mock`.

## 12. Architectural conflicts identified

1. **State list conflates two lifecycles.** `draft/pending_approval/approved/executing/
   completed/failed/rejected/suppressed/do_not_contact` describes outbound artifacts, but
   `executing/completed/failed` also describe queue tasks, and `do_not_contact` describes a
   person. Resolved in §5.2 by splitting into `ActionState`, `TaskStatus`, and `LeadStatus`
   rather than forcing one enum to mean three things.
2. **"Opportunity" is overloaded.** Business opportunity (Opportunity Discovery) vs. CRM
   opportunity (a deal). Kept as separate tables `opportunities` and `deals`.
3. **Audit Logging as an "agent".** It is cross-cutting infrastructure invoked by the runtime,
   not an LLM agent with a task queue. Implemented as `core/audit.py`. Listing it in the agent
   registry would imply it can be scheduled and fail like a worker, which would be a liability
   for the one component that must never silently drop a write.
4. **Compliance as an "agent".** Same reasoning: the blocking decision path is the
   deterministic policy engine (`core/policy.py`), because an LLM must not be the thing that
   decides whether an LLM's action is allowed. A future *advisory* Compliance agent can review
   and flag content, but it cannot be the gate.
5. **Chief of Staff could become a bottleneck or a god-agent.** It only translates CEO
   commands into tasks and escalates exceptions. It does not sit in the path of routine
   agent-to-agent flow, which moves via events.

## 13. Security and compliance risks

| Risk | Mitigation |
|---|---|
| **Prompt injection from scraped/inbound content** — web research and prospect replies are untrusted text entering LLM context | Untrusted content is wrapped and labeled as data in prompts; model output is parsed into a typed schema, never executed as instructions; tool access is out-of-band from the model; any state transition an LLM proposes passes the policy engine |
| **PII** (prospect names, emails, phones) | PII columns identified in models, access audited, retention and deletion configurable; PII excluded from LLM prompts unless the task requires it |
| **CAN-SPAM / GDPR / CASL** for outbound email | Suppression list checked before every send, unsubscribe handling, sender identity and physical address in templates, per-day caps, full audit trail of what was sent to whom and why |
| **Runaway spend** (LLM tokens, paid data APIs) | Per-run cost recorded, per-agent and global daily cost caps in policy, kill switch |
| **Runaway sending** | Default approval-required, dry run default on, campaign + global kill switches, rate limits, duplicate prevention |
| **Secret leakage** | Secrets only in env, loaded by providers; never passed into agent inputs, LLM prompts, or audit payloads; `.env` git-ignored |
| **Audit tampering** | No UPDATE/DELETE path in application code; Postgres grants restrict the same once deployed with separate roles |
| **Payments** | Not integrated. Mock only. Real payment execution will require approval regardless of autonomy settings |

## 14. Testing strategy

- **Unit**: pure logic — policy boundaries, state transitions, suppression matching, backoff math.
- **Contract**: generic test walks the agent registry and asserts every agent declares schemas,
  manifest fields, timeout, retry policy, and version.
- **Integration** (real Postgres): queue concurrency (`SKIP LOCKED` under parallel workers),
  lease expiry recovery, retry/backoff, outbox exactly-once dispatch, approval pause/resume,
  audit completeness.
- **Simulated end-to-end**: opportunity → company → prospect → lead → research → score →
  outreach → reply → qualification → deal → proposal → approval, entirely on mock providers,
  asserting no real external call occurs and every step is audited.
- **Gates**: `ruff` (lint), `mypy` (types), `pytest` — all must pass before a milestone closes.

## 15. Milestones

| # | Milestone | Acceptance criteria |
|---|---|---|
| ~~1~~ | ~~**Core infrastructure**~~ | **Done.** Models + migration applied; queue leases/retries/recovers under concurrency; outbox dispatches once; policy engine correct at boundaries; approval gate pauses and resumes a task; tool registry denies unlisted tools; audit log captures every transition; 143 tests + ruff + mypy green |
| 2 | Agent runtime + Chief of Staff | Worker executes registered agents with timeout/retry/cost tracking; CEO command translated into tasks |
| 3 | Discovery chain | Opportunity Discovery → Lead Discovery → Enrichment → Scoring on mock providers |
| 4 | Outreach chain | Outreach drafts → approval → simulated send; Conversation Management handles a mock reply |
| 5 | Close chain | Qualification → Sales → Proposal → approval → Customer |
| 6 | CEO surface | Dashboard metrics, approval queue, CEO command interface, kill switches |
| 7 | Simulated end-to-end test | Full slice green on mocks, nothing real sent |
| 8 | Real providers | Swap mocks one at a time behind the same interfaces |

No milestone starts before the previous one's tests pass.
