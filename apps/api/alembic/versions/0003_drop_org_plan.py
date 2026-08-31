"""Drop the organization plan tier.

Third Brain is free and open source - there are no tiers, so an org has no plan.
``PlanTier`` and ``Organization.plan`` are gone; this drops the column behind them.

Revision ID: 0003_drop_org_plan
Revises: 0002_drop_waitlist
Create Date: 2026-08-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_drop_org_plan"
down_revision = "0002_drop_waitlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('organizations', 'plan')


def downgrade() -> None:
    # Existing rows need a value for the NOT NULL column; the default is transient so the
    # restored column matches the 0001 baseline exactly.
    op.add_column('organizations',
    sa.Column('plan', sa.Enum('FREE', 'PRO', 'ENTERPRISE', name='plantier', native_enum=False, length=32), nullable=False, server_default='FREE')
    )
    op.alter_column('organizations', 'plan', server_default=None)
