/**
 * What `GET /api/payroll/xero/journal` returns (F4's `journal.current`):
 * the stored journal draft and ICO drafts with their push state, what the
 * inputs would build NOW (`proposed`), and the blockers as they stand.
 */
import type { IcoLeaveTax, IcoLine, JournalLine, PushBrief } from '../types';

export interface StoredJournal {
  id: string;
  kind: 'journal';
  week_id: string;
  snapshot_id: string | null;
  lines: JournalLine[];
  debit_cents: number;
  credit_cents: number;
  residual_cents: number;
  basis_sha256: string;
  built_at: string | null;
  built_by: string | null;
  /** The inputs moved since this draft was built — build again before pushing. */
  stale: boolean;
  journal: Record<string, unknown>;
  narration: string;
  live_push: PushBrief | null;
}

export interface StoredIco {
  id: string;
  kind: 'ico_invoice' | 'ico_bill';
  week_id: string;
  counterparty_venue_id: string | null;
  counterparty: string;
  contact: { contact_id: string; name: string | null } | null;
  lines: (IcoLine | { key: 'leave_uplift'; bp: number; amount_cents: number })[];
  wages_cents: number;
  uplift_bp: number | null;
  uplift_cents: number;
  subtotal_cents: number;
  basis_sha256: string;
  built_at: string | null;
  stale: boolean;
  live_push: PushBrief | null;
}

export interface JournalDocument {
  venue_id: string;
  venue: string;
  week_id: string | null;
  week_start: string;
  week_ending: string;
  journal: StoredJournal | null;
  ico: StoredIco[];
  proposed: {
    lines: JournalLine[];
    debit_cents: number;
    credit_cents: number;
    residual_cents: number;
    notes: string[];
    ico_documents: unknown[];
    basis_sha256: string;
  };
  blockers: string[];
  warnings: string[];
  intercompany: { wages_account: string | null; leave_account: string | null; uplift_bp: number; leave_tax?: IcoLeaveTax };
  live_pushes: PushBrief[];
}

/** Plain words for a Xero document's status. */
export function xeroStatusTone(status: string | null | undefined): 'neutral' | 'ok' | 'warn' | 'error' | 'info' {
  switch ((status || '').toUpperCase()) {
    case 'PAID': return 'ok';
    case 'POSTED':
    case 'AUTHORISED': return 'info';
    case 'SUBMITTED': return 'warn';
    case 'VOIDED':
    case 'DELETED': return 'error';
    default: return 'neutral';
  }
}

export function kindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case 'journal': return 'Payroll journal';
    case 'ico_invoice': return 'Intercompany invoice';
    case 'ico_bill': return 'Intercompany bill';
    case 'entertainer_bill': return 'Entertainer bill';
    default: return kind || '—';
  }
}
