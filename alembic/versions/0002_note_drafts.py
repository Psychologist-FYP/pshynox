"""persist note drafts

Revision ID: 0002_note_drafts
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_note_drafts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_drafts",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_note_drafts_session_id", "note_drafts", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_note_drafts_session_id", table_name="note_drafts")
    op.drop_table("note_drafts")
