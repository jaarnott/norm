'use client';

/**
 * The card every page write to Loaded or Xero shows before it lands
 * (plan §5.1). The page gives it two calls: `preview` (what would change,
 * from live data, with a fingerprint) and `confirm` (the write, sent with
 * that fingerprint). The server refuses the confirm with a 409 when Loaded
 * or Xero moved since the person looked and hands back a fresh card, which
 * this component shows for a second look; the person confirms THAT.
 *
 * `pageWrite()` builds the two calls for the generic routes
 * `POST /api/connectors/{name}/execute/{action}/preview` and `/confirm`;
 * a module's own wrappers (`/api/payroll/xero/journal/preview`) pass their
 * own two functions.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { TriangleAlert } from 'lucide-react';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { FinApiError, finSend, errorText } from './useFinanceApi';

export interface PreviewChange {
  field: string;
  before?: string;
  after: string;
}

export interface PreviewCard {
  title?: string;
  target?: string | null;
  venue?: string | null;
  changes?: PreviewChange[];
  warnings?: string[];
  note?: string | null;
  fingerprint?: string | null;
  more_changes?: number;
  [extra: string]: unknown;
}

export interface PreviewResult {
  card: PreviewCard | null;
  fingerprint: string | null;
  /** The write had nothing to do; the server already answered. */
  done?: unknown;
}

export interface ConfirmResult {
  success?: boolean;
  data?: unknown;
  [extra: string]: unknown;
}

export interface PageWrite {
  preview: () => Promise<PreviewResult>;
  confirm: (fingerprint: string | null) => Promise<ConfirmResult>;
}

/** The two calls for a tool on the generic connector routes. */
export function pageWrite(connector: string, action: string, params: Record<string, unknown>, venueId?: string | null): PageWrite {
  const base = `/api/connectors/${connector}/execute/${action}`;
  return {
    preview: () => finSend<PreviewResult>('POST', `${base}/preview`, { params, venue_id: venueId ?? undefined }),
    confirm: (fingerprint) => finSend<ConfirmResult>('POST', `${base}/confirm`, { params, venue_id: venueId ?? undefined, fingerprint }),
  };
}

/** A 409 from confirm carries the card as it is now. */
function changedCard(e: unknown): { card: PreviewCard | null; fingerprint: string | null; message: string } | null {
  if (!(e instanceof FinApiError) || e.status !== 409) return null;
  const d = e.detail as { error?: string; changed?: PreviewCard | null; fingerprint?: string | null } | null;
  if (!d || typeof d !== 'object') return null;
  return { card: d.changed ?? null, fingerprint: d.fingerprint ?? d.changed?.fingerprint ?? null, message: d.error || e.message };
}

export default function ApprovalCard({ write, confirmLabel, busyLabel, title, intro, onDone, onCancel, danger = false, autoPreview = true, children }: {
  write: PageWrite;
  confirmLabel: string;
  busyLabel?: string;
  /** Shown over the card's own title (which comes from the tool's preview). */
  title?: ReactNode;
  intro?: ReactNode;
  onDone: (result: ConfirmResult | { done: unknown }) => void;
  onCancel?: () => void;
  danger?: boolean;
  autoPreview?: boolean;
  /** Extra content under the card (a checkbox, a note). */
  children?: ReactNode;
}) {
  const [card, setCard] = useState<PreviewCard | null>(null);
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [loading, setLoading] = useState(autoPreview);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState<string | null>(null);
  const [previewed, setPreviewed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setChanged(null);
    try {
      const res = await write.preview();
      if (res.card === null && res.done !== undefined) {
        onDone({ done: res.done });
        return;
      }
      setCard(res.card);
      setFingerprint(res.fingerprint ?? res.card?.fingerprint ?? null);
      setPreviewed(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [write, onDone]);

  useEffect(() => {
    if (autoPreview) void load();
    // The write is built once by the dialog that opens the card.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await write.confirm(fingerprint);
      onDone(res);
    } catch (e) {
      const fresh = changedCard(e);
      if (fresh) {
        // Loaded or Xero moved: show what it is now and ask again.
        setCard(fresh.card);
        setFingerprint(fresh.fingerprint);
        setChanged(fresh.message || 'This changed since you looked — check it again and confirm.');
      } else {
        setError(errorText(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const changes = card?.changes ?? [];

  return (
    <div className="n-card" style={{ padding: 14, minWidth: 0 }} data-testid="approval-card">
      {title && <div style={{ fontWeight: 600, marginBottom: 6 }}>{title}</div>}
      {intro && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 10 }}>{intro}</div>}
      {changed && (
        <div role="alert" style={{ display: 'flex', gap: 6, alignItems: 'flex-start', padding: '8px 12px', marginBottom: 10, borderRadius: 'var(--radius)', background: 'var(--warn-bg)', color: 'var(--warn)', fontSize: 'var(--fs-sm)' }}>
          <TriangleAlert size={14} aria-hidden style={{ marginTop: 2, flex: '0 0 auto' }} />
          <span>{changed}</span>
        </div>
      )}
      {loading ? (
        <PageState kind="loading" title="Checking what this would change…" />
      ) : error && !card ? (
        <PageState kind="error" title="This can’t be done right now." detail={error} action={<Button size="sm" onClick={() => void load()}>Try again</Button>} />
      ) : !previewed ? (
        <Button size="sm" onClick={() => void load()}>Show what this would change</Button>
      ) : card ? (
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{card.title || 'What this does'}</span>
            {card.target && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{card.target}</span>}
            {card.venue && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{card.venue}</span>}
          </div>
          {changes.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(90px, max-content) 1fr', columnGap: 12, rowGap: 3, marginTop: 8, fontSize: 'var(--fs-sm)' }}>
              {changes.map((c, i) => (
                <div key={i} style={{ display: 'contents' }}>
                  <span style={{ color: 'var(--muted)', overflowWrap: 'anywhere' }}>{c.field}</span>
                  <span style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>
                    {c.before !== undefined && c.before !== null && (
                      <>
                        <span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>{c.before}</span>
                        <span aria-hidden style={{ color: 'var(--icon)', margin: '0 0.3rem' }}>→</span>
                      </>
                    )}
                    <span style={{ fontWeight: 500 }}>{c.after}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          {card.more_changes ? (
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 }}>and {card.more_changes} more change{card.more_changes === 1 ? '' : 's'} not listed</div>
          ) : null}
          {card.note && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 8, fontStyle: 'italic' }}>{card.note}</div>}
          {(card.warnings ?? []).map((w, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 5, fontSize: 'var(--fs-sm)', color: 'var(--warn)', marginTop: 6 }}>
              <TriangleAlert size={13} aria-hidden style={{ flex: '0 0 auto', marginTop: 2 }} />
              <span>{w}</span>
            </div>
          ))}
          {children && <div style={{ marginTop: 10 }}>{children}</div>}
          {error && (
            <div role="alert" style={{ marginTop: 10, padding: '8px 12px', fontSize: 'var(--fs-sm)', color: 'var(--error)', background: 'var(--error-bg)', borderRadius: 'var(--radius)' }}>{error}</div>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap', marginTop: 14 }}>
            {onCancel && <Button onClick={onCancel} disabled={busy}>Cancel</Button>}
            <Button size="sm" variant="quiet" onClick={() => void load()} disabled={busy}>Check again</Button>
            <Button variant={danger ? 'danger' : 'primary'} onClick={() => void confirm()} disabled={busy} aria-busy={busy || undefined}>
              {busy ? (busyLabel ?? `${confirmLabel}…`) : confirmLabel}
            </Button>
          </div>
        </div>
      ) : (
        <PageState kind="empty" title="Nothing to change." />
      )}
    </div>
  );
}
