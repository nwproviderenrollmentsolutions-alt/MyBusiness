# Viral library

Output of the AI UGC Viral Cloner (`ugc_cloner/`, see its README).

- `frameworks/` — reusable viral structures extracted from research (`ugc_cloner.library.save_framework`).
  Each file's `evidence` field says whether it came from real research (`INFERRED`) or is
  a seed pattern with no live signal behind it yet (`UNKNOWN`) — see
  `ugc_cloner/research.py`.
- `packages/` — one `<slug>.json` (the full `ProductionPackage`) and matching `<slug>.md`
  (human-readable summary) per `ugc_cloner.cli build` run.

Nothing in this directory is committed as "real" performance data — check each item's
`evidence` field before treating anything here as an observed fact rather than a
reusable pattern.
