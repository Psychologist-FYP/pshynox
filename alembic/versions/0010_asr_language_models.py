"""ASR language modes: the session's mode, per-track meeting overrides, and the mode and model used per recording and job.

Additive only. Existing rows keep NULL: a session without a mode uses the default (urdu_mixed); a recording or job
without one predates language modes (whisper base, language from WHISPER_LANGUAGE).
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_asr_language_models"
down_revision = "0009_internet_meetings"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("asr_mode", sa.String(20), nullable=True))
    with op.batch_alter_table("meetings") as batch:
        batch.add_column(sa.Column("asr_mode_clinician", sa.String(20), nullable=True))
        batch.add_column(sa.Column("asr_mode_client", sa.String(20), nullable=True))
    with op.batch_alter_table("recordings") as batch:
        batch.add_column(sa.Column("asr_mode", sa.String(20), nullable=True))
    with op.batch_alter_table("transcription_jobs") as batch:
        batch.add_column(sa.Column("asr_mode", sa.String(20), nullable=True))
        batch.add_column(sa.Column("asr_model", sa.String(200), nullable=True))


def downgrade():
    with op.batch_alter_table("transcription_jobs") as batch:
        batch.drop_column("asr_model")
        batch.drop_column("asr_mode")
    with op.batch_alter_table("recordings") as batch:
        batch.drop_column("asr_mode")
    with op.batch_alter_table("meetings") as batch:
        batch.drop_column("asr_mode_client")
        batch.drop_column("asr_mode_clinician")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("asr_mode")
