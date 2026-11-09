"""Internet meetings: meeting mode, audio-only option, verification and connection stats; chunk source and timing.

Additive only. Existing rows keep NULL for the new chunk columns (legacy clinician-side capture).
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_internet_meetings"
down_revision = "0008_progress_monitoring"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("meetings") as batch:
        batch.add_column(sa.Column("mode", sa.String(20), nullable=True))
        batch.add_column(sa.Column("audio_only", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("verification_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("connection_json", sa.Text(), nullable=True))
    with op.batch_alter_table("meeting_chunks") as batch:
        batch.add_column(sa.Column("source", sa.String(30), nullable=True))
        batch.add_column(sa.Column("offset_uncertainty_ms", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rtt_ms", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("meeting_chunks") as batch:
        for name in ("rtt_ms", "offset_uncertainty_ms", "source"):
            batch.drop_column(name)
    with op.batch_alter_table("meetings") as batch:
        for name in ("connection_json", "verification_confirmed_at", "audio_only", "mode"):
            batch.drop_column(name)
