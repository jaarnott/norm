"""Norm's MCP server — the external, OAuth-scoped surface.

Norm is the system of record. This package exposes a *curated* subset of its
capabilities to external AI clients (Claude) acting on behalf of an
authenticated user. Norm keeps permissions, tenant/venue access, business
logic, workflow execution, date resolution, approvals and audit.

Layout:
    server.py       JSON-RPC dispatch (initialize / tools-list / tools-call)
    instructions.py the server prompt handed to the client at initialize
    scopes.py       the mcp:* vocabulary and its mapping onto org scopes
    principal.py    McpPrincipal — the resolved caller
    projection.py   config rows -> MCP tool schemas (inverse of the client)
    dependencies.py require_mcp / resolve_mcp_venue — MCP-only authorization
    audit.py        the audit trail
    ratelimit.py    per-token/user/org limits

Invariants this package must hold, each guarding a specific known hole:

- Never import ``app.services.venue_service.get_user_venues`` — it fails open
  (a user with no access rows gets every venue on the platform). Enforced by
  ``tests/test_mcp_imports.py``.
- Never import from ``app.auth.dependencies`` — ``require_permission`` is not
  org-aware and bypasses all checks for platform admins.
- A caller-supplied ``venue_id`` is input to be checked, never an assertion.
"""
