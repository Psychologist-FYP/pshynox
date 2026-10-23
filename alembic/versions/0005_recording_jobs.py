"""Allow independent recording jobs within a session, preserving existing jobs."""
from alembic import op

revision = "0005_recording_jobs"
down_revision = "0004_speech"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite's existing session uniqueness is unnamed; name it during reflection.
    with op.batch_alter_table("transcription_jobs", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.drop_constraint("uq_transcription_jobs_session_id", type_="unique")
        batch.create_unique_constraint("uq_transcription_jobs_recording_id", ["recording_id"])
        batch.create_index("ix_transcription_jobs_session_id", ["session_id"])


def downgrade():
    # This correctly fails if sessions have multiple recordings; never delete jobs.
    with op.batch_alter_table("transcription_jobs") as batch:
        batch.drop_index("ix_transcription_jobs_session_id")
        batch.drop_constraint("uq_transcription_jobs_recording_id", type_="unique")
        batch.create_unique_constraint("uq_transcription_jobs_session_id", ["session_id"])
