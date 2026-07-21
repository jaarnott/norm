/**
 * The roster Artifact.
 *
 * Why this exists at all: the roster ALREADY renders inside Claude as an MCP
 * App (ui://norm/display-block). But that card is sized by the conversation,
 * and a 7-day x N-staff grid does not fit a conversation-width box. This is
 * the same component, the same live data, on a full page.
 *
 * The connection runs the other way from the MCP App. There, the host hands
 * the iframe a channel back into the Norm session that opened it. Here the
 * page is hosted by claude.ai and reaches Norm OUTWARD through
 * `window.claude.mcp`, using the viewer's own connector credentials — so it
 * shows each viewer their own venues, and shows nothing to someone without
 * the Norm connector. Read-only in this build (see artifact-api.ts).
 *
 * Two things are deliberately NOT computed here:
 *
 * - The window. The page holds a calendar DATE and steps it by seven days;
 *   Norm turns "week beginning <date>" into a trading week. A hospitality
 *   week runs 07:00 Monday to 06:59 the next Monday, in the venue's zone,
 *   across daylight-saving transitions. Every one of those is a fact this
 *   page would get wrong, so it never holds a timestamp at all.
 * - The venue list. Calling with no venue makes Norm answer with the venues
 *   this viewer consented to; the picker is built from that reply. Baking in
 *   a list would show one org's venues to everyone, and go stale.
 */
import { StrictMode, useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import RosterEditor from '../../web/app/components/display/RosterEditor';

// The connector's DISPLAY NAME, as the viewer sees it in claude.ai Settings —
// not the `mcp__claude_ai_Norm__…` prefix those tools carry inside Claude Code.
// That prefix is a client-side spelling; publishing it produced a manifest that
// matched no connector, and every call came back `not_in_manifest`. The
// authority is the connector list (`/v1/mcp_servers` → `display_name`), and it
// says "Norm". The same string must be used here and in the published
// manifest, or the two disagree and nothing resolves.
const SERVER = 'Norm';
const TOOL = 'loadedhub__get_roster_for_period';

/**
 * The connector channel, or null.
 *
 * Read synchronously at every use rather than cached in state: an effect that
 * sets `noMcp` runs AFTER the effects that would call, so a state flag leaves
 * a window in which the call sites dereference an absent `window.claude`. That
 * is not theoretical — it threw on first load and blanked the page, which is
 * the one outcome the no-MCP notice exists to prevent. The member check is
 * also the only availability gate valid on every runtime generation, and
 * unlike a probing call it cannot throw.
 */
function mcp(): typeof Claude.mcp | null {
  return window.claude?.mcp ?? null;
}

// ── Dates: calendar arithmetic only, never clocks ────────────────────────

/** Add days to a YYYY-MM-DD date. UTC so no local midnight/DST edge exists. */
function shiftDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

/** The Monday on or before today, in the viewer's own calendar. */
function thisMonday(): string {
  const now = new Date();
  const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
    now.getDate(),
  ).padStart(2, '0')}`;
  const dow = (new Date(`${iso}T00:00:00Z`).getUTCDay() + 6) % 7; // Mon = 0
  return shiftDays(iso, -dow);
}

function humanWeek(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  return at.toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  });
}

// ── Reading Norm's replies ───────────────────────────────────────────────

interface McpErrorish {
  code?: string;
  message?: string;
  server?: string;
  retryable?: boolean;
  retryAfterMs?: number;
  result?: unknown;
}

/** The text Norm sent, wherever the runtime put it. */
function normMessage(err: McpErrorish): string {
  const fromResult = err?.result as { error?: string } | undefined;
  if (typeof fromResult?.error === 'string') return fromResult.error;
  return err?.message || '';
}

/**
 * The venue names out of "Which venue? Specify one of: A, B, C".
 *
 * Parsing a message is normally a smell, but this IS the interface: the venue
 * enum lives in the tool's input schema, and listTools() returns names and
 * descriptions only — a page cannot read a schema. Norm's own refusal is the
 * only runtime source of the viewer's venues, and it is the same sentence a
 * human is shown. Returns [] when it doesn't match, and the caller then treats
 * the error as an error.
 */
function venuesFromRefusal(message: string): string[] {
  const m = message.match(/Specify one of:\s*(.+)$/);
  if (!m) return [];
  return m[1].split(',').map((s) => s.trim()).filter(Boolean);
}

interface Block {
  component?: string;
  data?: unknown;
  props?: Record<string, unknown>;
}

// ── Degraded states, one per cause ───────────────────────────────────────

/**
 * Copy per error code. Collapsing these into one banner is the thing to avoid:
 * "reconnect Norm", "add Norm", "your admin blocks this" and "try again" are
 * four different actions, and only one of them is the viewer's next move.
 */
function explain(err: McpErrorish): { title: string; detail: string; canRetry: boolean } {
  const server = err?.server || 'Norm';
  switch (err?.code) {
    case 'needs_reauth':
      return {
        title: `${server} needs reconnecting`,
        detail: `Your ${server} connection has expired. Reconnect it in claude.ai Settings → Connectors, then reload this page.`,
        canRetry: false,
      };
    case 'server_not_connected':
      return {
        title: `${server} isn't connected`,
        detail: `This roster comes from ${server}. Add the connector in claude.ai Settings → Connectors to see it.`,
        canRetry: false,
      };
    case 'selection_required':
      return {
        title: `Choose which ${server} to use`,
        detail: `You have more than one connector named ${server}. Pick one when claude.ai asks, then reload.`,
        canRetry: false,
      };
    case 'not_in_manifest':
      // Almost always a fault in the PAGE, not in the viewer's setup: the
      // published manifest named a connector or tool that does not resolve.
      // The first version of this copy told the viewer to reload and approve,
      // which no amount of approving could fix — it sent them chasing a
      // permission problem that did not exist. Say who has to fix it.
      return {
        title: 'This page is asking for the wrong thing',
        detail: `This page did not ask for ${server} correctly, so it cannot read the roster. Reloading will not help — the page itself needs updating.`,
        canRetry: false,
      };
    case 'blocked_by_policy':
      return {
        title: 'Blocked by your organisation',
        detail: `Your admin's policy blocks this page from calling ${server}.`,
        canRetry: false,
      };
    case 'approval_required':
      return {
        title: 'Needs approval',
        detail: 'Your organisation requires per-call approval for this tool, which artifacts cannot request yet. Ask Norm for the roster in a conversation instead.',
        canRetry: false,
      };
    case 'server_unavailable':
      return {
        title: 'Norm is briefly unreachable',
        detail: 'The connector did not answer. This usually clears on its own.',
        canRetry: true,
      };
    case 'rate_limited':
      return {
        title: 'Too many requests',
        detail: 'This page has made too many calls. Wait a moment before refreshing.',
        canRetry: true,
      };
    case 'tool_error':
      return {
        // Norm's own refusals (no access to that venue, unresolvable period)
        // are answers, not faults — show what it actually said.
        title: 'Norm could not answer',
        detail: normMessage(err) || 'Norm reported a problem with this request.',
        canRetry: false,
      };
    case 'not_granted':
    case 'capability_disabled':
    case 'capability_removed':
      return {
        title: 'No connector access here',
        detail: 'This view cannot reach your connectors. Open the artifact from a claude.ai conversation.',
        canRetry: false,
      };
    default:
      return {
        title: 'Could not load the roster',
        detail: normMessage(err) || 'Something went wrong reaching Norm.',
        canRetry: Boolean(err?.retryable),
      };
  }
}

function Notice({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) {
  return (
    <div className="notice">
      <strong>{title}</strong>
      <p>{detail}</p>
      {action}
    </div>
  );
}

// ── The page ─────────────────────────────────────────────────────────────

function App() {
  const [weekStart, setWeekStart] = useState<string>(() => thisMonday());
  const [venue, setVenue] = useState<string | null>(null);
  const [venueChoices, setVenueChoices] = useState<string[]>([]);
  const [block, setBlock] = useState<Block | null>(null);
  const [error, setError] = useState<McpErrorish | null>(null);
  const [loading, setLoading] = useState(true);
  const [storedAt, setStoredAt] = useState<number | null>(null);
  // Lazy initial state, not an effect: known before the first render, so no
  // call site can run in a window where availability is still unresolved.
  const [noMcp] = useState(() => mcp() === null);

  const period = useMemo(() => `week beginning ${weekStart}`, [weekStart]);

  /**
   * One probe with no venue. A viewer with a single venue gets their roster
   * straight back; a viewer with several gets the refusal that names them.
   * Either way the picker is populated from Norm rather than from this file.
   */
  useEffect(() => {
    const api = mcp();
    if (!api || venue !== null) return;
    let live = true;
    setLoading(true);
    api
      .callTool(SERVER, TOOL, { period }, { cache: { staleTime: 30_000 } })
      .then((res) => {
        if (!live) return;
        setBlock((res.payload || {}) as Block);
        setStoredAt(res.cache?.storedAt ?? null);
        setError(null);
        setLoading(false);
      })
      .catch((err: McpErrorish) => {
        if (!live) return;
        const names = err?.code === 'tool_error' ? venuesFromRefusal(normMessage(err)) : [];
        if (names.length) {
          setVenueChoices(names);
          setVenue(names[0]);
        } else {
          setError(err);
        }
        setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [venue, period]);

  /**
   * Once a venue is known the roster is WATCHED, not fetched: this is display
   * data that should stay current while the page sits open, and a watch
   * replays the cache, refreshes when stale, and coalesces.
   */
  useEffect(() => {
    const api = mcp();
    if (!api || !venue) return;
    setLoading(true);
    const stop = api.watchTool(
      SERVER,
      TOOL,
      { venue, period },
      (ev) => {
        if (ev.type === 'data') {
          setBlock((ev.result.payload || {}) as Block);
          setStoredAt(ev.result.cache?.storedAt ?? null);
          setError(null);
          setLoading(false);
          return;
        }
        const err = ev.error as McpErrorish;
        setError(err);
        setLoading(false);
        // An authorization denial must RETRACT what is on screen — continuing
        // to show a roster the viewer may no longer read is the worse failure.
        // Transient errors keep the last good grid visible.
        if (
          err?.code === 'needs_reauth' ||
          err?.code === 'server_not_connected' ||
          err?.code === 'blocked_by_policy' ||
          err?.code === 'approval_required'
        ) {
          setBlock(null);
        }
      },
      { cache: { staleTime: 60_000 } },
    );
    return stop;
  }, [venue, period]);

  // Dropping the cached entry makes the live watch re-execute and deliver —
  // there is no second code path for "refresh".
  const refresh = useCallback(() => {
    mcp()?.invalidate(SERVER, TOOL).catch(() => {});
  }, []);

  if (noMcp) {
    return (
      <Notice
        title="This page needs the Norm connector"
        detail="The roster is read live from Norm with your own credentials. Open this artifact from a claude.ai conversation where the Norm connector is available."
      />
    );
  }

  // Norm answers a period it cannot resolve inside the payload rather than as
  // a tool failure, so it is checked separately from the error branches.
  const payloadError = (block?.data as { error?: string } | undefined)?.error;
  const detail = error ? explain(error) : null;
  const shifts = Array.isArray(block?.data) ? block!.data : null;

  return (
    <div className="page">
      <header className="bar">
        <div className="ident">
          <span className="brand">Norm</span>
          <span className="muted">Roster</span>
        </div>

        {venueChoices.length > 1 && (
          <label className="venue">
            <span className="muted">Venue</span>
            <select value={venue ?? ''} onChange={(e) => setVenue(e.target.value)}>
              {venueChoices.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </label>
        )}

        <nav className="weeknav">
          <button onClick={() => setWeekStart((w) => shiftDays(w, -7))} aria-label="Previous week">←</button>
          <span className="week">Week of {humanWeek(weekStart)}</span>
          <button onClick={() => setWeekStart((w) => shiftDays(w, 7))} aria-label="Next week">→</button>
          <button className="today" onClick={() => setWeekStart(thisMonday())}>This week</button>
        </nav>

        <div className="freshness">
          {loading && <span className="muted">Loading…</span>}
          {/* Only a CACHED result carries storedAt; a fresh one has no marker,
              so this stays quiet rather than inventing a time from the clock. */}
          {!loading && storedAt && (
            <span className="muted">as at {new Date(storedAt).toLocaleTimeString()}</span>
          )}
          <button onClick={refresh}>Refresh</button>
        </div>
      </header>

      {detail && (
        <Notice
          title={detail.title}
          detail={detail.detail}
          action={detail.canRetry ? <button onClick={refresh}>Try again</button> : undefined}
        />
      )}

      {payloadError && !detail && <Notice title="Norm could not read that week" detail={payloadError} />}

      {shifts && (
        <div className="grid-wrap">
          <RosterEditor
            data={shifts as unknown as Record<string, unknown>}
            props={{ ...(block?.props || {}), embedded: true }}
          />
        </div>
      )}

      {!shifts && !detail && !payloadError && !loading && (
        <Notice title="Nothing rostered" detail="Norm returned no shifts for this week." />
      )}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
