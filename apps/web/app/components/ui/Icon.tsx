'use client';

import type { CSSProperties } from 'react';
import type { LucideIcon } from 'lucide-react';

/**
 * Every icon goes through here: lucide line icons in one neutral colour, at a
 * size named for its role. `duo` adds the duotone under-layer (the same icon
 * drawn first with its closed shapes filled tan, tokens.css .duo-fill) — only
 * for the ACTIVE item in the rail and menu, never as decoration.
 *
 * Presentational and free of next/* imports: Claude's MCP bundle uses it too.
 */
export const ICON_SIZES = { nav: 22, mobileNav: 24, menu: 18, inline: 16, dense: 14, meta: 12 } as const;
export type IconSize = keyof typeof ICON_SIZES | number;

/** inherit = currentColor; muted = inactive icons; strong = active/primary text. */
export type IconTone = 'inherit' | 'muted' | 'strong' | 'accent';
const TONE: Record<IconTone, string | undefined> = {
  inherit: undefined,
  muted: 'var(--icon)',
  strong: 'var(--text)',
  accent: 'var(--accent)',
};

interface IconProps {
  icon: LucideIcon;
  size?: IconSize;
  tone?: IconTone;
  duo?: boolean;
  strokeWidth?: number;
  style?: CSSProperties;
  className?: string;
  /** Announce the icon. Omit for decorative icons (the default): aria-hidden. */
  label?: string;
}

export default function Icon({ icon: Glyph, size = 'inline', tone = 'inherit', duo = false, strokeWidth = 1.75, style, className, label }: IconProps) {
  const px = typeof size === 'number' ? size : ICON_SIZES[size];
  const color = TONE[tone];
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true };
  const glyph = (
    <Glyph
      size={px}
      strokeWidth={strokeWidth}
      className={className}
      style={{ flex: '0 0 auto', ...(color ? { color } : {}), ...(duo ? { position: 'relative' } : {}), ...style }}
      {...a11y}
    />
  );
  if (!duo) return glyph;
  return (
    <span style={{ position: 'relative', display: 'inline-flex', flex: '0 0 auto', width: px, height: px, ...(color ? { color } : {}) }}>
      <Glyph size={px} className="duo-fill" aria-hidden style={{ position: 'absolute', inset: 0 }} />
      {glyph}
    </span>
  );
}
