'use client';

/**
 * The signed-in person's initial: one style for everyone (it used to be black
 * for admins and mint-with-white-text — 1.4:1 — for everyone else).
 */
export default function Avatar({ name, size = 32, title }: { name: string; size?: number; title?: string }) {
  return (
    <div
      title={title}
      aria-hidden={title ? undefined : true}
      style={{
        flex: '0 0 auto',
        width: size,
        height: size,
        borderRadius: '50%',
        background: 'var(--selected)',
        color: 'var(--accent-strong)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size >= 32 ? 'var(--fs-sm)' : 'var(--fs-xs)',
        fontWeight: 600,
        userSelect: 'none',
      }}
    >
      {(name || '?').trim().charAt(0).toUpperCase()}
    </div>
  );
}
