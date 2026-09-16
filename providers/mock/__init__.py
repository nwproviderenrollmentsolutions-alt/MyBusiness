"""MOCK providers. Nothing here touches the outside world.

Three independent safeguards keep simulated data from being mistaken for real:

1. Every generated identifier uses the ``.invalid`` TLD, which RFC 2606 reserves and which
   can never resolve. A mock email address is undeliverable by construction.
2. Every name and body carries a visible ``MOCK`` marker.
3. Every response sets ``is_mock = True``, which propagates to the ``is_mock`` column on
   any row derived from it and excludes those rows from revenue metrics.
"""

from providers.mock.calendar import MockCalendarProvider
from providers.mock.crm import MockCRMProvider
from providers.mock.email import MockEmailProvider
from providers.mock.lead_discovery import MockLeadDiscoveryProvider
from providers.mock.llm import MockLLMProvider
from providers.mock.payment import MockPaymentProvider
from providers.mock.web_research import MockWebResearchProvider

__all__ = [
    "MockCRMProvider",
    "MockCalendarProvider",
    "MockEmailProvider",
    "MockLLMProvider",
    "MockLeadDiscoveryProvider",
    "MockPaymentProvider",
    "MockWebResearchProvider",
]
