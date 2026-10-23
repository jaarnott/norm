'use client';

/**
 * The card `page_writes.preview` returns for a Xero bill (the shape
 * `services/previews.py` builds: title, target, venue, the changes that
 * will land, warnings, a note). The person confirms what they see; the
 * fingerprint travels with it so the write refuses if Xero moved.
 */
import { TriangleAlert } from 'lucide-react';
import Icon from '../../../ui/Icon';

export interface PreviewChange {
  field: string;
  before?: string | null;
  after: string;
}

export interface PreviewCardData {
  title?: string;
  target?: string | null;
  venue?: string | null;
  changes?: PreviewChange[];
  warnings?: string[];
  note?: string | null;
  more_changes?: number;
}

export default function PreviewCard({ card }: { card: PreviewCardData }) {
  const changes = card.changes ?? [];
  return (
    <div className="n-card" style={{ padding: '12px 14px', background: 'var(--surface)' }}>
      <div style={{ fontWeight: 600, fontSize: 'var(--fs-base)' }}>{card.title ?? 'Bill'}</div>
      {(card.target || card.venue) && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2 }}>{[card.target, card.venue].filter(Boolean).join(' · ')}</div>
      )}
      {changes.length > 0 && (
        <dl style={{ margin: '10px 0 0', display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 12px', fontSize: 'var(--fs-sm)' }}>
          {changes.map((c, i) => (
            <div key={`${c.field}-${i}`} style={{ display: 'contents' }}>
              <dt style={{ color: 'var(--muted)' }}>{c.field}</dt>
              <dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{c.before ? <><span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>{c.before}</span> → </> : null}{c.after}</dd>
            </div>
          ))}
        </dl>
      )}
      {!!card.more_changes && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 4 }}>and {card.more_changes} more</div>}
      {(card.warnings ?? []).length > 0 && (
        <ul style={{ margin: '10px 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {card.warnings!.map((w, i) => (
            <li key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
              <Icon icon={TriangleAlert} size={14} style={{ marginTop: 2 }} /> <span>{w}</span>
            </li>
          ))}
        </ul>
      )}
      {card.note && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 8 }}>{card.note}</div>}
    </div>
  );
}
