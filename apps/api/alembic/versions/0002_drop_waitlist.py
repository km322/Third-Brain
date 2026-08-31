"""Drop the public pre-launch waitlist.

Third Brain is free and open source - anyone can run it, so there is nothing to queue for.
The route, model and schemas are gone; this drops the table behind them.

``waitlist_entries`` was folded into the 0001 baseline rather than living in its own
revision, so ``downgrade`` recreates the table and its indexes exactly as that baseline did.

Revision ID: 0002_drop_waitlist
Revises: 0001_initial
Create Date: 2026-08-28 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_drop_waitlist"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(op.f('ix_waitlist_entries_id'), table_name='waitlist_entries')
    op.drop_index(op.f('ix_waitlist_entries_email'), table_name='waitlist_entries')
    op.drop_index('ix_waitlist_entries_created', table_name='waitlist_entries')
    op.drop_table('waitlist_entries')


def downgrade() -> None:
    op.create_table('waitlist_entries',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('company', sa.String(length=255), nullable=True),
    sa.Column('source', sa.String(length=64), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_waitlist_entries_created', 'waitlist_entries', ['created_at'], unique=False)
    op.create_index(op.f('ix_waitlist_entries_email'), 'waitlist_entries', ['email'], unique=True)
    op.create_index(op.f('ix_waitlist_entries_id'), 'waitlist_entries', ['id'], unique=False)
