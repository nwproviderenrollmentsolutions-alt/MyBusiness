"""Provider protocols: every door between this system and the outside world.

Each protocol has exactly one job and is swappable by configuration. Agents never import
a concrete provider; they call tools, tools call the provider the factory selected.

Every provider exposes ``is_mock``. That flag propagates into the ``is_mock`` column on
any row produced from its output, so simulated data can never be counted as real.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ProviderResponse(BaseModel):
    """Common envelope. ``is_mock`` is not optional metadata — it is a safety signal."""

    provider: str
    is_mock: bool


class LLMResponse(ProviderResponse):
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    #: Real money spent. A mock provider reports 0 because it spent nothing.
    cost_usd: Decimal = Decimal("0")
    stop_reason: str | None = None


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchResponse(ProviderResponse):
    query: str
    results: list[SearchResult] = Field(default_factory=list)


class FetchedPage(ProviderResponse):
    url: str
    title: str | None = None
    #: Untrusted content. Passed to an LLM only as labeled data, never as instructions.
    text: str = ""
    fetched_at: datetime | None = None


class CompanyRecord(BaseModel):
    name: str
    domain: str | None = None
    industry: str | None = None
    size_band: str | None = None
    location: str | None = None
    website: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProspectRecord(BaseModel):
    full_name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None


class CompanyDiscoveryResponse(ProviderResponse):
    companies: list[CompanyRecord] = Field(default_factory=list)


class ProspectDiscoveryResponse(ProviderResponse):
    prospects: list[ProspectRecord] = Field(default_factory=list)


class SendReceipt(ProviderResponse):
    accepted: bool
    provider_message_id: str | None = None
    #: True when the provider recognized the idempotency key and did not resend.
    deduplicated: bool = False
    error: str | None = None


class InboundMessage(BaseModel):
    provider_message_id: str
    from_email: str
    to_email: str
    subject: str | None
    body: str
    received_at: datetime
    in_reply_to: str | None = None


class InboxResponse(ProviderResponse):
    messages: list[InboundMessage] = Field(default_factory=list)


class CalendarSlot(BaseModel):
    start: datetime
    end: datetime


class CalendarResponse(ProviderResponse):
    slots: list[CalendarSlot] = Field(default_factory=list)
    booking_id: str | None = None


class PaymentResponse(ProviderResponse):
    invoice_id: str | None = None
    status: str = "none"
    amount_usd: Decimal = Decimal("0")


class CRMResponse(ProviderResponse):
    external_id: str | None = None
    synced: bool = False


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    is_mock: bool

    def complete(
        self,
        *,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


@runtime_checkable
class WebResearchProvider(Protocol):
    name: str
    is_mock: bool

    def search(self, *, query: str, limit: int = 5) -> WebSearchResponse: ...

    def fetch(self, *, url: str) -> FetchedPage: ...


@runtime_checkable
class LeadDiscoveryProvider(Protocol):
    name: str
    is_mock: bool

    def find_companies(
        self, *, icp: dict[str, Any], limit: int = 10
    ) -> CompanyDiscoveryResponse: ...

    def find_prospects(
        self, *, company: CompanyRecord, roles: list[str], limit: int = 3
    ) -> ProspectDiscoveryResponse: ...


@runtime_checkable
class EmailProvider(Protocol):
    name: str
    is_mock: bool

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        idempotency_key: str,
        from_email: str | None = None,
    ) -> SendReceipt: ...

    def fetch_replies(self, *, since: datetime | None = None) -> InboxResponse: ...


@runtime_checkable
class CRMProvider(Protocol):
    name: str
    is_mock: bool

    def upsert_contact(self, *, record: dict[str, Any]) -> CRMResponse: ...

    def upsert_deal(self, *, record: dict[str, Any]) -> CRMResponse: ...


@runtime_checkable
class CalendarProvider(Protocol):
    name: str
    is_mock: bool

    def propose_slots(self, *, days_ahead: int = 7, count: int = 3) -> CalendarResponse: ...

    def book(self, *, start: datetime, end: datetime, attendee_email: str) -> CalendarResponse: ...


@runtime_checkable
class PaymentProvider(Protocol):
    name: str
    is_mock: bool

    def create_invoice(
        self, *, customer_email: str, amount_usd: Decimal, description: str
    ) -> PaymentResponse: ...
