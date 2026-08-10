/**
 * Sandbox implementation of apps/web/app/lib/api for the MCP App bundle.
 *
 * The vite build swaps this in for `lib/api` (see vite.config.ts), so the
 * SAME components the web app mounts — PurchaseOrderEditor, RosterEditor —
 * run unmodified inside the sandboxed iframe. Where the web implementation
 * fetches Norm's REST API with a session token, this one routes the exact
 * same calls through the MCP host (`NormApp.callTool`), which re-enters
 * Norm's authenticated dispatch:
 *
 *   GET  /api/threads/:t/working-documents/:id  -> norm__get_working_document
 *   PATCH                                        -> norm__update_working_document
 *   callComponentApi(reads)                      -> norm__component_api (paged)
 *   callComponentApi('create_orders_batch')      -> norm__place_stock_order
 *
 * Anything else is answered with a 404-shaped Response — components already
 * treat that as "reference data unavailable" and degrade the way they do in
 * any other constrained surface.
 */

// window.NormApp comes from _bridge.js; its type lives in normapp.d.ts.

export function getToken(): string | null {
  return null;
}
export function setToken(_token: string): void {}
export function clearToken(): void {}

export function getStoredUser(): {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions?: string[];
  org_role?: { name: string; display_name: string } | null;
} | null {
  // No session in the sandbox. Components read this only for admin-gated
  // extras (debug panels), which have no place in an embedded card.
  return null;
}
export function setStoredUser(_user: unknown): void {}

export async function apiStream(): Promise<never> {
  throw new Error('apiStream is not available in the MCP sandbox');
}

interface ToolReply {
  structuredContent?: unknown;
  content?: { type: string; text?: string }[];
  isError?: boolean;
}

/** The payload out of a tools/call reply: structuredContent, else parsed text. */
function toolPayload(reply: unknown): Record<string, unknown> | null {
  const r = (reply || {}) as ToolReply;
  if (r.structuredContent && typeof r.structuredContent === 'object') {
    return r.structuredContent as Record<string, unknown>;
  }
  const t = Array.isArray(r.content) ? r.content.find((c) => c?.type === 'text') : null;
  if (t?.text) {
    try {
      const parsed = JSON.parse(t.text);
      if (parsed && typeof parsed === 'object') return parsed as Record<string, unknown>;
    } catch {
      /* not json */
    }
  }
  return null;
}

function isErrorReply(reply: unknown): boolean {
  return Boolean((reply as ToolReply)?.isError);
}

/** A minimal Response-shaped object — components use ok/status/json() only. */
function jsonResponse(ok: boolean, data: unknown, status?: number): Response {
  return {
    ok,
    status: status ?? (ok ? 200 : 400),
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as unknown as Response;
}

// Working documents (purchase-order editor) — thread-scoped or taskless.
const WD_URL = /\/(?:threads\/[^/]+\/)?working-documents\/([^/?]+)$/;
// Received-invoice drafts use a distinct URL so they route to the invoice-scoped
// tools (norm__*_invoice_document, scope mcp:invoices:draft) rather than the
// orders tools — MCP scope projection is all-of, so the two can't share a tool.
const INVOICE_WD_URL = /\/(?:threads\/[^/]+\/)?invoice-documents\/([^/?]+)$/;

async function routeDoc(
  docId: string,
  getTool: string,
  updateTool: string,
  init?: RequestInit,
): Promise<Response> {
  const method = (init?.method || 'GET').toUpperCase();
  try {
    if (method === 'GET') {
      const reply = await window.NormApp.callTool(getTool, { working_document_id: docId });
      const doc = toolPayload(reply);
      return jsonResponse(!isErrorReply(reply) && !!doc, doc ?? {});
    }
    if (method === 'PATCH') {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const reply = await window.NormApp.callTool(updateTool, {
        working_document_id: docId,
        ops: body.ops ?? [],
        version: body.version,
      });
      const doc = toolPayload(reply);
      if (doc && (doc as { conflict?: boolean }).conflict) {
        return jsonResponse(false, doc, 409);
      }
      return jsonResponse(!isErrorReply(reply) && !!doc, doc ?? {});
    }
  } catch {
    return jsonResponse(false, {}, 502);
  }
  return jsonResponse(false, { detail: 'Not available in this surface' }, 404);
}

export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const inv = url.match(INVOICE_WD_URL);
  if (inv) {
    return routeDoc(
      inv[1],
      'norm__get_invoice_document',
      'norm__update_invoice_document',
      init,
    );
  }
  const m = url.match(WD_URL);
  if (m) {
    return routeDoc(
      m[1],
      'norm__get_working_document',
      'norm__update_working_document',
      init,
    );
  }
  // Accept & Receive — the one invoice write. The editor sends only
  // {venue_id, invoice_id}; a lines-less invoice makes the API's
  // _receive_invoice take the doc-driven path (the server builds the receive
  // request from the working document, exactly like the web endpoint).
  if (/\/api\/invoice-fixes\/receive$/.test(url) && (init?.method || '').toUpperCase() === 'POST') {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    try {
      const reply = await window.NormApp.callTool('norm__receive_invoice', {
        venue_id: body.venue_id,
        invoice: {
          invoice_id: body.invoice_id,
          receive: true,
        },
      });
      const p = (toolPayload(reply) ?? {}) as { submitted?: boolean; detail?: unknown };
      if (isErrorReply(reply)) return jsonResponse(false, p, 400);
      return jsonResponse(true, p);
    } catch {
      return jsonResponse(false, {}, 502);
    }
  }
  return jsonResponse(false, { detail: 'Not available in this surface' }, 404);
}

export async function callComponentApi(
  componentKey: string,
  actionName: string,
  params: Record<string, unknown> | unknown[] = {},
  venueId?: string,
): Promise<{ data: unknown; status_code: number; error?: boolean }> {
  // The one write: Place Order. Same contract as the web path — an upstream
  // refusal comes back as {error: true} for the button's failed state.
  if (actionName === 'create_orders_batch') {
    const reply = await window.NormApp.callTool('norm__place_stock_order', {
      venue_id: venueId,
      orders: params,
    });
    if (isErrorReply(reply)) {
      const p = toolPayload(reply);
      throw new Error(String((p as { error?: string })?.error ?? 'Order could not be placed'));
    }
    const p = toolPayload(reply) ?? {};
    return {
      data: (p as { detail?: unknown }).detail,
      status_code: Number((p as { status_code?: number }).status_code ?? 0),
      error: (p as { submitted?: boolean }).submitted === false ? true : undefined,
    };
  }

  // Reads: the bridge pages large lists; reassemble before handing to the
  // component, which expects the full array in one go.
  const all: unknown[] = [];
  let page = 0;
  let totalPages = 1;
  let statusCode = 200;
  let single: unknown = null;
  let sawList = false;
  do {
    const reply = await window.NormApp.callTool('norm__component_api', {
      venue_id: venueId,
      component_key: componentKey,
      action_name: actionName,
      params,
      page,
    });
    if (isErrorReply(reply)) {
      const p = toolPayload(reply);
      throw new Error(String((p as { error?: string })?.error ?? 'Component API error'));
    }
    const p = toolPayload(reply);
    if (!p) throw new Error('Component API returned no data');
    statusCode = Number((p as { status_code?: number }).status_code ?? 200);
    const data = (p as { data?: unknown }).data;
    if (Array.isArray(data)) {
      sawList = true;
      all.push(...data);
      totalPages = Number((p as { total_pages?: number }).total_pages ?? 1);
      page += 1;
    } else {
      single = data;
      break;
    }
  } while (page < totalPages);

  return { data: sawList ? all : single, status_code: statusCode };
}
