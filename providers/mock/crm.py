"""MOCK CRM. Records upserts locally; syncs nothing."""

from __future__ import annotations

from typing import Any

from providers.base import CRMResponse


class MockCRMProvider:
    name = "mock_crm"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed
        self.contacts: list[dict[str, Any]] = []
        self.deals: list[dict[str, Any]] = []

    def upsert_contact(self, *, record: dict[str, Any]) -> CRMResponse:
        self.contacts.append(record)
        return CRMResponse(
            provider=self.name,
            is_mock=True,
            external_id=f"mock-contact-{len(self.contacts)}",
            synced=True,
        )

    def upsert_deal(self, *, record: dict[str, Any]) -> CRMResponse:
        self.deals.append(record)
        return CRMResponse(
            provider=self.name,
            is_mock=True,
            external_id=f"mock-deal-{len(self.deals)}",
            synced=True,
        )
