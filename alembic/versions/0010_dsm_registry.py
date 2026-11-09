"""Progress registry v2 (DSM-5-TR): session findings, DSM predictions, clinician registry verifications and
analysis batches (re-analysis and revert). Additive only: v1 tables keep their rows and meaning.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_dsm_registry"
down_revision = "0009_internet_meetings"
branch_labels = None
depends_on = None


def review_columns():
    return [sa.Column("provenance", sa.String(30), nullable=False), sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("original_ai_json", sa.Text(), nullable=True), sa.Column("reviewer_name", sa.String(120), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("reviewer_comment", sa.String(1000), nullable=True),
            sa.Column("extraction_job_id", sa.String(100), nullable=True), sa.Column("source_revisions_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("stale_from_status", sa.String(20), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table("dsm_batches",
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False), sa.Column("job_id", sa.String(100), nullable=True),
        sa.Column("session_ids_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("skipped_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True), sa.Column("reverted_by", sa.String(120), nullable=True))
    op.create_index("ix_dsm_batches_patient_id", "dsm_batches", ["patient_id"])
    op.create_table("dsm_findings",
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False), sa.Column("feature_key", sa.String(80), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("frequency", sa.String(40), nullable=False, server_default="unclear"), sa.Column("onset_or_duration_text", sa.String(300), nullable=False, server_default=""),
        sa.Column("time_frame", sa.String(20), nullable=False, server_default="unclear"), sa.Column("period_label", sa.String(160), nullable=False, server_default=""),
        sa.Column("qualifier", sa.String(40), nullable=True), sa.Column("degree", sa.String(30), nullable=True),
        sa.Column("contexts_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("linked_items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_segment_ids_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("counter_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("counter_check_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("basis", sa.String(600), nullable=False, server_default=""),
        sa.Column("clinician_intensity", sa.Integer(), nullable=True), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("batch_id", sa.String(100), nullable=True), *review_columns())
    op.create_index("ix_dsm_findings_patient_id", "dsm_findings", ["patient_id"])
    op.create_index("ix_dsm_findings_session_id", "dsm_findings", ["session_id"])
    op.create_table("dsm_finding_versions",
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("finding_id", sa.String(100), sa.ForeignKey("dsm_findings.id"), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=False), sa.Column("feature_key", sa.String(80), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("role", sa.String(20), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False), sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_dsm_finding_versions_finding_id", "dsm_finding_versions", ["finding_id"])
    op.create_table("dsm_predictions",
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("as_of_session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False), sa.Column("condition_key", sa.String(60), nullable=False),
        sa.Column("label", sa.String(30), nullable=False), sa.Column("role", sa.String(20), nullable=False),
        sa.Column("period_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("elements_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("specifiers_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("codes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("differentials_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("missing_information_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("periods_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("proposals_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("criteria_met_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("criteria_required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postprocess_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("model_version", sa.String(200), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(80), nullable=False, server_default=""), sa.Column("schema_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("registry_version", sa.String(200), nullable=False, server_default=""), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("superseded_by_id", sa.String(100), nullable=True), sa.Column("batch_id", sa.String(100), nullable=True), *review_columns())
    op.create_index("ix_dsm_predictions_patient_id", "dsm_predictions", ["patient_id"])
    op.create_index("ix_dsm_predictions_as_of_session_id", "dsm_predictions", ["as_of_session_id"])
    op.create_table("registry_verifications",
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("registry_kind", sa.String(20), nullable=False),
        sa.Column("entity_kind", sa.String(20), nullable=False), sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verified_by", sa.String(120), nullable=True), sa.Column("verified_on", sa.String(20), nullable=True),
        sa.Column("comment", sa.String(2000), nullable=False, server_default=""), sa.Column("import_batch_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_registry_verifications_entity", "registry_verifications", ["registry_kind", "entity_kind", "entity_id"])
    for table in ("domain_observations", "condition_predictions", "instrument_scores"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("retired_batch_id", sa.String(100), nullable=True))
    with op.batch_alter_table("risk_statements") as batch:
        batch.add_column(sa.Column("retired_batch_id", sa.String(100), nullable=True))
        batch.add_column(sa.Column("categories_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("note_kind", sa.String(20), nullable=False, server_default="flag"))
        batch.add_column(sa.Column("registry_kind", sa.String(10), nullable=False, server_default="v1"))


def downgrade():
    with op.batch_alter_table("risk_statements") as batch:
        for name in ("registry_kind", "note_kind", "categories_json", "retired_batch_id"):
            batch.drop_column(name)
    for table in ("instrument_scores", "condition_predictions", "domain_observations"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("retired_batch_id")
    op.drop_index("ix_registry_verifications_entity", "registry_verifications")
    op.drop_table("registry_verifications")
    op.drop_index("ix_dsm_predictions_as_of_session_id", "dsm_predictions")
    op.drop_index("ix_dsm_predictions_patient_id", "dsm_predictions")
    op.drop_table("dsm_predictions")
    op.drop_index("ix_dsm_finding_versions_finding_id", "dsm_finding_versions")
    op.drop_table("dsm_finding_versions")
    op.drop_index("ix_dsm_findings_session_id", "dsm_findings")
    op.drop_index("ix_dsm_findings_patient_id", "dsm_findings")
    op.drop_table("dsm_findings")
    op.drop_index("ix_dsm_batches_patient_id", "dsm_batches")
    op.drop_table("dsm_batches")
