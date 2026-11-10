"""SQLModel tables. Each model module is imported here so Alembic sees every table.

M0 has no tables yet; User comes in M1.
"""

from sqlmodel import SQLModel

# Give every constraint and index a predictable name, e.g. "fk_application_user_id_user".
# Without names, SQLite and Postgres invent different ones, and a later migration
# that drops or changes a constraint can't find it.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
