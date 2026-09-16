"""MOCK calendar. Proposes slots and records bookings without touching a real calendar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from providers.base import CalendarResponse, CalendarSlot


class MockCalendarProvider:
    name = "mock_calendar"
    is_mock = True

    def __init__(self, *, seed: int = 1337) -> None:
        self._seed = seed
        self.bookings: list[dict[str, str]] = []

    def propose_slots(self, *, days_ahead: int = 7, count: int = 3) -> CalendarResponse:
        base = datetime.now(UTC).replace(hour=15, minute=0, second=0, microsecond=0)
        slots = [
            CalendarSlot(
                start=base + timedelta(days=day + 1),
                end=base + timedelta(days=day + 1, minutes=30),
            )
            for day in range(min(count, days_ahead))
        ]
        return CalendarResponse(provider=self.name, is_mock=True, slots=slots)

    def book(self, *, start: datetime, end: datetime, attendee_email: str) -> CalendarResponse:
        booking_id = f"mock-booking-{len(self.bookings) + 1}"
        self.bookings.append(
            {
                "booking_id": booking_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "attendee_email": attendee_email,
            }
        )
        return CalendarResponse(provider=self.name, is_mock=True, booking_id=booking_id)
