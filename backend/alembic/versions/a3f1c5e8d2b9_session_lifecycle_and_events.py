"""session lifecycle, validation, and events (Phase 4.1)

Revision ID: a3f1c5e8d2b9
Revises: f2d8a87a8b44
Create Date: 2026-08-31 15:10:00.000000

Phase 4.1 — Session Management Foundation.

Changes to the sessions table:
- Replace the legacy 'status' column with two clearly separated states:
    * lifecycle_state  ('active','inactive','expired','invalid')
    * validation_state ('valid','invalid','expired','unknown','unavailable','error')
- Add: label, source, next_validation_at, activated_at, deactivated_at.

Legacy data migration:
- The old 'status' column held validation-like values
  ('valid','invalid','expired','unknown'). Those values are moved into
  validation_state; anything else becomes 'unknown'.
- No lifecycle information ever existed, so all existing rows become
  'inactive' (activation is always an explicit user action).
- Sessions were never created by any released feature, so in practice
  this table is empty — the mapping exists purely for correctness.

New table:
- session_events — per-session audit trail (mirrors credential_events).
  Secrets are never placed in event payloads.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1c5e8d2b9'
down_revision: Union[str, Sequence[str], None] = 'f2d8a87a8b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── 1. Add the new session columns (nullable first, tightened later) ──
    op.add_column('sessions', sa.Column('label', sa.String(length=255), nullable=True))
    op.add_column('sessions', sa.Column('source', sa.String(length=32), nullable=True))
    op.add_column('sessions', sa.Column('lifecycle_state', sa.String(length=32), nullable=True))
    op.add_column('sessions', sa.Column('validation_state', sa.String(length=32), nullable=True))
    op.add_column('sessions', sa.Column('next_validation_at', sa.Text(), nullable=True))
    op.add_column('sessions', sa.Column('activated_at', sa.Text(), nullable=True))
    op.add_column('sessions', sa.Column('deactivated_at', sa.Text(), nullable=True))

    # ── 2. Migrate legacy data ──
    # Old 'status' held validation-like values — move them into validation_state.
    op.execute(
        "UPDATE sessions SET validation_state = status "
        "WHERE status IN ('valid','invalid','expired','unavailable','error','unknown')"
    )
    op.execute("UPDATE sessions SET validation_state = 'unknown' WHERE validation_state IS NULL")
    # No lifecycle information ever existed — everything starts inactive.
    op.execute("UPDATE sessions SET lifecycle_state = 'inactive'")
    op.execute("UPDATE sessions SET source = 'manual' WHERE source IS NULL")

    # ── 3. Drop the legacy status column (SQLite requires batch mode) ──
    with op.batch_alter_table('sessions') as batch_op:
        batch_op.drop_column('status')
        batch_op.alter_column('lifecycle_state', existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column('validation_state', existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column('source', existing_type=sa.String(length=32), nullable=False)

    # ── 4. session_events audit table ──
    op.create_table('session_events',
        sa.Column('id', sa.String(length=12), nullable=False),
        sa.Column('provider_id', sa.String(length=12), nullable=True),
        sa.Column('session_id', sa.String(length=12), nullable=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_session_events_event_type'), 'session_events', ['event_type'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_session_events_event_type'), table_name='session_events')
    op.drop_table('session_events')

    with op.batch_alter_table('sessions') as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=32), nullable=True))

    op.execute(
        "UPDATE sessions SET status = validation_state "
        "WHERE validation_state IN ('valid','invalid','expired','unavailable','error','unknown')"
    )
    op.execute("UPDATE sessions SET status = 'unknown' WHERE status IS NULL")

    with op.batch_alter_table('sessions') as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=32), nullable=False)
        batch_op.drop_column('label')
        batch_op.drop_column('source')
        batch_op.drop_column('lifecycle_state')
        batch_op.drop_column('validation_state')
        batch_op.drop_column('next_validation_at')
        batch_op.drop_column('activated_at')
        batch_op.drop_column('deactivated_at')
