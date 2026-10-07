"""Record what a model call really used: cache tokens, billable tokens, cost.

Anthropic splits a call's input into full-price, cache-read and cache-write
tokens. Only the first was stored, so from 4 Oct 2026 — when the whole
conversation became cached — llm_calls and token_usage recorded close to zero
input while the bill did not (7 Oct review: 200 input tokens stored for a day
that processed 3.6M). Plan limits read token_usage, so they were measuring a
fraction of real use too.

llm_calls also gains organization_id: a call with no thread (invoice
extraction, summarisation — 510 calls in the week to 7 Oct) had no user, so it
reached no organisation's totals at all.

messages gains sent_content: a user turn exactly as the model was sent it, so
later turns replay it verbatim and the cached prefix holds (see the model).

Revision ID: x9y0z1a2b3c4
Revises: w8x9y0z1a2b3
"""

from alembic import op

revision = "x9y0z1a2b3c4"
down_revision = "w8x9y0z1a2b3"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("cache_read_tokens", "INTEGER"),
    ("cache_write_tokens", "INTEGER"),
    ("billable_tokens", "INTEGER"),
    ("cost_usd", "NUMERIC(14, 6)"),
)


# IF NOT EXISTS: the shared local dev/test database may already have these
# columns (added by hand while this was being built), and a re-run must not
# fail there.
def upgrade() -> None:
    for table in ("llm_calls", "token_usage"):
        for name, type_ in _COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {type_}")
    op.execute("ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS organization_id VARCHAR")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_calls_org_created "
        "ON llm_calls (organization_id, created_at)"
    )
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS sent_content TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS sent_content")
    op.execute("DROP INDEX IF EXISTS ix_llm_calls_org_created")
    op.execute("ALTER TABLE llm_calls DROP COLUMN IF EXISTS organization_id")
    for table in ("llm_calls", "token_usage"):
        for name, _ in _COLUMNS:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {name}")
