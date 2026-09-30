"""Provider selection.

Configuring a provider that is not implemented raises at startup rather than silently
falling back to a mock. Quietly substituting a mock for a real email provider would mean
believing messages were sent when they were not.
"""

from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings
from core.errors import PermanentError
from providers.anthropic_llm import AnthropicLLMProvider
from providers.base import (
    CalendarProvider,
    CRMProvider,
    EmailProvider,
    LeadDiscoveryProvider,
    LLMProvider,
    PaymentProvider,
    VideoGenProvider,
    VoiceProvider,
    WebResearchProvider,
)
from providers.elevenlabs_voice import ElevenLabsVoiceProvider
from providers.heygen_video_gen import HeyGenVideoGenProvider
from providers.mock import (
    MockCalendarProvider,
    MockCRMProvider,
    MockEmailProvider,
    MockLeadDiscoveryProvider,
    MockLLMProvider,
    MockPaymentProvider,
    MockVideoGenProvider,
    MockVoiceProvider,
    MockWebResearchProvider,
)


def _unsupported(kind: str, name: str) -> PermanentError:
    return PermanentError(
        f"{kind} provider '{name}' is not implemented. "
        f"Only 'mock' (every provider), 'anthropic' (LLM), 'heygen' (video generation), "
        f"and 'elevenlabs' (voice) exist today; the rest land one provider at a time."
    )


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "mock":
        return MockLLMProvider(seed=settings.mock_seed)
    if settings.llm_provider == "anthropic":
        return AnthropicLLMProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    raise _unsupported("LLM", settings.llm_provider)


@lru_cache(maxsize=1)
def get_web_research_provider() -> WebResearchProvider:
    settings = get_settings()
    if settings.web_research_provider == "mock":
        return MockWebResearchProvider(seed=settings.mock_seed)
    raise _unsupported("Web research", settings.web_research_provider)


@lru_cache(maxsize=1)
def get_lead_discovery_provider() -> LeadDiscoveryProvider:
    settings = get_settings()
    if settings.lead_discovery_provider == "mock":
        return MockLeadDiscoveryProvider(seed=settings.mock_seed)
    raise _unsupported("Lead discovery", settings.lead_discovery_provider)


@lru_cache(maxsize=1)
def get_email_provider() -> EmailProvider:
    settings = get_settings()
    if settings.email_provider == "mock":
        return MockEmailProvider(seed=settings.mock_seed)
    raise _unsupported("Email", settings.email_provider)


@lru_cache(maxsize=1)
def get_crm_provider() -> CRMProvider:
    settings = get_settings()
    if settings.crm_provider == "mock":
        return MockCRMProvider(seed=settings.mock_seed)
    raise _unsupported("CRM", settings.crm_provider)


@lru_cache(maxsize=1)
def get_calendar_provider() -> CalendarProvider:
    settings = get_settings()
    if settings.calendar_provider == "mock":
        return MockCalendarProvider(seed=settings.mock_seed)
    raise _unsupported("Calendar", settings.calendar_provider)


@lru_cache(maxsize=1)
def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.payment_provider == "mock":
        return MockPaymentProvider(seed=settings.mock_seed)
    raise _unsupported("Payment", settings.payment_provider)


@lru_cache(maxsize=1)
def get_video_gen_provider() -> VideoGenProvider:
    settings = get_settings()
    if settings.video_gen_provider == "mock":
        return MockVideoGenProvider(seed=settings.mock_seed)
    if settings.video_gen_provider == "heygen":
        return HeyGenVideoGenProvider(
            api_key=settings.heygen_api_key, avatar_id=settings.heygen_avatar_id
        )
    raise _unsupported("Video generation", settings.video_gen_provider)


@lru_cache(maxsize=1)
def get_voice_provider() -> VoiceProvider:
    settings = get_settings()
    if settings.voice_provider == "mock":
        return MockVoiceProvider(seed=settings.mock_seed)
    if settings.voice_provider == "elevenlabs":
        return ElevenLabsVoiceProvider(
            api_key=settings.elevenlabs_api_key, default_voice_id=settings.elevenlabs_voice_id
        )
    raise _unsupported("Voice", settings.voice_provider)


def all_providers_are_mock() -> bool:
    """Used by the dashboard to state plainly whether anything real is wired up."""
    return all(
        provider.is_mock
        for provider in (
            get_llm_provider(),
            get_web_research_provider(),
            get_lead_discovery_provider(),
            get_email_provider(),
            get_crm_provider(),
            get_calendar_provider(),
            get_payment_provider(),
            get_video_gen_provider(),
            get_voice_provider(),
        )
    )


def reset_providers() -> None:
    """Drop cached provider instances. Used by tests and after a config change."""
    for factory in (
        get_llm_provider,
        get_web_research_provider,
        get_lead_discovery_provider,
        get_email_provider,
        get_crm_provider,
        get_calendar_provider,
        get_payment_provider,
        get_video_gen_provider,
        get_voice_provider,
    ):
        factory.cache_clear()
