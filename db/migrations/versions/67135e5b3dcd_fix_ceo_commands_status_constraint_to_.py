"""fix ceo_commands status constraint to include dispatched

Alembic's autogenerate does not diff the body of CHECK constraints — only their
presence or absence — so adding CommandStatus.DISPATCHED after this table's original
migration was generated (see e7cf4f9229a5) shipped silently: models, code, and every
test built on Base.metadata.create_all() all agreed, but the real migrated database's
constraint still rejected the value. Found by running the CLI against a database built
via `alembic upgrade head` rather than create_all().

Revision ID: 67135e5b3dcd
Revises: e7cf4f9229a5
Create Date: 2026-09-16 11:51:46.418796

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '67135e5b3dcd'
down_revision: Union[str, Sequence[str], None] = 'e7cf4f9229a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_ceo_commands_commandstatus"
_OLD_VALUES = ("pending", "answered", "executed", "blocked", "unrecognized", "failed")
_NEW_VALUES = ("pending", "answered", "executed", "dispatched", "blocked", "unrecognized", "failed")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # op.f() marks the name as already fully resolved — without it, Alembic reapplies
    # the ck_%(table_name)s_%(constraint_name)s naming convention on top of a name that
    # already has it, doubling the prefix.
    op.drop_constraint(op.f(_CONSTRAINT), "ceo_commands", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "ceo_commands", f"status IN ({_in_list(_NEW_VALUES)})"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "ceo_commands", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "ceo_commands", f"status IN ({_in_list(_OLD_VALUES)})"
    )
