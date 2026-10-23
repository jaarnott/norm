"""Norm Finance: the fin_* tables (app/db/finance_models.py), plus the two
tables every finance module shares.

Budgets (versions, days, monthly lines), per-venue forecasting settings and
holidays, the daily actuals cache, forecast vintages, days, drivers,
overrides and scores, the Loaded budget log and every budget push into
Loaded; and the shared fin_job_runs (the ONE claim table for every finance
tick job) and fin_parity_runs / fin_parity_items (the shadow period's
record of Norm vs Orbit differences).

Idempotent on purpose, as the Functions and payroll migrations are.

Revision ID: ne9h1j3l5n7p
Revises: md8g0i2k4m6o
"""

import sqlalchemy as sa
from alembic import op

revision = "ne9h1j3l5n7p"
down_revision = "md8g0i2k4m6o"
branch_labels = None
depends_on = None

#: Creation order (parents first); downgrade drops in reverse. Pinned by
#: tests/test_finance_foundation.py against the model module.
FIN_TABLES = (
    "fin_holidays",
    "fin_job_runs",
    "fin_access_log",
    "fin_parity_runs",
    "fin_actuals_daily",
    "fin_budget_lines",
    "fin_budget_pushes",
    "fin_budget_versions",
    "fin_forecast_drivers",
    "fin_forecast_overrides",
    "fin_forecast_vintages",
    "fin_loaded_budget_log",
    "fin_parity_items",
    "fin_venue_settings",
    "fin_budget_days",
    "fin_forecast_days",
    "fin_forecast_scores",
)


def upgrade() -> None:
    op.create_table(
        "fin_holidays",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("package", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_holidays_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_holidays_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_holidays_org_id"),
        sa.UniqueConstraint(
            "venue_id",
            "date",
            "name",
            name="uq_fin_holidays_venue_date",
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_job_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("job", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_job_runs_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_job_runs_org_id"),
        sa.UniqueConstraint(
            "organization_id", "job", "scope_id", "slot", name="uq_fin_job_runs_slot"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_parity_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("totals", sa.JSON(), nullable=True),
        sa.Column("ran_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "module IN ('payroll', 'journal', 'entertainers', 'summary', 'inputs')",
            name="ck_fin_parity_runs_module",
        ),
        sa.CheckConstraint(
            "trigger IN ('weekly', 'manual')", name="ck_fin_parity_runs_trigger"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_parity_runs_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ran_by"],
            ["users.id"],
            name="fk_fin_parity_runs_ran_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_parity_runs_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "fin_actuals_daily",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("sales_incl_cents", sa.Integer(), nullable=False),
        sa.Column("sales_ex_cents", sa.Integer(), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_actuals_daily_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_actuals_daily_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_actuals_daily_org_id"
        ),
        sa.UniqueConstraint("venue_id", "date", name="uq_fin_actuals_daily_date"),
        if_not_exists=True,
    )
    op.create_table(
        "fin_budget_lines",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("line_item", sa.String(), nullable=False),
        sa.Column("value_cents", sa.Integer(), nullable=True),
        sa.Column("value_ratio", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "line_item IN ('wage_pct', 'wage_cents', 'cogs_pct', 'food_cogs_pct', 'bev_cogs_pct', 'leave_cents', 'opex_cents', 'depreciation_cents', 'rebates_pct')",
            name="ck_fin_budget_lines_line_item",
        ),
        sa.CheckConstraint(
            "(value_cents IS NULL) <> (value_ratio IS NULL)",
            name="ck_fin_budget_lines_one_value",
        ),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_fin_budget_lines_month"),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_budget_lines_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_budget_lines_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_budget_lines_org_id"),
        sa.UniqueConstraint(
            "venue_id",
            "fiscal_year",
            "month",
            "line_item",
            name="uq_fin_budget_lines_item",
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_budget_pushes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("before_cents", sa.Integer(), nullable=True),
        sa.Column("after_cents", sa.Integer(), nullable=False),
        sa.Column("loaded_id", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("pushed_by", sa.String(), nullable=True),
        sa.Column(
            "pushed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ok", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "method IN ('PUT', 'POST')", name="ck_fin_budget_pushes_method"
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'chat', 'forecast', 'restore')",
            name="ck_fin_budget_pushes_source",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_budget_pushes_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_budget_pushes_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pushed_by"],
            ["users.id"],
            name="fk_fin_budget_pushes_pushed_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_budget_pushes_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_budget_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('import', 'manual', 'forecast')",
            name="ck_fin_budget_versions_source",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_fin_budget_versions_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["locked_by"],
            ["users.id"],
            name="fk_fin_budget_versions_locked_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_budget_versions_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_budget_versions_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_budget_versions_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_forecast_drivers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("uplift_cents", sa.Integer(), nullable=True),
        sa.Column("uplift_bp", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('holiday', 'event', 'function', 'manual')",
            name="ck_fin_forecast_drivers_kind",
        ),
        sa.CheckConstraint(
            "(uplift_cents IS NULL) <> (uplift_bp IS NULL)",
            name="ck_fin_forecast_drivers_one_uplift",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_fin_forecast_drivers_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_forecast_drivers_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_forecast_drivers_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_forecast_drivers_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_forecast_overrides",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_fin_forecast_overrides_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_forecast_overrides_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_forecast_overrides_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_forecast_overrides_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_forecast_vintages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_forecast_vintages_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_forecast_vintages_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_forecast_vintages_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_loaded_budget_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("loaded_id", sa.String(), nullable=True),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_loaded_budget_log_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_loaded_budget_log_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_loaded_budget_log_org_id"
        ),
        sa.UniqueConstraint(
            "venue_id", "date", "captured_on", name="uq_fin_loaded_budget_log_capture"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_parity_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("norm_value", sa.Text(), nullable=True),
        sa.Column("orbit_value", sa.Text(), nullable=True),
        sa.Column("class", sa.String(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("signed_off_by", sa.String(), nullable=True),
        sa.Column("signed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "class IN ('expected', 'unexplained')", name="ck_fin_parity_items_class"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["fin_parity_runs.organization_id", "fin_parity_runs.id"],
            name="fk_fin_parity_items_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_parity_items_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signed_off_by"],
            ["users.id"],
            name="fk_fin_parity_items_signed_off_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_parity_items_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "fin_venue_settings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("closed_weekdays", sa.JSON(), nullable=False),
        sa.Column(
            "level_correction_on",
            sa.Boolean(),
            server_default=sa.text(
                "false"
            ),  # plan §8: a per-venue switch, off by default
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_venue_settings_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_venue_settings_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_venue_settings_org_id"
        ),
        sa.UniqueConstraint("venue_id", name="uq_fin_venue_settings_venue"),
        if_not_exists=True,
    )
    op.create_table(
        "fin_budget_days",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "version_id"],
            ["fin_budget_versions.organization_id", "fin_budget_versions.id"],
            name="fk_fin_budget_days_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_budget_days_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_budget_days_org_id"),
        sa.UniqueConstraint("version_id", "date", name="uq_fin_budget_days_date"),
        if_not_exists=True,
    )
    op.create_table(
        "fin_forecast_days",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("vintage_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("baseline_cents", sa.Integer(), nullable=False),
        sa.Column(
            "uplift_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("forecast_cents", sa.Integer(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "vintage_id"],
            ["fin_forecast_vintages.organization_id", "fin_forecast_vintages.id"],
            name="fk_fin_forecast_days_vintage_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_forecast_days_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_forecast_days_org_id"
        ),
        sa.UniqueConstraint("vintage_id", "date", name="uq_fin_forecast_days_date"),
        if_not_exists=True,
    )
    op.create_table(
        "fin_forecast_scores",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("vintage_id", sa.String(), nullable=True),
        sa.Column("benchmark", sa.String(), nullable=True),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("actual_cents", sa.Integer(), nullable=False),
        sa.Column("forecast_cents", sa.Integer(), nullable=False),
        sa.Column("ape", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("signed_error_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "vintage_id IS NOT NULL OR benchmark IS NOT NULL",
            name="ck_fin_forecast_scores_subject",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fin_forecast_scores_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "vintage_id"],
            ["fin_forecast_vintages.organization_id", "fin_forecast_vintages.id"],
            name="fk_fin_forecast_scores_vintage_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_forecast_scores_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fin_forecast_scores_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fin_access_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("subject_kind", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("route", sa.String(), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fin_access_log_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_fin_access_log_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fin_access_log_org_id"),
        sa.CheckConstraint(
            "subject_kind IN ('performer', 'staff_member')",
            name="ck_fin_access_log_subject_kind",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_access_log_subject",
        "fin_access_log",
        ["organization_id", "subject_kind", "subject_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_job_runs_started",
        "fin_job_runs",
        ["organization_id", "started_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_parity_runs_module",
        "fin_parity_runs",
        ["organization_id", "module", "ran_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_budget_pushes_venue_date",
        "fin_budget_pushes",
        ["organization_id", "venue_id", "date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_budget_versions_venue_id",
        "fin_budget_versions",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_forecast_drivers_venue_date",
        "fin_forecast_drivers",
        ["organization_id", "venue_id", "date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_forecast_overrides_venue_date",
        "fin_forecast_overrides",
        ["organization_id", "venue_id", "date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_forecast_vintages_venue_as_of",
        "fin_forecast_vintages",
        ["organization_id", "venue_id", "as_of"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fin_parity_items_run_id",
        "fin_parity_items",
        ["organization_id", "run_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fin_forecast_scores_subject_date",
        "fin_forecast_scores",
        ["venue_id", "vintage_id", "benchmark", "date"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    for table in reversed(FIN_TABLES):
        op.drop_table(table, if_exists=True)
