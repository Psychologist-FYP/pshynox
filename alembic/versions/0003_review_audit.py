"""add note review workflow and audit events

Revision ID: 0003_review_audit
Revises: 0002_note_drafts
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_review_audit"
down_revision = "0002_note_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("workflow_status", sa.Column("workflow_status", sa.String(length=20), nullable=True)),
        ("risk_review_status", sa.Column("risk_review_status", sa.String(length=20), nullable=True)),
        ("reviewer_name", sa.Column("reviewer_name", sa.String(length=120), nullable=True)),
        ("risk_review_completed_at", sa.Column("risk_review_completed_at", sa.DateTime(timezone=True), nullable=True)),
        ("review_note", sa.Column("review_note", sa.String(length=500), nullable=True)),
        ("decision_actor_name", sa.Column("decision_actor_name", sa.String(length=120), nullable=True)),
        ("decision_at", sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True)),
        ("rejection_reason", sa.Column("rejection_reason", sa.String(length=500), nullable=True)),
    ):
        op.add_column("note_drafts", column)
    op.execute("UPDATE note_drafts SET workflow_status = 'DRAFT' WHERE workflow_status IS NULL")
    op.execute("UPDATE note_drafts SET risk_review_status = 'PENDING' WHERE risk_review_status IS NULL")
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("note_draft_id", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("actor_name", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["note_draft_id"], ["note_drafts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_session_id", "audit_events", ["session_id"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_session_id", table_name="audit_events")
    op.drop_table("audit_events")
    for name in ("rejection_reason", "decision_at", "decision_actor_name", "review_note", "risk_review_completed_at", "reviewer_name", "risk_review_status", "workflow_status"):
        op.drop_column("note_drafts", name)
