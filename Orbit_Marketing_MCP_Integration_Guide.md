# Orbit Marketing — MCP Integration Guide

**Version**: 1.0  
**Date**: 2026-04-12  
**Contact**: Orbit Engineering

---

## 1. Overview

Orbit Marketing exposes an MCP (Model Context Protocol) server for managing marketing calendars, social posts, content plans, playbooks, and performance metrics. Norm connects to this server as a remote MCP tool provider, authenticates with a per-venue API key, and can render interactive UI components inside Norm's container.

---

## 2. Endpoint

| | |
|---|---|
| **URL** | `https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/mcp-marketing` |
| **Protocol** | MCP Streamable HTTP (POST) |
| **Content-Type** | `application/json` |
| **Accept** | `application/json, text/event-stream` |

---

## 3. Authentication

Every request must include an API key in the `Authorization` header:

```
Authorization: Bearer <api_key>
```

- Each API key is scoped to a single **venue** and a set of **permission scopes**.
- Keys are bcrypt-hashed at rest; the plaintext key is provided once at provisioning.
- Invalid or missing keys return HTTP 401.

### Permission Scopes

| Scope | Grants |
|-------|--------|
| `marketing:read` | Read calendar items, social posts, content plans, playbooks, metrics |
| `marketing:write` | Create/update/delete calendar items, create/edit/approve social posts, trigger imports |

---

## 4. MCP Request Format

Standard MCP JSON-RPC 2.0 over HTTP POST:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_calendar_items",
    "arguments": {
      "start_date": "2026-04-01",
      "end_date": "2026-04-30"
    }
  }
}
```

To discover available tools:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

---

## 5. Tool Reference

### 5.1 Read Tools

#### `get_calendar_items`
Fetch marketing calendar items by date range.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `start_date` | string | ✅ | Start date (YYYY-MM-DD) |
| `end_date` | string | ✅ | End date (YYYY-MM-DD) |
| `type` | string | | Filter: `social`, `event`, `promo`, `other` |
| `status` | string | | Filter: `idea`, `draft`, `approved`, `scheduled`, `completed`, `published`, `failed` |

**Scope**: `marketing:read`  
**Returns**: Array of calendar items with nested social posts and publication status.  
**Embed UI**: Returns an interactive calendar grid (`container_hint: full_page`).

---

#### `get_social_posts`
Fetch social posts with publication status.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `calendar_item_id` | string | | Filter by calendar item |
| `status` | string | | Filter by status |
| `limit` | number | | Max results (default 50) |

**Scope**: `marketing:read`

---

#### `get_content_plans`
Retrieve generated content plans.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | number | | Max results (default 10) |

**Scope**: `marketing:read`

---

#### `get_playbook`
Fetch the venue's marketing playbook. No parameters.

**Scope**: `marketing:read`

---

#### `get_social_metrics`
Fetch performance metrics for social posts.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `post_id` | string | | Filter by social post ID |

**Scope**: `marketing:read`

---

### 5.2 Write Tools

#### `create_calendar_item`
Create a new calendar entry.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date` | string | ✅ | Date (YYYY-MM-DD) |
| `type` | string | | `social`, `event`, `promo`, `other` (default: `social`) |
| `title` | string | | Title (default: "New Post") |
| `description` | string | | Description |
| `status` | string | | Status (default: `idea`) |
| `idempotency_key` | string | | Deduplication key |

**Scope**: `marketing:write`  
**Idempotent**: Yes, when `idempotency_key` provided.

---

#### `update_calendar_item`
Update an existing calendar item.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string | ✅ | Calendar item ID |
| `title` | string | | New title |
| `date` | string | | New date |
| `status` | string | | New status |
| `type` | string | | New type |
| `description` | string | | New description |

**Scope**: `marketing:write`

---

#### `delete_calendar_item`
Remove a calendar item.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string | ✅ | Calendar item ID |

**Scope**: `marketing:write`

---

#### `create_social_post`
Create a social post linked to a calendar item.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `calendar_item_id` | string | ✅ | Calendar item ID |
| `caption` | string | | Post caption |
| `platforms` | string | | Comma-separated: `instagram`, `facebook`, `tiktok`, etc. |
| `pillar` | string | | Content pillar |
| `objective` | string | | Post objective |

**Scope**: `marketing:write`

---

#### `update_social_post`
Edit social post details.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string | ✅ | Social post ID |
| `caption` | string | | New caption |
| `platforms` | string | | Comma-separated platforms |
| `status` | string | | New status |

**Scope**: `marketing:write`

---

#### `approve_social_post`
Move a social post to `approved` status (idempotent).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string | ✅ | Social post ID |

**Scope**: `marketing:write`  
**Idempotent**: Yes — returns `already_approved: true` if already approved.

---

#### `trigger_metricool_import`
Import posts from Metricool (deduplicates by external ID).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date_from` | string | | Start date (YYYY-MM-DD) |
| `date_to` | string | | End date (YYYY-MM-DD) |

**Scope**: `marketing:write`  
**Idempotent**: Yes.

---

## 6. Response Format

### Success (text only)

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"success\":true,\"data\":{\"items\":[...],\"count\":5}}"
    }
  ]
}
```

### Success with Embedded UI

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"success\":true,\"data\":{...},\"container_hint\":\"full_page\"}"
    },
    {
      "type": "resource",
      "resource": {
        "uri": "orbit-marketing://embed/calendar",
        "mimeType": "text/html",
        "text": "https://orbit-app.lovable.app/embed/marketing/calendar?venue_id=xxx&month=2026-04"
      }
    }
  ]
}
```

**`container_hint`** values: `full_page`, `side_panel`, `inline_card`

The `resource.text` field contains the HTTPS URL to embed in an iframe.

### Error

```json
{
  "content": [
    { "type": "text", "text": "{\"error\":\"Item not found\",\"code\":\"NOT_FOUND\"}" }
  ],
  "isError": true
}
```

**Error codes**: `UNAUTHORIZED`, `VALIDATION_ERROR`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`, `INTERNAL_ERROR`

---

## 7. Embeddable UI Components

### Available Components

| Route | Description | Container Hint |
|-------|-------------|----------------|
| `/embed/marketing/calendar` | Monthly calendar grid with social post status indicators | `full_page` |
| `/embed/marketing/post-editor` | Social post caption editor with approve/save actions | `full_page` |

**Base URL**: `https://orbit-app.lovable.app`

### URL Parameters

| Parameter | Description |
|-----------|-------------|
| `venue_id` | Required. The venue UUID. |
| `month` | For calendar view. Format: `YYYY-MM` |
| `post_id` | For post editor. The social post UUID. |

### Theme Tokens

Pass design tokens as URL query parameters to style embedded components:

| Parameter | CSS Variable | Description |
|-----------|-------------|-------------|
| `norm-bg` | `--norm-bg` | Background color |
| `norm-text` | `--norm-text` | Text color |
| `norm-primary` | `--norm-primary` | Primary accent color |
| `norm-primary-foreground` | `--norm-primary-foreground` | Text on primary |
| `norm-secondary` | `--norm-secondary` | Secondary color |
| `norm-muted` | `--norm-muted` | Muted background |
| `norm-muted-foreground` | `--norm-muted-foreground` | Muted text |
| `norm-border` | `--norm-border` | Border color |
| `norm-radius` | `--norm-radius` | Border radius |
| `norm-font-family` | `--norm-font-family` | Font family |
| `norm-font-size` | `--norm-font-size` | Base font size |
| `norm-spacing` | `--norm-spacing` | Base spacing unit |
| `norm-mode` | `--norm-mode` | `dark` or `light` |

**Example**:
```
https://orbit-app.lovable.app/embed/marketing/calendar?venue_id=abc123&month=2026-04&norm-primary=%23007AFF&norm-mode=dark
```

### postMessage Bridge

#### Outbound (Orbit Marketing → Norm)

All messages have `source: "orbit-embed"`.

**`resize`** — Report content height
```json
{ "source": "orbit-embed", "type": "resize", "payload": { "height": 842 } }
```

**`action`** — User interaction for follow-up MCP calls
```json
{ "source": "orbit-embed", "type": "action", "payload": { "action": "approve_post", "postId": "..." } }
```

Actions emitted:
| Action | Payload | Description |
|--------|---------|-------------|
| `select_date` | `{ date: "2026-04-15" }` | User clicked a calendar date |
| `select_post` | `{ postId: "..." }` | User selected a social post |
| `approve_post` | `{ postId: "..." }` | User approved a post |
| `save_caption` | `{ postId: "...", caption: "..." }` | User saved a caption edit |

**`navigate`** — Request view change
```json
{ "source": "orbit-embed", "type": "navigate", "payload": { "target": "post-editor", "params": { "postId": "..." } } }
```

**`state_update`** — Data change notification
```json
{ "source": "orbit-embed", "type": "state_update", "payload": { "entity": "social_post", "id": "...", "changes": { "status": "approved" } } }
```

#### Inbound (Norm → Orbit Marketing)

All messages must have `source: "norm-host"`.

| Event | Payload | Description |
|-------|---------|-------------|
| `theme_update` | `{ "norm-primary": "#007AFF", ... }` | Push new design tokens |
| `set_filter` | `{ "status": "approved" }` | Change active filter |
| `refresh` | `{}` | Reload data |

---

## 8. Quick Start

### List tools

```bash
curl -X POST \
  https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/mcp-marketing \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### Call a tool

```bash
curl -X POST \
  https://wmemzqupwrmyydacifoy.supabase.co/functions/v1/mcp-marketing \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0","id":1,
    "method":"tools/call",
    "params":{"name":"get_calendar_items","arguments":{"start_date":"2026-04-01","end_date":"2026-04-30"}}
  }'
```

---

## 9. Provisioning

1. **We provide**: A venue-scoped API key with `marketing:read` and `marketing:write` scopes
2. **Norm configures**: The MCP server URL as a remote tool provider with `Bearer` auth
3. **Norm implements**: iframe embedding for `resource` responses, listening for `postMessage` events with `source: "orbit-embed"`
4. **Norm sends** (optional): `theme_update`, `set_filter`, `refresh` messages with `source: "norm-host"`

---

## 10. Data Isolation & Safety

- All data access is scoped to the authenticated venue — a key for Venue A cannot access Venue B's data.
- All write tools that accept duplicated inputs are **idempotent** (documented per tool).
- No explicit rate limiting is currently enforced.
