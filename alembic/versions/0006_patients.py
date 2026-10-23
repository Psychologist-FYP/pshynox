"""Patient profiles and optional ownership of existing sessions."""
from alembic import op
import sqlalchemy as sa

revision = "0006_patients"
down_revision = "0005_recording_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("patients",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("intake_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("patient_id", sa.String(100), nullable=True))
        batch.create_foreign_key("fk_sessions_patient_id", "patients", ["patient_id"], ["id"])
        batch.create_index("ix_sessions_patient_id", ["patient_id"])


def downgrade():
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_patient_id")
        batch.drop_constraint("fk_sessions_patient_id", type_="foreignkey")
        batch.drop_column("patient_id")
    op.drop_table("patients")
