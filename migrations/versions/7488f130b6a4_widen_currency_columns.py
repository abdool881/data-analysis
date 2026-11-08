"""Use 64-bit integer storage for currency minor units.

Revision ID: 7488f130b6a4
Revises: 91dc520849d6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7488f130b6a4"
down_revision: Union[str, Sequence[str], None] = "91dc520849d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, columns in (
        ("products", ("unit_cost_kobo", "selling_price_kobo")),
        ("sales", ("unit_price_kobo", "unit_cost_kobo")),
        ("restocks", ("unit_cost_kobo",)),
        ("customer_debts", ("principal_kobo",)),
        ("debt_entries", ("amount_kobo",)),
    ):
        for column in columns:
            op.alter_column(
                table, column, existing_type=sa.Integer(), type_=sa.BigInteger(),
                postgresql_using=f"{column}::bigint",
            )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, columns in (
        ("products", ("unit_cost_kobo", "selling_price_kobo")),
        ("sales", ("unit_price_kobo", "unit_cost_kobo")),
        ("restocks", ("unit_cost_kobo",)),
        ("customer_debts", ("principal_kobo",)),
        ("debt_entries", ("amount_kobo",)),
    ):
        for column in columns:
            op.alter_column(
                table, column, existing_type=sa.BigInteger(), type_=sa.Integer(),
                postgresql_using=f"{column}::integer",
            )
