'use client';

import { TriangleAlert } from 'lucide-react';
import Button from '../ui/Button';

interface Props {
  onClose: () => void;
  /** Opens Settings → Billing, where the plan is restarted. */
  onOpenBilling?: () => void;
}

/**
 * Shown when Norm refuses a message because the organisation's plan isn't
 * active (cancelled or past due). Using up the month's allowance never
 * lands here: Norm carries on with its lighter model instead (Oct 2026).
 */
export default function QuotaExceededModal({ onClose, onOpenBilling }: Props) {
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
        <div style={{ textAlign: 'center', marginBottom: 20 }}>
          <div style={{ width: 44, height: 44, margin: '0 auto 12px', borderRadius: 'var(--radius-lg)', background: 'var(--warn-bg)', color: 'var(--warn)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <TriangleAlert size={22} strokeWidth={1.75} aria-hidden />
          </div>
          <h2 id="quota-title" style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--text)', margin: 0 }}>Norm is paused</h2>
          <p style={{ fontSize: 'var(--fs-base)', color: 'var(--text-soft)', margin: '6px 0 0' }}>
            This organisation&apos;s plan isn&apos;t active, so Norm can&apos;t answer right now. The roster, hiring board and other pages still work.
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {onOpenBilling && <Button variant="primary" onClick={onOpenBilling}>Open billing</Button>}
          <Button variant="quiet" onClick={onClose}>Close</Button>
        </div>
      </div>
    </div>
  );
}
