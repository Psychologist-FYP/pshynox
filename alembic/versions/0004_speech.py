"""Persist recordings, transcription jobs and source revisions."""
from alembic import op
import sqlalchemy as sa

revision = "0004_speech"
down_revision = "0003_review_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("recordings",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_recordings_session_id", "recordings", ["session_id"])
    op.create_table("transcription_jobs",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False, unique=True),
        sa.Column("recording_id", sa.String(100), sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100)), sa.Column("error_message", sa.Text()),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("segment_sources",
        sa.Column("segment_id", sa.String(100), sa.ForeignKey("transcript_segments.id"), primary_key=True),
        sa.Column("recording_id", sa.String(100), sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("job_id", sa.String(100), sa.ForeignKey("transcription_jobs.id"), nullable=False),
        sa.Column("channel", sa.Integer(), nullable=False))
    op.add_column("note_drafts", sa.Column("source_revisions_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("note_drafts", sa.Column("sources_stale", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("note_drafts", "sources_stale")
    op.drop_column("note_drafts", "source_revisions_json")
    op.drop_table("segment_sources")
    op.drop_table("transcription_jobs")
    op.drop_index("ix_recordings_session_id", table_name="recordings")
    op.drop_table("recordings")
