"""MOCK lead discovery. Invents companies and people that provably do not exist."""

from __future__ import annotations

import random
from typing import Any

from providers.base import (
    CompanyDiscoveryResponse,
    CompanyRecord,
    ProspectDiscoveryResponse,
    ProspectRecord,
)

#: RFC 2606 reserves .invalid. Nothing here can resolve, and no email here can be
#: delivered, even if every other safeguard in the system failed simultaneously.
_TLD = "invalid"

_NAME_STEMS = [
    "Northwind",
    "Cascade",
    "Ironwood",
    "Blue Harbor",
    "Copper Creek",
    "Summit Ridge",
    "Lakeside",
    "Granite Bay",
]
_SUFFIXES = ["Services", "Group", "Partners", "Co", "Industries"]
_FIRST_NAMES = ["Alex", "Jordan", "Sam", "Riley", "Casey", "Morgan", "Taylor", "Jamie"]
_LAST_NAMES = ["Rivera", "Chen", "Patel", "Okafor", "Nowak", "Silva", "Haddad", "Larsen"]
_SIZE_BANDS = ["1-10", "11-50", "51-200", "201-500"]


class MockLeadDiscoveryProvider:
    """Deterministic fake prospect data, labeled MOCK and unreachable by construction."""

    name = "mock_lead_discovery"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed

    def find_companies(self, *, icp: dict[str, Any], limit: int = 10) -> CompanyDiscoveryResponse:
        rng = random.Random(f"{self._seed}|companies|{sorted(icp.items())}")
        industry = str(icp.get("industry", "general"))
        location = str(icp.get("location", "Unspecified"))

        companies = []
        for _ in range(limit):
            stem = rng.choice(_NAME_STEMS)
            suffix = rng.choice(_SUFFIXES)
            slug = f"{stem}-{suffix}".lower().replace(" ", "-")
            companies.append(
                CompanyRecord(
                    name=f"MOCK {stem} {industry.title()} {suffix}",
                    domain=f"{slug}.{_TLD}",
                    industry=industry,
                    size_band=rng.choice(_SIZE_BANDS),
                    location=location,
                    website=f"https://{slug}.{_TLD}",
                    attributes={"mock": True, "generated_from_icp": icp},
                )
            )
        return CompanyDiscoveryResponse(provider=self.name, is_mock=True, companies=companies)

    def find_prospects(
        self, *, company: CompanyRecord, roles: list[str], limit: int = 3
    ) -> ProspectDiscoveryResponse:
        rng = random.Random(f"{self._seed}|prospects|{company.domain}")
        domain = company.domain or f"unknown.{_TLD}"

        prospects = []
        for index in range(limit):
            first = rng.choice(_FIRST_NAMES)
            last = rng.choice(_LAST_NAMES)
            role = roles[index % len(roles)] if roles else "Owner"
            prospects.append(
                ProspectRecord(
                    full_name=f"MOCK {first} {last}",
                    title=role,
                    email=f"{first.lower()}.{last.lower()}@{domain}",
                    phone=None,
                    linkedin_url=None,
                )
            )
        return ProspectDiscoveryResponse(provider=self.name, is_mock=True, prospects=prospects)
