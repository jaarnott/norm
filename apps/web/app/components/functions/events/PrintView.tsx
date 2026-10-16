'use client';

/**
 * Print sheets: the full event sheet, the run sheet (no food) and the
 * kitchen's food pre-order, laid out from `GET /events/{id}/print/{kind}`.
 *
 * The sheet opens as a preview over the page and prints only itself: it is
 * portalled to <body> (so no scrolling ancestor clips it), and its @media
 * print rules hide everything else on the page and the toolbar. Fields the
 * venue switched off arrive as null and are simply not printed.
 */
import { useEffect, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Printer, X } from 'lucide-react';
import Button from '../../ui/Button';
import PageState from '../../ui/PageState';
import { fnGet } from '../shared/api';
import { dateLabel, instantLabel, money, timeLabel, timeRangeLabel } from '../shared/format';
import { AV_LABEL, ENTERTAINMENT_LABEL, STATUS_LABEL, depositStatusLabel, foodTimingText, parseRestrictions, paymentLabel } from './logic';
import { useLoad, errorText } from './useLoad';
import type { PrintKind, PrintSheet, SectionKey } from './types';

export const PRINT_KINDS: { id: PrintKind; label: string; title: string }[] = [
  { id: 'full', label: 'Full event sheet', title: 'Event sheet' },
  { id: 'run_sheet', label: 'Run sheet', title: 'Run sheet' },
  { id: 'food', label: 'Food pre-order', title: 'Kitchen pre-order' },
];

const PRINT_CSS = `
@media print {
  @page { size: A4 portrait; margin: 12mm; }
  html, body { height: auto !important; overflow: visible !important; background: var(--bg) !important; }
  body > *:not(.fn-print-root) { display: none !important; }
  .fn-print-root { position: static !important; inset: auto !important; overflow: visible !important; background: none !important; padding: 0 !important; }
  .fn-print-toolbar { display: none !important; }
  .fn-print-paper { box-shadow: none !important; border: none !important; margin: 0 !important; padding: 0 !important; max-width: none !important; font-size: 10pt !important; }
  .fn-print-section { break-inside: avoid-page; }
  .fn-print-table tr { break-inside: avoid; }
}
`;

const sectionTitle: CSSProperties = {
  margin: '0 0 6px', paddingBottom: 3, borderBottom: '1.5px solid var(--text)',
  fontSize: '1.05em', fontWeight: 700, color: 'var(--text)',
};
const labelStyle: CSSProperties = { color: 'var(--text-soft)', fontWeight: 600, whiteSpace: 'nowrap', verticalAlign: 'top', paddingRight: 12 };
const cell: CSSProperties = { padding: '3px 6px', borderBottom: '1px solid var(--line)', textAlign: 'left', verticalAlign: 'top' };
const headCell: CSSProperties = { ...cell, borderBottom: '1px solid var(--line-strong)', fontWeight: 600, color: 'var(--text-soft)', fontSize: '0.92em' };
const num: CSSProperties = { textAlign: 'right', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' };

function Section({ title, skip, timeZone, children }: {
  title: string;
  skip?: { note: string | null; at: string | null; by: string | null };
  timeZone?: string;
  children: ReactNode;
}) {
  return (
    <section className="fn-print-section" style={{ marginTop: 16 }}>
      <h2 style={sectionTitle}>{title}</h2>
      {skip && (
        <p style={{ margin: '0 0 6px', fontStyle: 'italic', color: 'var(--text-soft)', fontSize: '0.92em' }}>
          Client confirmation skipped{skip.by ? ` by ${skip.by}` : ''}{skip.at ? ` on ${instantLabel(skip.at, timeZone, true)}` : ''}
          {skip.note ? ` — ${skip.note}` : ''}
        </p>
      )}
      {children}
    </section>
  );
}

/** Label/value rows; empty values are left out entirely. */
function Pairs({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false);
  if (!shown.length) return null;
  return (
    <table style={{ borderCollapse: 'collapse', width: '100%' }}>
      <tbody>
        {shown.map(([k, v]) => (
          <tr key={k}>
            <td style={{ ...labelStyle, padding: '2px 12px 2px 0', width: '1%' }}>{k}</td>
            <td style={{ padding: '2px 0', whiteSpace: 'pre-wrap' }}>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Grid({ head, rows }: { head: ReactNode[]; rows: ReactNode[][] }) {
  return (
    <table className="fn-print-table" style={{ borderCollapse: 'collapse', width: '100%', marginTop: 4 }}>
      <thead><tr>{head.map((h, i) => <th key={i} style={headCell}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} style={cell}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

const range = (s: string | null, e: string | null, next = false) => timeRangeLabel(s, e, next) || '—';

export function PrintSheetView({ sheet, timeZone, tablePlanUrl }: { sheet: PrintSheet; timeZone: string; tablePlanUrl?: string | null }) {
  const ev = sheet.event;
  const cur = sheet.currency;
  const title = PRINT_KINDS.find((k) => k.id === sheet.kind)?.title ?? 'Event sheet';
  const skip = (s: SectionKey) => sheet.skips?.[s] ?? undefined;
  const d = sheet.details;
  const t = sheet.timings;
  const b = sheet.beverages;
  const f = sheet.food;

  return (
    <div style={{ color: 'var(--text)', lineHeight: 1.4 }}>
      <header style={{ borderBottom: '2px solid var(--text)', paddingBottom: 8 }}>
        <div style={{ fontSize: '0.85em', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-soft)' }}>{title}</div>
        <h1 style={{ margin: '2px 0 4px', fontSize: '1.6em', fontWeight: 700 }}>{ev.name || 'Event'}</h1>
        <div style={{ fontSize: '1.05em' }}>
          {[
            dateLabel(ev.event_date),
            timeRangeLabel(ev.start_time, ev.end_time, ev.ends_next_day),
            ev.guests ? `${ev.guests} guests` : null,
          ].filter(Boolean).join(' · ')}
        </div>
        <div style={{ color: 'var(--text-soft)' }}>
          {[ev.venue_name, ev.area_name, ev.booking_label].filter(Boolean).join(' · ')}
          {' · '}
          {STATUS_LABEL[ev.status]}
          {ev.edited_since_confirmed ? ' (edited since confirmed)' : ''}
        </div>
      </header>

      {sheet.cancelled && (
        <div style={{ marginTop: 12, padding: '8px 10px', border: '2px solid var(--error)', color: 'var(--error)', fontWeight: 700 }}>
          Cancelled{sheet.cancelled.at ? ` ${instantLabel(sheet.cancelled.at, timeZone, true)}` : ''}
          {sheet.cancelled.reason ? ` — ${sheet.cancelled.reason}` : ''}
        </div>
      )}

      <Section title="Contact">
        <Pairs rows={[
          ['Name', ev.contact.name],
          ['Company', ev.contact.company],
          ['Email', ev.contact.email],
          ['Phone', ev.contact.phone],
        ]} />
      </Section>

      {d && (
        <Section title="Details" skip={skip('details')} timeZone={timeZone}>
          <Pairs rows={[
            ['Min spend', d.min_spend_cents !== null ? `${money(d.min_spend_cents, cur)} ${sheet.tax_label}` : null],
            ['Payment', paymentLabel(d.payment_method)],
            ['Deposit', d.deposit ? [money(d.deposit.cents, cur), depositStatusLabel(d.deposit.status)].filter(Boolean).join(' — ') : null],
            ['Description', d.description],
            ['Notes', d.notes],
            ['Special instructions', d.special_instructions],
            ['Table plan', d.table_plan],
          ]} />
          {tablePlanUrl && (
            // eslint-disable-next-line @next/next/no-img-element -- an authenticated blob: URL, not a static asset
            <img src={tablePlanUrl} alt="Table plan" style={{ display: 'block', maxWidth: '100%', maxHeight: '120mm', marginTop: 8, objectFit: 'contain' }} />
          )}
          {d.security && d.security.length > 0 && (
            <>
              <div style={{ ...labelStyle, marginTop: 8 }}>Security</div>
              <Grid head={['Name', 'Time', 'Notes']} rows={d.security.map((s) => [s.name || '—', range(s.start_time, s.end_time), s.notes || ''])} />
            </>
          )}
          {d.entertainment && d.entertainment.length > 0 && (
            <>
              <div style={{ ...labelStyle, marginTop: 8 }}>Entertainment</div>
              <Grid
                head={['Type', 'Name', 'Time', 'Link / notes']}
                rows={d.entertainment.map((e) => [ENTERTAINMENT_LABEL[e.kind] ?? e.kind, e.name || '—', range(e.start_time, e.end_time), [e.link, e.notes].filter(Boolean).join(' · ')])}
              />
            </>
          )}
          {d.av && d.av.length > 0 && (
            <>
              <div style={{ ...labelStyle, marginTop: 8 }}>A/V</div>
              <Grid head={['Type', 'Details']} rows={d.av.map((a) => [AV_LABEL[a.kind] ?? a.kind, a.details || ''])} />
            </>
          )}
        </Section>
      )}

      {t && (
        <Section title="Timings">
          <Pairs rows={[
            ['Arrival', timeLabel(t.arrival_time)],
            ['Seated', timeLabel(t.seated_time)],
            ['Preference', t.preference],
            ['Notes', t.notes],
          ]} />
          {t.schedule && t.schedule.length > 0 && (
            <table className="fn-print-table" style={{ borderCollapse: 'collapse', width: '100%', marginTop: 6 }}>
              <thead><tr><th style={{ ...headCell, width: '22%' }}>Time</th><th style={headCell}>Activity</th></tr></thead>
              <tbody>
                {t.schedule.map((r, i) => r.is_heading ? (
                  <tr key={i}><td colSpan={2} style={{ ...cell, fontWeight: 700, paddingTop: 8 }}>{r.activity}</td></tr>
                ) : (
                  <tr key={i}>
                    <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                      {[timeLabel(r.at_time) + (r.at_time && r.ends_next_day ? ' (next day)' : ''), r.label].filter(Boolean).join(' · ') || '—'}
                    </td>
                    <td style={cell}>{r.activity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {b && (
        <Section title="Beverages" skip={skip('beverages')} timeZone={timeZone}>
          <Pairs rows={[
            ['Bar tab', b.bar_tab_cents !== null ? (b.bar_tab_cents > 0 ? money(b.bar_tab_cents, cur) : 'No bar tab') : null],
            ['Authorised people', b.authorised_people],
            ['Restrictions', parseRestrictions(b.restrictions).join(', ')],
            ['Arrival drinks', b.arrival_drinks],
            ['Table ordering', b.table_ordering ? (b.table_ordering.on ? `Yes${b.table_ordering.limit_cents ? ` — limit ${money(b.table_ordering.limit_cents, cur)}` : ''}` : 'No') : null],
            ['Notes', b.notes],
          ]} />
        </Section>
      )}

      {f && (
        <Section title="Food" skip={skip('food')} timeZone={timeZone}>
          {f.allergies && (
            <div style={{ margin: '2px 0 8px', padding: '6px 8px', border: '1.5px solid var(--text)', fontWeight: 600 }}>
              Allergies and dietary: <span style={{ fontWeight: 400, whiteSpace: 'pre-wrap' }}>{f.allergies}</span>
            </div>
          )}
          <Pairs rows={[
            ['Food timing', f.timing && f.timing.type ? foodTimingText(f.timing.type, f.timing.time, f.timing.end_time) : null],
            ['Notes', f.notes],
          ]} />
          {f.menus.map((m) => (
            <div key={m.id} className="fn-print-section" style={{ marginTop: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontWeight: 700 }}>
                <span>{m.name}{m.is_set_menu ? ` — set menu, ${m.guest_count} × ${money(m.price_per_person_cents, cur)}` : ''}</span>
                {sheet.kind === 'full' && <span style={num}>{money(m.total_cents, cur)}</span>}
              </div>
              {m.lines.length ? (
                <table className="fn-print-table" style={{ borderCollapse: 'collapse', width: '100%', marginTop: 4 }}>
                  <thead>
                    <tr>
                      <th style={headCell}>Dish</th>
                      <th style={{ ...headCell, ...num, width: '10%' }}>Qty</th>
                      {sheet.kind === 'full' && !m.is_set_menu && <th style={{ ...headCell, ...num, width: '14%' }}>Price</th>}
                      {sheet.kind === 'full' && !m.is_set_menu && <th style={{ ...headCell, ...num, width: '14%' }}>Total</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {m.lines.map((l, i) => l.kind === 'header' ? (
                      <tr key={i}><td colSpan={4} style={{ ...cell, fontWeight: 700, paddingTop: 6 }}>{l.name}</td></tr>
                    ) : (
                      <tr key={i}>
                        <td style={cell}>{l.name}{l.dietaries && l.dietaries.length ? ` (${l.dietaries.join(', ')})` : ''}</td>
                        <td style={{ ...cell, ...num }}>{l.quantity}</td>
                        {sheet.kind === 'full' && !m.is_set_menu && <td style={{ ...cell, ...num }}>{l.unit_price_cents !== null && l.unit_price_cents !== undefined ? money(l.unit_price_cents, cur) : ''}</td>}
                        {sheet.kind === 'full' && !m.is_set_menu && <td style={{ ...cell, ...num }}>{l.line_total_cents !== null && l.line_total_cents !== undefined ? money(l.line_total_cents, cur) : ''}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div style={{ color: 'var(--text-soft)', fontStyle: 'italic' }}>No dishes ordered yet.</div>
              )}
            </div>
          ))}
          {!f.menus.length && <div style={{ color: 'var(--text-soft)', fontStyle: 'italic' }}>No pre-order menus.</div>}
          {sheet.kind === 'food' && f.kitchen_counts.length > 0 && (
            <div className="fn-print-section" style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 700 }}>Kitchen counts (all menus)</div>
              <Grid
                head={['Dish', <span key="q" style={num}>Qty</span>]}
                rows={f.kitchen_counts.map((k) => [`${k.name}${k.dietaries.length ? ` (${k.dietaries.join(', ')})` : ''}`, <span key="n" style={{ ...num, display: 'block' }}>{k.quantity}</span>])}
              />
            </div>
          )}
        </Section>
      )}

      {sheet.money && (
        <Section title="Money">
          <Pairs rows={[
            ['Expected spend', `${money(sheet.money.expected_spend_cents, cur)} ${sheet.tax_label}`],
            ['Min spend', sheet.money.min_spend_cents !== null ? money(sheet.money.min_spend_cents, cur) : null],
            ['Food', money(sheet.money.food_cents, cur)],
            ['Bar tab', money(sheet.money.bar_tab_cents, cur)],
          ]} />
        </Section>
      )}

      <footer style={{ marginTop: 18, fontSize: '0.85em', color: 'var(--text-soft)' }}>
        Printed from Norm {instantLabel(sheet.generated_at, timeZone, true)}
      </footer>
    </div>
  );
}

/** The page rules inside the print frame: the sheet alone on A4. */
const FRAME_CSS = `
@page { size: A4 portrait; margin: 12mm; }
html, body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-sans); }
body { font-size: 10pt; line-height: 1.4; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.fn-print-section { break-inside: avoid-page; }
.fn-print-table tr { break-inside: avoid; }
@media screen { body { padding: 24px; } }
`;

const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/**
 * The print document: the rendered sheet with the app's own stylesheets (for
 * the design tokens and fonts), as a complete HTML page. Printing it from a
 * hidden frame means only the sheet is laid out for paper — the app around
 * it never re-lays itself out at paper width.
 */
export function printDocument(sheetHtml: string, title: string): string {
  // The head's styles only — the preview's own @media print rules (in the
  // body) would hide the sheet inside the frame.
  const styles = Array.from(document.head.querySelectorAll('style, link[rel="stylesheet"]'))
    .map((node) => node.outerHTML)
    .join('\n');
  const htmlClass = escapeHtml(document.documentElement.className || '');
  const bodyClass = escapeHtml(document.body.className || '');
  return `<!doctype html><html class="${htmlClass}"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>${styles}<style>${FRAME_CSS}</style></head><body class="${bodyClass}">${sheetHtml}</body></html>`;
}

/** The preview overlay with Print / Close. */
export default function PrintView({ eventId, kind, timeZone, tablePlanUrl, onClose }: {
  eventId: string;
  kind: PrintKind;
  timeZone: string;
  tablePlanUrl?: string | null;
  onClose: () => void;
}) {
  const { data, error, loading } = useLoad(`print:${eventId}:${kind}`, () => fnGet<PrintSheet>(`/events/${eventId}/print/${kind}`));
  const sheetRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      frameRef.current?.remove();
      frameRef.current = null;
    };
  }, [onClose]);

  const label = PRINT_KINDS.find((k) => k.id === kind)?.label ?? 'Print';
  const title = `${data?.event.name || 'Event'} — ${PRINT_KINDS.find((k) => k.id === kind)?.title ?? 'Event sheet'}`;

  const printNow = () => {
    const sheet = sheetRef.current;
    if (!sheet) return;
    frameRef.current?.remove();
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.setAttribute('data-fn-print-frame', kind);
    frame.title = title;
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    frame.onload = () => {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
    };
    frame.srcdoc = printDocument(sheet.innerHTML, title);
    document.body.appendChild(frame);
    frameRef.current = frame;
  };

  return createPortal(
    <div
      className="fn-print-root"
      role="dialog"
      aria-modal="true"
      aria-label={`${label} — print preview`}
      style={{ position: 'fixed', inset: 0, zIndex: 1100, overflowY: 'auto', background: 'rgba(26, 26, 26, 0.45)', padding: '16px 12px 40px' }}
    >
      {/* Ctrl+P while the preview is open prints the preview alone too. */}
      <style>{PRINT_CSS}</style>
      <div className="fn-print-toolbar" style={{ maxWidth: 820, margin: '0 auto 12px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--on-primary)', fontWeight: 600, fontSize: 'var(--fs-base)', marginRight: 'auto' }}>{label}</span>
        <Button icon={X} onClick={onClose}>Close</Button>
        <Button variant="primary" icon={Printer} disabled={!data} onClick={printNow}>Print</Button>
      </div>
      <div
        className="fn-print-paper"
        style={{ maxWidth: 820, margin: '0 auto', background: 'var(--bg)', borderRadius: 'var(--radius)', padding: '28px 32px', boxShadow: '0 12px 40px rgba(26, 26, 26, 0.25)', fontSize: 'var(--fs-sm)' }}
      >
        {loading && <PageState kind="loading" title="Preparing the sheet…" />}
        {error !== undefined && <PageState kind="error" title="Couldn’t prepare the sheet" detail={errorText(error)} />}
        {data && (
          <div ref={sheetRef}>
            <PrintSheetView sheet={data} timeZone={timeZone} tablePlanUrl={tablePlanUrl} />
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
