"""Add hashed API keys for authenticated shop access.

Revision ID: 91dc520849d6
Revises: e89314efd691
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "91dc520849d6"
down_revision: Union[str, Sequence[str], None] = "e89314efd691"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("shops", sa.Column("api_key_hash", sa.String(length=64), nullable=True))
        op.create_index("ix_shops_api_key_hash", "shops", ["api_key_hash"], unique=True)
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("shops")}
    if "api_key_hash" not in columns:
        op.add_column("shops", sa.Column("api_key_hash", sa.String(length=64), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("shops")}
    if "ix_shops_api_key_hash" not in indexes:
        op.create_index("ix_shops_api_key_hash", "shops", ["api_key_hash"], unique=True)


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_index("ix_shops_api_key_hash", table_name="shops")
        op.drop_column("shops", "api_key_hash")
        return
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("shops")}
    if "ix_shops_api_key_hash" in indexes:
        op.drop_index("ix_shops_api_key_hash", table_name="shops")
    columns = {column["name"] for column in inspector.get_columns("shops")}
    if "api_key_hash" in columns:
        op.drop_column("shops", "api_key_hash")
