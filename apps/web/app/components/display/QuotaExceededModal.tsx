'use client';

interface Props {
  used: number;
  quota: number;
  onClose: () => void;
  onTopUp?: () => void;
  onUpgrade?: () => void;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export default function QuotaExceededModal({ used, quota, onClose, onTopUp, onUpgrade }: Props) {
  const usagePercent = quota > 0 ? Math.min(100, (used / quota) * 100) : 100;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      backgroundColor: 'rgba(0,0,0,0.5)',
    }}>
      <div style={{
        backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', maxWidth: 420, width: '90%',
        boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '1rem' }}>
          <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>&#9888;&#65039;</div>
          <h2 style={{ fontSize: '1.1rem', fontWeight: 700, color: '#333', margin: 0 }}>Token Limit Reached</h2>
          <p style={{ fontSize: '0.82rem', color: '#666', margin: '0.5rem 0 0' }}>
            You&apos;ve used all your tokens for this billing period.
          </p>
        </div>

        {/* Usage bar */}
        <div style={{ marginBottom: '1rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: '#888', marginBottom: 4 }}>
            <span>{formatTokens(used)} used</span>
            <span>{formatTokens(quota)} limit</span>
          </div>
          <div style={{ height: 10, backgroundColor: '#e2e8f0', borderRadius: 5, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${usagePercent}%`, backgroundColor: '#e53e3e', borderRadius: 5 }} />
          </div>
        </div>

        <p style={{ fontSize: '0.78rem', color: '#666', textAlign: 'center', margin: '0 0 1rem' }}>
          You can still use the roster, hiring board, and other UI components. AI-powered features are paused until you add more tokens.
        </p>

        {/* Actions */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {onTopUp && (
            <button
              onClick={onTopUp}
              style={{
                padding: '10px 16px', fontSize: '0.85rem', fontWeight: 600, border: 'none', borderRadius: 8,
                backgroundColor: '#2563eb', color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
              }}
            >Buy More Tokens ($10 / 500K)</button>
          )}
          {onUpgrade && (
            <button
              onClick={onUpgrade}
              style={{
                padding: '10px 16px', fontSize: '0.85rem', fontWeight: 600,
                border: '1px solid #2563eb', borderRadius: 8,
                backgroundColor: '#fff', color: '#2563eb', cursor: 'pointer', fontFamily: 'inherit',
              }}
            >Upgrade Plan</button>
          )}
          <button
            onClick={onClose}
            style={{
              padding: '8px 16px', fontSize: '0.78rem', border: 'none', borderRadius: 8,
              backgroundColor: 'transparent', color: '#888', cursor: 'pointer', fontFamily: 'inherit',
            }}
          >Close</button>
        </div>
      </div>
    </div>
  );
}
