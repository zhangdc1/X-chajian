"""Shared job type names for the sidecar automation system."""

JOB_GENERATE_GROK_PLAN = "generate_grok_plan"
JOB_PARSE_PLAN = "parse_plan"
JOB_CONTENT_DRAFT = "content_draft"
JOB_COMMENT_DRAFT = "comment_draft"
JOB_MANUAL_REVIEW = "manual_review"
JOB_LEGACY_MODE_RUN = "legacy_mode_run"
JOB_SCORE_GROK_PLAN = "score_grok_plan"

SAFE_JOB_TYPES = {
    JOB_GENERATE_GROK_PLAN,
    JOB_PARSE_PLAN,
    JOB_CONTENT_DRAFT,
    JOB_COMMENT_DRAFT,
    JOB_MANUAL_REVIEW,
    JOB_LEGACY_MODE_RUN,
    JOB_SCORE_GROK_PLAN,
}
