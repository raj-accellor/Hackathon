"""nsdl fpi daily

Revision ID: c3d4e5f6a7b8
Revises: b7c1d2e3f4a5
Create Date: 2026-10-26 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.models  # noqa: F401  (custom column types)


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b7c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    num = lambda: sa.Numeric(precision=18, scale=2)  # noqa: E731
    op.create_table('fpi_daily',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reporting_date', sa.Date(), nullable=False),
    sa.Column('equity_exchange_buy', num(), nullable=True),
    sa.Column('equity_exchange_sell', num(), nullable=True),
    sa.Column('equity_exchange_net', num(), nullable=True),
    sa.Column('equity_primary_net', num(), nullable=True),
    sa.Column('equity_net', num(), nullable=True),
    sa.Column('equity_net_usd_mn', num(), nullable=True),
    sa.Column('total_net', num(), nullable=True),
    sa.Column('usd_inr', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('retrieved_at', app.db.models.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_fpi_daily_reporting_date'), 'fpi_daily', ['reporting_date'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_fpi_daily_reporting_date'), table_name='fpi_daily')
    op.drop_table('fpi_daily')
