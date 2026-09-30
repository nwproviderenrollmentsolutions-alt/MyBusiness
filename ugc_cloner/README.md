# AI UGC Viral Cloner

Turns a viral short-form video into a production-ready ad script, shot list, and
generation prompts for the CEO's own locked AI avatar. Lives alongside the rest of
`MyBusiness` but is deliberately **not** wired into the Postgres-backed agent
runtime (`core/agent_base.py`, the task queue, the worker) — see "Why standalone"
below.

## The one rule that matters

The avatar is locked. This pipeline refuses to invent, substitute, or reinterpret the
presenter. If `avatar/avatar-profile.md` isn't filled in and locked, every code path that
would generate a presenter raises `ugc_cloner.avatar.AvatarLockError` with:

> AVATAR REQUIRED: The approved AI clone reference is missing. Upload or connect the
> user's approved avatar reference before generating the presenter.

See `avatar/UPLOAD_INSTRUCTIONS.md` for exactly what to upload and the recommended
vendor pairing (HeyGen for video, ElevenLabs for voice) to go from locked-but-mocked to
actually rendering.

`ugc_cloner/qc.py` checks this mechanically on every run, too: it's a blocker if any
generated prompt doesn't name the locked avatar or is missing one of its negative
constraints (`different person`, `stock actor`, `celebrity likeness`, ...).

## Quickstart

```bash
python -m ugc_cloner.cli init-avatar          # scaffolds avatar/, writes the profile template
# ... follow avatar/UPLOAD_INSTRUCTIONS.md: reference photo/video, voice sample, profile ...
python -m ugc_cloner.cli avatar-status        # confirms the lock is satisfied

python -m ugc_cloner.cli build \
  --product "Replit" \
  --objective "drive signups" \
  --category "ai coding tools" \
  --description "@transcript.txt" \
  --out replit-launch

python -m ugc_cloner.cli render viral-library/packages/replit-launch.json
```

`build` writes `viral-library/packages/<slug>.json` (the full `ProductionPackage`) and a
matching `.md` summary. `render` sends each generation prompt to the configured video/voice
provider — today that's always the mock (see "What's stubbed").

## Pipeline

```
source video (description/transcript, optionally a manual shot breakdown)
  -> video_analysis.py    Phase 1: hook, timeline, shots, editing metrics, performance style
  -> research.py           Phase 2/3: viral research + framework extraction (OBSERVED/INFERRED/UNKNOWN)
  -> blueprint.py          Phase 4/5: rule-based creative scoring + the creative blueprint
  -> avatar.py             CORE RULE: require_avatar() — raises AvatarLockError if not locked
  -> script.py              Phase 6: script adaptation to product/brand, avatar's voice
  -> shots.py               Phase 7/8: shot-by-shot plan + vendor-agnostic generation prompts
  -> qc.py                  Phase 9: avatar-lock + fabrication checks
  -> library.py              Phase 10: ProductionPackage written to viral-library/
```

`pipeline.py` runs all of this end to end; each module also works standalone (e.g. call
`video_analysis.analyze_video()` on its own to just get a breakdown).

## Evidence labeling

Per the spec, nothing here states a metric as fact unless it was actually observed.
Every measurable field carries an `Evidence` tag: `OBSERVED` (a person supplied real
shot-by-shot data, or a real, non-mock provider returned it), `INFERRED` (an LLM or a
real search result implied it, unverified), or `UNKNOWN`. The mock web-research and mock
LLM providers can never produce `OBSERVED` — see `research.py` and `qc.py`.

## What's real vs. stubbed

| Piece | Status |
|---|---|
| Avatar lock, profile loading, reference-asset detection | Real, file-based |
| Video analysis from a manual shot breakdown | Real (pure arithmetic, no LLM needed) |
| Video analysis from free text (hook classification, performance style) | Real when `LLM_PROVIDER=anthropic` is set (reuses `providers/anthropic_llm.py`); UNKNOWN with the default mock |
| Viral research | Real search when a real `WebResearchProvider` exists — **none is implemented yet**, only `mock` (see `providers/factory.py`); always returns the spec's three seed frameworks, labeled `UNKNOWN`, until one is |
| Creative scoring | Real, rule-based (same approach as `agents/lead_scoring/`) |
| Script adaptation | Real copy when `LLM_PROVIDER=anthropic`; a clearly labeled stub marker otherwise |
| Shot plan, generation prompts, voice prompts | Real — pure templating, avatar lock baked into every prompt |
| Video rendering | Real when `VIDEO_GEN_PROVIDER=heygen` and `HEYGEN_API_KEY`/`HEYGEN_AVATAR_ID` are set (`providers/heygen_video_gen.py`); mock queues nothing otherwise |
| Voice synthesis | Real when `VOICE_PROVIDER=elevenlabs` and `ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID` are set (`providers/elevenlabs_voice.py`); mock synthesizes nothing otherwise |

## Why standalone (not a `core.agent_base.Agent`)

The rest of this repo's agents are bounded functions that read/write Postgres rows through
a task queue a worker drains (`ARCHITECTURE.md`). This pipeline has no state that needs a
database: the avatar profile and the viral library are both files, matching the spec's own
design (`avatar/avatar-profile.md` as "the source of truth"). Wrapping it in the `Agent`
ABC would mean inventing DB tables and task-queue wiring this content-generation workflow
doesn't need yet. Wiring it into the task queue/worker (so a CEO command can trigger a
build, or an approval gate can review a package before `render`) is a natural follow-up
once the creative pipeline itself has been used for real — see `ARCHITECTURE.md` §3,
which lists Marketing as a deferred domain "added as new agent modules ... [requiring] no
changes to the task queue, event bus, policy engine, approval gate, audit log, or agent
contract" when that day comes.

## Tests

`ugc_cloner/tests/` — no database needed (see `pyproject.toml`'s `testpaths`):

```bash
pytest ugc_cloner
```
