"""create session and transcript tables

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("pseudonym_id", sa.String(length=100), nullable=False),
        sa.Column("session_type", sa.String(length=40), nullable=False),
        sa.Column("consent_json", sa.Text(), nullable=False),
        sa.Column("workflow_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("speaker_role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transcript_segments_session_id", "transcript_segments", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_transcript_segments_session_id", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_table("sessions")
