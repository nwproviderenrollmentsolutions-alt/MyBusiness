"""MOCK web research. Fabricates pages instead of fetching them."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from providers.base import FetchedPage, SearchResult, WebSearchResponse

_TLD = "invalid"


class MockWebResearchProvider:
    """Deterministic fake search and page text.

    Returned text is prefixed with a MOCK banner. Any agent feeding this into a prompt
    must treat it as untrusted data regardless — that discipline has to hold for real
    pages too, so the mock does not pretend to be safe.
    """

    name = "mock_web_research"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed

    def search(self, *, query: str, limit: int = 5) -> WebSearchResponse:
        rng = random.Random(f"{self._seed}|search|{query}")
        results = [
            SearchResult(
                title=f"MOCK result {i + 1} for {query}",
                url=f"https://mock-source-{rng.randint(100, 999)}.{_TLD}/article-{i + 1}",
                snippet=f"MOCK snippet {i + 1}: simulated context about {query}.",
            )
            for i in range(limit)
        ]
        return WebSearchResponse(
            provider=self.name, is_mock=True, query=query, results=results
        )

    def fetch(self, *, url: str) -> FetchedPage:
        return FetchedPage(
            provider=self.name,
            is_mock=True,
            url=url,
            title=f"MOCK page at {url}",
            text=(
                f"[MOCK PAGE CONTENT — NOT REAL] Simulated body text for {url}. "
                "No network request was made."
            ),
            fetched_at=datetime.now(UTC),
        )
