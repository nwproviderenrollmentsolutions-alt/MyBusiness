# Viral library

Output of the AI UGC Viral Cloner (`ugc_cloner/`, see its README), structured per the
spec's Phase 10 ("Viral Format Library").

- `frameworks/` — reusable viral structures: the overall hook-to-CTA shape of a format.
  Contains a `_template.yaml` (the spec's YAML schema — copy it to start a new entry)
  plus the three seed frameworks named in the spec: `i-didnt-expect-this.yaml`,
  `stop-doing-x.yaml`, `i-tried-it.yaml`. The pipeline also writes here at runtime via
  `ugc_cloner.library.save_framework`, as `<slug>.json` (the `ViralFramework` model).
- `hooks/`, `UGC/`, `talking-head/`, `product-demo/`, `storytelling/`, `problem-solution/`,
  `reaction/`, `testimonial/`, `before-after/` — per-category folders for finer-grained
  patterns filed under a specific format as research accumulates (e.g. a reusable opening
  line goes in `hooks/`, a testimonial-style structure in `testimonial/`). Empty for now
  aside from a `.gitkeep`; use the same `frameworks/_template.yaml` schema for entries
  saved here.
- `packages/` — one `<slug>.json` (the full `ProductionPackage`) and matching `<slug>.md`
  (human-readable summary) per `ugc_cloner.cli build` run.

Every entry's `evidence` field says how it was arrived at:
`OBSERVED` (from a specific reference video or research finding), `INFERRED` (a reasonable
pattern not tied to one observed video — the three seed frameworks are labeled this way,
since they come from the spec's own named examples rather than a specific video), or
`UNKNOWN` (no evidence yet — never fabricate a value to fill this in). Check this field
before treating anything here as an observed fact rather than a reusable pattern.
