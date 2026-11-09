"""Longitudinal progress monitoring, evidence-cited condition hypotheses, patient-level audit.

Additive except the approved audit change: audit_events gains patient_id and session_id
becomes nullable (patient-level actions without a session).
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_progress_monitoring"
down_revision = "0007_meetings"
branch_labels = None
depends_on = None


def _review_columns():
    return [
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("review_status", sa.String(20), nullable=False),
        sa.Column("original_ai_json", sa.Text(), nullable=True),
        sa.Column("reviewer_name", sa.String(120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_comment", sa.String(1000), nullable=True),
        sa.Column("extraction_job_id", sa.String(100), nullable=True),
        sa.Column("source_revisions_json", sa.Text(), nullable=False),
        sa.Column("stale_from_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column("session_id", existing_type=sa.String(100), nullable=True)
        batch.add_column(sa.Column("patient_id", sa.String(100), nullable=True))
        batch.create_foreign_key("fk_audit_events_patient_id", "patients", ["patient_id"], ["id"])
        batch.create_index("ix_audit_events_patient_id", ["patient_id"])

    op.create_table("registry_versions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "version", "sha256", name="uq_registry_versions"))

    op.create_table("instrument_scores",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=True),
        sa.Column("instrument_key", sa.String(40), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False),
        sa.Column("administered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_scores_json", sa.Text(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("total_is_partial", sa.Boolean(), nullable=False),
        sa.Column("subtotals_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("superseded_by_id", sa.String(100), nullable=True),
        *_review_columns())
    op.create_index("ix_instrument_scores_patient_id", "instrument_scores", ["patient_id"])
    op.create_index("ix_instrument_scores_session_id", "instrument_scores", ["session_id"])
    op.create_index("uq_instrument_scores_active", "instrument_scores", ["session_id", "instrument_key", "provenance"],
                    unique=True, sqlite_where=sa.text("is_active = 1 AND session_id IS NOT NULL"))

    op.create_table("domain_observations",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("domain_key", sa.String(80), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("status_value", sa.String(30), nullable=False),
        sa.Column("direction", sa.String(40), nullable=True),
        sa.Column("timeframe", sa.String(20), nullable=False),
        sa.Column("frequency_text", sa.String(300), nullable=False),
        sa.Column("intensity_text", sa.String(300), nullable=False),
        sa.Column("duration_text", sa.String(300), nullable=False),
        sa.Column("basis", sa.String(600), nullable=False),
        sa.Column("evidence_segment_ids_json", sa.Text(), nullable=False),
        *_review_columns(),
        sa.UniqueConstraint("session_id", "domain_key", name="uq_domain_observation_session_domain"))
    op.create_index("ix_domain_observations_patient_id", "domain_observations", ["patient_id"])
    op.create_index("ix_domain_observations_session_id", "domain_observations", ["session_id"])

    op.create_table("domain_observation_versions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("observation_id", sa.String(100), sa.ForeignKey("domain_observations.id"), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("domain_key", sa.String(80), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_domain_observation_versions_observation_id", "domain_observation_versions", ["observation_id"])
    op.create_index("ix_domain_observation_versions_session_id", "domain_observation_versions", ["session_id"])

    op.create_table("condition_predictions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("as_of_session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("condition_key", sa.String(60), nullable=False),
        sa.Column("subtype_key", sa.String(60), nullable=True),
        sa.Column("prediction", sa.String(30), nullable=False),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("calibration_id", sa.String(100), nullable=True),
        sa.Column("criteria_met_count", sa.Integer(), nullable=False),
        sa.Column("criteria_required", sa.Integer(), nullable=False),
        sa.Column("duration_status", sa.String(20), nullable=False),
        sa.Column("impairment_status", sa.String(20), nullable=False),
        sa.Column("rule_outs_json", sa.Text(), nullable=False),
        sa.Column("differentials_json", sa.Text(), nullable=False),
        sa.Column("missing_information_json", sa.Text(), nullable=False),
        sa.Column("rationale_json", sa.Text(), nullable=False),
        sa.Column("postprocess_json", sa.Text(), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("registry_version", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("superseded_by_id", sa.String(100), nullable=True),
        *_review_columns())
    op.create_index("ix_condition_predictions_patient_id", "condition_predictions", ["patient_id"])
    op.create_index("ix_condition_predictions_as_of_session_id", "condition_predictions", ["as_of_session_id"])

    op.create_table("criterion_findings",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("prediction_id", sa.String(100), sa.ForeignKey("condition_predictions.id"), nullable=False),
        sa.Column("criterion_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("counter_evidence_json", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.String(20), nullable=False),
        sa.Column("duration_note", sa.String(600), nullable=False))
    op.create_index("ix_criterion_findings_prediction_id", "criterion_findings", ["prediction_id"])

    op.create_table("risk_statements",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("segment_ids_json", sa.Text(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("basis", sa.String(600), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_review_columns())
    op.create_index("ix_risk_statements_patient_id", "risk_statements", ["patient_id"])
    op.create_index("ix_risk_statements_session_id", "risk_statements", ["session_id"])

    op.create_table("change_events",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("measure_kind", sa.String(20), nullable=False),
        sa.Column("measure_key", sa.String(80), nullable=False),
        sa.Column("series", sa.String(30), nullable=False),
        sa.Column("from_session_id", sa.String(100), nullable=False),
        sa.Column("to_session_id", sa.String(100), nullable=False),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("from_label", sa.String(60), nullable=True),
        sa.Column("to_label", sa.String(60), nullable=True),
        sa.Column("rule_id", sa.String(60), nullable=False),
        sa.Column("provenance_of_inputs", sa.String(20), nullable=False),
        sa.Column("acknowledged_by", sa.String(120), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_change_events_patient_id", "change_events", ["patient_id"])

    op.create_table("plan_followups",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("patient_id", sa.String(100), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("origin_session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("origin_note_item_id", sa.String(100), nullable=False),
        sa.Column("plan_text", sa.String(1000), nullable=False),
        sa.Column("followup_session_id", sa.String(100), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("basis", sa.String(600), nullable=False),
        sa.Column("evidence_segment_ids_json", sa.Text(), nullable=False),
        *_review_columns())
    op.create_index("ix_plan_followups_patient_id", "plan_followups", ["patient_id"])
    op.create_index("ix_plan_followups_origin_session_id", "plan_followups", ["origin_session_id"])
    op.create_index("ix_plan_followups_followup_session_id", "plan_followups", ["followup_session_id"])

    op.create_table("progress_evidence_links",
        sa.Column("segment_id", sa.String(100), primary_key=True),
        sa.Column("owner_kind", sa.String(30), primary_key=True),
        sa.Column("owner_id", sa.String(100), primary_key=True),
        sa.Column("revision_at_link", sa.Integer(), nullable=False))
    op.create_index("ix_progress_evidence_links_owner", "progress_evidence_links", ["owner_kind", "owner_id"])


def downgrade():
    for table in ("progress_evidence_links", "plan_followups", "change_events", "risk_statements", "criterion_findings",
                  "condition_predictions", "domain_observation_versions", "domain_observations", "instrument_scores",
                  "registry_versions"):
        op.drop_table(table)
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM audit_events WHERE session_id IS NULL")).scalar():
        raise RuntimeError("Patient-level audit events exist; refusing to make audit_events.session_id NOT NULL again")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_index("ix_audit_events_patient_id")
        batch.drop_constraint("fk_audit_events_patient_id", type_="foreignkey")
        batch.drop_column("patient_id")
        batch.alter_column("session_id", existing_type=sa.String(100), nullable=False)
