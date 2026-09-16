"""MOCK payments. Creates invoice records only — no money ever moves.

Payment execution additionally requires human approval regardless of autonomy settings
(``ALWAYS_APPROVAL_ACTIONS`` in db/enums.py), so this provider being a mock is the second
line of defense, not the only one.
"""

from __future__ import annotations

from decimal import Decimal

from providers.base import PaymentResponse


class MockPaymentProvider:
    name = "mock_payment"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed
        self.invoices: list[dict[str, str]] = []

    def create_invoice(
        self, *, customer_email: str, amount_usd: Decimal, description: str
    ) -> PaymentResponse:
        invoice_id = f"mock-invoice-{len(self.invoices) + 1}"
        self.invoices.append(
            {
                "invoice_id": invoice_id,
                "customer_email": customer_email,
                "amount_usd": str(amount_usd),
                "description": description,
            }
        )
        return PaymentResponse(
            provider=self.name,
            is_mock=True,
            invoice_id=invoice_id,
            status="mock_draft",
            amount_usd=amount_usd,
        )
