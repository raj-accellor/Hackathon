"""fii/dii flows

Revision ID: b7c1d2e3f4a5
Revises: 0e59e2fb0d7a
Create Date: 2026-10-26 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.models  # noqa: F401  (custom column types)


revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '0e59e2fb0d7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('fii_dii_flows',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('buy_value', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('sell_value', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('net_value', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('retrieved_at', app.db.models.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trade_date', 'category', name='uq_fii_dii_day_category')
    )
    op.create_index(op.f('ix_fii_dii_flows_trade_date'), 'fii_dii_flows', ['trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_fii_dii_flows_trade_date'), table_name='fii_dii_flows')
    op.drop_table('fii_dii_flows')
