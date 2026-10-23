/**
 * Display formatting shared by the finance pages: plain dates and instants
 * (Norm Functions' helpers — a plan date is never shifted through the
 * browser's zone; a fact is shown in the venue's zone), hours and
 * percentages, and the clock-in times a Payrun row carries.
 */
export { dateLabel, instantLabel, relativeLabel, timeLabel } from '../../functions/shared/format';
export { formatCents, formatHours, formatSignedCents, parseCents, centsToInput } from './money';

/** 0.289 → "28.9%"; null → an em dash. */
export function pct(v: number | null | undefined, dp = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(dp)}%`;
}

/** 1200 basis points → "12%". */
export function bpLabel(bp: number | null | undefined): string {
  if (bp === null || bp === undefined || !Number.isFinite(bp)) return '—';
  return `${(bp / 100).toLocaleString('en-NZ', { maximumFractionDigits: 2 })}%`;
}

/** A count with a thousands separator. */
export function num(v: number | string | null | undefined, dp = 0): string {
  if (v === null || v === undefined || v === '') return '—';
  const n = typeof v === 'string' ? Number(v) : v;
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-NZ', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

/** "Sat 18 Oct, 6:30 pm – 11:05 pm" for a clock-in and clock-out, in the venue's zone. */
export function clockLabel(inIso: string | null | undefined, outIso: string | null | undefined, timeZone = 'Pacific/Auckland'): string {
  if (!inIso) return '—';
  const start = new Date(inIso);
  if (Number.isNaN(start.getTime())) return inIso;
  const day = start.toLocaleDateString('en-NZ', { timeZone, weekday: 'short', day: 'numeric', month: 'short' }).replace(',', '');
  const t = (d: Date) => d.toLocaleTimeString('en-NZ', { timeZone, hour: 'numeric', minute: '2-digit' });
  if (!outIso) return `${day}, ${t(start)} – (still clocked in)`;
  const end = new Date(outIso);
  if (Number.isNaN(end.getTime())) return `${day}, ${t(start)}`;
  return `${day}, ${t(start)} – ${t(end)}`;
}

/** "3 people" / "1 shift" — a count with its noun. */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString('en-NZ')} ${n === 1 ? one : many}`;
}

/** Staff names arrive "Last, First" from Loaded; show them as people say them. */
export function personName(name: string | null | undefined): string {
  if (!name) return '—';
  const m = /^([^,]+),\s*(.+)$/.exec(name);
  return m ? `${m[2]} ${m[1]}` : name;
}
