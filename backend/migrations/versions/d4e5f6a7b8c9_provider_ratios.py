"""provider ratios (Tickertape fallback for missing fundamentals)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-26 23:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.models  # noqa: F401  (custom column types)


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('provider_ratios',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('provider_ref', sa.String(length=40), nullable=True),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('raw', sa.JSON(), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=True),
    sa.Column('retrieved_at', app.db.models.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'provider')
    )
    op.create_index(op.f('ix_provider_ratios_instrument_id'), 'provider_ratios', ['instrument_id'], unique=False)
    op.create_index(op.f('ix_provider_ratios_retrieved_at'), 'provider_ratios', ['retrieved_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_provider_ratios_retrieved_at'), table_name='provider_ratios')
    op.drop_index(op.f('ix_provider_ratios_instrument_id'), table_name='provider_ratios')
    op.drop_table('provider_ratios')
