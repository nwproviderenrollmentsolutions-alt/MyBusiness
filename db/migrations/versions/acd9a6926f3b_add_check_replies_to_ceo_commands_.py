"""add check_replies to ceo_commands intent constraint

Written by hand, before shipping the model change — not after — per the lesson in
test_migrations.py's docstring: Alembic's autogenerate does not diff CHECK constraint
bodies, so a forgotten migration here would ship silently and only surface at runtime.

Revision ID: acd9a6926f3b
Revises: 67135e5b3dcd
Create Date: 2026-09-16 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'acd9a6926f3b'
down_revision: Union[str, Sequence[str], None] = '67135e5b3dcd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_ceo_commands_commandintent"
_OLD_VALUES = (
    "find_opportunities", "find_leads", "start_campaign", "pause_campaign",
    "resume_campaign", "kill_campaign", "show_decisions", "show_status",
    "engage_kill_switch", "release_kill_switch", "unknown",
)
_NEW_VALUES = (
    "find_opportunities", "find_leads", "start_campaign", "pause_campaign",
    "resume_campaign", "kill_campaign", "show_decisions", "show_status",
    "check_replies", "engage_kill_switch", "release_kill_switch", "unknown",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "ceo_commands", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "ceo_commands", f"intent IN ({_in_list(_NEW_VALUES)})"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "ceo_commands", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "ceo_commands", f"intent IN ({_in_list(_OLD_VALUES)})"
    )
