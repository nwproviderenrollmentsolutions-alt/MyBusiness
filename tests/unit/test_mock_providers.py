"""Mock providers must be impossible to mistake for real ones (requirements 21 and 22)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config.settings import get_settings
from providers.factory import (
    all_providers_are_mock,
    get_email_provider,
    get_llm_provider,
    reset_providers,
)
from providers.mock import (
    MockEmailProvider,
    MockLeadDiscoveryProvider,
    MockLLMProvider,
    MockWebResearchProvider,
)


def test_every_configured_provider_is_mock_today():
    assert all_providers_are_mock() is True


def test_unimplemented_provider_raises_rather_than_falling_back(monkeypatch):
    """Silently mocking email would mean believing messages were sent."""
    reset_providers()
    settings = get_settings()
    monkeypatch.setattr(settings, "email_provider", "sendgrid")
    with pytest.raises(Exception, match="not implemented"):
        get_email_provider()
    reset_providers()


def test_llm_output_is_labeled_and_free():
    response = MockLLMProvider(seed=1).complete(prompt="write an email")
    assert response.is_mock is True
    assert "MOCK" in response.text
    # A mock spends no money, so reporting anything but zero would be a fabricated cost.
    assert response.cost_usd == Decimal("0")
    assert response.tokens_in > 0


def test_llm_is_deterministic_for_a_seed():
    a = MockLLMProvider(seed=7).complete(prompt="same prompt")
    b = MockLLMProvider(seed=7).complete(prompt="same prompt")
    assert a.text == b.text


def test_discovered_companies_cannot_resolve():
    response = MockLeadDiscoveryProvider(seed=1).find_companies(icp={"industry": "hvac"}, limit=3)
    assert response.is_mock is True
    assert len(response.companies) == 3
    for company in response.companies:
        assert company.name.startswith("MOCK ")
        # RFC 2606 reserves .invalid — these domains can never resolve to anything real.
        assert company.domain.endswith(".invalid")


def test_discovered_prospect_emails_are_undeliverable():
    provider = MockLeadDiscoveryProvider(seed=1)
    company = provider.find_companies(icp={"industry": "hvac"}, limit=1).companies[0]
    prospects = provider.find_prospects(company=company, roles=["Owner"], limit=2).prospects

    assert len(prospects) == 2
    for prospect in prospects:
        assert prospect.full_name.startswith("MOCK ")
        assert prospect.email.endswith(".invalid")


def test_web_research_content_is_labeled():
    provider = MockWebResearchProvider(seed=1)
    page = provider.fetch(url="https://anything.invalid/x")
    assert page.is_mock is True
    assert "MOCK" in page.text


def test_email_provider_sends_nothing_and_honors_idempotency():
    provider = MockEmailProvider()
    first = provider.send(
        to="someone@example.invalid", subject="s", body="b", idempotency_key="key-1"
    )
    second = provider.send(
        to="someone@example.invalid", subject="s", body="b", idempotency_key="key-1"
    )

    assert first.accepted and first.deduplicated is False
    assert second.deduplicated is True
    assert second.provider_message_id == first.provider_message_id
    # One logical send, one recorded send — the retry did not duplicate it.
    assert len(provider.sent) == 1


def test_llm_provider_factory_returns_mock():
    assert get_llm_provider().is_mock is True
