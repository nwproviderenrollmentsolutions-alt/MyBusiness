"""Phase 2/3 — viral research and framework extraction.

Hard rule from the spec: never assume a video is viral because it "appears popular", and
never fabricate an engagement number. This module enforces that mechanically rather than
by convention: engagement_signals is only ever populated from a search result that itself
carries a real (non-mock) provider's data, and every ResearchFinding and ViralFramework
built here carries an explicit Evidence tag a caller can filter or display on.

No real web-research provider exists in this repository yet (see providers/factory.py —
only "mock" is implemented). Until one is connected, every finding is UNKNOWN by
construction; this module still runs end to end against the mock so the rest of the
pipeline can be exercised.
"""

from __future__ import annotations

from providers.base import LLMProvider, WebResearchProvider
from ugc_cloner.models import Evidence, ResearchFinding, ViralFramework

#: The spec's own three seed frameworks, used as the fallback when no live research is
#: connected. Explicitly labeled UNKNOWN — these are known reusable patterns, not a claim
#: that a specific real video was observed performing well.
_SEED_FRAMEWORKS: list[ViralFramework] = [
    ViralFramework(
        name="I Didn't Expect This",
        pattern=["hook: skepticism", "problem", "demonstration", "unexpected payoff", "cta"],
        example_hook="I didn't think this would actually work...",
        evidence=Evidence.UNKNOWN,
    ),
    ViralFramework(
        name="Stop Doing X",
        pattern=[
            "pattern interrupt",
            "common mistake",
            "better solution",
            "demonstration",
            "result",
            "cta",
        ],
        example_hook="Stop doing this if you want to...",
        evidence=Evidence.UNKNOWN,
    ),
    ViralFramework(
        name="I Tried It",
        pattern=["claim", "experiment", "live demonstration", "reaction", "result", "cta"],
        example_hook="I tried this for a week and...",
        evidence=Evidence.UNKNOWN,
    ),
]


def _findings_from_search(
    web: WebResearchProvider, query: str, limit: int = 5
) -> list[ResearchFinding]:
    response = web.search(query=query, limit=limit)
    # A mock provider's snippets are fabricated by definition — evidence stays UNKNOWN and
    # no engagement number is ever read out of them, regardless of what the mock text says.
    evidence = Evidence.UNKNOWN if response.is_mock else Evidence.INFERRED
    return [
        ResearchFinding(
            title=result.title,
            url=result.url,
            hook_summary=result.snippet if not response.is_mock else None,
            engagement_signals={},
            evidence=evidence,
        )
        for result in response.results
    ]


def research_viral_formats(
    *,
    product_category: str,
    web: WebResearchProvider,
    llm: LLMProvider | None = None,
) -> list[ViralFramework]:
    queries = [
        f"viral UGC {product_category}",
        f"viral TikTok {product_category}",
        f"best performing UGC hooks {product_category}",
    ]
    findings: list[ResearchFinding] = []
    for query in queries:
        findings.extend(_findings_from_search(web, query))

    if not findings or all(f.evidence is Evidence.UNKNOWN for f in findings):
        # No live signal to cluster into a framework — return the labeled seed library
        # rather than inventing a "trend" out of fabricated search snippets.
        return [f.model_copy(update={"based_on": []}) for f in _SEED_FRAMEWORKS]

    grouped = ViralFramework(
        name=f"Observed pattern — {product_category}",
        pattern=["hook", "problem/context", "demonstration", "payoff", "cta"],
        example_hook=findings[0].hook_summary,
        based_on=findings,
        evidence=Evidence.INFERRED,
    )
    return [grouped, *_SEED_FRAMEWORKS]
