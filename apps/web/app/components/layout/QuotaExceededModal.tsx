'use client';

import { TriangleAlert } from 'lucide-react';
import Button from '../ui/Button';

interface Props {
  used: number;
  quota: number;
  onClose: () => void;
  /** Opens Settings → Billing, where tokens are topped up. */
  onTopUp?: () => void;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export default function QuotaExceededModal({ used, quota, onClose, onTopUp }: Props) {
  const usagePercent = quota > 0 ? Math.min(100, (used / quota) * 100) : 100;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      backgroundColor: 'rgba(26, 26, 26, 0.35)', padding: 16,
    }}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="quota-title" style={{
        backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)', padding: 24, maxWidth: 420, width: '100%',
        boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>
          <div style={{ width: 44, height: 44, margin: '0 auto 12px', borderRadius: 'var(--radius-lg)', background: 'var(--warn-bg)', color: 'var(--warn)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <TriangleAlert size={22} strokeWidth={1.75} aria-hidden />
          </div>
          <h2 id="quota-title" style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--text)', margin: 0 }}>Token limit reached</h2>
          <p style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', margin: '6px 0 0' }}>
            You&apos;ve used all your tokens for this billing period.
          </p>
        </div>

        {/* Usage */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 4, fontVariantNumeric: 'tabular-nums' }}>
            <span>{formatTokens(used)} used</span>
            <span>{formatTokens(quota)} limit</span>
          </div>
          <div style={{ height: 8, backgroundColor: 'var(--line-soft)', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${usagePercent}%`, backgroundColor: 'var(--warn)', borderRadius: 4 }} />
          </div>
        </div>

        <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', textAlign: 'center', margin: '0 0 20px' }}>
          You can still use the roster, hiring board, and other pages. AI features are paused until you add more tokens.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {onTopUp && <Button variant="primary" onClick={onTopUp}>Top up ($10 / 500K tokens)</Button>}
          <Button variant="quiet" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}
