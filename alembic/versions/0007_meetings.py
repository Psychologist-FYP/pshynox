"""Meeting capabilities and separately attributed audio chunks."""
from alembic import op
import sqlalchemy as sa

revision = "0007_meetings"
down_revision = "0006_patients"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("meetings",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("host_hash", sa.String(64), nullable=False),
        sa.Column("guest_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_ms", sa.Integer(), nullable=False),
        sa.Column("expires_ms", sa.Integer(), nullable=False),
        sa.Column("started_ms", sa.Integer(), nullable=True),
        sa.Column("guest_consented", sa.Boolean(), nullable=False))
    op.create_index("ix_meetings_session_id", "meetings", ["session_id"])
    op.create_table("meeting_chunks",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("meeting_id", sa.String(100), sa.ForeignKey("meetings.id"), nullable=False),
        sa.Column("recording_id", sa.String(100), sa.ForeignKey("recordings.id"), nullable=False, unique=True),
        sa.Column("speaker_role", sa.String(20), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False))
    op.create_index("ix_meeting_chunks_meeting_id", "meeting_chunks", ["meeting_id"])


def downgrade():
    op.drop_table("meeting_chunks")
    op.drop_table("meetings")
