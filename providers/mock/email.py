"""MOCK email provider. Records what would have been sent; sends nothing."""

from __future__ import annotations

from datetime import UTC, datetime

from providers.base import InboundMessage, InboxResponse, SendReceipt


class MockEmailProvider:
    """Captures sends in memory so tests and the CEO can inspect them.

    ``sent`` is the record of every call. The idempotency key is honored the way a real
    provider would honor it, so duplicate-send protection is exercised for real rather
    than only in theory.
    """

    name = "mock_email"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed
        self.sent: list[dict[str, str]] = []
        self._by_key: dict[str, str] = {}
        self._inbox: list[InboundMessage] = []

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        idempotency_key: str,
        from_email: str | None = None,
    ) -> SendReceipt:
        if idempotency_key in self._by_key:
            return SendReceipt(
                provider=self.name,
                is_mock=True,
                accepted=True,
                provider_message_id=self._by_key[idempotency_key],
                deduplicated=True,
            )

        message_id = f"mock-msg-{len(self.sent) + 1}-{idempotency_key[:8]}"
        self._by_key[idempotency_key] = message_id
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "from_email": from_email or "mock-sender@mybusiness.invalid",
                "idempotency_key": idempotency_key,
                "message_id": message_id,
            }
        )
        return SendReceipt(
            provider=self.name,
            is_mock=True,
            accepted=True,
            provider_message_id=message_id,
        )

    def fetch_replies(self, *, since: datetime | None = None) -> InboxResponse:
        messages = [m for m in self._inbox if since is None or m.received_at >= since]
        return InboxResponse(provider=self.name, is_mock=True, messages=messages)

    def seed_reply(
        self,
        *,
        from_email: str,
        body: str,
        subject: str | None = None,
        in_reply_to: str | None = None,
    ) -> InboundMessage:
        """Inject a reply for the simulated end-to-end test.

        Explicitly a test affordance: replies only exist because something called this.
        """
        message = InboundMessage(
            provider_message_id=f"mock-inbound-{len(self._inbox) + 1}",
            from_email=from_email,
            to_email="mock-sender@mybusiness.invalid",
            subject=subject or "MOCK reply",
            body=body,
            received_at=datetime.now(UTC),
            in_reply_to=in_reply_to,
        )
        self._inbox.append(message)
        return message
