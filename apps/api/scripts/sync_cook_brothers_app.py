"""Add the Cook Brothers App connector (MCP + OAuth 2.1) and retire Orbit.

Cook Brothers App is the successor to Orbit Marketing: the same Supabase backend,
a new endpoint (``/functions/v1/mcp/venue``), and new auth — a public-client
OAuth 2.1 server with PKCE (S256) and RFC 7591 dynamic registration (no
client_secret). Norm self-registers a client on first connect (see
``oauth_service.register_client`` / ``routers/oauth.oauth_authorize``), so the
``client_id`` here is left unset and filled in at connect time.

Tools are left empty on purpose — discover them from the live server with
``POST /connector-specs/cook_brothers_app/sync-mcp-tools`` once a venue has
connected (the discovery call authenticates with the venue's OAuth token).

This also DELETES the old ``orbit_marketing`` spec and its agent bindings (a
backup of those rows was captured separately before removal).

The config DB is shared across every environment, so committing this reaches
production immediately. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_cook_brothers_app.py --dry-run
    .venv/bin/python scripts/sync_cook_brothers_app.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "cook_brothers_app"
BASE_URL = "https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/mcp/venue"

OAUTH_CONFIG = {
    "authorize_url": "https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/oauth/authorize",
    "token_url": "https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/oauth/token",
    "registration_url": "https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/oauth/register",
    "revocation_url": "https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/oauth/revoke",
    "scopes": "mcp",
    "token_endpoint_auth_method": "none",
    "pkce": True,
    "client_name": "Norm",
}

# Fields the running app writes onto oauth_config at connect time — never clobber
# them when re-running this script.
_PRESERVE_OAUTH_KEYS = (
    "client_id",
    "client_secret",
    "registration_access_token",
    "registration_client_uri",
    "client_id_issued_at",
)

OLD_CONNECTOR = "orbit_marketing"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectorBinding, ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        # --- 1. Create / update the cook_brothers_app spec -------------------
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if spec:
            # Refresh the endpoints but preserve a registered client_id + tools.
            merged = dict(OAUTH_CONFIG)
            existing_oauth = spec.oauth_config or {}
            for k in _PRESERVE_OAUTH_KEYS:
                if existing_oauth.get(k):
                    merged[k] = existing_oauth[k]
            spec.display_name = "Cook Brothers App"
            spec.category = "marketing"
            spec.execution_mode = "mcp"
            spec.auth_type = "oauth2"
            spec.base_url_template = BASE_URL
            spec.oauth_config = merged
            spec.enabled = True
            flag_modified(spec, "oauth_config")
            print(
                f"updating existing '{CONNECTOR}' spec "
                f"(preserving client_id={'yes' if merged.get('client_id') else 'no'}, "
                f"tools={len(spec.tools or [])})"
            )
        else:
            spec = ConnectorSpec(
                connector_name=CONNECTOR,
                display_name="Cook Brothers App",
                category="marketing",
                execution_mode="mcp",
                auth_type="oauth2",
                auth_config={},
                base_url_template=BASE_URL,
                tools=[],
                credential_fields=[],
                oauth_config=dict(OAUTH_CONFIG),
                enabled=True,
            )
            db.add(spec)
            print(f"creating '{CONNECTOR}' spec (execution_mode=mcp, auth_type=oauth2)")
        print(f"  base_url: {BASE_URL}")
        print("  tools: [] (discover with sync-mcp-tools after first connect)")

        # --- 2. Retire orbit_marketing --------------------------------------
        orbit = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == OLD_CONNECTOR)
            .first()
        )
        orbit_bindings = (
            db.query(AgentConnectorBinding)
            .filter(AgentConnectorBinding.connector_name == OLD_CONNECTOR)
            .all()
        )
        print(
            f"retiring '{OLD_CONNECTOR}': spec={'present' if orbit else 'absent'}, "
            f"bindings={[b.agent_slug for b in orbit_bindings]}"
        )

        if args.dry_run:
            print("(dry run — nothing written)")
            return

        for b in orbit_bindings:
            db.delete(b)
        if orbit:
            db.delete(orbit)
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
